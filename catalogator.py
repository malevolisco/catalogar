# -*- coding: utf-8 -*-
"""
Catalogator - el lanzador de catalogar para quien no quiere saber nada de Python.

Un solo fichero, Catalogator.exe, que lleva dentro Python y todas las librerias. Al abrirlo:
  1. Mira en GitHub si hay una version nueva de la herramienta (el codigo: servidor, reglas, panel) y,
     si la hay, la baja y la instala. Lo tuyo (config.json, fichas aprobadas, cola, sesiones de las
     agencias) no se toca nunca.
  2. Si es la primera vez, prepara la carpeta de trabajo (y si encuentra una instalacion antigua de
     catalogar, se trae sus datos) y pide lo minimo: la clave de la pagina y como redactar.
  3. Arranca el servidor, lo publica con Tailscale si esta instalado, y abre la pagina en su propia
     ventana de escritorio (pywebview, con el motor Edge WebView2 de Windows): sin navegador y sin
     teclear la clave. Desde fuera se sigue entrando por la direccion de Tailscale, con la clave.
Si la ventana de escritorio no puede abrirse (falta WebView2), queda la ventana pequeña de antes, que
abre la pagina en el navegador. Cerrar la ventana para el servidor.

Donde vive todo:  %LOCALAPPDATA%\\Catalogator\\app   (Windows)   ~/.local/share/catalogator/app (otros)
Se puede cambiar con un catalogator.json junto al exe: {"carpeta": "D:\\\\catalogar", "repo": "usuario/catalogar"}

Modos (para el propio lanzador; el usuario no los necesita):
  Catalogator.exe                 ventana normal
  Catalogator.exe --mediacentral  el relleno de MediaCentral (equipo de teletrabajo), en una consola; admite
                                  los mismos argumentos que mediacentral.py (--aprender, --max 3, --revisar)
  Catalogator.exe --servidor      (interno) arranca servidor.py desde la carpeta de la app
  Catalogator.exe --actualizar    solo comprueba e instala actualizaciones, en consola, y sale
  Catalogator.exe --consola       todo en consola, sin ventana (para ver errores de arranque)
  Catalogator.exe --clasica       la ventana pequeña de antes, con la pagina en el navegador
  python catalogator.py --consola --servicio   como servicio de systemd (Raspberry, instalar_pi.sh): al
                                  reiniciar desde la pagina sale y systemd lo vuelve a arrancar (y actualiza)
Desarrollo: python catalogator.py funciona igual, con el Python instalado.
"""
import io
import json
import os
import re
import runpy
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path

NOMBRE = "Catalogator"
REPO_DEFECTO = "malevolisco/catalogar"
ASSET_APP = "catalogator-app.zip"          # el codigo de la herramienta, tal como lo publica GitHub Actions
ASSET_EXE = "Catalogator.exe"              # el lanzador, por si una version nueva lo necesita
try:
    from catalogator_version import EXE_VERSION   # lo escribe GitHub Actions al compilar
except ImportError:
    EXE_VERSION = "dev"

CONGELADO = getattr(sys, "frozen", False)
EXE = Path(sys.executable if CONGELADO else __file__).resolve()
AQUI = EXE.parent

# lo que es del usuario y nunca se sustituye al actualizar (el zip de la app tampoco lo trae)
DATOS_USUARIO = ("config.json", "cola", "ejemplos.md", "reglas_extra.md", "fichas.csv", "perfil_chromium",
                 "perfil_mediacentral", "mediacentral.json", "mediacentral_registro.csv", "miniaturas", "escenas",
                 "escenas_auto", "escenas_modelo.npz", "escenas_cache.npz", "modelos", "debug", "catalogator.log",
                 "criterio_cambios.json", "escenas_clases.json", "escenas_entreno.json")


