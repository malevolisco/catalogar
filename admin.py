# -*- coding: utf-8 -*-
"""
admin.py - La parte de administracion del servidor: lo que usa la pestaña Admin de la pagina y la
edicion comoda de reglas, criterio base y fichas aprobadas.

    registro en vivo        todo lo que el servidor escribe en consola, para verlo desde la pagina
    ajustes                 config.json en un formulario (las contraseñas nunca se devuelven), agenda incluida
    probar correo           manda un correo de prueba y comprueba el buzon
    reiniciar               el servidor sale con el codigo 75 y el lanzador lo vuelve a arrancar
    historial de correos    cola/correos.jsonl (desde esta version) y lo que hay en los lotes (de antes)
    estadisticas            fichas por dia y por agencia, tiempos, aprobadas
    reglas y criterio       editar, mover y quitar; el criterio base con criterio.py
    entrada local           /local?t=... : la ventana de escritorio entra sin teclear la clave

servidor.py llama a iniciar(globals()) y monta router.
"""
import collections
import imaplib
import json
import os
import re
import secrets
import smtplib
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

import criterio
from visor import VISOR

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
CORREOS_PATH = BASE_DIR / "cola" / "correos.jsonl"
CODIGO_REINICIO = 75          # el lanzador vuelve a arrancar el servidor si sale con este codigo

router = APIRouter()
CEREBROS = {"claude_code": "Claude Code", "api": "Claude (API)", "openai": "ChatGPT"}
S = {}                        # los globales de servidor.py (CFG, ESTADO, WORKER, log...)


def iniciar(globales):
    S.clear()
    S.update({"_g": globales})
    instalar_registro()


def g(nombre):
    return S["_g"][nombre]


# ====================================================================== registro en vivo
REGISTRO = collections.deque(maxlen=3000)
_CONTADOR = [0]
_CERROJO_REG = threading.Lock()
NIVEL_ERROR = re.compile(r"\b(error|fallo|falla|traceback|exception|no se ha podido|no se pudo|caducad|antibot|parad[oa])\b", re.I)
NIVEL_AVISO = re.compile(r"\b(aviso|ignorad|sin repartir|reintent|pausa)\b", re.I)


class _Copia:
    """Deja pasar lo que se escribe en consola (el lanzador lo recoge) y guarda las lineas para la pagina."""

    def __init__(self, flujo):
        self.flujo = flujo
        self.trozo = ""

    def write(self, texto):
        with _CERROJO_REG:              # dentro del cerrojo: dos hilos escribiendo a la vez no se mezclan
            try:
                self.flujo.write(texto)
            except Exception:
                pass
            self.trozo += texto
            *lineas, self.trozo = self.trozo.split("\n")
            for l in lineas:
                if l.strip():
                    _CONTADOR[0] += 1
                    REGISTRO.append((_CONTADOR[0], datetime.now().strftime("%d/%m %H:%M:%S"), l.rstrip()))
        return len(texto)

    def flush(self):
        try:
            self.flujo.flush()
        except Exception:
            pass

    def __getattr__(self, nombre):
        return getattr(self.flujo, nombre)


def instalar_registro():
    if not isinstance(sys.stdout, _Copia):
        sys.stdout = _Copia(sys.stdout)
    if not isinstance(sys.stderr, _Copia):
        sys.stderr = _Copia(sys.stderr)


def _nivel(texto):
    return "error" if NIVEL_ERROR.search(texto) else "aviso" if NIVEL_AVISO.search(texto) else ""


@router.get("/api/admin/registro")
def registro(desde: int = 0):
    with _CERROJO_REG:
        lineas = [{"n": n, "cuando": c, "texto": t, "nivel": _nivel(t)} for n, c, t in REGISTRO if n > desde]
    return {"lineas": lineas[-1500:], "ultimo": _CONTADOR[0]}


# ====================================================================== entrada desde la ventana de escritorio
@router.get("/local")
def entrada_local(t: str = ""):
    """El lanzador arranca el servidor con un token de un solo uso en CATALOGATOR_TOKEN_LOCAL y abre
    su ventana en /local?t=...: entra sin pedir la clave. Desde fuera (Tailscale) sin ese token, nada."""
    esperado = os.environ.get("CATALOGATOR_TOKEN_LOCAL", "")
    if not esperado or not secrets.compare_digest(t.encode("utf-8"), esperado.encode("utf-8")):
        return RedirectResponse("/", status_code=303)
    token = secrets.token_urlsafe(32)
    g("SESIONES").add(token)
    resp = RedirectResponse("/?escritorio=1", status_code=303)
    resp.set_cookie(g("COOKIE"), token, httponly=True, samesite="strict", max_age=60 * 60 * 24 * 30)
    return resp


# ====================================================================== direccion de fuera (Tailscale Funnel)
_DIRECCION = {"url": "", "hasta": 0.0, "motivo": ""}


def _tailscale():
    import shutil
    ts = shutil.which("tailscale")
    if not ts and sys.platform == "win32":
        for r in (r"C:\Program Files\Tailscale\tailscale.exe", r"C:\Program Files (x86)\Tailscale\tailscale.exe"):
            if Path(r).exists():
                return r
    return ts


def direccion_fuera():
    """La direccion publica con la que se entra desde fuera (https://equipo.red.ts.net), o "" y el motivo.
    Se pregunta a Tailscale y se guarda un minuto."""
    if time.time() < _DIRECCION["hasta"]:
        return _DIRECCION["url"], _DIRECCION["motivo"]
    import subprocess
    url, motivo = "", ""
    ts = _tailscale()
    if not ts:
        motivo = "Tailscale no está instalado en este PC: la página solo se ve aquí."
    else:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            r = subprocess.run([ts, "funnel", "status", "--json"], capture_output=True, text=True, timeout=15, creationflags=flags)
            datos = json.loads(r.stdout or "{}")
            # {"AllowFunnel": {"equipo.red.ts.net:443": true}, "Web": {"equipo.red.ts.net:443": {...}}}
            for clave, si in (datos.get("AllowFunnel") or {}).items():
                if si:
                    host, _, puerto = clave.rpartition(":")
                    url = f"https://{host}" + ("" if puerto in ("443", "") else f":{puerto}")
                    break
            if not url:
                motivo = "Tailscale está instalado pero la página no está publicada (Funnel apagado)."
        except Exception as e:
            motivo = f"No he podido preguntar a Tailscale ({type(e).__name__})."
    _DIRECCION.update(url=url, motivo=motivo, hasta=time.time() + 60)
    return url, motivo


