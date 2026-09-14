# -*- coding: utf-8 -*-
"""
extractor.py - Localiza un envio en Reuters Connect (Edit No de 4 cifras) o en
AP Newsroom (Story No de 7 cifras) y devuelve el texto de la ficha.

Uso directo (prueba):
    python extractor.py 0624
    python extractor.py 0624 06/09/2026 --debug --headed
    python extractor.py 4681323 --debug --headed

Requiere: pip install playwright ; playwright install chromium
La sesion de ambas agencias se guarda en ./perfil_chromium (crearla con login.py).
"""
import re
import sys
import json
import argparse
from pathlib import Path
import json as _json
from urllib.parse import urljoin, unquote, quote

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

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
ARGS_LIGEROS = ["--disable-dev-shm-usage", "--disable-gpu", "--disable-software-rasterizer",
                "--disable-extensions", "--mute-audio", "--js-flags=--max-old-space-size=512"]


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


def abrir_contexto(pw, headless=False, canal="auto", ruta=None, ligero=None):
    """Abre el navegador con perfil persistente y sin marcas de automatizacion.
    canal: 'chrome', 'msedge', 'chromium' (el de Playwright), 'sistema' (el instalado) o 'auto'.
    ruta: ejecutable concreto (tiene prioridad). ligero: opciones de bajo consumo (por defecto, en Linux)."""
    import sys as _sys
    if ligero is None:
        ligero = _sys.platform.startswith("linux")
    kwargs = dict(
        headless=headless,
        chromium_sandbox=True,
        viewport={"width": 1400, "height": 1000},
        locale="es-ES",
        ignore_default_args=["--enable-automation"],
        args=ARGS_BASE + (ARGS_LIGEROS if ligero else []),
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
    def __init__(self, headless=False, debug=False, canal="auto", ruta=None, miniaturas=0,
                 espera_login=60):
        self.headless = headless
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
        self._ctx, self.navegador = abrir_contexto(self._pw, headless=self.headless, canal=self.canal, ruta=self.ruta)
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
    def _dump(self, etiqueta):
        if not self.debug:
            return
        DEBUG_DIR.mkdir(exist_ok=True)
        try:
            self._page.screenshot(path=str(DEBUG_DIR / f"{etiqueta}.png"), full_page=True)
            (DEBUG_DIR / f"{etiqueta}.html").write_text(self._page.content(), encoding="utf-8")
            (DEBUG_DIR / f"{etiqueta}.txt").write_text(
                self._page.locator("body").inner_text(), encoding="utf-8"
            )
        except Exception as e:  # el volcado nunca debe tumbar la extraccion
            print(f"[debug] no se pudo volcar {etiqueta}: {e}", file=sys.stderr)

    def _esperar(self, ms=1500):
        try:
            self._page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout:
            pass
        self._page.wait_for_timeout(ms)

    def _is_login(self):
        url = self._page.url.lower()
        if any(k in url for k in ("login", "signin", "sign-in", "auth")):
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
        headline = ""
        for sel in ("h1", "h2"):
            try:
                if page.locator(sel).count() > 0:
                    headline = page.locator(sel).first.inner_text().strip()
                    if headline:
                        break
            except Exception:
                pass
        if page.locator("main").count() > 0:
            texto = page.locator("main").first.inner_text()
        else:
            texto = page.locator("body").inner_text()
        return headline, self._limpiar(texto, headline)

    @staticmethod
    def _limpiar(texto, headline):
        t = texto.replace("\r", "")
        if headline and headline in t:
            t = t[t.index(headline):]
        # fuera transcripcion automatica y lista de escenas (no revisadas por la agencia)
        t = re.sub(r"Video Transcript.*?Show Scene List", "", t, flags=re.S)
        t = re.sub(r"Disclaimer.*?chat function\.", "", t, flags=re.S)
        # fuera bloques de "mas como este", salvo que la metadata venga despues de ellos
        for corte in ("More like this", "MORE LIKE THIS"):
            if corte in t:
                i = t.index(corte)
                j = t.find("Video Metadata")
                if j == -1 or j < i:
                    t = t[:i]
        # lineas que son solo un timecode
        t = "\n".join(l for l in t.split("\n") if not re.fullmatch(r"\s*\d{2}:\d{2}:\d{2}\s*", l))
        t = re.sub(r"\n{3,}", "\n\n", t).strip()
        return t[:25000]

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

    @staticmethod
    def _elegir(candidatos, fecha):
        if fecha:
            filtrados = [c for c in candidatos if c["fecha"] == fecha]
            if not filtrados:
                fechas = sorted({c["fecha"] for c in candidatos})
                raise NotFound(f"NO ENCONTRADO EN ESA FECHA. Fechas disponibles: {', '.join(fechas) or 'ninguna'}")
            candidatos = filtrados
        if not candidatos:
            raise NotFound("NO ENCONTRADO")
        # mas reciente; a igualdad, revision mas alta
        return sorted(candidatos, key=lambda c: (c["orden"], c["rev"]), reverse=True)[0]

    def _abrir_reuters(self, cand, numero):
        page = self._page
        if cand["href"]:
            page.goto(cand["href"], wait_until="domcontentloaded")
        else:
            # k-esimo elemento con "Edit No: NNNN" y el primer enlace que le sigue en el DOM
            els = page.get_by_text(re.compile(rf"Edit No:\s*{numero}\b")).all()
            if not els:
                raise NotFound("NO ENCONTRADO (cabecera sin elemento)")
            el = els[min(cand["locator"], len(els) - 1)]
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

    def _fetch_reuters(self, numero, fecha):
        import time as _time
        page = self._page
        t0 = _time.time()
        page.goto(SEARCH_URL.format(numero=numero), wait_until="domcontentloaded")
        # sondear la lista hasta que aparezca algun candidato (max 25 s), sin esperar a networkidle
        candidatos = []
        restante = 25000
        while restante > 0:
            try:
                candidatos = self._candidatos_por_enlace(numero) or self._candidatos_por_cabecera(numero)
            except Exception:
                candidatos = []
            if candidatos:
                break
            page.wait_for_timeout(500)
            restante -= 500
        t_lista = _time.time() - t0
        if not candidatos:
            self._comprobar_acceso("Reuters Connect", SEARCH_URL.format(numero=numero))
            candidatos = self._candidatos_por_enlace(numero) or self._candidatos_por_cabecera(numero)
        if not candidatos:
            self._dump(f"{numero}_resultados")
        elif self.debug:
            self._dump(f"{numero}_resultados")
            print(f"[debug] Reuters lista en {t_lista:.1f} s · {len(candidatos)} candidato(s)", file=sys.stderr)
        cand = self._elegir(candidatos, fecha)
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
        headline, texto = self._leer_ficha()
        minis = self.miniaturas(numero, self.n_miniaturas)
        return {
            "agencia": "REUTERS",
            "miniaturas": minis,
            "numero": numero,
            "fecha": info.get("fecha") or cand["fecha"],
            "rev": info.get("rev") or cand["rev"],
            "slug": cand.get("slug", ""),
            "headline": headline,
            "url": page.url,
            "texto": texto,
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
                datos = _json.loads(txt)
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
            debug_previo = self.debug
            self.debug = True
            self._dump(f"AP{numero}_fallo")
            self.debug = debug_previo
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
    def fetch(self, numero, fecha=None):
        """Devuelve dict con agencia, numero, fecha, rev, slug, headline, url, texto.
        4 cifras -> Reuters Connect (Edit No); 7 cifras -> AP Newsroom (Story No)."""
        numero = numero.strip().upper()
        if numero.startswith("AP"):
            numero = numero[2:].strip()
        if fecha:
            fecha = fecha.strip()
            if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", fecha):
                raise ValueError("La fecha debe ser DD/MM/AAAA")
        if not numero.isdigit():
            raise ValueError(f"Numero no valido: {numero}")
        if len(numero) >= 6:
            return self._fetch_ap(numero, fecha)
        return self._fetch_reuters(numero.zfill(4), fecha)


def main():
    ap = argparse.ArgumentParser(description="Extrae la ficha de un envio de Reuters Connect o AP Newsroom")
    ap.add_argument("numero", help="Edit No de Reuters (4 cifras) o Story No de AP (7 cifras)")
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
