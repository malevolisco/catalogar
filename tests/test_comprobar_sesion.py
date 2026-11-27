# -*- coding: utf-8 -*-
"""Comprobar sesion de agencia: no basta con que la portada abra; se abre de verdad un envio reciente ya hecho de
cada agencia. Si no se abre, la sesion se da por caducada, se inicia sesion sola y se vuelve a probar."""
from datetime import datetime, timedelta

import pytest

import servidor
from extractor import NeedsLogin, NotFound
from test_colas import lote


def _hecha(l, numero, fecha, creado, **extra):
    f = next(f for f in l["fichas"] if f["numero"] == numero)
    f.update(estado="hecha", fecha=fecha, creado=creado, **extra)
    return f


@pytest.fixture
def lotes(monkeypatch):
    hoy = datetime.now()
    d = lambda dias: (hoy - timedelta(days=dias)).strftime("%d/%m/%Y %H:%M")
    l = lote("L", ["5455", "5460", "4696362", "2026_10420363", "5470", "5480"])
    _hecha(l, "5455", "20/11/2026", d(3))
    _hecha(l, "5460", "25/11/2026", d(1))
    _hecha(l, "4696362", "24/11/2026", d(2))
    _hecha(l, "2026_10420363", "", d(1))
    _hecha(l, "5470", "", d(0), texto_csv="texto de la lista")         # del CSV: no prueba la web
    _hecha(l, "5480", "01/09/2026", d(60))                               # muy viejo
    monkeypatch.setattr(servidor.ESTADO, "lotes", [l])
    return l


def test_elige_los_ultimos_hechos_de_cada_agencia_con_su_fecha(lotes):
    assert servidor.envios_de_prueba("Reuters") == [("5460", "25/11/2026"), ("5455", "20/11/2026")]
    assert servidor.envios_de_prueba("AP") == [("4696362", "24/11/2026")]
    assert servidor.envios_de_prueba("EBU") == [("2026_10420363", "")]
    lotes["fichas"][0]["estado"] = "error"
    assert servidor.envios_de_prueba("Reuters") == [("5460", "25/11/2026")]


class Pagina:
    url = "https://www.reutersconnect.com/home"

    def __init__(self):
        self.visitas = []

    def goto(self, url, **_):
        self.visitas.append(url)

    def wait_for_timeout(self, ms):
        pass


class Extractor:
    """Navegador falso: abre los envios de `abren` (o todos tras entrar si `abre_tras_entrar`)."""
    def __init__(self, abren=(), entra=True, abre_tras_entrar=True, motivo="la agencia no acepta la contraseña",
                 caducada_en_portada=False, **_):
        self._page, self._ctx = Pagina(), None
        self.abren, self.entra, self.abre_tras_entrar = set(abren), entra, abre_tras_entrar
        self.motivo, self.caducada_en_portada = motivo, caducada_en_portada
        self.login_fallido, self.dentro = "", False
        self.pedidos, self.logins, self.cerrado = [], [], False

    def open(self):
        pass

    def close(self):
        self.cerrado = True

    def _comprobar_acceso(self, agencia, url=None):
        if self.caducada_en_portada:
            raise NeedsLogin(f"Sesion de {agencia} caducada")

    def fetch(self, numero, fecha=None, creado=None):
        self.pedidos.append((numero, fecha))
        if numero in self.abren or (self.dentro and self.abre_tras_entrar):
            return {"texto": "SHOTLIST ...", "headline": "H"}
        raise NotFound("NO ENCONTRADO")

    def _login_auto(self, agencia):
        self.logins.append(agencia)
        self.dentro = self.entra
        self.login_fallido = "" if self.entra else self.motivo
        return self.entra


@pytest.fixture
def navegador(monkeypatch, lotes):
    creados = []

    def nuevo(**opciones):
        def fabrica(**_):
            ex = Extractor(**opciones)
            creados.append(ex)
            return ex
        monkeypatch.setattr(servidor, "Extractor", fabrica)
        return creados

    monkeypatch.setattr(servidor.VISOR, "conectar", lambda *a, **k: None)
    monkeypatch.setattr(servidor.VISOR, "desconectar", lambda *a, **k: None)
    registro = []
    monkeypatch.setattr(servidor, "log", registro.append)
    for clave, valor in {"headless": True, "ebu": True}.items():
        monkeypatch.setitem(servidor.CFG, clave, valor)
    return nuevo, registro


def test_si_los_envios_se_abren_la_sesion_esta_activa(navegador):
    nuevo, _ = navegador
    creados = nuevo(abren={"5460", "4696362", "2026_10420363"})
    assert servidor.comprobar_sesion() is None
    ex = creados[0]
    assert ex.pedidos == [("5460", "25/11/2026"), ("4696362", "24/11/2026"), ("2026_10420363", None)]
    assert ex.logins == [] and ex.cerrado


def test_si_no_se_abren_inicia_sesion_sola_y_lo_vuelve_a_probar(navegador):
    nuevo, registro = navegador
    creados = nuevo(abren={"4696362", "2026_10420363"})          # Reuters: la portada abre, los envios no
    assert servidor.comprobar_sesion() is None
    ex = creados[0]
    assert ex.logins == ["Reuters"]
    assert servidor.reuters_login() in ex._page.visitas             # a la pagina de entrada de Reuters
    assert ex.pedidos[:3] == [("5460", "25/11/2026"), ("5455", "20/11/2026"), ("5460", "25/11/2026")]
    assert any("renovada" in r for r in registro)


def test_si_no_puede_entrar_la_da_por_caducada_con_el_motivo(navegador):
    nuevo, _ = navegador
    creados = nuevo(abren={"5460", "5455"}, entra=False)          # AP: ni envio ni inicio de sesion
    problema = servidor.comprobar_sesion()
    assert problema.startswith("Sesion de AP caducada") and "4696362" in problema
    assert "no acepta la contraseña" in problema
    assert creados[0].logins == ["AP"] and creados[0].cerrado


def test_entra_bien_pero_el_envio_sigue_sin_abrirse_no_para_la_cola(navegador):
    nuevo, registro = navegador
    nuevo(abren={"5460", "4696362"}, abre_tras_entrar=False)      # EBU: retirado, pero la sesion es buena
    assert servidor.comprobar_sesion() is None
    assert any("se da la sesion por buena" in r for r in registro)


def test_sin_envios_recientes_solo_mira_la_portada(navegador, monkeypatch):
    nuevo, registro = navegador
    monkeypatch.setattr(servidor.ESTADO, "lotes", [])
    creados = nuevo()
    assert servidor.comprobar_sesion() is None
    assert creados[0].pedidos == [] and creados[0].logins == []
    assert sum("solo se ha mirado la portada" in r for r in registro) == 3


def test_la_portada_caducada_sigue_avisando_como_antes(navegador):
    nuevo, _ = navegador
    nuevo(caducada_en_portada=True)
    assert servidor.comprobar_sesion() == "Sesion de Reuters caducada"