def _es_este_pc(request):
    """La peticion viene de este mismo PC (ventana de escritorio o navegador local), no de fuera por Funnel."""
    return not request.headers.get("x-forwarded-for") and (request.client.host if request.client else "") in ("127.0.0.1", "::1")


@router.get("/api/admin/direccion")
def direccion(request: Request):
    url, motivo = direccion_fuera()
    datos = {"url": url, "motivo": motivo, "este_pc": _es_este_pc(request)}
    if datos["este_pc"] and desbloqueado(request):
        datos["clave"] = g("CFG").get("servidor_clave", "")     # solo a quien esta delante del PC y en Admin
    return datos


# ====================================================================== boton prohibido: tus sonidos y gifs
# Lo que sube el catalogador para el boton (clic derecho sobre el): se queda en este equipo, en
# broma/archivos/, nunca va al repositorio y viaja con la copia de datos.
BROMA_DIR = BASE_DIR / "broma" / "archivos"
BROMA_SONIDO = (".mp3", ".ogg", ".wav", ".m4a")
BROMA_IMAGEN = (".gif", ".webp", ".png", ".apng")
BROMA_MAX = 100 * 1024 * 1024        # una cancion entera, incluso en WAV


def _broma_lista():
    if not BROMA_DIR.is_dir():
        return [], []
    ficheros = sorted(p for p in BROMA_DIR.iterdir() if p.is_file())
    url = lambda p: f"/api/admin/broma/{p.name}?v={int(p.stat().st_mtime)}"
    return ([{"nombre": p.name, "url": url(p)} for p in ficheros if p.suffix.lower() in BROMA_SONIDO],
            [{"nombre": p.name, "url": url(p)} for p in ficheros if p.suffix.lower() in BROMA_IMAGEN])


@router.get("/api/admin/broma")
def broma_lista():
    sonidos, imagenes = _broma_lista()
    return {"sonidos": sonidos, "imagenes": imagenes}


@router.get("/api/admin/broma/{nombre}")
def broma_archivo(nombre: str):
    from fastapi.responses import FileResponse
    ruta = BROMA_DIR / nombre
    if not re.match(r"^[\w.-]{1,100}$", nombre) or nombre.startswith(".") or not ruta.is_file():
        raise HTTPException(404, "No está")
    return FileResponse(ruta)


@router.post("/api/admin/broma")
async def broma_subir(request: Request):
    formulario = await request.form()
    BROMA_DIR.mkdir(parents=True, exist_ok=True)
    guardados, malos = 0, []
    for f in formulario.getlist("ficheros"):
        if not hasattr(f, "read"):
            continue
        nombre = re.sub(r"[^\w.-]+", "_", Path(f.filename or "archivo").name)[:80].lstrip(".") or "archivo"
        ext = Path(nombre).suffix.lower()
        datos = await f.read()
        if ext not in BROMA_SONIDO + BROMA_IMAGEN or len(datos) > BROMA_MAX:
            malos.append(f.filename or nombre)
            continue
        destino = BROMA_DIR / nombre
        if destino.exists():
            destino = BROMA_DIR / f"{Path(nombre).stem}-{secrets.token_hex(2)}{ext}"
        destino.write_bytes(datos)
        guardados += 1
    sonidos, imagenes = _broma_lista()
    return {"guardados": guardados, "malos": malos, "sonidos": sonidos, "imagenes": imagenes}


@router.delete("/api/admin/broma/{nombre}")
def broma_quitar(nombre: str):
    ruta = BROMA_DIR / nombre
    if re.match(r"^[\w.-]{1,100}$", nombre) and not nombre.startswith(".") and ruta.is_file():
        ruta.unlink()
    sonidos, imagenes = _broma_lista()
    return {"sonidos": sonidos, "imagenes": imagenes}


# ====================================================================== contraseña de Admin
# Aparte de la clave de la pagina: la pestaña Admin (ajustes, contraseñas, copia de datos, reiniciar) pide
# la suya. Se guarda en config.json como huella (admin_clave_hash: sal$pbkdf2), nunca en claro. Si se
# olvida: borrar la linea "admin_clave_hash" de config.json y crearla de nuevo al entrar en Admin.
ADMIN_ABIERTO = {}               # token de la sesion de la pagina -> hasta cuando vale (8 h)
ADMIN_LIBRES = {"/api/admin/direccion", "/api/admin/clave", "/api/admin/entrar", "/api/admin/crear-clave"}
DURA_ADMIN = 8 * 3600
_FALLOS_ADMIN = {"n": 0, "hasta": 0.0}


def _huella_clave(clave, sal=None):
    import hashlib
    sal = sal or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", clave.encode("utf-8"), sal.encode("ascii"), 200_000).hex()
    return f"{sal}${h}"


def _clave_admin():
    return str(_config_cruda().get("admin_clave_hash") or "")


def _token(request):
    return request.cookies.get(g("COOKIE")) or ""


def desbloqueado(request):
    hasta = ADMIN_ABIERTO.get(_token(request), 0)
    return bool(_clave_admin()) and hasta > time.time()


def ruta_protegida(ruta):
    """Lo que solo se usa desde la pestaña Admin y exige su contraseña (la comprueba el middleware de servidor.py)."""
    return ruta.startswith("/api/admin/") and ruta not in ADMIN_LIBRES


def _abrir(request):
    ADMIN_ABIERTO[_token(request)] = time.time() + DURA_ADMIN
    for t in [t for t, h in ADMIN_ABIERTO.items() if h < time.time()]:
        ADMIN_ABIERTO.pop(t, None)


def _guardar_clave_admin(clave):
    cruda = _config_cruda()
    cruda["admin_clave_hash"] = _huella_clave(clave)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cruda, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)


def _valida(clave):
    import hmac
    guardada = _clave_admin()
    if "$" not in guardada:
        return False
    sal, _ = guardada.split("$", 1)
    return hmac.compare_digest(_huella_clave(clave, sal), guardada)


@router.get("/api/admin/clave")
def estado_clave(request: Request):
    return {"configurada": bool(_clave_admin()), "desbloqueado": desbloqueado(request)}


@router.post("/api/admin/crear-clave")
async def crear_clave(request: Request):
    if _clave_admin():
        raise HTTPException(409, "Ya hay contraseña de Admin")
    clave = str((await g("_json")(request)).get("clave") or "")
    if len(clave) < 6:
        raise HTTPException(400, "La contraseña tiene que tener al menos 6 caracteres")
    _guardar_clave_admin(clave)
    _abrir(request)
    g("log")("Contraseña de Admin creada")
    return {"ok": True}


