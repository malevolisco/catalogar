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
    # como Reuters Connect: la pagina es una aplicacion que dibuja el formulario al rato, con el aviso de cookies encima
    "reuters": f"""<div id=app></div>
        <div id=cookies style="position:fixed;inset:0;background:#0008;z-index:9"><button onclick="cookies.remove()">Accept All</button></div>
        <script>setTimeout(() => app.innerHTML = `<input type=text name=email><input type=password id=c>
          <button onclick="if (c.value=='buena') {{ {ENTRAR.replace("'", "&quot;")} }}">Sign in</button>`, 1500)</script>""",
    # como AP (Auth0): primero el correo y Continue; luego otra pantalla con el correo ya puesto y la contraseña
    "ap": f"""<form onsubmit="event.preventDefault(); document.body.innerHTML = document.getElementById('p2').innerHTML">
        <input id=username name=username><button type=submit name=action>Continue</button></form>
        <template id=p2><input name=username value="yo@rtve.es" readonly><input id=password type=password>
          <button onclick="if (password.value=='buena') {{ {ENTRAR.replace("'", "&quot;")} }}">Continue</button>
          <button>Continue with Google</button></template>""",
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


def test_como_reuters(navegador):
    assert probar(navegador, "reuters") == (True, "")


def test_como_ap(navegador):
    assert probar(navegador, "ap") == (True, "")


def test_direccion_de_entrada_de_reuters():
    import extractor
    assert extractor.reuters_login() == ("https://www.reutersconnect.com/login?"
                                         "url64=aHR0cHM6Ly93d3cucmV1dGVyc2Nvbm5lY3QuY29tL2luZGV4Lmh0bWw=")


PORTADA_AP = """<h2>Have an AP Newsroom account?</h2><p>For existing AP customers, please sign in now to access all
    the content you need.</p><button>Sign in</button><p>Discover new, recent and historic content</p>"""
FICHA_AP = """<h1>Hastert dies</h1><p>SHOTLIST: ... users must sign in to access the archive, said the spokesman.</p>"""


def pagina_en(navegador, url, html):
    page = navegador.new_page()
    page.route("**/*", lambda ruta: ruta.fulfill(status=200, content_type="text/html", body=html))
    page.goto(url)
    return page


def test_portada_de_ap_sin_sesion_es_un_login(navegador):
    import extractor
    ex = extractor.Extractor()
    ex._page = pagina_en(navegador, "https://newsroom.ap.org/", PORTADA_AP)
    assert ex._is_login()
    ex._page = pagina_en(navegador, "https://newsroom.ap.org/detail/x/abc/video", FICHA_AP)
    assert not ex._is_login()                     # en una ficha, el texto del guion no cuenta


def test_portada_de_ap_entra_sola(navegador):
    page = pagina_en(navegador, "https://newsroom.ap.org/", PORTADA_AP.replace(
        "<button>Sign in</button>",
        f"""<button onclick="document.body.innerHTML = document.getElementById('f').innerHTML">Sign in</button>
        <template id=f><input name=username><input type=password id=c>
        <button onclick="if (c.value=='buena') {{ {ENTRAR.replace("'", "&quot;")} }}">Continue</button></template>"""))
    import extractor
    ex = extractor.Extractor()
    ex._page = page
    assert acceso.entrar(page, "yo@rtve.es", "buena", ex._is_login, espera=3) == (True, "")
