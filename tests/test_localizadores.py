# -*- coding: utf-8 -*-
"""Localizadores: los envios que en la lista piden "TRATAR AGENCIA CON LOCALIZADORES" llevan, ademas de la ficha, los
localizadores sacados del shotlist. Solo se ven en el panel: ni correo, ni MediaCentral."""
import threading
import time
from unittest import mock

import correo
import redactor
import servidor
from listas import leer_lista
from test_colas import lote, worker  # noqa: F401  (fixture)

SHOTLIST = ("VIDEO SHOWS: KIEV, UKRAINE (OCTOBER 12, 2026) (REUTERS - ACCESS ALL)\n"
            "1. AERIAL OF DAMAGED BUILDING\n2. JOURNALISTS FILMING\n"
            "3. (SOUNDBITE) (Ukrainian) MAYOR VITALI KLITSCHKO, SAYING:\nSTORY: Russia attacked Kyiv overnight.")


def test_la_lista_marca_los_envios_con_localizadores():
    csv = ('Name,Catalogador,Comments,Created\n'
           '"0707 FRANCE-PROTEST: Police",,"TRATAR AGENCIA CON LOCALIZADORES",""\n'
           '"0708 FRANCE-X: Y",,"Tratar agencias con localizadores",""\n'
           '"0709 FRANCE-Z: W",,"TRATAR AGENCIAS",""\n')
    envios, _, _ = leer_lista("Search Results.csv", csv.encode("utf-8"))
    assert {e["numero"]: e["localizadores"] for e in envios} == {"0707": True, "0708": True, "0709": False}
    assert all(e["texto"] == "" for e in envios)              # la nota no es texto de agencia: se va a la web


def test_se_leen_las_lineas_del_modelo_y_lo_demas_se_descarta():
    respuesta = ("1|Kiev|Ucrania|12/10/2026|Vista aérea de un edificio dañado|\n"
                 "Esto no es un localizador\n"
                 "3 | KIEV | UCRANIA | 12/10/2026 | DECLARACIONES DE VITALI KLITSCHKO, ALCALDE DE KIEV, SOBRE EL ATAQUE |\n"
                 "5|VARSOVIA|POLONIA|13/10/2026|LLEGADA DE REFUGIADOS A LA ESTACION|ROTULAR CORTESIA DE ''TVP''\n"
                 "6|VARSOVIA|POLONIA|13/10/2026|CENTRO DE ACOGIDA|SIN AVISO\n")      # SIN AVISO no es restriccion
    datos = {"texto": SHOTLIST, "agencia": "REUTERS", "numero": "0707", "fecha": "12/10/2026", "headline": "H"}
    with mock.patch("redactor.llamar_modelo", return_value=respuesta) as llamada:
        locs = redactor.localizadores(datos)
    assert locs == [
        {"plano": "1", "texto": "KIEV. DOCE DE OCTUBRE DE 2026. VISTA AEREA DE UN EDIFICIO DAÑADO."},
        {"plano": "3", "texto": "KIEV. DOCE DE OCTUBRE DE 2026. DECLARACIONES DE VITALI KLITSCHKO, ALCALDE DE KIEV, "
                                "SOBRE EL ATAQUE."},
        # cambia de pais: se dice a cual; y la restriccion del plano, al final
        {"plano": "5", "texto": "VARSOVIA (POLONIA). TRECE DE OCTUBRE DE 2026. LLEGADA DE REFUGIADOS A LA ESTACION. "
                                "ROTULAR CORTESIA DE ''TVP''."},
        {"plano": "6", "texto": "VARSOVIA. TRECE DE OCTUBRE DE 2026. CENTRO DE ACOGIDA."},
    ]
    assert "SOUNDBITE" in llamada.call_args.args[1]                # el shotlist va al modelo
    assert "ROTULAR CORTESIA DE" in llamada.call_args.args[0]      # con la tabla de restricciones del criterio
    with mock.patch("redactor.llamar_modelo") as llamada:
        assert redactor.localizadores({"texto": "Un titular suelto."}) == []
    assert not llamada.called                                        # sin texto de agencia no se gasta una llamada


