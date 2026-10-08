# -*- coding: utf-8 -*-
"""Inicio de sesion automatico (acceso.py) contra paginas de login de mentira, en un navegador de verdad.
Sin navegador instalado (como en GitHub), se salta."""
import pytest

import acceso

sync_api = pytest.importorskip("playwright.sync_api")

ENTRAR = "document.body.innerHTML = '<h1>Portada de la agencia</h1>'"
PAGINAS = {
    "un_paso": f"""<form onsubmit="event.preventDefault(); if (u.value=='yo@rtve.es' && c.value=='buena') {{ {ENTRAR} }}">
        <input id=u type=email><input id=c type=password><button type=submit>Sign in</button></form>""",
    "dos_pasos": f"""<div id=paso1><input id=u name=username><button onclick="paso1.hidden=true; paso2.hidden=false">Next</button></div>
        <div id=paso2 hidden><input id=c type=password><button onclick="if (c.value=='buena') {{ {ENTRAR} }}">Verify</button></div>""",
    "codigo": """<form onsubmit="event.preventDefault(); document.body.innerHTML = '<input autocomplete=one-time-code><input type=password>'">
        <input type=email><input type=password><button>Log in</button></form>""",
    "portada": f"""<a href="#" onclick="document.body.innerHTML = `<form onsubmit='event.preventDefault(); {ENTRAR.replace("'", "&quot;")}'><input type=email><input type=password><button>Continue</button></form>`">Log in</a>""",
}


@pytest.fixture(scope="module")
def navegador():
    pw = sync_api.sync_playwright().start()
    nav = None
    for opciones in ({}, {"executable_path": "/opt/pw-browsers/chromium"}):   # el instalado o uno del sistema
        try:
            nav = pw.chromium.launch(**opciones)
            break
        except Exception as e:
            motivo = e
    if nav is None:
        pw.stop()
        pytest.skip(f"sin navegador: {str(motivo)[:120]}")
    yield nav
    nav.close()
    pw.stop()


def probar(navegador, pagina, clave="buena"):
    page = navegador.new_page()
    page.set_content(PAGINAS[pagina])
    en_login = lambda: page.locator("input[type=password], input[type=email], input[name=username], a").count() > 0
    return acceso.entrar(page, "yo@rtve.es", clave, en_login, espera=3)


def test_un_paso(navegador):
    assert probar(navegador, "un_paso") == (True, "")


def test_dos_pasos(navegador):
    assert probar(navegador, "dos_pasos") == (True, "")


def test_contrasena_mala(navegador):
    ok, motivo = probar(navegador, "un_paso", clave="mala")
    assert not ok and "no acepta" in motivo


def test_pide_codigo(navegador):
    ok, motivo = probar(navegador, "codigo")
    assert not ok and "código de verificación" in motivo


def test_portada_con_boton(navegador):
    assert probar(navegador, "portada") == (True, "")


def test_un_intento_cada_media_hora():
    acceso._ultimos.clear()
    assert acceso.puede_intentar("ap", 1000)
    assert not acceso.puede_intentar("ap", 1000 + 60)
    assert acceso.puede_intentar("reuters", 1060)
    assert acceso.puede_intentar("ap", 1000 + acceso.INTENTO_CADA + 1)
