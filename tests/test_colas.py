# -*- coding: utf-8 -*-
"""Varias fichas a la vez: dos encargos avanzan a la par, cada lote se cierra una sola vez y el total tarda
mucho menos que uno detras de otro. La agencia y el modelo son de mentira (esperas cortas)."""
import threading
import time

import pytest

servidor = pytest.importorskip("servidor")


def lote(id_, numeros):
    return {"id": id_, "tipo": "numeros", "nombre": id_, "estado": "pendiente", "creado": "08/10/2026 10:00",
            "fichas": [{"id": f"{id_}{n}", "numero": n, "fecha": "", "etiqueta": n, "creado": "", "estado": "pendiente"}
                       for n in numeros]}


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.setattr(servidor.ESTADO, "guardar", lambda: None)
    monkeypatch.setattr(servidor.ESTADO, "lotes", [lote("B", ["0003", "0004", "0005"]), lote("A", ["0001", "0002", "0006"])])
    monkeypatch.setattr(servidor.ESTADO, "sesion_agencia", "abierta")
    for clave, valor in {"colas": 3, "claude_model": "sonnet", "claude_extra_args": [], "claude_timeout": 60,
                         "acortar_comment": False}.items():
        monkeypatch.setitem(servidor.CFG, clave, valor)
    orden, cerrados = [], []

    class Navegador:
        def fetch(self, numero, fecha=None, creado=None):
            time.sleep(0.05)
            orden.append(numero)
            return {"headline": "H", "fecha": "08/10/2026"}

    def redactar(datos, **kw):
        time.sleep(0.6)
        return {"ENVIO": "", "NAME": "N", "COMMENT": "C", "RESTRICCIONES": "SIN AVISO"}, [], None

    monkeypatch.setattr(servidor, "redactar", redactar)
    monkeypatch.setattr(servidor, "guardar_csv", lambda c, a: None)
    monkeypatch.setattr(servidor, "quiere_normal", lambda: False)
    w = servidor.Worker()
    monkeypatch.setattr(w, "abrir_navegador", lambda: Navegador())
    monkeypatch.setattr(w, "_terminar_lote", lambda l: cerrados.append(l["id"]))
    return w, orden, cerrados


def test_dos_encargos_a_la_par(worker):
    w, orden, cerrados = worker
    threading.Thread(target=w.run, daemon=True).start()
    t0 = time.time()
    while len(cerrados) < 2 and time.time() - t0 < 10:
        time.sleep(0.05)
    tardado = time.time() - t0
    w.parar = True
    assert sorted(cerrados) == ["A", "B"]                     # cada lote se cierra una vez
    assert all(f["estado"] == "hecha" for l in servidor.ESTADO.lotes for f in l["fichas"])
    assert {orden[0][-1], orden[1][-1]} == {"1", "3"}         # empieza por los dos encargos, no por uno entero
    assert tardado < 6 * 0.6 * 0.7, f"tardo {tardado:.1f} s: no va en paralelo"
    assert servidor.ESTADO.trabajando is None


def test_pausa_del_modelo_devuelve_el_lote_a_la_cola(worker, monkeypatch):
    w, orden, cerrados = worker
    llamadas = []

    def redactar(datos, **kw):
        llamadas.append(1)
        time.sleep(0.2)
        if len(llamadas) >= 2:
            raise servidor.ModeloNoDisponible("limite de uso", 60)
        return {"ENVIO": "", "NAME": "N", "COMMENT": "C", "RESTRICCIONES": "SIN AVISO"}, [], None

    monkeypatch.setattr(servidor, "redactar", redactar)
    monkeypatch.setitem(servidor.CFG, "colas", 1)
    w2 = servidor.Worker()
    monkeypatch.setattr(w2, "abrir_navegador", w.abrir_navegador)
    monkeypatch.setattr(w2, "_terminar_lote", w._terminar_lote)
    threading.Thread(target=w2.run, daemon=True).start()
    t0 = time.time()
    while not w2.pausa_hasta and time.time() - t0 < 5:
        time.sleep(0.05)
    time.sleep(0.5)
    w2.parar = True
    assert w2.pausa_hasta and not cerrados                   # en pausa: ningun lote se cierra ni manda correos
    estados = [f["estado"] for l in servidor.ESTADO.lotes for f in l["fichas"]]
    assert estados.count("hecha") == 1 and "en curso" not in estados
    assert all(l["estado"] == "pendiente" for l in servidor.ESTADO.lotes)


def test_colas_se_leen_al_arrancar_no_al_crear(monkeypatch):
    # el Worker de verdad se crea al importar servidor.py, con CFG aun vacio: las colas se fijan en run()
    monkeypatch.setattr(servidor, "CFG", {})
    w = servidor.Worker()
    monkeypatch.setattr(servidor, "CFG", {"colas": 3})
    monkeypatch.setattr(w, "_vuelta", lambda: setattr(w, "parar", True))
    w.run()
    assert w.colas == 3


def test_sesion_caducada_reintenta_y_avisa_una_vez(worker, monkeypatch):
    w, orden, cerrados = worker
    correos = []
    monkeypatch.setattr(servidor, "enviar", lambda cfg, a, asunto, t, **k: correos.append(asunto))
    for clave, valor in {"correo_copia": "yo@x", "correo_usuario": "u", "correo_clave": "k"}.items():
        monkeypatch.setitem(servidor.CFG, clave, valor)
    monkeypatch.setattr(servidor, "ESPERA_AGENCIA", 0.6)

    class Caducada:
        def fetch(self, *a, **k):
            raise servidor.NeedsLogin("Reuters pide iniciar sesion")

    caducada = [True]
    navegador = w.abrir_navegador
    monkeypatch.setattr(w, "abrir_navegador", lambda: Caducada() if caducada[0] else navegador())
    monkeypatch.setattr(w, "cerrar_navegador", lambda: None)
    threading.Thread(target=w.run, daemon=True).start()
    time.sleep(0.4)
    estados = [f["estado"] for l in servidor.ESTADO.lotes for f in l["fichas"]]
    assert "error" not in estados and w.pausa_agencia_hasta     # nada en error: todo espera
    assert correos == ["Catalogator: sesion de agencias caducada"]
    caducada[0] = False                                          # vuelve la sesion: sigue sola
    t0 = time.time()
    while len(cerrados) < 2 and time.time() - t0 < 10:
        time.sleep(0.05)
    w.parar = True
    assert sorted(cerrados) == ["A", "B"] and len(correos) == 1