def test_el_shotlist_es_el_texto_entero_numerado_o_no():
    """Reuters: la ficha trae Details antes del guion y los planos sin numerar; todo eso es el shotlist."""
    texto = ("U.S. MIDTERMS: SCENES ACROSS AMERICA AS THE NATION GOES TO THE POLLS\nDetails\nDuration 00:03:12\n"
             "ANCHORAGE, ALASKA (NOVEMBER 3, 2026) (REUTERS - ACCESS ALL)\nVOTERS QUEUING OUTSIDE POLLING STATION\n"
             "(SOUNDBITE) (English) VOTER, JANE DOE, SAYING: We came early.\nSTORY: Americans voted on Tuesday.")
    respuesta = ("|ANCHORAGE|EEUU|03/11/2026|VOTANTES HACIENDO COLA ANTE UN COLEGIO ELECTORAL|\n"
                 "-|ANCHORAGE|EEUU|03/11/2026|TESTIMONIO DE JANE DOE, VOTANTE, SOBRE LA JORNADA|\n")
    with mock.patch("redactor.llamar_modelo", return_value=respuesta) as llamada:
        locs = redactor.localizadores({"texto": texto, "fecha": "03/11/2026"})
    assert "VOTERS QUEUING" in llamada.call_args.args[1] and "SOUNDBITE" in llamada.call_args.args[1]
    assert locs == [{"plano": "", "texto": "ANCHORAGE. TRES DE NOVIEMBRE DE 2026. VOTANTES HACIENDO COLA ANTE UN "
                                           "COLEGIO ELECTORAL."},
                    {"plano": "", "texto": "ANCHORAGE. TRES DE NOVIEMBRE DE 2026. TESTIMONIO DE JANE DOE, VOTANTE, "
                                           "SOBRE LA JORNADA."}]


def test_la_fecha_va_en_letra():
    assert redactor.fecha_en_letra("12/10/2026") == "DOCE DE OCTUBRE DE 2026"
    assert redactor.fecha_en_letra("1/9/2026") == "UNO DE SEPTIEMBRE DE 2026"
    assert redactor.fecha_en_letra("31/12/2026") == "TREINTA Y UNO DE DICIEMBRE DE 2026"
    assert redactor.fecha_en_letra("") == "" and redactor.fecha_en_letra("32/1/2026") == ""


def _arrancar(w, ficha_extra, monkeypatch, sacar):
    monkeypatch.setattr(servidor.ESTADO, "lotes", [lote("L", ["0707"])])
    ficha = servidor.ESTADO.lotes[0]["fichas"][0]
    ficha.update(ficha_extra)
    monkeypatch.setattr(servidor, "sacar_localizadores", sacar)
    threading.Thread(target=w.run, daemon=True).start()
    t0 = time.time()
    while ficha["estado"] != "hecha" and time.time() - t0 < 10:
        time.sleep(0.05)
    w.parar = True
    return ficha


def test_el_servidor_los_guarda_solo_si_la_lista_los_pide(worker, monkeypatch):  # noqa: F811
    w, _, _ = worker
    locs = [{"plano": "1", "texto": "KIEV. DOCE DE OCTUBRE DE 2026. VISTA AEREA."}]
    ficha = _arrancar(w, {"con_localizadores": True}, monkeypatch, lambda datos, timeout=None: locs)
    assert ficha["estado"] == "hecha" and ficha["localizadores"] == locs
    assert ficha["localizadores_v"] == redactor.LOCALIZADORES_VERSION


def test_sin_peticion_no_se_piden(worker, monkeypatch):  # noqa: F811
    w, _, _ = worker
    pedidos = []
    ficha = _arrancar(w, {}, monkeypatch, lambda datos, timeout=None: pedidos.append(1) or [])
    assert ficha["estado"] == "hecha" and "localizadores" not in ficha and not pedidos


def test_si_fallan_la_ficha_queda_hecha_con_un_aviso(worker, monkeypatch):  # noqa: F811
    w, _, _ = worker

    def falla(datos, timeout=None):
        raise RuntimeError("sin respuesta")

    ficha = _arrancar(w, {"con_localizadores": True}, monkeypatch, falla)
    assert ficha["estado"] == "hecha" and ficha["NAME"] == "N" and ficha["localizadores_fallo"]
    aviso = next(a for a in ficha["avisos"] if "localizadores" in a)
    assert "RuntimeError" not in aviso and "vuelve a subir la lista" in aviso      # sin nombres internos, y que hacer


