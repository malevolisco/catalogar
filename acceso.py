# -*- coding: utf-8 -*-
"""
acceso.py - Inicio de sesion automatico en las agencias cuando la sesion caduca.

extractor.py lo llama al encontrarse la pagina de login, si en config.json hay usuario y contraseña de esa
agencia (reuters_usuario/reuters_clave, ap_usuario/ap_clave, ebu_usuario/ebu_clave) y login_auto esta
encendido. Es generico a proposito: no depende de como este dibujada cada pagina de login, busca el campo
de usuario y el de contraseña y sirve igual para formularios de un paso (los dos campos a la vez) y de dos
(primero el correo, "Siguiente", luego la contraseña), que es como suelen ser Okta, Auth0 o Microsoft.

Lo que NO hace, a proposito:
    - codigos de verificacion (SMS, aplicacion): si la pagina pide uno, para y avisa
    - antibot o captchas: eso lo tiene que pasar una persona
    - insistir: como mucho un intento por agencia cada INTENTO_CADA, para que una contraseña mala no
      bloquee la cuenta a fuerza de reintentos
La contraseña nunca se escribe en el registro ni en los volcados de depuracion.
"""
import re
import time

INTENTO_CADA = 30 * 60            # segundos entre intentos automaticos por agencia
ESPERA_PASO = 15                  # segundos que se espera a que la pagina responda tras cada paso

CAMPOS_USUARIO = (
    "input[autocomplete=username]", "input[type=email]",
    "input[name*=user i]", "input[name*=email i]", "input[name*=login i]",
    "input[id*=user i]", "input[id*=email i]", "input[id*=login i]",
    "input[type=text]",
)
CAMPO_CLAVE = ("input[type=password]",)
CAMPOS_CODIGO = (
    "input[autocomplete=one-time-code]", "input[name*=otp i]", "input[id*=otp i]",
    "input[name*=passcode i]", "input[name*=mfa i]", "input[name*=verification i]",
)
BOTON_ENVIAR = re.compile(r"^\s*(sign ?in|log ?in|next|continue|submit|verify|iniciar sesi[oó]n|entrar|acceder|"
                          r"siguiente|continuar)\s*$", re.I)
BOTON_COOKIES = re.compile(r"^\s*(accept all( cookies)?|accept( cookies)?|i accept|agree|i agree|allow all( cookies)?|"
                           r"aceptar( todo| todas| cookies)?|acepto|permitir todas)\s*$", re.I)
ESPERA_FORMULARIO = 20            # segundos que puede tardar en dibujarse el formulario (paginas que son aplicaciones)
BOTON_ENTRAR = re.compile(r"\b(sign ?in|log ?in|iniciar sesi[oó]n|acceder)\b", re.I)

_ultimos = {}                     # agencia -> momento del ultimo intento


def puede_intentar(agencia, ahora=None):
    """True si no se ha intentado con esa agencia en los ultimos INTENTO_CADA segundos (y lo apunta)."""
    ahora = time.time() if ahora is None else ahora
    ultimo = _ultimos.get(agencia)
    if ultimo is not None and ahora - ultimo < INTENTO_CADA:
        return False
    _ultimos[agencia] = ahora
    return True


def _visible(page, selectores):
    for sel in selectores:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 6)):
                el = loc.nth(i)
                if el.is_visible() and el.is_enabled():
                    return el
        except Exception:
            continue
    return None


def quitar_cookies(page):
    """El aviso de cookies tapa a veces el formulario (y el clic en "Sign in" no llega): se acepta."""
    for rol in ("button", "link"):
        try:
            boton = page.get_by_role(rol, name=BOTON_COOKIES)
            for i in range(min(boton.count(), 3)):
                if boton.nth(i).is_visible():
                    boton.nth(i).click(timeout=3000)
                    page.wait_for_timeout(500)
                    return True
        except Exception:
            continue
    return False