@router.post("/api/admin/entrar")
async def entrar_admin(request: Request):
    if time.time() < _FALLOS_ADMIN["hasta"]:
        raise HTTPException(429, "Demasiados intentos; espera un momento")
    clave = str((await g("_json")(request)).get("clave") or "")
    if not _valida(clave):
        _FALLOS_ADMIN["n"] += 1
        _FALLOS_ADMIN["hasta"] = time.time() + min(2 ** _FALLOS_ADMIN["n"], 60)
        g("log")("Contraseña de Admin incorrecta")
        raise HTTPException(403, "Contraseña incorrecta")
    _FALLOS_ADMIN.update(n=0, hasta=0.0)
    _abrir(request)
    return {"ok": True}


@router.post("/api/admin/salir")
def salir_admin(request: Request):
    ADMIN_ABIERTO.pop(_token(request), None)
    return {"ok": True}


@router.post("/api/admin/cambiar-clave")
async def cambiar_clave(request: Request):
    datos = await g("_json")(request)
    if not _valida(str(datos.get("actual") or "")):
        raise HTTPException(403, "La contraseña actual no es esa")
    nueva = str(datos.get("nueva") or "")
    if len(nueva) < 6:
        raise HTTPException(400, "La contraseña nueva tiene que tener al menos 6 caracteres")
    _guardar_clave_admin(nueva)
    g("log")("Contraseña de Admin cambiada")
    return {"ok": True}