# ====================================================================== carpetas y ajustes del lanzador
def ajustes_lanzador():
    """catalogator.json junto al exe (opcional): carpeta de trabajo y repositorio."""
    ruta = AQUI / "catalogator.json"
    if ruta.exists():
        try:
            return json.loads(ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {}


AJUSTES = ajustes_lanzador()


def carpeta_app():
    if os.environ.get("CATALOGATOR_DIR"):
        return Path(os.environ["CATALOGATOR_DIR"])
    if AJUSTES.get("carpeta"):
        return Path(AJUSTES["carpeta"])
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / NOMBRE / "app"
    return Path.home() / ".local" / "share" / NOMBRE.lower() / "app"


APP = carpeta_app()
REPO = AJUSTES.get("repo") or REPO_DEFECTO
LOG = APP / "catalogator.log"


def registrar(texto, ventana=None):
    linea = f"[{datetime.now():%d/%m %H:%M:%S}] {texto}"
    try:
        APP.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except OSError:
        pass
    if ventana is not None:
        ventana.escribir(linea)
    else:
        try:
            print(linea, flush=True)
        except Exception:
            pass


# ====================================================================== versiones y GitHub
def version_tupla(v):
    """'v2026.10.4' -> (2026, 10, 4); 'dev' -> ()"""
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def version_instalada():
    try:
        return (APP / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _token():
    """Token de GitHub si lo hay (repo privado): catalogator.json o el github_token del config.json."""
    if AJUSTES.get("token"):
        return AJUSTES["token"]
    try:
        return json.loads((APP / "config.json").read_text(encoding="utf-8")).get("github_token") or ""
    except (OSError, ValueError):
        return ""


class _SinTokenAlRedirigir(urllib.request.HTTPRedirectHandler):
    """GitHub manda las descargas a otro servidor: ahi el token no debe ir (lo rechaza)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        nuevo = super().redirect_request(req, fp, code, msg, headers, newurl)
        if nuevo is not None and "github.com" not in urllib.parse.urlparse(newurl).netloc:
            nuevo.remove_header("Authorization")
        return nuevo


_ABRIDOR = urllib.request.build_opener(_SinTokenAlRedirigir)


def _pedir(url, binario=False, timeout=30):
    cab = {"User-Agent": NOMBRE, "Accept": "application/octet-stream" if binario else "application/vnd.github+json"}
    tok = _token()
    if tok:
        cab["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(url, headers=cab)
    with _ABRIDOR.open(req, timeout=timeout) as r:
        return r.read()


def ultima_version():
    """(tag, {nombre_asset: url_api}) de la ultima release del repositorio, o (None, {}) sin red o sin releases."""
    try:
        datos = json.loads(_pedir(f"https://api.github.com/repos/{REPO}/releases/latest"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError):
        return None, {}
    assets = {a["name"]: a["url"] for a in datos.get("assets", [])}
    return datos.get("tag_name") or "", assets


def descargar(url, destino, ventana=None):
    datos = _pedir(url, binario=True, timeout=300)
    destino.write_bytes(datos)
    return destino


# ====================================================================== instalar y actualizar la app
def instalar_zip(ruta_zip, tag, ventana=None):
    """Sustituye el codigo de la app por el del zip. Antes guarda el actual en _anterior/ por si hay que
    volver. Lo del usuario (DATOS_USUARIO) no se toca aunque viniera en el zip, que no viene."""
    APP.mkdir(parents=True, exist_ok=True)
    anterior = APP / "_anterior"
    with zipfile.ZipFile(ruta_zip) as z:
        nombres = [n for n in z.namelist() if not n.endswith("/")]
        # copia de seguridad de lo que se va a sustituir
        if anterior.exists():
            shutil.rmtree(anterior, ignore_errors=True)
        for n in nombres:
            viejo = APP / n
            if viejo.exists():
                destino = anterior / n
                destino.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(viejo, destino)
        try:
            for n in nombres:
                primero = n.split("/")[0]
                if primero in DATOS_USUARIO:
                    continue
                destino = APP / n
                destino.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as origen, destino.open("wb") as salida:
                    shutil.copyfileobj(origen, salida)
        except Exception:
            # a medias no se queda: se restaura lo anterior
            for n in nombres:
                copia = anterior / n
                if copia.exists():
                    shutil.copy2(copia, APP / n)
            raise
    (APP / "VERSION").write_text(tag, encoding="utf-8")


def actualizar_app(ventana=None):
    """Comprueba la ultima release e instala el zip si es mas nueva. Devuelve (tag instalado, cambio?)."""
    local = version_instalada()
    tag, assets = ultima_version()
    if tag is None:
        registrar("Sin conexion con GitHub: se sigue con la version instalada" + (f" ({local})" if local else ""), ventana)
        return local, False
    if not tag or ASSET_APP not in assets:
        registrar(f"El repositorio {REPO} no tiene ninguna version publicada con {ASSET_APP}", ventana)
        return local, False
    if local and version_tupla(tag) <= version_tupla(local):
        registrar(f"Al dia: version {local}", ventana)
        return local, False
    registrar(f"Version nueva {tag} (instalada: {local or 'ninguna'}). Descargando...", ventana)
    tmp = APP / f"_{ASSET_APP}"
    descargar(assets[ASSET_APP], tmp, ventana)
    instalar_zip(tmp, tag, ventana)
    tmp.unlink(missing_ok=True)
    registrar(f"Instalada la version {tag}", ventana)
    # si la version nueva exige un lanzador mas nuevo, se baja y se cambia al salir
    exe_min = ""
    try:
        exe_min = (APP / "EXE_MIN").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if CONGELADO and exe_min and EXE_VERSION != "dev" and version_tupla(exe_min) > version_tupla(EXE_VERSION) \
            and ASSET_EXE in assets:
        registrar(f"Esta version necesita un {NOMBRE}.exe mas nuevo ({exe_min}): se descarga y se cambia solo", ventana)
        nuevo = EXE.with_name(EXE.stem + ".nuevo.exe")
        descargar(assets[ASSET_EXE], nuevo, ventana)
        programar_cambio_exe(nuevo)
    return tag, True


def instalar_app_embebida(ventana=None):
    """Sin red la primera vez: el exe lleva dentro una copia del zip de la app con la que se compilo."""
    base = Path(getattr(sys, "_MEIPASS", AQUI))
    zip_dentro = base / ASSET_APP
    if zip_dentro.exists():
        tag = "embebida"
        try:
            with zipfile.ZipFile(zip_dentro) as z:
                if "VERSION" in z.namelist():
                    tag = z.read("VERSION").decode("utf-8").strip()
        except Exception:
            pass
        instalar_zip(zip_dentro, tag, ventana)
        registrar(f"Instalada la version que venia dentro del exe ({tag})", ventana)
        return True
    return False


def programar_cambio_exe(nuevo):
    """Windows no deja sustituir un exe en marcha: un .bat espera a que este se cierre y lo cambia."""
    bat = EXE.with_name("_cambiar_catalogator.bat")
    bat.write_text(
        "@echo off\r\n"
        ":espera\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        f"move /y \"{nuevo}\" \"{EXE}\" >nul 2>nul || goto espera\r\n"
        f"start \"\" \"{EXE}\"\r\n"
        "del \"%~f0\"\r\n", encoding="utf-8")
    globals()["CAMBIO_EXE_PENDIENTE"] = bat


CAMBIO_EXE_PENDIENTE = None


# ====================================================================== primera vez: datos y config
def instalacion_antigua():
    """Una carpeta catalogar de antes (con config.json), junto al exe o en el Escritorio, para traerse sus datos."""
    candidatas = [AQUI, AQUI / "catalogar", Path.home() / "Desktop" / "catalogar", Path.home() / "Escritorio" / "catalogar",
                  Path.home() / "OneDrive" / "Escritorio" / "catalogar", Path.home() / "OneDrive" / "Desktop" / "catalogar"]
    for c in candidatas:
        if c != APP and (c / "config.json").exists() and (c / "servidor.py").exists():
            return c
    return None


def importar_datos(origen, ventana=None):
    """Copia a la carpeta de la app lo que es del usuario en una instalacion antigua."""
    n = 0
    for nombre in DATOS_USUARIO:
        src = origen / nombre
        if not src.exists() or (APP / nombre).exists():
            continue
        if src.is_dir():
            shutil.copytree(src, APP / nombre)
        else:
            shutil.copy2(src, APP / nombre)
        n += 1
    registrar(f"Traidos {n} elemento(s) de {origen}", ventana)
    return n


def config_minima():
    """Crea config.json a partir de config.example.json con una clave de pagina nueva. Devuelve la clave."""
    ejemplo = APP / "config.example.json"
    try:
        cfg = json.loads(ejemplo.read_text(encoding="utf-8")) if ejemplo.exists() else {}
    except ValueError:
        cfg = {}
    clave = secrets.token_urlsafe(12)
    cfg["servidor_clave"] = clave
    cfg.setdefault("servidor_puerto", 8765)
    for k in ("github_repo", "github_token", "api_key", "correo_clave"):
        if cfg.get(k) and (str(cfg[k]).startswith(("TU_", "github_pat_x", "la-")) or "xxxx" in str(cfg[k])):
            cfg[k] = ""
    (APP / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return clave


def leer_config():
    try:
        return json.loads((APP / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar_config(cfg):
    (APP / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def hay_claude_code():
    if shutil.which("claude"):
        return True
    return any((Path.home() / ".local" / "bin" / n).exists() for n in ("claude.exe", "claude"))


# ====================================================================== el servidor
def orden_servidor():
    if CONGELADO:
        return [str(EXE), "--servidor"]
    return [sys.executable, str(Path(__file__).resolve()), "--servidor"]


def modo_servidor():
    """Proceso hijo: ejecuta servidor.py desde la carpeta de la app, con el Python de dentro del exe."""
    os.chdir(APP)
    sys.path.insert(0, str(APP))
    os.environ["PYTHONUTF8"] = "1"
    for canal in ("stdout", "stderr"):
        s = getattr(sys, canal)
        if s is None:                      # exe sin consola: que los print no revienten
            setattr(sys, canal, io.StringIO())
        else:
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    sys.argv = ["servidor.py"]
    runpy.run_path(str(APP / "servidor.py"), run_name="__main__")


def consola_propia():
    """El exe no tiene consola (es de ventana): para los modos de texto se abre una."""
    if sys.platform != "win32" or not CONGELADO:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.AllocConsole()
        sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        sys.stderr = sys.stdout
        ctypes.windll.kernel32.SetConsoleTitleW(f"{NOMBRE} - MediaCentral")
    except Exception:
        pass


def modo_mediacentral(args):
    """Equipo de teletrabajo: mediacentral.py con el Python de dentro del exe, en su consola."""
    consola_propia()
    os.chdir(APP)
    sys.path.insert(0, str(APP))
    os.environ["PYTHONUTF8"] = "1"
    if not (APP / "mediacentral.py").exists():
        c = Consola()
        try:
            actualizar_app(c)
        except Exception as e:
            print(f"No se ha podido descargar la herramienta: {type(e).__name__}: {e}")
        if not (APP / "mediacentral.py").exists() and not instalar_app_embebida(c):
            input("No hay version instalada. Pulsa Enter para cerrar... ")
            return 1
    if not (APP / "mediacentral.json").exists() and (APP / "mediacentral.example.json").exists():
        shutil.copy2(APP / "mediacentral.example.json", APP / "mediacentral.json")
        print(f"Primera vez: he creado mediacentral.json en {APP}. Pon ahi servidor_url, servidor_clave y mediacentral_url.")
        abrir_carpeta(APP)
        input("Cuando lo hayas rellenado y guardado, pulsa Enter... ")
    sys.argv = ["mediacentral.py"] + [a for a in args if a != "--mediacentral"]
    runpy.run_path(str(APP / "mediacentral.py"), run_name="__main__")
    return 0


def puerto_config():
    try:
        return int(leer_config().get("servidor_puerto") or 8765)
    except (TypeError, ValueError):
        return 8765


def responde(puerto):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/", timeout=2) as r:
            return r.status < 500
    except Exception:
        return False


def publicar_tailscale(puerto, ventana=None):
    ts = shutil.which("tailscale")
    if not ts and sys.platform == "win32" and Path(r"C:\Program Files\Tailscale\tailscale.exe").exists():
        ts = r"C:\Program Files\Tailscale\tailscale.exe"
    if not ts:
        registrar("Tailscale no esta instalado: la pagina solo se ve en este PC", ventana)
        return
    try:
        r = subprocess.run([ts, "funnel", "--bg", str(puerto)], capture_output=True, text=True, timeout=30)
        registrar("Publicado en internet con Tailscale" if r.returncode == 0 else
                  f"Tailscale no ha podido publicar: {(r.stderr or r.stdout).strip()[:160]}", ventana)
    except Exception as e:
        registrar(f"Tailscale: {type(e).__name__}", ventana)


def matar(proceso):
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proceso.pid)], capture_output=True, timeout=15)
        else:
            proceso.terminate()
        proceso.wait(timeout=10)
    except Exception:
        try:
            proceso.kill()
        except Exception:
            pass


# ====================================================================== la ventana
class Ventana:
    def __init__(self):
        import tkinter as tk
        from tkinter import scrolledtext, ttk
        self.tk, self.ttk = tk, ttk
        self.raiz = tk.Tk()
        self.raiz.title(f"{NOMBRE}" + (f"  ·  lanzador {EXE_VERSION}" if EXE_VERSION != "dev" else ""))
        self.raiz.geometry("640x420")
        self.raiz.minsize(520, 320)
        self.proceso = None
        self.puerto = 8765
        self.estado = tk.StringVar(value="Arrancando...")
        marco = ttk.Frame(self.raiz, padding=10)
        marco.pack(fill="both", expand=True)
        ttk.Label(marco, textvariable=self.estado, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.texto = scrolledtext.ScrolledText(marco, height=14, font=("Consolas", 9), state="disabled")
        self.texto.pack(fill="both", expand=True, pady=(8, 8))
        botones = ttk.Frame(marco)
        botones.pack(fill="x")
        self.b_abrir = ttk.Button(botones, text="Abrir la pagina", command=self.abrir_pagina, state="disabled")
        self.b_abrir.pack(side="left")
        ttk.Button(botones, text="Carpeta", command=lambda: abrir_carpeta(APP)).pack(side="left", padx=6)
        ttk.Button(botones, text="Registro", command=lambda: abrir_carpeta(LOG)).pack(side="left")
        ttk.Button(botones, text="Buscar actualizaciones", command=self.buscar_actualizaciones).pack(side="left", padx=6)
        ttk.Button(botones, text="MediaCentral", command=self.mediacentral).pack(side="left")
        ttk.Button(botones, text="Salir", command=self.salir).pack(side="right")
        self.raiz.protocol("WM_DELETE_WINDOW", self.salir)

    def escribir(self, linea):
        def _():
            self.texto.configure(state="normal")
            self.texto.insert("end", linea + "\n")
            self.texto.see("end")
            self.texto.configure(state="disabled")
        try:
            self.raiz.after(0, _)
        except Exception:
            pass

    def poner_estado(self, texto):
        try:
            self.raiz.after(0, lambda: self.estado.set(texto))
        except Exception:
            pass

    def abrir_pagina(self):
        webbrowser.open(f"http://127.0.0.1:{self.puerto}")

    def buscar_actualizaciones(self):
        def _():
            try:
                tag, cambio = actualizar_app(self)
                if cambio:
                    registrar("Para usar la version nueva: Salir y volver a abrir Catalogator", self)
                    self.poner_estado(f"Version {tag} instalada: cierra y vuelve a abrir")
            except Exception as e:
                registrar(f"No se ha podido actualizar: {type(e).__name__}: {str(e)[:200]}", self)
        threading.Thread(target=_, daemon=True).start()

    def mediacentral(self):
        orden = [str(EXE), "--mediacentral"] if CONGELADO else [sys.executable, str(Path(__file__).resolve()), "--mediacentral"]
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if sys.platform == "win32" else 0
        subprocess.Popen(orden, cwd=str(APP), creationflags=flags)

    def preguntar_si(self, titulo, texto):
        from tkinter import messagebox
        return messagebox.askyesno(titulo, texto, parent=self.raiz)

    def pedir_texto(self, titulo, texto, inicial=""):
        from tkinter import simpledialog
        return simpledialog.askstring(titulo, texto, initialvalue=inicial, parent=self.raiz)

    def avisar(self, titulo, texto):
        from tkinter import messagebox
        messagebox.showinfo(titulo, texto, parent=self.raiz)

    def salir(self):
        self.saliendo = True
        if self.proceso is not None and self.proceso.poll() is None:
            registrar("Parando el servidor...", self)
            matar(self.proceso)
        try:
            self.raiz.destroy()
        except Exception:
            pass
        if CAMBIO_EXE_PENDIENTE:
            subprocess.Popen(["cmd", "/c", str(CAMBIO_EXE_PENDIENTE)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


HTML_ARRANQUE = """<!doctype html><html lang="es"><head><meta charset="utf-8"><title>Catalogator</title>
<style>
  html, body { margin:0; height:100%; }
  body { background:#0f141c; color:#aeb7c5; font:14px/1.5 "Segoe UI Variable Text", "Segoe UI", system-ui, Arial, sans-serif;
         display:flex; align-items:center; justify-content:center; }
  main { width:min(640px, 90vw); }
  .marca { display:flex; align-items:center; gap:14px; margin-bottom:26px; }
  .marca svg { width:48px; height:48px; border-radius:12px; box-shadow:0 6px 18px rgba(47,91,211,.45); }
  h1 { color:#fff; font-size:24px; font-weight:600; margin:0; letter-spacing:-.01em; }
  .sub { color:#7d8797; font-size:13px; }
  #estado { color:#e6eaf0; margin:0 0 12px; display:flex; align-items:center; gap:10px; font-weight:500; }
  .barra { height:3px; border-radius:3px; background:#1c2533; overflow:hidden; margin-bottom:16px; }
  .barra::after { content:""; display:block; height:100%; width:35%; border-radius:3px; background:linear-gradient(90deg,#4f7cff,#8fb0ff);
                  animation:ir 1.4s ease-in-out infinite; }
  @keyframes ir { 0% { transform:translateX(-100%) } 100% { transform:translateX(290%) } }
  #lineas { background:#0b1017; border:1px solid #1d2532; border-radius:10px; padding:12px 14px; height:min(40vh, 300px); overflow:auto;
            font:12px/1.65 "Cascadia Mono", Consolas, monospace; white-space:pre-wrap; color:#8892a3; }
</style></head><body><main>
<div class="marca"><svg viewBox="0 0 32 32"><defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4f7cff"/><stop offset="1" stop-color="#2346b0"/></linearGradient></defs><rect width="32" height="32" rx="8" fill="url(#lg)"/><path d="M21.5 10.2A8 8 0 1 0 21.5 21.8" fill="none" stroke="#fff" stroke-width="3.2" stroke-linecap="round"/><rect x="20.6" y="14.4" width="3.2" height="3.2" rx="1" fill="#fff"/></svg>
<div><h1>Catalogator</h1><div class="sub">Archivo · fichas de agencia</div></div></div>
<p id="estado"><span id="estado-texto">Arrancando...</span></p>
<div class="barra"></div>
<div id="lineas"></div>
</main><script>
  function anadir(t) { const d = document.getElementById("lineas"); d.textContent += t + "\\n"; d.scrollTop = d.scrollHeight; }
  function estado(t) { document.getElementById("estado-texto").textContent = t; }
</script></body></html>"""


class ApiLanzador:
    """Lo que la pagina puede pedirle al lanzador desde la ventana de escritorio (Admin → Aplicacion).
    pywebview lo publica como window.pywebview.api; lo que empieza por _ no se publica."""

    def __init__(self, ventana):
        self._v = ventana

    def info(self):
        return {"lanzador": EXE_VERSION, "app": version_instalada() or "?", "carpeta": str(APP)}

    def abrir_carpeta(self):
        abrir_carpeta(APP)
        return ""

    def mediacentral(self):
        orden = [str(EXE), "--mediacentral"] if CONGELADO else [sys.executable, str(Path(__file__).resolve()), "--mediacentral"]
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if sys.platform == "win32" else 0
        subprocess.Popen(orden, cwd=str(APP), creationflags=flags)
        return "Se abre el relleno de MediaCentral en otra ventana."

    def buscar_actualizaciones(self):
        try:
            tag, cambio = actualizar_app(self._v)
        except Exception as e:
            return f"No se ha podido actualizar: {type(e).__name__}: {str(e)[:200]}"
        if not cambio:
            return f"Ya tienes la ultima version ({tag or '?'})."
        if CAMBIO_EXE_PENDIENTE:
            return f"Version {tag} instalada. Necesita un Catalogator.exe nuevo: cierra la ventana y se abrira solo con el nuevo."
        # el codigo nuevo lo carga el servidor al arrancar: se reinicia solo
        self._v.reinicio_pedido = True
        threading.Timer(1.5, lambda: matar(self._v.proceso)).start()
        return f"Version {tag} instalada. Reinicio el servidor para usarla (unos segundos)."


class VentanaWeb:
    """La ventana de escritorio: primero una pantalla de arranque con lo que va pasando y, en cuanto el
    servidor responde, la propia pagina (entrando con un token de un solo uso, sin clave)."""

    def __init__(self, webview):
        self.webview = webview
        self.proceso = None
        self.puerto = 8765
        self.token = secrets.token_urlsafe(24)
        self.saliendo = False
        self.reinicio_pedido = False
        self.en_panel = False
        self.cargada = threading.Event()
        self.win = webview.create_window(NOMBRE, html=HTML_ARRANQUE, js_api=ApiLanzador(self), width=1320, height=900,
                                         min_size=(960, 640), text_select=True, background_color="#0f141c")
        self.win.events.loaded += lambda *a: self.cargada.set()

    def _js(self, codigo):
        if self.en_panel or self.saliendo:
            return
        if self.cargada.wait(10):
            try:
                self.win.evaluate_js(codigo)
            except Exception:
                pass

    def escribir(self, linea):
        self._js(f"anadir({json.dumps(linea)})")

    def poner_estado(self, texto):
        self._js(f"estado({json.dumps(texto)})")

    def abrir_pagina(self):
        self.en_panel = True
        self.win.load_url(f"http://127.0.0.1:{self.puerto}/local?t={self.token}")

    def preguntar_si(self, titulo, texto):
        self.cargada.wait(10)
        try:
            return bool(self.win.create_confirmation_dialog(titulo, texto))
        except Exception:
            return False

    def pedir_texto(self, titulo, texto, inicial=""):
        # no hay cuadro de texto nativo: la clave de la API se pone en Admin → Ajustes
        registrar("Sin Claude Code: pon la clave de la API en la pagina, Admin → Ajustes → Redaccion", self)
        return ""

    def avisar(self, titulo, texto):
        registrar(texto.replace("\n\n", " ").replace("\n", " "), self)
        self.preguntar_si(titulo, texto)

    def salir(self):
        self.saliendo = True
        if self.proceso is not None and self.proceso.poll() is None:
            registrar("Parando el servidor...", None)
            matar(self.proceso)
        if CAMBIO_EXE_PENDIENTE:
            subprocess.Popen(["cmd", "/c", str(CAMBIO_EXE_PENDIENTE)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def ventana_escritorio():
    """Abre la ventana de escritorio y no vuelve hasta que se cierra. False si no se ha podido abrir."""
    try:
        import webview
    except Exception as e:
        registrar(f"Sin ventana de escritorio ({type(e).__name__}): se usa el navegador")
        return False
    try:
        v = VentanaWeb(webview)
    except Exception as e:
        registrar(f"No se ha podido crear la ventana de escritorio ({type(e).__name__}: {e}): se usa el navegador")
        return False
    registrar(f"{NOMBRE} lanzador {EXE_VERSION} · carpeta {APP} · repo {REPO}", v)
    almacen = APP / "_ventana"
    try:
        webview.settings["ALLOW_DOWNLOADS"] = True        # que los enlaces de descarga de la pagina funcionen
    except Exception:
        pass
    try:
        almacen.mkdir(parents=True, exist_ok=True)
        webview.start(arrancar, (v,), private_mode=False, storage_path=str(almacen))
    except Exception as e:
        registrar(f"La ventana de escritorio ha fallado ({type(e).__name__}: {e}): se usa el navegador")
        if v.proceso is None:
            return False                         # no llego a arrancar nada: se prueba con la ventana clasica
    v.salir()
    return True


def abrir_carpeta(ruta):
    try:
        if sys.platform == "win32":
            os.startfile(str(ruta))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(ruta)])
        else:
            subprocess.Popen(["xdg-open", str(ruta)])
    except Exception:
        pass


def token_servicio():
    """El token de entrada local del servicio, guardado junto a la app (solo lo lee el usuario). La pantalla
    de la Raspberry lo usa para abrir el panel sin clave."""
    ruta = APP.parent / "token_local"
    try:
        token = ruta.read_text(encoding="utf-8").strip()
        if len(token) >= 20:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(24)
    try:
        ruta.write_text(token + "\n", encoding="utf-8")
        os.chmod(ruta, 0o600)
    except OSError:
        pass
    return token


class Consola:
    """Misma interfaz que la ventana, pero en texto (modo --consola y --actualizar)."""
    proceso = None
    puerto = 8765
    token = ""
    relanza_el_sistema = False

    def escribir(self, linea):
        print(linea, flush=True)

    def poner_estado(self, texto):
        print("== " + texto, flush=True)

    def abrir_pagina(self):
        if self.relanza_el_sistema:
            return                 # servicio (Raspberry): la pantalla, si la hay, la abre catalogator-pantalla
        webbrowser.open(f"http://127.0.0.1:{self.puerto}")

    def preguntar_si(self, titulo, texto):
        return input(f"{texto} [s/n]: ").strip().lower().startswith("s")

    def pedir_texto(self, titulo, texto, inicial=""):
        r = input(f"{texto} [{inicial}]: ").strip()
        return r or inicial

    def avisar(self, titulo, texto):
        print(texto)


# ====================================================================== la secuencia de arranque
def preparar(ventana):
    """Actualiza (o instala), prepara la carpeta y el config.json. Devuelve True si se puede arrancar."""
    APP.mkdir(parents=True, exist_ok=True)
    ventana.poner_estado("Comprobando actualizaciones...")
    try:
        actualizar_app(ventana)
    except Exception as e:
        registrar(f"No se ha podido actualizar ({type(e).__name__}: {str(e)[:160]}); se sigue con lo que hay", ventana)
    if not (APP / "servidor.py").exists():
        if not instalar_app_embebida(ventana):
            ventana.poner_estado("No hay version instalada y no se ha podido descargar")
            ventana.avisar(NOMBRE, "Hace falta conexion a internet la primera vez para descargar la herramienta.")
            return False
    if not (APP / "config.json").exists():
        antigua = instalacion_antigua()
        if antigua and ventana.preguntar_si(NOMBRE, f"He encontrado una instalacion anterior en:\n{antigua}\n\n¿Me traigo su configuracion, fichas aprobadas y sesiones?"):
            importar_datos(antigua, ventana)
    if not (APP / "config.json").exists():
        clave = config_minima()
        registrar("Primera vez: creado config.json", ventana)
        cfg = leer_config()
        if hay_claude_code():
            registrar("Claude Code esta instalado: se redacta con el", ventana)
        else:
            clave_api = ventana.pedir_texto(NOMBRE, "No encuentro Claude Code en este PC.\nPega aqui una clave de API de Anthropic para redactar (o dejalo vacio y pon luego \"api_key\" en config.json):", "")
            if clave_api:
                cfg["redactor"] = "api"
                cfg["api_key"] = clave_api.strip()
                guardar_config(cfg)
        ventana.avisar(NOMBRE, f"Listo. La clave para entrar en la pagina es:\n\n{clave}\n\n(esta guardada en config.json, en la carpeta de {NOMBRE})")
    cfg = leer_config()
    if len(str(cfg.get("servidor_clave") or "")) < 12:
        cfg["servidor_clave"] = secrets.token_urlsafe(12)
        guardar_config(cfg)
        ventana.avisar(NOMBRE, f"La clave de la pagina era demasiado corta. La nueva es:\n\n{cfg['servidor_clave']}")
    return True


CODIGO_REINICIO = 75      # el servidor sale con este codigo cuando la pagina pide reiniciarlo (admin.py)


def arrancar(ventana):
    if not preparar(ventana):
        return
    ventana.puerto = puerto_config()
    ventana.poner_estado("Arrancando el servidor...")
    publicar_tailscale(ventana.puerto, ventana)
    lanzar_servidor(ventana, primera=True)


def lanzar_servidor(ventana, primera=False):
    env = dict(os.environ, PYTHONUTF8="1")
    if getattr(ventana, "token", ""):
        env["CATALOGATOR_TOKEN_LOCAL"] = ventana.token      # la ventana de escritorio entra sin clave
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    ventana.reinicio_pedido = False
    ventana.proceso = subprocess.Popen(orden_servidor(), cwd=str(APP), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace", env=env, creationflags=flags)
    threading.Thread(target=_volcar_salida, args=(ventana, ventana.proceso), daemon=True).start()
    for _ in range(60):
        if responde(ventana.puerto):
            ventana.poner_estado(f"En marcha: http://127.0.0.1:{ventana.puerto}  ·  version {version_instalada() or '?'}")
            try:
                ventana.b_abrir.configure(state="normal")
            except Exception:
                pass
            if primera or getattr(ventana, "token", ""):
                ventana.abrir_pagina()
            return
        if ventana.proceso.poll() is not None:
            ventana.poner_estado("El servidor se ha parado nada mas arrancar: mira el registro")
            return
        time.sleep(1)
    ventana.poner_estado("El servidor no responde: mira el registro")


def _volcar_salida(ventana, proceso):
    try:
        for linea in proceso.stdout:
            registrar(linea.rstrip("\n"), ventana)
    except Exception:
        pass
    codigo = proceso.wait()
    if getattr(ventana, "saliendo", False):
        return
    if getattr(ventana, "relanza_el_sistema", False):
        return               # servicio (Raspberry): sale el lanzador entero y systemd lo arranca de nuevo, actualizando
    if codigo == CODIGO_REINICIO or getattr(ventana, "reinicio_pedido", False):
        registrar("Reiniciando el servidor...", ventana)
        ventana.poner_estado("Reiniciando el servidor...")
        lanzar_servidor(ventana)
        return
    ventana.poner_estado("El servidor se ha parado")


def main():
    args = sys.argv[1:]
    if "--servidor" in args:
        modo_servidor()
        return 0
    if "--mediacentral" in args:
        return modo_mediacentral(args)
    if "--actualizar" in args:
        c = Consola()
        try:
            actualizar_app(c)
        except Exception as e:
            print(f"No se ha podido actualizar: {type(e).__name__}: {e}")
            return 1
        return 0
    if "--consola" in args or (not CONGELADO and os.environ.get("CATALOGATOR_CONSOLA")):
        c = Consola()
        # como servicio (--servicio, la Raspberry) el reinicio lo hace systemd: sale con el codigo del servidor
        c.relanza_el_sistema = "--servicio" in args
        if c.relanza_el_sistema:
            # token fijo para la pantalla de la Raspberry (catalogator-pantalla): entra sin clave por /local
            c.token = token_servicio()
        codigo = 0
        try:
            arrancar(c)
            if c.proceso is not None:
                codigo = c.proceso.wait()
        except KeyboardInterrupt:
            pass
        finally:
            if c.proceso is not None and c.proceso.poll() is None:
                matar(c.proceso)
        return codigo if c.relanza_el_sistema else 0
    if "--clasica" not in args and ventana_escritorio():
        return 0
    try:
        v = Ventana()
    except Exception as e:                   # sin tkinter (raro): consola
        print(f"Sin ventana ({type(e).__name__}); modo consola")
        return main_consola()
    registrar(f"{NOMBRE} lanzador {EXE_VERSION} · carpeta {APP} · repo {REPO}", v)
    threading.Thread(target=arrancar, args=(v,), daemon=True).start()
    v.raiz.mainloop()
    return 0


def main_consola():
    sys.argv.append("--consola")
    return main()


if __name__ == "__main__":
    sys.exit(main())
