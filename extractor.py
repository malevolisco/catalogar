# -*- coding: utf-8 -*-
"""
extractor.py - Localiza un envio en Reuters Connect (Edit No de 4 cifras), en AP Newsroom
(Story No de 7 cifras) o en EBU News Exchange (Item ID tipo 2026_10420363) y devuelve el
texto de la ficha.

Uso directo (prueba):
    python extractor.py 0624
    python extractor.py 0624 06/09/2026 --debug --headed
    python extractor.py 4681323 --debug --headed

Requiere: pip install playwright ; playwright install chromium
La sesion de ambas agencias se guarda en ./perfil_chromium (crearla con login.py).
"""
import re
import html as _html
import sys
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, unquote, quote, urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from paginas_word import alerta_script, FIN_RE as FIN_SCRIPT_RE
from situaciones import detectar as detectar_situaciones

BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "perfil_chromium"
DEBUG_DIR = BASE_DIR / "debug"

# ---------------------------------------------------------------- Reuters Connect
BASE = "https://www.reutersconnect.com"
SEARCH_URL = BASE + "/all?media-types=vid&search=all%3A{numero}"
# Identificador observado en la barra de direcciones:
#   newsml_RW 0624 06 09 2026 RP1 :5   -> numero, dia, mes, anio, sufijo, revision
ID_RE = re.compile(
    r"newsml_RW(\d{4})(\d{2})(\d{2})(\d{4})([A-Z0-9]*?)(?::(\d+))?(?=[&?/\s\"'#]|$)",
    re.I,
)
# Cabecera de cada tarjeta en la lista de resultados: "06/09/2026 10:21   Edit No: 0624 v5"
HEADER_RE = re.compile(r"(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2})\s*Edit No:\s*(\d{4})\s*v(\d+)", re.I)
SLUG_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-/ .]{3,}$")

# ---------------------------------------------------------------- AP Newsroom
AP_BASE = "https://newsroom.ap.org"
AP_SEARCH_URL = AP_BASE + "/home/search?query={numero}&mediaType=video"
AP_DETAIL_URL = AP_BASE + "/detail/{slug}/{hexid}/video"
HEX32_RE = re.compile(r"\b[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}\b", re.I)
AP_MESES = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


# ---------------------------------------------------------------- EBU News Exchange (Eurovision)
# La ficha se abre directamente por su Item ID (2026_10420363). El tramo intermedio de la URL es un
# codigo de procedencia (estadisticas), no identifica el envio: la propia web enlaza sus items con "1".
EBU_BASE = "https://news-exchange.ebu.ch"
EBU_ITEM_URL = EBU_BASE + "/item_detail/1/{numero}"
EBU_ID_RE = re.compile(r"^\d{4}_\d{6,9}$")
# Marcas con las que empieza el shotlist o el script en las tres agencias (mismas que paginas_word)
SCRIPT_RE = re.compile(r"^[^A-Za-z\n]{0,6}(VIDEO SHOWS|SHOWS|SHOTLIST|STORY(?:LINE)?|DOPESHEET)\b", re.M)
TOPE_TEXTO = 25000       # caracteres de ficha que se mandan al modelo; el script se recorta, no el resto


def recortar_texto(texto, tope=TOPE_TEXTO):
    """Si la ficha pasa del tope, se recorta el script (lo de en medio) y se conserva lo de despues
    (Details, Restrictions, metadatos). Devuelve (texto, recortado)."""
    if len(texto) <= tope:
        return texto, False
    m = SCRIPT_RE.search(texto)
    ini = m.start() if m else 0
    m = FIN_SCRIPT_RE.search(texto, ini + 1)
    fin = m.start() if m else len(texto)
    cola = texto[fin:]
    marca = "\n[... SCRIPT RECORTADO: ver la ficha completa en la agencia ...]\n"
    hueco = tope - len(texto[:ini]) - len(cola) - len(marca)
    if hueco < 2000:                              # la cola es enorme: se recorta a secas
        return texto[:tope], True
    return texto[:ini + hueco] + marca + cola, True

EBU_BOILERPLATE = (
    "EBU MEMBERS, SUBLICENSEES, AND EBU RADIO ONLY MEMBERS ARE DEFINED HERE",
    "FOR FULL RESTRICTIONS AND TERMS OF USE",
    "RIGHTS OF USE MAY BE WITHDRAWN",
    "PARTICIPANTS MUST IMMEDIATELY CEASE",
    "COPYRIGHT BELONGS TO THE SOURCE",
    "ADDITIONAL RESTRICTIONS MAY APPLY",
    "IF THERE IS ANY CONFLICT BETWEEN THESE RESTRICTIONS",
)