# ====================================================================== resumen y acciones
def _version():
    try:
        return (BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "sin version"


@router.get("/api/admin/resumen")
def resumen():
    cfg, estado = g("CFG"), g("ESTADO")
    buzon = g("BUZON")
    arrancado = g("ARRANCADO")
    with estado.lock:
        lotes = list(estado.lotes)
        en_cola = sum(1 for l in lotes for f in l["fichas"] if f["estado"] == "pendiente")
        errores = sum(1 for l in lotes for f in l["fichas"] if f["estado"] == "error")
    return {
        "version": _version(),
        "arrancado": arrancado.strftime("%d/%m/%Y %H:%M"),
        "minutos": int((datetime.now() - arrancado).total_seconds() // 60),
        "redactor": CEREBROS.get(cfg.get("redactor", "claude_code"), "Claude Code"),
        "modelo": {"api": cfg.get("api_model"), "openai": cfg.get("openai_model")}.get(cfg.get("redactor"), cfg.get("claude_model")),
        "criterio": cfg.get("reglas", "reglas.md"),
        "criterio_cambios": criterio.hay_cambios(cfg.get("reglas", "reglas.md")),
        "buzon_minutos": float(cfg.get("correo_buzon_minutos") or 0),
        "buzon_vivo": buzon.is_alive(),
        "navegador_oculto": bool(cfg.get("navegador_oculto", True)),
        "lotes": len(lotes), "en_cola": en_cola, "errores": errores,
        "escritorio": bool(os.environ.get("CATALOGATOR_TOKEN_LOCAL")),
        **estado.resumen(),
    }


@router.post("/api/admin/reiniciar")
def reiniciar():
    """Sale con el codigo 75: el lanzador lo ve y lo vuelve a arrancar (y la pagina se recarga sola)."""
    g("log")("Reiniciando el servidor a peticion de la pagina...")

    def _salir():
        try:
            g("WORKER").parar = True
            g("BUZON").parar = True
            g("WORKER").cerrar_navegador()
        except Exception:
            pass
        os._exit(CODIGO_REINICIO)
    threading.Timer(0.8, _salir).start()
    return {"ok": True}


# ====================================================================== ajustes
# (clave, grupo, etiqueta, tipo, ayuda, opciones, pide_reinicio)
CAMPOS = [
    ("redactor", "Redacción", "Cerebro que redacta", "opcion",
     "Claude Code usa tu suscripción; la API de Anthropic y ChatGPT, una clave de pago por uso. El criterio, los ejemplos "
     "y las comprobaciones son los mismos con cualquiera.",
     [("claude_code", "Claude (Claude Code)"), ("api", "Claude (API de Anthropic)"), ("openai", "ChatGPT (OpenAI o Azure)")], False),
    ("claude_model", "Redacción", "Modelo (Claude Code)", "opcion", "", [("sonnet", "Sonnet"), ("opus", "Opus"), ("haiku", "Haiku")], False),
    ("claude_token", "Redacción", "Token de Claude Code (dura un año)", "secreto",
     "Para que la sesión no caduque. En el equipo de Catalogator abre una ventana de comandos, escribe  claude setup-token, "
     "entra con tu cuenta y pega aquí el token que te da.", None, False),
    ("api_key", "Redacción", "Clave de la API", "secreto", "Para redactar con la API, o de reserva si Claude Code pierde la sesión.", None, False),
    ("api_reserva", "Redacción", "Usar la API de reserva si Claude Code pierde la sesión", "si_no",
     "Solo si hay clave de la API. Cuesta céntimos por ficha y evita que la cola se pare.", None, False),
    ("api_model", "Redacción", "Modelo (API)", "texto", "", None, False),
    ("openai_key", "Redacción", "Clave de ChatGPT", "secreto", "La de OpenAI, o la de Azure OpenAI si usáis el de la empresa.", None, False),
    ("openai_model", "Redacción", "Modelo (ChatGPT)", "texto", "Por ejemplo gpt-5-mini o gpt-5. En Azure da igual: manda el despliegue.", None, False),
    ("openai_url", "Redacción", "Dirección de Azure OpenAI", "texto",
     "Vacía para OpenAI. Para Azure, la dirección completa del despliegue que os den en informática "
     "(…openai.azure.com/openai/deployments/NOMBRE/chat/completions?api-version=…).", None, False),
    ("reglas", "Redacción", "Criterio base", "opcion", "El ligero es el mismo criterio en la mitad de tamaño.",
     [("reglas_patrones.md", "Completo (reglas_patrones.md)"), ("reglas_ligeras.md", "Ligero (reglas_ligeras.md)")], False),
    ("generar_normal", "Redacción", "Pedir también la versión en texto normal", "si_no", "", None, False),
    ("acortar_comment", "Redacción", "Acortar el COMMENT si se pasa", "si_no", "Segunda pasada automática.", None, False),
    ("presentacion", "Redacción", "Cómo se ven las fichas en la página", "opcion", "", [("mayusculas", "MAYÚSCULAS"), ("normalizado", "Texto normal")], False),
    ("miniaturas", "Redacción", "Fotogramas que se mandan al modelo", "numero", "0 = ninguno.", None, False),
    ("ejemplos_por_ficha", "Redacción", "Fichas aprobadas que acompañan a cada envío", "numero",
     "Solo las más parecidas al envío; así puedes aprobar todas las que quieras sin alargar la redacción.", None, False),
    ("aprender_correcciones", "Redacción", "Aprender de mis correcciones", "si_no",
     "Al aprobar una ficha que has corregido, propone la regla que enseña (Reglas → Sugerencias). Nunca la añade sola.", None, False),
    ("reglas_auto_criterio", "Redacción", "Pasar solas al criterio las reglas asentadas", "si_no",
     "Una regla de Mis reglas que lleva unos días sin tocarse pasa a su sitio del criterio y sale de Mis reglas. Se puede deshacer.", None, False),
    ("reglas_auto_dias", "Redacción", "Días para dar una regla por asentada", "numero", "", None, False),
    ("escenas", "Redacción", "Reconocer escenas con el clasificador local", "si_no", "", None, False),
    ("escenas_modelo", "Redacción", "Modelo de reconocimiento de imágenes", "opcion",
     "El ligero ocupa la cuarta parte y reconoce más rápido cada fotograma; al cambiarlo se vuelve a entrenar solo.",
     [("ligero", "Ligero (rápido, recomendado)"), ("completo", "Completo")], True),
    ("escenas_entrenar_auto", "Redacción", "Volver a entrenar las imágenes solo", "si_no",
     "Cuando hay imágenes nuevas (fichas aprobadas o subidas), unos minutos después.", None, False),

    ("correo_usuario", "Correo", "Cuenta de correo de Catalogator", "texto", "La dirección desde la que se envía y en la que se reciben las peticiones.", None, True),
    ("correo_clave", "Correo", "Contraseña de aplicación", "secreto", "En Gmail: Cuenta de Google → Seguridad → Contraseñas de aplicaciones.", None, True),
    ("correo_servidor", "Correo", "Servidor de salida (SMTP)", "texto", "", None, False),
    ("correo_puerto", "Correo", "Puerto SMTP", "numero", "", None, False),
    ("correo_imap", "Correo", "Servidor de entrada (IMAP)", "texto", "", None, True),
    ("correo_imap_puerto", "Correo", "Puerto IMAP", "numero", "", None, True),
    ("correo_remitente", "Correo", "Nombre o dirección del remitente", "texto", "Vacío: la cuenta de correo.", None, False),
    ("correo_copia", "Correo", "Tu dirección (\"yo\")", "texto", "Recibe copia y es \"yo\" en los repartos.", None, False),
    ("correo_buzon_minutos", "Correo", "Mirar el buzón cada (minutos)", "numero", "0 = no mirar el correo.", None, True),
    ("correo_dominios", "Correo", "Dominios que pueden pedir fichas", "lista", "Separados por comas, por ejemplo: rtve.es", None, False),
    ("correo_presentacion", "Correo", "Cómo van las fichas en los correos", "opcion", "", [("mayusculas", "MAYÚSCULAS"), ("normalizado", "Texto normal")], False),
    ("correo_html", "Correo", "Correos con diseño (HTML)", "si_no", "", None, False),
    ("correo_saludos", "Correo", "Saludo y despedida en los informales", "si_no", "", None, False),

    ("navegador_oculto", "Agencias", "Navegador de las agencias oculto", "si_no", "Trabaja fuera de la pantalla; solo aparece si hace falta que inicies sesión o pases una verificación.", None, True),
    ("agencias_solo_texto", "Agencias", "Cargar solo el texto de las agencias", "si_no",
     "Sin vídeo, imágenes ni tipos de letra: las páginas cargan antes. Si una agencia dejara de leerse bien, apágalo.", None, True),
    ("reuters_xml", "Agencias", "Leer Reuters por su XML", "si_no",
     "Descarga el XML de cada ficha (botón XML): guion completo y restricciones exactas. Si falla, se lee la página como antes.", None, False),
    ("ap_datos", "Agencias", "Leer AP por sus datos", "si_no",
     "Usa los datos que la página de AP recibe por detrás: guion completo y restricción exacta. Si faltan, se lee la página como antes.", None, False),
    ("navegador", "Agencias", "Navegador", "opcion", "", [("auto", "Automático (Chrome, si no Edge)"), ("chrome", "Chrome"), ("msedge", "Edge"), ("chromium", "Chromium")], True),
    ("espera_login", "Agencias", "Segundos de espera para iniciar sesión a mano", "numero", "0 = no esperar.", None, False),
    ("ebu", "Agencias", "EBU News Exchange activado", "si_no", "", None, True),
    ("comprobar_sesion_al_arrancar", "Agencias", "Comprobar la sesión al arrancar", "si_no", "", None, False),

    ("lote_max_envios", "Lotes", "Envíos máximos por lote", "numero", "Una lista más grande se trocea.", None, False),
    ("ya_hecha_dias", "Lotes", "Días en que un Reuters ya hecho se reutiliza", "numero", "", None, False),
    ("script_max_paginas", "Lotes", "Páginas máximas del script", "numero", "Más, y la ficha lleva ALERTA.", None, False),

    ("salud_avisos", "Salud e informes", "Avisarme por correo si algo falla", "si_no",
     "Llega a «Tu dirección» cuando algo deja de ir bien y cuando se arregla; no se repite cada hora.", None, False),
    ("salud_diario", "Salud e informes", "Correo diario de «sigo vivo»", "si_no",
     "Una línea con lo de ayer. Si un día no llega, el equipo está apagado o sin internet.", None, False),
    ("salud_hora", "Salud e informes", "Hora del correo diario", "texto", "Por ejemplo 08:00. El informe semanal sale los lunes a esta hora.", None, False),
    ("salud_cada_minutos", "Salud e informes", "Minutos entre chequeos", "numero", "", None, False),
    ("informe_semanal", "Salud e informes", "Informe semanal", "si_no", "Fichas, aprobadas sin tocar, tiempo ahorrado y consumo.", None, False),
    ("informe_destinatarios", "Salud e informes", "Quién más recibe el informe semanal", "lista",
     "Direcciones separadas por comas, además de la tuya.", None, False),
    ("copias_locales", "Salud e informes", "Copia de seguridad cada noche", "si_no",
     "A las 03:00, en la carpeta copias del equipo. Se guardan las últimas.", None, False),
    ("copias_guardar", "Salud e informes", "Copias que se guardan", "numero", "", None, False),
    ("copia_correo_semanal", "Salud e informes", "Copia semanal a mi correo", "si_no",
     "Los lunes, sin contraseñas. Es la que te salva si se estropea la tarjeta de la Raspberry.", None, False),
    ("informe_minutos_a_mano", "Salud e informes", "Minutos por ficha a mano", "numero", "Para calcular el tiempo ahorrado.", None, False),
    ("informe_minutos_revision", "Salud e informes", "Minutos de revisión por ficha", "numero", "", None, False),

    ("precio_kwh", "Consumo", "Precio de la luz (€/kWh)", "numero", "El de vuestra factura; sirve para calcular el coste en Admin.", None, False),
    ("consumo_vatios_reposo", "Consumo", "Vatios en reposo", "numero",
     "Solo si el equipo no puede medirse a sí mismo (una Raspberry Pi 5 sí puede). 0 = 3 W en una Raspberry, 40 W en un PC.", None, False),
    ("consumo_vatios_carga", "Consumo", "Vatios trabajando", "numero", "0 = 9 W en una Raspberry, 90 W en un PC.", None, False),

    ("servidor_clave", "Acceso", "Clave de la página", "secreto", "Para entrar desde fuera de este PC. Mínimo 12 caracteres.", None, False),
    ("servidor_puerto", "Acceso", "Puerto", "numero", "", None, True),
]
SECRETOS = {c[0] for c in CAMPOS if c[3] == "secreto"}


def _config_cruda():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _agenda_lista(cruda):
    salida = []
    for nombre, d in (cruda.get("documentalistas") or {}).items():
        if isinstance(d, str):
            d = {"correo": d}
        ids = d.get("id") or []
        salida.append({"nombre": nombre, "correo": d.get("correo", ""),
                       "id": ", ".join([ids] if isinstance(ids, str) else ids),
                       "formal": bool(d.get("formal")), "presentacion": d.get("presentacion", "")})
    return salida


@router.get("/api/admin/ajustes")
def ver_ajustes():
    cfg = g("CFG")
    cruda = _config_cruda()
    campos = []
    for clave, grupo, etiqueta, tipo, ayuda, opciones, reinicio in CAMPOS:
        valor = cruda.get(clave, cfg.get(clave))
        if tipo == "secreto":
            valor, tiene = "", bool(cruda.get(clave) or cfg.get(clave))
        else:
            tiene = None
        if tipo == "lista":
            valor = ", ".join(valor or [])
        campos.append({"clave": clave, "grupo": grupo, "etiqueta": etiqueta, "tipo": tipo, "ayuda": ayuda,
                       "opciones": [{"valor": v, "texto": t} for v, t in (opciones or [])],
                       "valor": valor, "tiene_valor": tiene, "reinicio": reinicio})
    return {"campos": campos, "agenda": _agenda_lista(cruda)}


def _convertir(tipo, valor, etiqueta):
    if tipo == "si_no":
        return bool(valor)
    if tipo == "numero":
        try:
            n = float(str(valor).replace(",", "."))
        except ValueError:
            raise HTTPException(400, f"{etiqueta}: tiene que ser un número")
        return int(n) if n == int(n) else n
    if tipo == "lista":
        return [x.strip() for x in str(valor or "").split(",") if x.strip()]
    return str(valor or "").strip()


@router.post("/api/admin/ajustes")
async def guardar_ajustes(request: Request):
    datos = await g("_json")(request)
    valores = datos.get("valores") or {}
    cruda = _config_cruda()
    antes = dict(cruda)
    por_clave = {c[0]: c for c in CAMPOS}
    for clave, valor in valores.items():
        if clave not in por_clave:
            continue
        _, _, etiqueta, tipo, _, opciones, _ = por_clave[clave]
        if tipo == "secreto" and not str(valor or "").strip():
            continue                                  # vacio: se deja la que habia
        v = _convertir(tipo, valor, etiqueta)
        # vale una de la lista o la que ya tenia config.json (un modelo o un criterio que no esta en la lista)
        if opciones and v not in [o[0] for o in opciones] and v != antes.get(clave) and clave != "claude_model":
            raise HTTPException(400, f"{etiqueta}: valor no valido")
        if clave == "servidor_clave" and len(v) < 12:
            raise HTTPException(400, "La clave de la página tiene que tener al menos 12 caracteres")
        cruda[clave] = v
    if "agenda" in datos:
        agenda = {}
        for p in datos["agenda"] or []:
            nombre, correo = str(p.get("nombre") or "").strip(), str(p.get("correo") or "").strip()
            if not nombre and not correo:
                continue
            if not nombre or "@" not in correo:
                raise HTTPException(400, f"Agenda: a \"{nombre or correo}\" le falta el nombre o un correo válido")
            bloque = {"correo": correo}
            ids = [x.strip().upper() for x in str(p.get("id") or "").split(",") if x.strip()]
            if ids:
                bloque["id"] = ids[0] if len(ids) == 1 else ids
            if p.get("formal"):
                bloque["formal"] = True
            if p.get("presentacion") in ("mayusculas", "normalizado"):
                bloque["presentacion"] = p["presentacion"]
            agenda[nombre] = bloque if len(bloque) > 1 else correo
        cruda["documentalistas"] = agenda
    # se comprueba que el resultado se puede cargar antes de escribirlo
    from config import desplegar_agenda, DEFAULTS, ConfigError
    try:
        nuevo = desplegar_agenda(dict(DEFAULTS, **json.loads(json.dumps(cruda))))
    except ConfigError as e:
        raise HTTPException(400, str(e))
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cruda, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)
    # lo que se puede aplicar en caliente se aplica ya; lo demas, al reiniciar
    cfg = g("CFG")
    cfg.update(nuevo)
    g("configurar")(cfg)                        # redactor
    try:
        g("paginas_word").configurar(cfg)
        g("listas").configurar(cfg)
    except Exception:
        pass
    cambiadas = [c for c in por_clave if antes.get(c) != cruda.get(c)]
    if {"claude_token", "api_key", "openai_key", "openai_url", "redactor", "api_reserva"} & set(cambiadas):
        # puede que ya se pueda redactar: la pausa del modelo se levanta y se prueba en la siguiente ficha
        sys.modules["redactor"].CLAUDE_SIN_SESION["hasta"] = 0.0
        if g("WORKER").pausa_hasta:
            g("WORKER").pausa_hasta = time.time()
    reinicio = [por_clave[c][2] for c in cambiadas if por_clave[c][6]]
    g("log")("Ajustes guardados desde la pagina: " + (", ".join(cambiadas) if cambiadas else "agenda"))
    return {"ok": True, "reiniciar": bool(reinicio), "que": reinicio}


@router.post("/api/admin/correo/probar")
async def probar_correo(request: Request):
    """Manda un correo de prueba y comprueba que se puede entrar en el buzon."""
    datos = await g("_json")(request)
    cfg = g("CFG")
    destino = (datos.get("a") or cfg.get("correo_copia") or cfg.get("correo_usuario") or "").strip()
    resultado = {"envio": "", "envio_ok": False, "buzon": "", "buzon_ok": False}
    if not (cfg.get("correo_usuario") and cfg.get("correo_clave")):
        raise HTTPException(400, "Faltan la cuenta de correo o su contraseña de aplicación")
    try:
        g("enviar")(cfg, destino, "Catalogator: correo de prueba",
                    "Si te llega esto, el correo de Catalogator funciona.\n")
        resultado.update(envio=f"Enviado a {destino}", envio_ok=True)
    except Exception as e:
        resultado["envio"] = f"No se ha podido enviar: {e}"
    try:
        imap = imaplib.IMAP4_SSL(cfg.get("correo_imap") or "imap.gmail.com", int(cfg.get("correo_imap_puerto") or 993), timeout=20)
        imap.login(cfg["correo_usuario"], cfg["correo_clave"])
        imap.select("INBOX", readonly=True)
        _, datos_imap = imap.search(None, "UNSEEN")
        sin_leer = len((datos_imap[0] or b"").split())
        imap.logout()
        resultado.update(buzon=f"Buzón accesible · {sin_leer} correo(s) sin leer", buzon_ok=True)
    except Exception as e:
        resultado["buzon"] = f"No se ha podido entrar en el buzón: {type(e).__name__}: {str(e)[:160]}"
    return resultado


# ====================================================================== historial de correos
_CERROJO_CORREOS = threading.Lock()


def anotar_correo(tipo, direccion, etiqueta, fichas, lote, origen, ok=True, error="", formal=False, asunto="", detalle=""):
    """Una linea en cola/correos.jsonl. Nunca falla hacia fuera: el historial es un extra."""
    try:
        linea = {"cuando": datetime.now().strftime("%d/%m/%Y %H:%M"), "tipo": tipo, "direccion": direccion,
                 "etiqueta": etiqueta, "fichas": fichas, "origen": origen, "ok": ok, "error": error,
                 "formal": formal, "asunto": asunto, "detalle": detalle,
                 "lote": (lote or {}).get("nombre", ""), "lote_id": (lote or {}).get("id", "")}
        with _CERROJO_CORREOS:
            CORREOS_PATH.parent.mkdir(exist_ok=True)
            with CORREOS_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(linea, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _fecha(texto):
    try:
        return datetime.strptime((texto or "")[:16], "%d/%m/%Y %H:%M")
    except ValueError:
        return datetime.min


@router.get("/api/admin/correos")
def correos(limite: int = 300):
    filas = []
    try:
        with CORREOS_PATH.open(encoding="utf-8") as f:
            for l in f:
                try:
                    filas.append(json.loads(l))
                except ValueError:
                    pass
    except OSError:
        pass
    # lo de antes de que existiera el historial: se reconstruye de los lotes
    desde = min((_fecha(x["cuando"]) for x in filas), default=datetime.max)
    estado = g("ESTADO")
    with estado.lock:
        for lote in estado.lotes:
            creado = _fecha(lote.get("creado"))
            if creado >= desde:
                continue
            if lote.get("tipo") == "correo" and lote.get("responder_a"):
                filas.append({"cuando": lote.get("creado", ""), "tipo": "recibido", "direccion": lote["responder_a"],
                              "etiqueta": "", "fichas": len(lote["fichas"]), "origen": "peticion", "ok": True,
                              "error": "", "asunto": lote.get("asunto_origen", ""), "detalle": lote.get("nota", ""),
                              "lote": lote.get("nombre", ""), "lote_id": lote["id"], "anterior": True})
            for e in lote.get("envios") or []:
                filas.append({"cuando": lote.get("enviado") or lote.get("creado", ""), "tipo": "enviado",
                              "direccion": e.get("a", ""), "etiqueta": e.get("etiqueta", ""),
                              "fichas": len(e.get("fichas") or []), "origen": e.get("origen", ""), "ok": e.get("ok", True),
                              "error": e.get("error", ""), "formal": e.get("formal", False), "asunto": "", "detalle": "",
                              "lote": lote.get("nombre", ""), "lote_id": lote["id"], "anterior": True})
    filas.sort(key=lambda x: _fecha(x.get("cuando")), reverse=True)
    hoy = datetime.now().date()
    resumen = {
        "recibidos_hoy": sum(1 for x in filas if x["tipo"] == "recibido" and _fecha(x["cuando"]).date() == hoy),
        "enviados_hoy": sum(1 for x in filas if x["tipo"] == "enviado" and x.get("ok") and _fecha(x["cuando"]).date() == hoy),
        "fallos": sum(1 for x in filas if not x.get("ok")),
    }
    return {"correos": filas[:limite], "total": len(filas), "resumen": resumen}


# ====================================================================== estadisticas
@router.get("/api/admin/estadisticas")
def estadisticas(dias: int = 30):
    agencia_de = g("agencia_de")
    estado = g("ESTADO")
    hoy = datetime.now().date()
    por_dia = collections.OrderedDict(((hoy - timedelta(days=i)).strftime("%d/%m"), 0) for i in range(dias - 1, -1, -1))
    agencias = collections.Counter()
    hechas = errores = aprobadas = copiadas = 0
    segundos = []
    with estado.lock:
        for lote in estado.lotes:
            dia = _fecha(lote.get("creado")).date() if _fecha(lote.get("creado")) != datetime.min else None
            for f in lote["fichas"]:
                if f["estado"] == "hecha":
                    hechas += 1
                    if dia and dia.strftime("%d/%m") in por_dia and (hoy - dia).days < dias:
                        por_dia[dia.strftime("%d/%m")] += 1
                    try:
                        agencias[agencia_de(f.get("numero") or "") or "Otra"] += 1
                    except Exception:
                        agencias["Otra"] += 1
                    if f.get("aprobada"):
                        aprobadas += 1
                    if f.get("copiada"):
                        copiadas += 1
                    elif f.get("segundos"):
                        segundos.append(f["segundos"])
                elif f["estado"] == "error":
                    errores += 1
    medio = round(sum(segundos) / len(segundos)) if segundos else 0
    return {
        "hechas": hechas, "errores": errores, "aprobadas": aprobadas, "copiadas": copiadas,
        "segundos_medio": medio, "redactadas": len(segundos),
        "por_dia": [{"dia": d, "n": n} for d, n in por_dia.items()],
        "agencias": [{"agencia": a, "n": n} for a, n in agencias.most_common()],
        "ejemplos": len(g("listar_ejemplos")()), "reglas": len(g("listar_reglas_extra")()),
        "nota": "Cuenta lo que hay en la cola (los lotes que no se han borrado).",
    }


# ====================================================================== reglas añadidas: editar y mover
def _reescribir_reglas(reglas):
    ruta = g("REGLAS_EXTRA_PATH")
    cabecera = [l for l in ruta.read_text(encoding="utf-8").splitlines() if l.startswith("#")] if ruta.exists() else []
    ruta.write_text("\n".join(cabecera + reglas) + "\n", encoding="utf-8")


@router.put("/api/reglas/{indice}")
async def editar_regla(indice: int, request: Request):
    datos = await g("_json")(request)
    texto = " ".join(str(datos.get("texto") or "").split()).lstrip("-• ")
    lista = g("listar_reglas_extra")()
    if not 1 <= indice <= len(lista):
        raise HTTPException(404, f"No hay regla {indice}")
    if not texto:
        raise HTTPException(400, "La regla no puede quedar vacía")
    fecha = re.search(r"\s+\(\d{4}-\d{2}-\d{2}\)$", lista[indice - 1])
    if not re.search(r"\(\d{4}-\d{2}-\d{2}\)$", texto) and fecha:
        texto += fecha.group(0)
    lista[indice - 1] = texto
    _reescribir_reglas(lista)
    g("log")(f"Regla {indice} editada")
    return {"reglas": lista}


@router.post("/api/reglas/mover")
async def mover_regla(request: Request):
    datos = await g("_json")(request)
    lista = g("listar_reglas_extra")()
    de, a = int(datos.get("de") or 0), int(datos.get("a") or 0)
    if not (1 <= de <= len(lista) and 1 <= a <= len(lista)):
        raise HTTPException(400, "Posición fuera de la lista")
    lista.insert(a - 1, lista.pop(de - 1))
    _reescribir_reglas(lista)
    return {"reglas": lista}


# ====================================================================== fichas aprobadas: editar en su sitio
@router.put("/api/ejemplos/{numero}")
async def editar_ejemplo(numero: str, request: Request):
    datos = await g("_json")(request)
    redactor = sys.modules["redactor"]
    ejemplos = redactor.listar_ejemplos()
    i = next((k for k, e in enumerate(ejemplos) if e["numero"] == numero), None)
    if i is None:
        raise HTTPException(404, "No hay ninguna ficha aprobada con ese número")
    e = ejemplos[i]
    nuevo = {"envio": " ".join(str(datos.get("ENVIO", e["envio"])).split()),
             "name": " ".join(str(datos.get("NAME", e["name"])).split()),
             "comment": " ".join(str(datos.get("COMMENT", e["comment"])).split()),
             "restricciones": redactor.limpiar_restricciones(" ".join(str(datos.get("RESTRICCIONES", e["restricciones"])).split()) or "SIN AVISO")}
    if not nuevo["name"] or not nuevo["comment"]:
        raise HTTPException(400, "NAME y COMMENT no pueden quedar vacíos")
    ejemplos[i] = dict(e, **nuevo)
    ruta = redactor.EJEMPLOS_PATH
    cabecera = [l for l in ruta.read_text(encoding="utf-8").splitlines() if l.startswith("#")]
    bloques = [f"ENVIO: {x['envio']}\nNAME: {x['name']}\nCOMMENT: {x['comment']}\nRESTRICCIONES: {x['restricciones']}\n"
               for x in ejemplos]
    ruta.write_text("\n".join(cabecera) + "\n" + ("\n" + "\n".join(bloques) if bloques else ""), encoding="utf-8")
    g("log")(f"Ficha aprobada {numero} editada")
    return {"ejemplos": redactor.listar_ejemplos()}


# ====================================================================== criterio base
def _criterio_base():
    cfg = g("CFG")
    ruta = BASE_DIR / cfg.get("reglas", "reglas.md")
    if not ruta.exists():
        ruta = BASE_DIR / "reglas.md"
    return ruta.name, ruta.read_text(encoding="utf-8")


@router.get("/api/criterio")
def ver_criterio():
    nombre, texto = _criterio_base()
    return criterio.vista(nombre, texto)


@router.post("/api/criterio/{accion}")
async def cambiar_criterio(accion: str, request: Request):
    datos = await g("_json")(request)
    nombre, texto = _criterio_base()
    ident = str(datos.get("id") or "")
    try:
        if accion == "editar":
            criterio.editar(nombre, texto, ident, datos.get("texto"))
        elif accion == "quitar":
            criterio.quitar(nombre, texto, ident)
        elif accion == "restaurar":
            criterio.restaurar(nombre, ident)
        elif accion == "anadir":
            criterio.anadir(nombre, texto, str(datos.get("despues_de") or ""), datos.get("texto"))
        elif accion == "recolocar":
            criterio.recolocar(nombre, texto, ident, str(datos.get("sobre") or ""))
        else:
            raise HTTPException(404, "Acción desconocida")
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e).strip("'\""))
    g("log")(f"Criterio base: {accion} ({ident or 'nueva'})")
    return criterio.vista(nombre, texto)