def esperar_formulario(page, espera=ESPERA_FORMULARIO):
    """Espera a que se dibuje el formulario (o el boton de entrar), quitando el aviso de cookies si sale."""
    t0 = time.time()
    while time.time() - t0 < espera:
        quitar_cookies(page)
        if _visible(page, CAMPO_CLAVE + CAMPOS_USUARIO) or _boton_de_entrar(page):
            return True
        page.wait_for_timeout(700)
    return False


def _clic(page, el):
    """Clic sin esperar 30 s si algo lo tapa: se quita el aviso de cookies y se prueba otra vez."""
    try:
        el.click(timeout=4000)
        return True
    except Exception:
        if quitar_cookies(page):
            try:
                el.click(timeout=4000)
                return True
            except Exception:
                pass
    return False


def _pulsar(page, campo):
    """El boton de enviar del formulario si se ve; si no, Intro en el campo."""
    for rol in ("button", "link"):
        try:
            boton = page.get_by_role(rol, name=BOTON_ENVIAR)
            for i in range(min(boton.count(), 4)):
                if boton.nth(i).is_visible() and _clic(page, boton.nth(i)):
                    return
        except Exception:
            continue
    try:
        sub = page.locator("input[type=submit], button[type=submit]")
        if sub.count() and sub.first.is_visible() and _clic(page, sub.first):
            return
    except Exception:
        pass
    campo.press("Enter")


def _boton_de_entrar(page):
    """En una portada sin formulario, el boton o enlace de "Iniciar sesion"."""
    for rol in ("button", "link"):
        try:
            boton = page.get_by_role(rol, name=BOTON_ENTRAR)
            for i in range(min(boton.count(), 4)):
                if boton.nth(i).is_visible():
                    return boton.nth(i)
        except Exception:
            continue
    return None


def entrar(page, usuario, clave, sigue_en_login, espera=ESPERA_PASO):
    """Intenta iniciar sesion en la pagina que esta abierta. sigue_en_login(): True mientras se vea el login.
    Devuelve (ok, motivo); el motivo dice por que no se ha podido, para el aviso por correo."""
    clave_puesta = False
    esperar_formulario(page)
    for _ in range(4):                                     # portada → usuario → contraseña → (respuesta)
        if _visible(page, CAMPOS_CODIGO):
            return False, "la agencia pide un código de verificación: eso lo tiene que hacer una persona"
        campo_clave = _visible(page, CAMPO_CLAVE)
        campo_usuario = _visible(page, CAMPOS_USUARIO)
        if campo_clave:
            if campo_usuario and not (campo_usuario.input_value() or "").strip():
                campo_usuario.fill(usuario)
            campo_clave.fill(clave)
            clave_puesta = True
            _pulsar(page, campo_clave)
        elif campo_usuario:
            campo_usuario.fill(usuario)
            _pulsar(page, campo_usuario)
        else:
            boton = _boton_de_entrar(page)
            if boton is None:
                return False, "no encuentro el formulario de inicio de sesión"
            _clic(page, boton)
            esperar_formulario(page)
        # se espera a que la pagina conteste: o ya no es un login, o ha cambiado de paso
        t0 = time.time()
        while time.time() - t0 < espera:
            page.wait_for_timeout(800)
            try:
                if not sigue_en_login():
                    return True, ""
            except Exception:
                continue
            if _visible(page, CAMPOS_CODIGO):
                break
            if not clave_puesta and _visible(page, CAMPO_CLAVE):
                break                                      # paso siguiente: la contraseña
            if clave_puesta and campo_clave and not _visible(page, CAMPO_CLAVE):
                continue                                   # va cargando
        else:
            if clave_puesta:
                return False, "la agencia no acepta el usuario o la contraseña guardados"
    if not sigue_en_login():
        return True, ""
    return False, ("la agencia no acepta el usuario o la contraseña guardados" if clave_puesta
                   else "no he llegado a la pantalla de la contraseña")