def _texto(fragmento):
    t = re.sub(r"<br\s*/?>", "\n", fragmento or "", flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = _html.unescape(t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    t = "\n".join(l.strip() for l in t.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def ebu_limpiar_restricciones(texto):
    """Quita el texto fijo que EBU pone en todos los envios y las lineas que solo afectan a otras cadenas.
    Se queda con las restricciones propias del envio y con lo que dice de los EBU MEMBERS (RTVE lo es)."""
    dentro = []
    seccion_archivo = False
    for linea in (texto or "").splitlines():
        l = linea.strip()
        if not l:
            continue
        L = l.upper()
        if L.startswith("HTTP"):
            continue
        if any(L.startswith(b) for b in EBU_BOILERPLATE):
            continue
        if L.startswith("ARCHIVE"):
            seccion_archivo = True
            continue
        if l.startswith("*"):                     # excepciones de cadenas concretas (Deutsche Welle, RFE...)
            if re.search(r"RTVE|ESRTVE|ESTVE|\bSPAIN\b|SPANISH|ESPAÑA", L):
                dentro.append("RTVE: " + l.lstrip("* ").strip())   # ...salvo la que nos nombra a nosotros
            continue
        if l.startswith("•") or l.startswith("-"):
            cuerpo = l.lstrip("•- ").strip()
            if cuerpo.upper().startswith("EBU MEMBERS"):
                cuerpo = re.sub(r"^EBU MEMBERS\*?\s*:\s*", "", cuerpo, flags=re.I)
                dentro.append(("ARCHIVE, EBU MEMBERS: " if seccion_archivo else "EBU MEMBERS: ") + cuerpo)
            continue                              # sublicensees, ASBU, radio only, cadenas sueltas: no son RTVE
        dentro.append(l)
    return "\n".join(dentro)


def ebu_parsear(html):
    """Lee una pagina item_detail de EBU News Exchange (el HTML entero) y devuelve sus campos."""
    d = {"headline": "", "slug": "", "fecha": "", "hora": "", "meta": {}, "restricciones": "",
         "dopesheet": "", "shotlist": ""}
    m = re.search(r"<title>(.*?)\s*\|\s*News\s*</title>", html, flags=re.S | re.I)
    if m:
        d["slug"] = _texto(m.group(1))
    m = re.search(r'<div class="txt-part lead">(.*?)</div>', html, flags=re.S)
    if m:
        d["headline"] = _texto(m.group(1))
    m = re.search(r"(\d{2}:\d{2})\s*-\s*\d{2}:\d{2}\s*GMT\s*-\s*(\d{2}/\d{2}/\d{4})", html)
    if m:
        d["hora"], d["fecha"] = m.group(1), m.group(2)
    for dt, dd in re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", html, flags=re.S):
        d["meta"][_texto(dt)] = _texto(dd)
    m = re.search(r'<div id="TextContent02">\s*<div>(.*?)</div>', html, flags=re.S)
    if m:
        d["restricciones"] = _texto(m.group(1))
    m = re.search(r"<h2>\s*Dopesheet\s*</h2>\s*<div>(.*?)</div>", html, flags=re.S | re.I)
    if m:
        d["dopesheet"] = _texto(m.group(1))
    m = re.search(r"<h2>\s*Shotlist\s*</h2>\s*<div>(.*?)</div>", html, flags=re.S | re.I)
    if m:
        d["shotlist"] = _texto(m.group(1))
    if not d["fecha"]:
        d["fecha"] = d["meta"].get("Date shot", "")
    return d


MESES = ("JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
         "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER")


def ebu_texto_ficha(d):
    """Monta el texto que recibe el redactor con las mismas marcas que usa Reuters (SHOWS, SHOTLIST, STORY,
    RESTRICTIONS), para que el criterio se aplique igual."""
    meta = d["meta"]
    lugar = ", ".join(x for x in (meta.get("Location", ""), meta.get("Country", "")) if x).upper()
    fecha = d["fecha"]
    try:
        dd, mm, yyyy = fecha.split("/")
        fecha_txt = f"{MESES[int(mm) - 1]} {int(dd)}, {yyyy}"
    except Exception:
        fecha_txt = fecha
    fuente = meta.get("Source", "")
    partes = [d["headline"] or d["slug"], ""]
    if d["slug"]:
        partes += [f"SLUG: {d['slug']}", ""]
    partes += [f"SHOWS: {lugar} ({fecha_txt})" + (f" ({fuente.upper()})" if fuente else ""), ""]
    if d["shotlist"]:
        partes += ["SHOTLIST:", d["shotlist"], ""]
    if d["dopesheet"]:
        partes += ["STORY: " + d["dopesheet"], ""]
    detalles = [f"{k}: {v}" for k, v in meta.items()
                if k in ("Item ID", "Date shot", "Location", "Province", "Country", "Sound", "Language", "Source")]
    if detalles:
        partes += ["DETAILS:"] + detalles + [""]
    limpias = ebu_limpiar_restricciones(d["restricciones"])
    partes += ["RESTRICTIONS (EBU News Exchange; RTVE is an EBU Member):", limpias or "None stated"]
    return "\n".join(partes).strip()


ANTIBOT_RE = re.compile(
    r"just a moment|verify you are human|verifica que eres humano|checking your browser|access denied|"
    r"attention required|are you a robot|captcha|datadome|bot detection|unusual traffic|request blocked",
    re.I,
)

CANALES = ("chrome", "msedge", None)   # orden de preferencia; None = Chromium de Playwright
# Chromium del sistema (Raspberry Pi OS y otras distribuciones)
RUTAS_SISTEMA = ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome")

ARGS_BASE = ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"]
# equipos con poca memoria (Raspberry): /dev/shm pequeña y sin GPU util
# ventana "oculta": de verdad abierta (no headless, que el antibot distingue), pero fuera de la pantalla
FUERA_DE_PANTALLA = (-10000, -10000)   # lejos de cualquier monitor (los de la izquierda tienen x negativa)
# fuera de la pantalla Windows la da por tapada y Chrome dejaria de pintarla (y de emitirla a la pestaña Navegador)
ARGS_OCULTO = ["--disable-features=CalculateNativeWinOcclusion", "--disable-backgrounding-occluded-windows",
               "--disable-renderer-backgrounding", "--disable-background-timer-throttling"]
ARGS_LIGEROS = ["--disable-dev-shm-usage", "--disable-gpu", "--disable-software-rasterizer",
                "--disable-extensions", "--mute-audio", "--js-flags=--max-old-space-size=512"]


def mostrar_ventana(ctx, page, visible=True):
    """Mueve la ventana del navegador a la pantalla (visible) o fuera de ella, sin cerrarla."""
    try:
        s = ctx.new_cdp_session(page)
        w = s.send("Browser.getWindowForTarget")["windowId"]
        s.send("Browser.setWindowBounds", {"windowId": w, "bounds": {"windowState": "normal"}})
        if visible:
            bounds = {"left": 60, "top": 40, "width": 1400, "height": 1000}
        else:
            bounds = {"left": FUERA_DE_PANTALLA[0], "top": FUERA_DE_PANTALLA[1]}
        s.send("Browser.setWindowBounds", {"windowId": w, "bounds": bounds})
        if visible:
            page.bring_to_front()
        s.detach()
        return True
    except Exception:
        return False


def ruta_sistema():
    """Ruta del navegador instalado en el sistema, o None."""
    from shutil import which
    for r in RUTAS_SISTEMA:
        if Path(r).exists():
            return r
    for nombre in ("chromium", "chromium-browser", "google-chrome"):
        r = which(nombre)
        if r:
            return r
    return None


def abrir_contexto(pw, headless=False, canal="auto", ruta=None, ligero=None, oculto=False):
    """Abre el navegador con perfil persistente y sin marcas de automatizacion.
    canal: 'chrome', 'msedge', 'chromium' (el de Playwright), 'sistema' (el instalado) o 'auto'.
    ruta: ejecutable concreto (tiene prioridad). ligero: opciones de bajo consumo (por defecto, en Linux).
    oculto: la ventana se abre fuera de la pantalla; mostrar_ventana() la trae cuando hace falta una persona."""
    import sys as _sys
    if ligero is None:
        ligero = _sys.platform.startswith("linux")
    kwargs = dict(
        headless=headless,
        chromium_sandbox=True,
        viewport={"width": 1400, "height": 1000},
        locale="es-ES",
        ignore_default_args=["--enable-automation"],
        args=ARGS_BASE + (ARGS_LIGEROS if ligero else [])
             + ([f"--window-position={FUERA_DE_PANTALLA[0]},{FUERA_DE_PANTALLA[1]}"] + ARGS_OCULTO if oculto and not headless else []),
    )
    intentos = []          # (etiqueta, kwargs extra)
    if ruta:
        intentos.append((f"sistema ({ruta})", {"executable_path": ruta}))
    if canal == "sistema":
        r = ruta or ruta_sistema()
        if not r:
            raise RuntimeError("No encuentro chromium en el sistema: instala 'sudo apt install chromium'")
        intentos.append((f"sistema ({r})", {"executable_path": r}))
    elif canal == "chromium":
        intentos.append(("chromium", {}))
    elif canal in ("chrome", "msedge"):
        intentos.append((canal, {"channel": canal}))
        intentos.append(("chromium", {}))
    else:  # auto: Chrome, Edge, Chromium de Playwright y, por ultimo, el del sistema
        for c in CANALES:
            intentos.append((c or "chromium", {"channel": c} if c else {}))
        r = ruta_sistema()
        if r:
            intentos.append((f"sistema ({r})", {"executable_path": r}))
    ultimo = None
    for etiqueta, extra in intentos:
        try:
            return pw.chromium.launch_persistent_context(str(PROFILE_DIR), **kwargs, **extra), etiqueta
        except Exception as e:
            ultimo = e
    raise RuntimeError(f"No se pudo abrir ningun navegador: {ultimo}")


class NeedsLogin(Exception):
    """La sesion de la agencia ha caducado o no existe."""


class AntiBot(Exception):
    """La agencia muestra una verificacion antibot que hay que superar a mano."""


class NotFound(Exception):
    """No hay ningun envio con ese numero (y fecha)."""


class Extractor:
    _url_busqueda_vista = False      # ya se ha anotado en consola la URL de una busqueda a mano
    def __init__(self, headless=False, debug=False, canal="auto", ruta=None, miniaturas=0,
                 espera_login=60, oculto=False):
        self.headless = headless
        self.oculto = oculto and not headless
        self.espera_login = int(espera_login or 0)
        self.debug = debug
        self.n_miniaturas = int(miniaturas or 0)
        self.canal = canal
        self.ruta = ruta
        self.navegador = ""
        self._pw = None
        self._ctx = None
        self._page = None

    # ------------------------------------------------------------------ ciclo de vida
    def open(self):
        if self._ctx:
            return
        self._pw = sync_playwright().start()
        self._ctx, self.navegador = abrir_contexto(self._pw, headless=self.headless, canal=self.canal, ruta=self.ruta,
                                                   oculto=self.oculto)
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

    def close(self):
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()
            self._ctx = self._pw = self._page = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ utilidades
    def _dump(self, etiqueta, forzar=False):
        """Guarda texto, HTML y captura de la pagina en debug/. Solo en modo debug, salvo forzar=True:
        para los fallos que hay que poder investigar despues (una busqueda sin resultados)."""
        if not (self.debug or forzar):
            return
        DEBUG_DIR.mkdir(exist_ok=True)
        # cada pieza por separado: la captura full_page falla en las fichas largas de Reuters (llevan
        # reproductor de video) y antes se llevaba por delante el HTML y el texto, que son los que sirven
        piezas = (
            ("txt", lambda: (DEBUG_DIR / f"{etiqueta}.txt").write_text(
                self._page.locator("body").inner_text(), encoding="utf-8")),
            ("html", lambda: (DEBUG_DIR / f"{etiqueta}.html").write_text(
                self._page.content(), encoding="utf-8")),
            ("png", lambda: self._page.screenshot(path=str(DEBUG_DIR / f"{etiqueta}.png"), full_page=True)),
        )
        for nombre, hacer in piezas:
            try:
                hacer()
            except Exception as e:  # el volcado nunca debe tumbar la extraccion
                print(f"[debug] no se pudo volcar {etiqueta}.{nombre}: {str(e).splitlines()[0][:160]}",
                      file=sys.stderr)

    def _esperar(self, ms=1500):
        try:
            self._page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout:
            pass
        self._page.wait_for_timeout(ms)

    def _is_login(self):
        """Pagina de inicio de sesion: por la ruta (no por la URL entera, que en las fichas de AP y
        Reuters lleva el titular: "Authorities say..." no es un login) o por un campo de contrasena."""
        u = urlparse(self._page.url)
        if re.match(r"(login|signin|sign-in|auth|sso|id|account)\.", (u.hostname or "").lower()):
            return True                                  # login.microsoftonline.com, auth.okta.com...
        if re.search(r"/(login|signin|sign-in|auth|oauth2?|sso)(/|$)", u.path.lower()):
            return True
        return self._page.locator("input[type=password]").count() > 0

    def _es_antibot(self):
        try:
            titulo = self._page.title() or ""
            cuerpo = self._page.locator("body").inner_text()[:3000]
        except Exception:
            return False
        return bool(ANTIBOT_RE.search(titulo)) or (len(cuerpo) < 1500 and bool(ANTIBOT_RE.search(cuerpo)))

    def _esperar_persona(self, agencia, motivo):
        """Da tiempo a resolver el login o el antibot a mano en la ventana antes de rendirse.
        Solo tiene sentido con ventana visible: en headless no hay nadie mirando."""
        import time as _t
        if self.headless or not self.espera_login:
            return False
        limite = self.espera_login
        if self.oculto:                 # estaba fuera de la pantalla: se trae para que la vea alguien
            mostrar_ventana(self._ctx, self._page, True)
        try:
            return self._esperar_persona_visible(agencia, motivo, limite)
        finally:
            if self.oculto:
                mostrar_ventana(self._ctx, self._page, False)

    def _esperar_persona_visible(self, agencia, motivo, limite):
        import time as _t
        print(f"\n  {motivo} en {agencia}.")
        print(f"  Resuelvelo en la ventana del navegador. Espero {limite} segundos.")
        print("  No cierres la ventana: en cuanto entres, sigo solo.")
        t0 = _t.time()
        avisado = set()
        while True:
            transcurrido = _t.time() - t0
            if transcurrido >= limite:
                break
            self._page.wait_for_timeout(3000)
            try:
                if not self._is_login() and not self._es_antibot():
                    print(f"  Acceso recuperado tras {int(_t.time() - t0)} s, continuo.\n")
                    return True
            except Exception:
                pass
            restante = int(limite - (_t.time() - t0))
            for marca in (30, 15, 5):
                if restante <= marca and marca not in avisado:
                    avisado.add(marca)
                    print(f"  quedan {marca} s")
        print("  Se acabo el tiempo de espera.\n")
        return False

    def _recargar(self, url):
        """Vuelve a la pagina que se estaba pidiendo, ya con la sesion iniciada."""
        try:
            self._page.goto(url, wait_until="domcontentloaded")
            self._page.wait_for_timeout(1500)
        except Exception:
            pass

    def _comprobar_acceso(self, agencia, url=None):
        if self._es_antibot():
            if self._esperar_persona(agencia, "Verificacion antibot"):
                if url:
                    self._recargar(url)
                if not self._es_antibot() and not self._is_login():
                    return
            self._dump(f"antibot_{agencia}")
            raise AntiBot(f"{agencia} muestra una verificacion antibot. Ejecuta python login.py, superala en la ventana y vuelve a intentarlo.")
        if self._is_login():
            if self._esperar_persona(agencia, "Sesion caducada"):
                if url:
                    self._recargar(url)
                if not self._is_login():
                    return
            raise NeedsLogin(f"Sesion de {agencia} caducada: ejecuta python login.py")

    @staticmethod
    def _parse_id(texto):
        m = ID_RE.search(unquote(texto or ""))
        if not m:
            return None
        n, dd, mm, yyyy, _suf, rev = m.groups()
        return {
            "numero": n,
            "fecha": f"{dd}/{mm}/{yyyy}",
            "orden": f"{yyyy}{mm}{dd}",
            "rev": int(rev or 0),
        }

    # ------------------------------------------------------------------ lectura de la ficha (comun)
    def _leer_ficha(self):
        page = self._page
        # desplegar el script completo si esta cortado
        for etiqueta in ("VIEW MORE", "View more", "SHOW MORE", "Show more", "Read more", "READ MORE"):
            try:
                loc = page.get_by_text(etiqueta, exact=False)
                if loc.count() > 0:
                    loc.first.click(timeout=2000)
                    page.wait_for_timeout(600)
            except Exception:
                pass
        # la ficha de Reuters marca sus campos; el h1 es solo el respaldo
        headline = ""
        for sel in ('[data-qa-component="item-headline"]', "h1", "h2"):
            try:
                if page.locator(sel).count() > 0:
                    headline = page.locator(sel).first.inner_text().strip()
                    if headline:
                        break
            except Exception:
                pass
        # el slug propio de la ficha: en Paid Content se abre por enlace directo y la tarjeta no lo trae
        slug = ""
        for sel in ('[data-qa-component="item-slug"]', '[data-rc-highlight="slug"]'):
            try:
                if page.locator(sel).count() > 0:
                    slug = (page.locator(sel).first.inner_text() or "").strip()
                    if slug:
                        break
            except Exception:
                pass
        if page.locator("main").count() > 0:
            texto = page.locator("main").first.inner_text()
        else:
            texto = page.locator("body").inner_text()
        return headline, self._limpiar(texto, headline), slug

    @staticmethod
    def _limpiar(texto, headline):
        t = texto.replace("\r", "")
        if headline and headline in t:
            t = t[t.index(headline):]
        # fuera transcripcion automatica y lista de escenas (no revisadas por la agencia)
        t = re.sub(r"Video Transcript.*?Show Scene List", "", t, flags=re.S)
        t = re.sub(r"Disclaimer.*?chat function\.", "", t, flags=re.S)
        # Paid Content: el script del proveedor trae parrafos de autopromocion y descargo que no son
        # noticia (contacto comercial, presentacion de la agencia, aviso de Reuters). Fuera, parrafo a
        # parrafo. Lo que si se queda: "Clients are required: Please credit ...", que es una restriccion.
        RELLENO = (
            r"available for licensing",          # ...contact <agencia> at <correo> or call <telefono>
            r"Thank you for using",
            r"leading news agency",              # presentacion de la agencia proveedora
            r"with reporters worldwide",
            r"does not guarantee the accuracy",  # descargo de Thomson Reuters
            r"has not verified or endorsed",     # descargo de Reuters Connect
        )
        parrafos = [b for b in re.split(r"\n\s*\n", t)
                    if not any(re.search(pat, b, flags=re.I) for pat in RELLENO)]
        t = "\n\n".join(parrafos)
        # fuera bloques de "mas como este", salvo que la metadata venga despues de ellos
        for corte in ("More like this", "MORE LIKE THIS"):
            if corte in t:
                i = t.index(corte)
                j = t.find("Video Metadata")
                if j == -1 or j < i:
                    t = t[:i]
        # lineas que son solo un timecode (salvo la que sigue a "Duration", que es la duracion del video)
        lineas, anterior = [], ""
        for l in t.split("\n"):
            if re.fullmatch(r"\s*\d{2}:\d{2}:\d{2}\s*", l) and anterior != "Duration":
                continue
            lineas.append(l)
            if l.strip():
                anterior = l.strip()
        t = "\n".join(lineas)
        t = re.sub(r"\n{3,}", "\n\n", t).strip()
        return t

    # ================================================================== REUTERS CONNECT
    def _candidatos_por_enlace(self, numero):
        """Via 1: enlaces cuyo href contiene el identificador newsml_RW...."""
        vistos = {}
        for a in self._page.locator("a[href]").all():
            try:
                href = a.get_attribute("href") or ""
            except Exception:
                continue
            info = self._parse_id(href)
            if not info or info["numero"] != numero:
                continue
            clave = (info["orden"], info["rev"])
            if clave in vistos:
                continue
            slug = ""
            try:
                t = (a.inner_text() or "").strip()
                if SLUG_RE.match(t):
                    slug = t
            except Exception:
                pass
            info.update({"href": urljoin(BASE, href), "slug": slug, "locator": None})
            vistos[clave] = info
        return list(vistos.values())

    def _candidatos_por_cabecera(self, numero):
        """Via 2: texto 'dd/mm/aaaa hh:mm Edit No: NNNN vX' de cada tarjeta."""
        body = self._page.locator("body").inner_text()
        out = []
        idx = 0
        for m in HEADER_RE.finditer(body):
            fecha, hora, n, v = m.groups()
            if n != numero:
                continue
            dd, mm, yyyy = fecha.split("/")
            out.append({
                "numero": n,
                "fecha": fecha,
                "hora": hora,
                "orden": f"{yyyy}{mm}{dd}{hora.replace(':', '')}",
                "rev": int(v),
                "href": None,
                "slug": "",
                "locator": idx,   # posicion entre las cabeceras con este numero
            })
            idx += 1
        return out

    JS_TARJETAS = r"""
    (numero) => {
      const re = new RegExp("Edit No:\\s*" + numero + "\\s*v(\\d+)", "i");
      const cualquiera = /Edit No:\s*\d{4}/i;
      const fechaRe = /(\d{2}\/\d{2}\/\d{4})\s*(\d{2}:\d{2})?/;
      const texto = e => (e.innerText || e.textContent || "").replace(/\s+/g, " ").trim();
      // elementos mas pequenos que contienen "Edit No: NNNN"
      const hojas = [...document.querySelectorAll("body *")].filter(e =>
        re.test(texto(e)) && ![...e.children].some(c => re.test(texto(c))));
      const cuenta = el => [...el.querySelectorAll("*")].filter(x => re.test(texto(x)) && ![...x.children].some(c => re.test(texto(c)))).length + (re.test(texto(el)) && ![...el.children].some(c => re.test(texto(c))) ? 1 : 0);
      const out = [];
      hojas.forEach((hoja, i) => {
        // subir hasta el mayor contenedor que solo tenga esta cabecera: es la tarjeta
        let tarjeta = hoja;
        while (tarjeta.parentElement && tarjeta.parentElement !== document.body) {
          const p = tarjeta.parentElement;
          const n = [...p.querySelectorAll("*")].filter(x => cualquiera.test(texto(x)) && ![...x.children].some(c => cualquiera.test(texto(c)))).length;
          if (n > 1) break;
          tarjeta = p;
        }
        const m = texto(tarjeta).match(fechaRe);
        const rev = parseInt((texto(hoja).match(re) || [])[1] || "0", 10);
        const enlaces = [...tarjeta.querySelectorAll("a[href]")];
        let href = null;
        // el enlace del video es el que envuelve la miniatura; si no, el de texto mas largo
        const preferido = enlaces.find(a => a.querySelector("img"))
                       || enlaces.find(a => /newsml|\/item|\/video|\/story/i.test(a.getAttribute("href") || ""))
                       || [...enlaces].sort((a, b) => texto(b).length - texto(a).length)[0];
        if (preferido) href = preferido.href;
        out.push({ fecha: m ? m[1] : null, hora: m ? (m[2] || null) : null, rev, href, indice: i,
                   enlaces: enlaces.length, slug: (enlaces.map(a => texto(a)).find(s => /^[A-Z0-9][A-Z0-9\-\/ .]{3,}$/.test(s) && /[-\/]/.test(s)) || "") });
      });
      return out;
    }
    """

    def _candidatos_por_tarjeta(self, numero):
        """Via 3: recorre el DOM tarjeta a tarjeta. Para cada 'Edit No: NNNN' sube hasta el contenedor de su tarjeta
        y lee de ahi fecha, hora, revision y el enlace. No depende de identificadores newsml (funciona con Paid Content)
        ni de contar posiciones (no se desordena al cargarse la lista por partes)."""
        out = []
        for c in self._page.evaluate(self.JS_TARJETAS, numero) or []:
            if not c.get("fecha"):
                continue
            dd, mm, yyyy = c["fecha"].split("/")
            hora = c.get("hora") or ""
            out.append({
                "numero": numero, "fecha": c["fecha"], "hora": hora,
                "orden": f"{yyyy}{mm}{dd}{hora.replace(':', '')}",
                "rev": int(c.get("rev") or 0), "href": c.get("href"), "slug": c.get("slug") or "",
                "locator": c.get("indice"), "tarjeta": True,
            })
        return out

    def _candidatos(self, numero):
        """La cabecera de cada tarjeta ("15/09/2026 11:57 Edit No: 2656 v2") es la fuente de la fecha y la hora:
        es la fecha de creacion que muestra la lista, y funciona igual para video propio de Reuters, Paid Content
        o material de archivo. La via de los enlaces solo aporta el href y el slug cuando su fecha y revision
        coinciden con una cabecera; sus fechas no se usan para ordenar (en el material revisado traen la fecha
        de creacion original, no la de la lista). Si no hay cabeceras, se usa la via de enlaces tal cual."""
        por_enlace = por_cabecera = []
        try:
            por_tarjeta = self._candidatos_por_tarjeta(numero)
        except Exception:
            por_tarjeta = []
        if por_tarjeta and all(c.get("href") for c in por_tarjeta):
            return por_tarjeta                 # la mejor via: cada tarjeta con su fecha y su enlace
        try:
            por_cabecera = self._candidatos_por_cabecera(numero)
        except Exception:
            pass
        if por_tarjeta and not por_cabecera:
            return por_tarjeta
        try:
            por_enlace = self._candidatos_por_enlace(numero)
        except Exception:
            pass
        if not por_cabecera:
            return por_enlace
        enlaces = {(c["fecha"], c["rev"]): c for c in por_enlace}
        for c in por_cabecera:
            e = enlaces.get((c["fecha"], c["rev"]))
            if e:
                c["href"] = c.get("href") or e.get("href")
                c["slug"] = c.get("slug") or e.get("slug", "")
        return por_cabecera

    @staticmethod
    def _clave_orden(c):
        """Fecha y hora en 12 cifras, para comparar candidatos de una via y de la otra."""
        return (str(c.get("orden", "")).ljust(12, "0"), c.get("rev", 0))

    @staticmethod
    def _momento(c):
        """Fecha y hora de un candidato como datetime (sin hora: las 00:00), o None."""
        try:
            return datetime.strptime(f"{c.get('fecha', '')} {c.get('hora') or '00:00'}"[:16], "%d/%m/%Y %H:%M")
        except ValueError:
            return None

    @staticmethod
    def _elegir(candidatos, fecha, creado=None):
        """Con fecha, el de esa fecha. Con creado (cuando entro en MediaCentral, datetime), el ultimo que la
        agencia mando antes de ese momento: un envio no puede entrar antes de mandarse, y los numeros de
        Reuters se repiten cada pocos dias. Devuelve (candidato, motivo) con motivo "" o el texto de aviso."""
        if fecha:
            filtrados = [c for c in candidatos if c["fecha"] == fecha]
            if not filtrados:
                fechas = sorted({c["fecha"] for c in candidatos})
                raise NotFound(f"NO ENCONTRADO EN ESA FECHA. Fechas disponibles: {', '.join(fechas) or 'ninguna'}")
            candidatos = filtrados
        if not candidatos:
            raise NotFound("NO ENCONTRADO")
        orden = sorted(candidatos, key=Extractor._clave_orden, reverse=True)      # mas reciente primero; a igualdad, revision mas alta
        if creado and not fecha and len({c["fecha"] for c in candidatos}) > 1:
            tope = creado + timedelta(minutes=30)             # margen por relojes y zonas horarias
            antes = [c for c in orden if Extractor._momento(c) and Extractor._momento(c) <= tope]
            if antes:
                return antes[0], ""
            return orden[0], (f"todos los envios con este numero son posteriores a su entrada en MediaCentral "
                              f"({creado:%d/%m/%Y %H:%M}); cogido el mas reciente")
        return orden[0], ""

    def _abrir_reuters(self, cand, numero):
        page = self._page
        if cand["href"]:
            page.goto(cand["href"], wait_until="domcontentloaded")
        else:
            # la tarjeta se localiza por su fecha y hora (unicas en la lista); si no, por su posicion
            el = None
            if cand.get("hora"):
                marcas = page.get_by_text(re.compile(rf"{re.escape(cand['fecha'])}\s+{re.escape(cand['hora'])}")).all()
                if marcas:
                    el = marcas[0]
            if el is None:
                els = page.get_by_text(re.compile(rf"Edit No:\s*{numero}\b")).all()
                if not els:
                    raise NotFound("NO ENCONTRADO (cabecera sin elemento)")
                el = els[min(cand.get("locator") or 0, len(els) - 1)]
            enlace = el.locator("xpath=following::a[1]")
            try:
                cand["slug"] = (enlace.inner_text() or "").strip()
            except Exception:
                pass
            enlace.click()
        try:
            page.wait_for_selector("h1", timeout=20000)
        except PWTimeout:
            raise NotFound("La ficha no ha cargado (sin h1)")

    def _sondear_lista(self, numero, maximo_ms):
        """Sondea la lista de resultados hasta que aparezcan candidatos y dejen de crecer (la lista de
        Reuters se pinta por partes: con la primera tanda se puede coger un envio de otro dia)."""
        candidatos, estable, restante = [], 0, maximo_ms
        while restante > 0:
            anteriores = len(candidatos)
            candidatos = self._candidatos(numero)
            if candidatos and len(candidatos) == anteriores:
                estable += 1
                if estable >= 3:              # tres sondeos seguidos sin candidatos nuevos (1,5 s)
                    break
            else:
                estable = 0
            self._page.wait_for_timeout(500)
            restante -= 500
        return candidatos

    def _caja_busqueda(self):
        return self._page.locator("input[placeholder*='Search' i], input[type='search']").first

    def _caja_trae(self, numero):
        """True si la caja "Search for Video" ya lleva el numero (la aplicacion ha leido la URL)."""
        try:
            return numero in (self._caja_busqueda().input_value(timeout=3000) or "")
        except Exception:
            return False

    def _buscar_a_mano(self, numero):
        """Escribe el numero en la caja "Search for Video" y pulsa Enter: lo que haria una persona. Es
        lo que funciona cuando la pagina se abre con la caja vacia y "0 items" aunque la URL lleve el
        numero, que es como la esta dejando ultimamente la aplicacion de Reuters."""
        try:
            caja = self._caja_busqueda()
            caja.click(timeout=5000)
            caja.fill("")
            caja.fill(numero)
            caja.press("Enter")
            self._page.wait_for_timeout(1500)
            if not Extractor._url_busqueda_vista:
                # se anota una vez la URL que deja la busqueda a mano, para poder actualizar SEARCH_URL
                Extractor._url_busqueda_vista = True
                print(f"[reuters] busqueda escrita a mano; la URL que deja Reuters es: {self._page.url}", file=sys.stderr)
            return True
        except Exception:
            return False

    def _fetch_reuters(self, numero, fecha, creado=None):
        import time as _time
        page = self._page
        t0 = _time.time()
        url = SEARCH_URL.format(numero=numero)
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        # Si la aplicacion no ha leido el numero de la URL (caja vacia), no vale la pena esperar 25 s a
        # una lista que va a seguir en "0 items": se escribe en la caja y listo
        if not self._caja_trae(numero):
            self._buscar_a_mano(numero)
        candidatos = self._sondear_lista(numero, 25000)
        intentos = 1
        if not candidatos:
            self._comprobar_acceso("Reuters Connect", url)
            candidatos = self._candidatos(numero)
        if not candidatos and self._buscar_a_mano(numero):     # segunda vez, por si se escribio antes de tiempo
            intentos += 1
            candidatos = self._sondear_lista(numero, 15000)
        t_lista = _time.time() - t0
        if not candidatos:
            self._dump(f"{numero}_resultados", forzar=True)
            raise NotFound(f"NO ENCONTRADO: la busqueda de Reuters no ha dado ningun resultado para {numero} "
                           f"en {intentos} intento(s); la pagina esta guardada en debug\\{numero}_resultados.txt")
        if self.debug:
            self._dump(f"{numero}_resultados")
            print(f"[debug] Reuters lista en {t_lista:.1f} s · {len(candidatos)} candidato(s)"
                  f" ({'tarjetas' if candidatos[0].get('tarjeta') else 'cabeceras/enlaces'}): "
                  + ", ".join(f"{c['fecha']} {c.get('hora') or ''} v{c['rev']}{' [enlace]' if c.get('href') else ' [sin enlace]'}"
                              for c in sorted(candidatos, key=Extractor._clave_orden, reverse=True)),
                  file=sys.stderr)
        cand, motivo = self._elegir(candidatos, fecha, creado)
        avisos_lista = []
        otras = sorted({c["fecha"] for c in candidatos} - {cand["fecha"]})
        if otras and not fecha:
            cuando = cand["fecha"] + (f" a las {cand['hora']}" if cand.get("hora") else "")
            if creado and not motivo:
                avisos_lista.append(f"Hay mas envios con el numero {numero}: {', '.join(otras)}. Cogido el del {cuando}, "
                                    f"el ultimo antes de entrar en MediaCentral ({creado:%d/%m/%Y %H:%M})")
            elif creado:
                avisos_lista.append(f"Hay mas envios con el numero {numero}: {', '.join(otras)}; {motivo} ({cuando}). Comprobar")
            else:
                avisos_lista.append(f"Hay mas envios con el numero {numero}: {', '.join(otras)}. "
                                    f"Cogido el mas reciente, del {cuando}")
        t1 = _time.time()
        self._abrir_reuters(cand, numero)
        self._comprobar_acceso("Reuters Connect")
        # esperar a que la ficha tenga datos (script y Details), max 30 s
        restante = 30000
        while restante > 0:
            try:
                cuerpo = page.locator("body").inner_text()
            except Exception:
                cuerpo = ""
            if ("Edit No" in cuerpo or "Duration" in cuerpo) and ("SHOWS" in cuerpo or "STORY" in cuerpo or "SHOTLIST" in cuerpo):
                break
            page.wait_for_timeout(500)
            restante -= 500
        if self.debug:
            print(f"[debug] Reuters ficha en {_time.time() - t1:.1f} s · total {_time.time() - t0:.1f} s", file=sys.stderr)
        self._dump(f"{numero}_ficha")

        info = self._parse_id(page.url) or {}
        headline, texto, slug_ficha = self._leer_ficha()
        minis = self.miniaturas(numero, self.n_miniaturas)
        return {
            "agencia": "REUTERS",
            "miniaturas": minis,
            "numero": numero,
            "fecha": cand["fecha"] or info.get("fecha"),
            "rev": cand["rev"] or info.get("rev"),
            "slug": cand.get("slug") or slug_ficha,
            "headline": headline,
            "url": page.url,
            "texto": texto,
            "avisos": avisos_lista,
        }

    # ------------------------------------------------------------------ miniaturas
    def miniaturas(self, numero, cuantas=8):
        """Muestrea fotogramas del visor y los guarda en miniaturas/<numero>/.
        Devuelve la lista de rutas. Nunca lanza: si algo falla, devuelve []."""
        if not cuantas:
            return []
        page = self._page
        try:
            crudas = page.eval_on_selector_all(
                "img",
                "els => els.map(e => ({s: e.currentSrc || e.src || '', w: e.naturalWidth, h: e.naturalHeight}))")
        except Exception:
            return []
        vistas, cand = set(), []
        for im in crudas:
            s = (im.get("s") or "").strip()
            if not s or s.startswith("data:") or s in vistas:
                continue
            if im.get("w", 0) < 60 or im.get("h", 0) < 40:
                continue                                   # iconos, logos, avatares
            if re.search(r"logo|icon|avatar|sprite|placeholder|profile", s, re.I):
                continue
            vistas.add(s)
            cand.append(s)
        if self.debug:
            print(f"[miniaturas] {len(cand)} candidatas de {len(crudas)} imagenes en la pagina")
        if len(cand) < 3:
            return []
        if len(cand) > cuantas:                            # muestreo repartido por la duracion
            paso = len(cand) / cuantas
            cand = [cand[int(i * paso)] for i in range(cuantas)]
        destino = BASE_DIR / "miniaturas" / str(numero)
        destino.mkdir(parents=True, exist_ok=True)
        for viejo in destino.glob("*"):
            try:
                viejo.unlink()
            except OSError:
                pass
        rutas = []
        for i, u in enumerate(cand, 1):
            try:
                resp = page.request.get(u, timeout=15000)   # usa la sesion del navegador
                if not resp.ok:
                    continue
                tipo = (resp.headers.get("content-type") or "").lower()
                ext = ".png" if "png" in tipo or u.lower().endswith(".png") else ".jpg"
                f = destino / f"{i:02d}{ext}"
                f.write_bytes(resp.body())
                rutas.append(str(f))
            except Exception:
                continue
        if self.debug:
            print(f"[miniaturas] descargadas {len(rutas)} en {destino}")
        return rutas

    # ================================================================== AP NEWSROOM
    # Estructura real de newsroom.ap.org (volcado AP4683016_ventana.html):
    #   lista:  div.card[id="video_<hex32>"] > ... h2.card-title (titular) ... card-footer span (Story No)
    #           span[role=button][aria-label^="Open video details modal"]  -> abre la modal en la misma pagina
    #   modal:  lib-video-detail  con #video_script (shotlist), #tr_video_slug, #tr_video_arrival_date, #tr_video_id
    #   ficha:  /detail/<titular>/<hex32>/video  (misma estructura de metadata)

    @staticmethod
    def _visible(loc):
        try:
            return loc.is_visible()
        except Exception:
            return False

    @staticmethod
    def _ap_desde_json(obj, numero):
        """Busca en un JSON un objeto que contenga el Story No y devuelve (hexid, titular) o None."""
        encontrados = []

        def hexes_en(o, prof=0):
            out = []
            if prof > 4:
                return out
            if isinstance(o, dict):
                for v in o.values():
                    out += hexes_en(v, prof + 1)
            elif isinstance(o, list):
                for v in o[:50]:
                    out += hexes_en(v, prof + 1)
            elif isinstance(o, str):
                out += [h.replace("-", "").lower() for h in HEX32_RE.findall(o)]
            return out

        def titular_en(o):
            if not isinstance(o, dict):
                return ""
            for k in ("headline", "title", "caption", "name", "friendlyKey", "slugline"):
                v = o.get(k)
                if isinstance(v, str) and 10 < len(v) < 300:
                    return v.strip()
            return ""

        def walk(o, padres):
            if isinstance(o, dict):
                if any(isinstance(v, (str, int)) and str(v).strip() == numero for v in o.values()):
                    for cand in [o] + padres[::-1][:3]:
                        hx = hexes_en(cand)
                        if hx:
                            encontrados.append((hx[0], titular_en(o) or titular_en(cand)))
                            break
                for v in o.values():
                    walk(v, padres + [o])
            elif isinstance(o, list):
                for v in o:
                    walk(v, padres)

        walk(obj, [])
        return encontrados[0] if encontrados else None

    def _ap_url_desde_red(self, respuestas, numero):
        """Recorre las respuestas JSON capturadas y devuelve la URL /detail/ del Story No, o None."""
        vistas = []
        for r in list(respuestas):
            try:
                url = r.url
                txt = r.text()
            except Exception:
                continue
            vistas.append(url)
            if numero not in txt:
                continue
            try:
                datos = json.loads(txt)
            except Exception:
                continue
            if self.debug:
                DEBUG_DIR.mkdir(exist_ok=True)
                (DEBUG_DIR / f"AP{numero}_api.json").write_text(txt[:2_000_000], encoding="utf-8")
            hit = self._ap_desde_json(datos, numero)
            if hit:
                hexid, titular = hit
                return AP_DETAIL_URL.format(slug=quote(titular or "item", safe=""), hexid=hexid)
        if self.debug:
            DEBUG_DIR.mkdir(exist_ok=True)
            (DEBUG_DIR / f"AP{numero}_red.txt").write_text("\n".join(vistas), encoding="utf-8")
        return None

    @staticmethod
    def _ap_fecha(texto):
        """'Sep 7, 2026 17:17 (GMT)' o 'Arrival Date\nSep 7, 2026 ...' -> 07/09/2026."""
        m = re.search(r"([A-Za-z]{3})\w*\.?\s+(\d{1,2}),\s*(\d{4})", texto or "")
        if not m:
            return ""
        mes = AP_MESES.get(m.group(1).lower())
        if not mes:
            return ""
        return f"{int(m.group(2)):02d}/{mes:02d}/{m.group(3)}"

    def _ap_tarjeta(self, numero, timeout_ms):
        """Tarjeta de resultados que contiene el Story No, sondeando hasta timeout_ms. None si no aparece."""
        page = self._page
        patron = re.compile(rf"\b{numero}\b")
        restante = timeout_ms
        while restante > 0:
            try:
                tarjetas = page.locator("div.card[id^='video_']").filter(has_text=patron)
                if tarjetas.count() > 0:
                    return tarjetas.first
            except Exception:
                pass
            page.wait_for_timeout(500)
            restante -= 500
        return None

    def _ap_esperar_datos(self, numero, timeout_ms):
        """True cuando la ficha (modal o pagina) muestra el ID correcto y el script tiene texto."""
        page = self._page
        restante = timeout_ms
        while restante > 0:
            try:
                ids = page.locator("#tr_video_id .cell--val")
                if ids.count() > 0 and numero in (ids.last.inner_text() or ""):
                    script = page.locator("#video_script")
                    if script.count() == 0 or len((script.last.inner_text() or "").strip()) > 10:
                        return True
            except Exception:
                pass
            page.wait_for_timeout(500)
            restante -= 500
        return False

    def _ap_leer(self, titular_tarjeta):
        """Lee la ficha abierta (modal si existe, si no la pagina). Devuelve dict con headline, texto, slug, fecha, id."""
        page = self._page
        modal = page.locator("lib-video-detail")
        ambito = modal.last if modal.count() > 0 else page

        def valor(id_fila):
            try:
                loc = ambito.locator(f"#{id_fila} .cell--val")
                return (loc.last.inner_text() or "").strip() if loc.count() > 0 else ""
            except Exception:
                return ""

        headline = titular_tarjeta or ""
        if not headline:
            for sel in ("h1", "h2"):
                try:
                    for el in ambito.locator(sel).all()[:4]:
                        tx = (el.inner_text() or "").strip()
                        if tx and tx.lower() not in ("video metadata", "shotlist"):
                            headline = tx
                            break
                except Exception:
                    pass
                if headline:
                    break
        try:
            texto = ambito.inner_text()
        except Exception:
            texto = page.locator("body").inner_text()
        texto = self._limpiar(texto, headline)
        return {
            "headline": headline,
            "texto": texto,
            "slug": valor("tr_video_slug"),
            "fecha": self._ap_fecha(valor("tr_video_arrival_date") or valor("tr_video_creation_date")),
            "id": valor("tr_video_id"),
        }

    def _fetch_ap(self, numero, fecha):
        import time as _time
        page = self._page
        avisos = []
        respuestas = []

        def _on_response(resp):
            try:
                ct = (resp.headers.get("content-type") or "").lower()
            except Exception:
                ct = ""
            if "json" in ct or "graphql" in resp.url.lower():
                respuestas.append(resp)

        t0 = _time.time()
        page.on("response", _on_response)
        try:
            page.goto(AP_SEARCH_URL.format(numero=numero), wait_until="domcontentloaded")
            tarjeta = self._ap_tarjeta(numero, 30000)
        finally:
            page.remove_listener("response", _on_response)
        if tarjeta is None:
            self._comprobar_acceso("AP Newsroom")
        t_lista = _time.time() - t0

        modo = None
        hexid, titular = "", ""
        if tarjeta is not None:
            try:
                hexid = (tarjeta.get_attribute("id") or "")[6:]
                titular = (tarjeta.locator("h2.card-title").first.inner_text() or "").strip()
            except Exception:
                pass
            if self.debug:
                print(f"[debug] AP tarjeta en {t_lista:.1f} s · id {hexid} · {titular[:60]}", file=sys.stderr)
            self._dump(f"AP{numero}_resultados")

            # A) modal en la misma pagina
            boton = tarjeta.locator("[aria-label^='Open video details modal']")
            if boton.count() == 0:
                boton = tarjeta.locator("h2.card-title")
            try:
                boton.first.click(timeout=5000)
                if self._ap_esperar_datos(numero, 45000):
                    modo = "modal"
            except Exception:
                pass

            # B) ficha completa por URL construida con el hex de la tarjeta
            if not modo and hexid:
                page.goto(AP_DETAIL_URL.format(slug=quote(titular or "item", safe=""), hexid=hexid),
                          wait_until="domcontentloaded")
                self._comprobar_acceso("AP Newsroom")
                if self._ap_esperar_datos(numero, 45000):
                    modo = "pagina"

        # C) ultimo recurso: URL desde la API de busqueda capturada
        if not modo:
            destino = self._ap_url_desde_red(respuestas, numero)
            if destino:
                page.goto(destino, wait_until="domcontentloaded")
                self._comprobar_acceso("AP Newsroom")
                if self._ap_esperar_datos(numero, 45000):
                    modo = "red"

        if not modo:
            self._dump(f"AP{numero}_fallo", forzar=True)
            raise NotFound("NO ENCONTRADO" if tarjeta is None else "La ficha de AP no llego a mostrar sus datos")

        if self.debug:
            print(f"[debug] AP ficha via {modo}, {_time.time() - t0:.1f} s en total", file=sys.stderr)
        self._dump(f"AP{numero}_ficha")
        datos = self._ap_leer(titular)
        if numero not in (datos["id"] or "") and numero not in datos["texto"]:
            raise NotFound(f"La ficha abierta no contiene el ID {numero}")
        fecha_ficha = datos["fecha"] or fecha or ""
        if fecha and fecha_ficha and fecha != fecha_ficha:
            raise NotFound(f"NO ENCONTRADO EN ESA FECHA. El envio {numero} es del {fecha_ficha}")

        minis = self.miniaturas(numero, self.n_miniaturas)

        # dejar la pagina limpia para el siguiente envio
        if modo == "modal":
            try:
                page.locator("#btn_close").first.click(timeout=2000)
            except Exception:
                pass

        return {
            "agencia": "AP",
            "miniaturas": minis,
            "numero": numero,
            "fecha": fecha_ficha,
            "rev": "",
            "slug": datos["slug"],
            "headline": datos["headline"],
            "url": page.url,
            "texto": datos["texto"],
            "avisos": avisos,
        }

    # ------------------------------------------------------------------ API publica
    # ================================================================== EBU NEWS EXCHANGE
    def _fetch_ebu(self, numero):
        """Abre la ficha por su Item ID y la lee campo a campo (metadatos, restricciones, dopesheet, shotlist)."""
        import time as _time
        page = self._page
        t0 = _time.time()
        url = EBU_ITEM_URL.format(numero=numero)
        page.goto(url, wait_until="domcontentloaded")
        restante = 20000                          # la tabla de metadatos es la senal de que la ficha ha cargado
        while restante > 0:
            try:
                if page.locator("div.media-meta").count() > 0:
                    break
            except Exception:
                pass
            page.wait_for_timeout(500)
            restante -= 500
        self._comprobar_acceso("EBU", url)
        try:
            hay_ficha = page.locator("div.media-meta").count() > 0
        except Exception:
            hay_ficha = False
        if not hay_ficha:
            self._dump(f"{numero}_resultados", forzar=True)
            raise NotFound(f"NO ENCONTRADO en EBU: {numero} (la pagina no muestra ninguna ficha; comprueba el Item ID)")
        self._dump(f"{numero}_ficha")
        d = ebu_parsear(page.content())
        avisos = []
        visto = d["meta"].get("Item ID", "")
        if visto and visto != numero:
            avisos.append(f"La pagina muestra el Item ID {visto}, no {numero}")
        if not d["shotlist"] and not d["dopesheet"]:
            avisos.append("La ficha EBU no trae dopesheet ni shotlist: revisa el texto")
        if self.debug:
            print(f"[debug] EBU ficha en {_time.time() - t0:.1f} s · {d['slug']} · {len(d['dopesheet'])} car de dopesheet",
                  file=sys.stderr)
        return {
            "agencia": "EBU",
            "miniaturas": [],
            "numero": numero,
            "fecha": d["fecha"],
            "rev": 0,
            "slug": d["slug"],
            "headline": d["headline"] or d["slug"],
            "url": page.url,
            "texto": ebu_texto_ficha(d),
            "avisos": avisos,
        }

    def fetch(self, numero, fecha=None, creado=None):
        """Devuelve dict con agencia, numero, fecha, rev, slug, headline, url, texto.
        4 cifras -> Reuters Connect (Edit No); 7 cifras -> AP Newsroom (Story No);
        AAAA_NNNNNNNN -> EBU News Exchange (Item ID).
        creado: cuando entro el envio en MediaCentral ("dd/mm/aaaa HH:MM"), para elegir entre los
        envios de Reuters que repiten numero; con fecha no hace falta."""
        numero = numero.strip().upper()
        momento = None
        if creado:
            try:
                momento = datetime.strptime(str(creado).strip()[:16], "%d/%m/%Y %H:%M")
            except ValueError:
                momento = None
        if EBU_ID_RE.match(numero):
            ficha = self._fetch_ebu(numero)
        else:
            if numero.startswith("AP"):
                numero = numero[2:].strip()
            if fecha:
                fecha = fecha.strip()
                if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", fecha):
                    raise ValueError("La fecha debe ser DD/MM/AAAA")
            if not numero.isdigit():
                raise ValueError(f"Numero no valido: {numero}")
            if len(numero) >= 6:
                ficha = self._fetch_ap(numero, fecha)
            else:
                ficha = self._fetch_reuters(numero.zfill(4), fecha, momento)
        # una ficha kilometrica (rushes, multi-parte) se recorta por el script, nunca por el final,
        # que es donde van Restrictions y los metadatos
        ficha["texto"], recortado = recortar_texto(ficha.get("texto") or "")
        if recortado:
            ficha.setdefault("avisos", []).append(
                f"Texto de la ficha recortado a {TOPE_TEXTO} caracteres (script muy largo): comprobar en la agencia")
        # sin shotlist ni script el modelo solo veria el titular y se inventaria los planos: se avisa
        if not SCRIPT_RE.search(ficha.get("texto") or ""):
            ficha.setdefault("avisos", []).append(
                "La ficha se ha leido sin SHOWS/SHOTLIST/STORY (la pagina no cargo el script o ha cambiado): "
                "revisar en la agencia antes de dar por buena la ficha")
        # cuantas paginas de Word ocupa el script: si pasa del tope, ficha["alerta"] con el enlace
        try:
            alerta_script(ficha)
        except Exception as e:                    # la medida nunca tumba la extraccion
            ficha.setdefault("avisos", []).append(f"No se pudo medir el script ({type(e).__name__})")
        # lo que la agencia dice del material (restricciones, archivo, mudo...): pistas para el modelo,
        # alertas para todos y avisos para el catalogador
        try:
            detectar_situaciones(ficha)
        except Exception as e:
            ficha.setdefault("avisos", []).append(f"No se pudieron detectar las situaciones ({type(e).__name__})")
        return ficha


def main():
    ap = argparse.ArgumentParser(description="Extrae la ficha de un envio de Reuters Connect o AP Newsroom")
    ap.add_argument("numero", help="Edit No de Reuters (4 cifras), Story No de AP (7 cifras) o Item ID de EBU (2026_10420363)")
    ap.add_argument("fecha", nargs="?", default=None, help="DD/MM/AAAA (opcional)")
    ap.add_argument("--debug", action="store_true", help="guarda capturas, HTML y texto en ./debug")
    ap.add_argument("--headed", action="store_true", help="muestra el navegador")
    args = ap.parse_args()
    try:
        from config import cargar_config
        cfg = cargar_config()
    except Exception:
        cfg = {"headless": False, "navegador": "auto"}
    try:
        with Extractor(headless=(cfg.get("headless", False) and not args.headed), debug=args.debug,
                       canal=cfg.get("navegador", "auto"), ruta=cfg.get("navegador_ruta")) as ex:
            ficha = ex.fetch(args.numero, args.fecha)
    except (NeedsLogin, NotFound, AntiBot) as e:
        print(f"ERROR: {e}")
        sys.exit(2)
    print(json.dumps({k: v for k, v in ficha.items() if k != "texto"}, ensure_ascii=False, indent=2))
    print("\n----- TEXTO -----\n")
    print(ficha["texto"])


if __name__ == "__main__":
    main()