def test_no_van_ni_al_correo_ni_a_mediacentral():
    ficha = {"numero": "0707", "estado": "hecha", "ENVIO": "0707", "NAME": "UCRANIA ATAQUE KIEV", "COMMENT": "KIEV. DAÑOS.",
             "RESTRICCIONES": "SIN AVISO", "etiqueta": "0707",
             "localizadores": [{"plano": "1", "texto": "LOCALIZADOR SECRETO."}]}
    assert "localizadores" not in servidor._ficha_para_fuera(ficha, {"id": "L"})
    assert "LOCALIZADOR SECRETO" not in correo.cuerpo_fichas([ficha])
    assert "LOCALIZADOR SECRETO" not in correo.cuerpo_fichas_html([ficha])


def test_el_panel_tiene_la_seccion_y_el_enlace():
    from pathlib import Path
    panel = (Path(__file__).resolve().parent.parent / "panel" / "index.html").read_text(encoding="utf-8")
    assert 'data-vista="localizadores"' in panel and 'id="vista-localizadores"' in panel
    assert "#localizadores/" in panel and "data-locs" in panel


def test_una_ficha_ya_hecha_sin_nada_que_marcar_se_copia_sin_volver_a_pedirlos(monkeypatch):
    w = servidor.Worker()
    hecha = {"numero": "0707", "estado": "hecha", "NAME": "N", "COMMENT": "C", "localizadores": [],
             "localizadores_v": redactor.LOCALIZADORES_VERSION}
    monkeypatch.setattr(servidor.ESTADO, "ya_hecha", lambda *a, **k: ({"creado": "06/11/2026"}, hecha))
    monkeypatch.setattr(servidor.ESTADO, "guardar", lambda: None)
    ficha = {"numero": "0707", "etiqueta": "0707", "con_localizadores": True}
    assert w.volcar_hecha({"id": "L"}, ficha) and ficha["localizadores"] == []
    # los sacados con la version de antes (sin version) no se copian: al subir otra vez la lista se rehacen
    vieja = {"numero": "0707", "estado": "hecha", "NAME": "N", "COMMENT": "C", "localizadores": []}
    monkeypatch.setattr(servidor.ESTADO, "ya_hecha", lambda *a, **k: ({"creado": "06/11/2026"}, vieja))
    assert not w.volcar_hecha({"id": "L"}, {"numero": "0707", "etiqueta": "0707", "con_localizadores": True})


def test_se_guardan_todos_aunque_el_lote_ya_no_salga_en_fichas(monkeypatch):
    import json
    lotes = [lote(f"L{i}", ["0001"]) for i in range(servidor.MAX_LOTES_EN_PANEL + 10)]
    vieja = lotes[-1]["fichas"][0]                          # de un lote que ya no cabe en la vista Fichas
    vieja.update(con_localizadores=True, estado="hecha", NAME="N", COMMENT="C", correo_x="no",
                 localizadores=[{"plano": "1", "texto": "KIEV. DOCE DE OCTUBRE DE 2026. VISTA AEREA."}])
    monkeypatch.setattr(servidor.ESTADO, "lotes", lotes)
    items = json.loads(servidor.localizadores_guardados().body)["items"]
    assert [(i["lote"], i["f"]["id"]) for i in items] == [(lotes[-1]["id"], vieja["id"])]
    assert items[0]["f"]["localizadores"][0]["texto"].startswith("KIEV.") and "COMMENT" not in items[0]["f"]


def test_en_la_ficha_es_un_enlace_en_las_notas_y_en_la_pestana_se_pliegan():
    from pathlib import Path
    panel = (Path(__file__).resolve().parent.parent / "panel" / "index.html").read_text(encoding="utf-8")
    assert "Ver localizadores" in panel and 'class="notas"><p><a href="#localizadores/' in panel
    assert '<details class="fila fila-loc"' in panel and "/api/localizadores" in panel
    assert "No se han podido cargar los localizadores" in panel        # un corte no parece "no hay ninguno"