# ====================================================================== pestaña Navegador (visor.py)
@router.get("/api/visor/estado")
def visor_estado():
    e = VISOR.estado()
    e["sesion_agencia"] = g("ESTADO").sesion_agencia
    e["login_activo"] = g("WORKER").login_activo
    return e


@router.get("/api/visor/imagen")
def visor_imagen():
    datos = VISOR.ultima_imagen()
    if not datos:
        return Response(status_code=204)
    return Response(datos, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/api/visor/directo")
def visor_directo():
    """La imagen en directo como video MJPEG: el navegador de la pagina la pinta sola en un <img>, cuadro
    a cuadro, sin preguntar. Se corta a los 10 minutos o al cerrarse el navegador (la pagina reconecta)."""
    from fastapi.responses import StreamingResponse

    def cuadros():
        # Chrome no pinta un cuadro hasta ver la marca del siguiente: la marca va justo detras de cada imagen
        vista, fin = -1, time.time() + 600
        yield b"--cuadro\r\n"
        while time.time() < fin:
            seq, imagen = VISOR.esperar_imagen(vista, 5)
            if not VISOR.activo:
                return
            if seq != vista and imagen:
                vista = seq
                yield (b"Content-Type: image/jpeg\r\nContent-Length: " + str(len(imagen)).encode()
                       + b"\r\n\r\n" + imagen + b"\r\n--cuadro\r\n")
    return StreamingResponse(cuadros(), media_type="multipart/x-mixed-replace; boundary=cuadro",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


ORDENES_VISOR = {"clic", "rueda", "texto", "tecla", "pestana", "atras", "recargar", "ir", "ventana"}


@router.post("/api/visor/orden")
async def visor_orden(request: Request):
    datos = await g("_json")(request)
    ordenes = datos.get("ordenes") or [datos]
    for o in ordenes[:100]:
        if not isinstance(o, dict) or o.get("t") not in ORDENES_VISOR:
            raise HTTPException(400, "Orden desconocida")
        if not VISOR.ordenar(o):
            raise HTTPException(409, "No hay ningún navegador abierto")
    return {"ok": True}


# ====================================================================== copia de los datos del usuario (PC -> Raspberry)
# lo que es del usuario y se puede llevar a otro equipo. Los perfiles del navegador no: las cookies van
# cifradas para ese equipo y en otro no sirven (alli se inicia sesion otra vez en la pestaña Navegador).
COPIA = ["config.json", "ejemplos.md", "reglas_extra.md", "criterio_cambios.json", "fichas.csv", "mediacentral.json",
         "cola/estado.json", "cola/correos.jsonl", "cola/sugerencias.json", "cola/consolidadas.json", "cola/ejemplos_uso.json", "escenas_modelo.npz", "escenas_clases.json",
         "escenas_entreno.json", "cola/consumo.json", "cola/salud.json"]
CARPETAS_COPIA = ("escenas", "escenas_auto", "broma")   # imagenes de entrenar y lo del boton prohibido: viajan enteras
# lo que depende del equipo: al cargar una copia se queda lo de este
PROPIAS_DEL_EQUIPO = ("navegador", "navegador_ruta", "headless", "servidor_puerto")


def _zip_copia(excluir=()):
    """La copia de los datos en un zip: (bytes, nombre). excluir: ficheros o carpetas que no entran (la copia
    que va por correo deja fuera config.json, con las contraseñas, y lo pesado)."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in COPIA:
            ruta = BASE_DIR / rel
            if ruta.is_file() and rel not in excluir:
                z.write(ruta, rel)
        for carpeta in CARPETAS_COPIA:
            if carpeta in excluir:
                continue
            for ruta in sorted((BASE_DIR / carpeta).glob("*/*")):
                if ruta.is_file():
                    z.write(ruta, ruta.relative_to(BASE_DIR).as_posix(), compress_type=zipfile.ZIP_STORED)
        z.writestr("COPIA_CATALOGATOR", f"{_version()} {datetime.now():%d/%m/%Y %H:%M}\n")
    return buf.getvalue(), f"catalogator-datos-{datetime.now():%Y%m%d-%H%M}.zip"


@router.get("/api/admin/copia")
def descargar_copia():
    datos, nombre = _zip_copia()
    g("log")("Copia de los datos descargada desde la pagina")
    return Response(datos, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.post("/api/admin/copia/guardar")
def guardar_copia(request: Request):
    """La ventana de escritorio no descarga ficheros: la copia se guarda directamente en Descargas de
    este PC. Solo para quien esta en este PC (por Funnel se usa la descarga normal)."""
    if not _es_este_pc(request):
        raise HTTPException(403, "Solo desde este PC; desde fuera usa Descargar")
    datos, nombre = _zip_copia()
    ruta = _guardar_en_descargas(datos, nombre)              # y abre Descargas con el fichero marcado
    g("log")(f"Copia de los datos guardada en {ruta}")
    return {"ok": True, "ruta": str(ruta)}


def _guardar_en_descargas(datos, nombre):
    carpeta = Path.home() / "Downloads"
    if not carpeta.is_dir():
        carpeta = Path.home()
    ruta = carpeta / nombre
    ruta.write_bytes(datos)
    if sys.platform == "win32":
        try:
            import subprocess
            subprocess.Popen(["explorer", "/select,", str(ruta)])
        except Exception:
            pass
    return ruta


@router.post("/api/admin/copia")
async def cargar_copia(request: Request):
    """Carga una copia hecha con descargar_copia. Lo que habia se guarda antes en _antes_de_la_copia/."""
    import io
    import shutil
    import zipfile
    formulario = await request.form()
    fichero = formulario.get("fichero")
    if fichero is None:
        raise HTTPException(400, "Falta el fichero de la copia")
    datos = await fichero.read()
    try:
        z = zipfile.ZipFile(io.BytesIO(datos))
    except zipfile.BadZipFile:
        raise HTTPException(400, "Ese fichero no es una copia de Catalogator (no es un zip)")
    if "COPIA_CATALOGATOR" not in z.namelist():
        raise HTTPException(400, "Ese zip no es una copia de Catalogator (Admin → Estado → Descargar copia)")
    def _de_carpeta(n):
        partes = n.split("/")
        return (len(partes) == 3 and partes[0] in CARPETAS_COPIA and all(partes)
                and not any(x in (".", "..") for x in partes) and re.match(r"^[\w.-]+$", partes[1] + partes[2]))
    cargados = [n for n in z.namelist() if n in COPIA or _de_carpeta(n)]
    if not cargados:
        raise HTTPException(400, "La copia no trae nada que cargar")
    aparte = BASE_DIR / "_antes_de_la_copia"
    aparte.mkdir(exist_ok=True)
    for rel in cargados:
        ruta = BASE_DIR / rel
        if ruta.exists():
            (aparte / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ruta, aparte / rel)
    avisos = []
    for rel in cargados:
        contenido = z.read(rel)
        if rel == "config.json":
            nuevo = json.loads(contenido.decode("utf-8"))
            actual = _config_cruda()
            for k in PROPIAS_DEL_EQUIPO:
                if k in actual:
                    nuevo[k] = actual[k]
                else:
                    nuevo.pop(k, None)
            if float(nuevo.get("correo_buzon_minutos") or 0) > 0:
                avisos.append("Esta copia mira el buzón de correo: cierra Catalogator en el otro equipo (o pon allí el buzón a 0); "
                              "si no, los dos contestarán los mismos correos.")
            contenido = json.dumps(nuevo, ensure_ascii=False, indent=2).encode("utf-8")
        destino = BASE_DIR / rel
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(contenido)
    sueltos = [n for n in cargados if n in COPIA]
    imagenes = len(cargados) - len(sueltos)
    g("log")(f"Copia cargada desde la pagina: {', '.join(sueltos)}" + (f" y {imagenes} imagen(es) de entrenar" if imagenes else "")
             + " (lo anterior, en _antes_de_la_copia)")
    return {"ok": True, "cargados": cargados, "avisos": avisos}


@router.post("/api/admin/probar-redaccion")
def probar_redaccion():
    """Prueba el modelo con una llamada minima; si atiende y la cola estaba en pausa, la levanta."""
    ok, texto = sys.modules["redactor"].probar_modelo()
    if ok and g("WORKER").pausa_hasta:
        g("WORKER").pausa_hasta = time.time()
        g("log")("Prueba de redaccion correcta: se levanta la pausa del modelo")
    return {"ok": ok, "texto": texto}
