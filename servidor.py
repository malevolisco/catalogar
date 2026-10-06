# -*- coding: utf-8 -*-
"""
servidor.py - Servidor web propio de catalogar. Sustituye a worker.py (cola de Issues de GitHub).

Arranca en el portatil de casa, solo escucha en 127.0.0.1 (nadie de la red local lo ve), y se
publica hacia fuera con Tailscale Funnel. Sirve la pagina panel/index.html y una API que la
pagina usa para: meter numeros de envio o una lista de MediaCentral, ver las fichas segun salen, aprobarlas
o corregirlas, y gestionar reglas y fichas aprobadas.

Uso:
    python servidor.py                 (puerto y clave en config.json: servidor_puerto, servidor_clave)
    python servidor.py --puerto 8765

Todo lo que se sube queda en la carpeta cola/ de este PC. Nunca sale de aqui.
"""
import sys
import json
import time
import secrets
import argparse
import threading
import traceback
from datetime import datetime, timedelta
from pathlib import Path

# La consola de Windows usa cp1252 y revienta al escribir ideogramas o el espacio ancho U+3000:
# un log con el headline de un envio chino tumbaria el hilo que lo escribe.
for _flujo in ("stdout", "stderr"):
    try:
        getattr(sys, _flujo).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, Response
import uvicorn

from playwright.sync_api import sync_playwright

from config import cargar_config, ConfigError
from extractor import Extractor, NeedsLogin, NotFound, AntiBot, abrir_contexto, BASE, AP_BASE, EBU_BASE
from redactor import (redactar, validar, configurar, RedactorError, ModeloNoDisponible, presentar_normal, version_normal,
                      anadir_ejemplo, listar_ejemplos, quitar_ejemplo,
                      anadir_regla, listar_reglas_extra, REGLAS_EXTRA_PATH)
from correo import (enviar_lote, enviar_seleccion, enviar_fichas, analizar_reparto, enviar, CorreoError,
                    hay_excepcion_normalizada, es_para_mi, es_mio, es_formal, nombre_saludo, cabecera_fichas,
                    adorno_para, cuerpo_aviso_html, resolver_destino, _norm)
from buzon import leer as leer_buzon, asunto_respuesta, nota_adjuntos, MARCA as MARCA_CORREO, BuzonError
from listas import leer_lista, leer_texto, resumen as resumen_lista, resumen_agencias, agencia_de, ListaError
import paginas_word
import listas
from actos import huella_fotogramas, guardar_para_entrenar
from catalogar import guardar_csv, FECHA_RE
import admin
import aprender
import imagenes
from visor import VISOR

BASE_DIR = Path(__file__).resolve().parent
COLA_DIR = BASE_DIR / "cola"
ESTADO_PATH = COLA_DIR / "estado.json"
PANEL_PATH = BASE_DIR / "panel" / "index.html"
MAX_LOTES_EN_PANEL = 40
FALLOS_SEGUIDOS_PAUSA = 3      # redacciones fallidas seguidas a partir de las que se pausa la cola 5 min
CAMPOS = ("ENVIO", "NAME", "COMMENT", "RESTRICCIONES")

CFG = {}
app = FastAPI(title="catalogar", docs_url=None, redoc_url=None)
ARRANCADO = datetime.now()


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ====================================================================== estado compartido
class Estado:
    """Lotes y fichas, protegidos con un cerrojo y guardados en cola/estado.json tras cada cambio."""

    def __init__(self):
        self.lock = threading.RLock()
        self.lotes = []
        self.trabajando = None            # texto: que esta haciendo ahora el worker
        self.sesion_agencia = "sin abrir"  # sin abrir | abierta | caducada
        self.modelo_parado = ""           # texto: por que el modelo no atiende y hasta cuando (o "")
        self.cargar()

    def cargar(self):
        if ESTADO_PATH.exists():
            try:
                lotes = json.loads(ESTADO_PATH.read_text(encoding="utf-8"))
                if not isinstance(lotes, list) or not all(isinstance(l, dict) and "fichas" in l for l in lotes):
                    raise ValueError("no es una lista de lotes")
                self.lotes = lotes
            except Exception as e:
                # no se pisa: se aparta con fecha para poder recuperar los lotes a mano
                aparte = ESTADO_PATH.with_name(f"estado.corrupto-{datetime.now():%Y%m%d_%H%M%S}.json")
                try:
                    ESTADO_PATH.replace(aparte)
                    donde = f"apartado como {aparte.name}"
                except OSError:
                    donde = "no se ha podido apartar"
                log(f"estado.json no se ha podido leer ({type(e).__name__}: {str(e)[:80]}); {donde}, se empieza vacio")
                self.lotes = []
        # lo que se quedo "en curso" al apagar el servidor vuelve a pendiente
        for lote in self.lotes:
            if lote["estado"] == "en curso":
                lote["estado"] = "pendiente"
            for f in lote["fichas"]:
                if f["estado"] == "en curso":
                    f["estado"] = "pendiente"

    def guardar(self):
        """Escribe estado.json. En Windows el antivirus o OneDrive tienen el fichero abierto un momento
        y el reemplazo falla con PermissionError: se reintenta, y si aun asi no se puede se anota y se
        sigue (lo de memoria esta bien y se guardara en el siguiente cambio), nunca se tumba el worker."""
        with self.lock:
            texto = json.dumps(self.lotes, ensure_ascii=False, indent=1)
            for intento in range(5):
                try:
                    COLA_DIR.mkdir(exist_ok=True)
                    tmp = ESTADO_PATH.with_suffix(".tmp")
                    tmp.write_text(texto, encoding="utf-8")
                    tmp.replace(ESTADO_PATH)
                    return True
                except OSError as e:
                    ultimo = e
                    time.sleep(0.3 * (intento + 1))
            log(f"No se ha podido guardar estado.json ({type(ultimo).__name__}: {str(ultimo)[:120]}); se reintenta en el siguiente cambio")
            return False

    def nuevo_lote(self, tipo, nombre, fichas, **extra):
        """Crea el lote completo de una vez (reparto, responder_a, nota... van en extra): el worker
        puede cogerlo en cuanto esta en la lista, y no debe verlo a medio rellenar."""
        with self.lock:
            lote = {
                "id": datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(2),
                "tipo": tipo, "nombre": nombre,
                "creado": datetime.now().strftime("%d/%m/%Y %H:%M"),
                "estado": "pendiente", "mensaje": "",
                "fichas": fichas,
                "reparto": "", "envios": [], "responder_a": "", "asunto_origen": "", "nota": "",
                "formal": False, "rehacer": False,
            }
            lote.update(extra)
            self.lotes.insert(0, lote)
            self.guardar()
            return lote

    def lote(self, id_lote):
        with self.lock:
            return next((l for l in self.lotes if l["id"] == id_lote), None)

    def ya_hecha(self, numero, fecha="", salvo_lote=None, creado=""):
        """La ficha hecha mas reciente de ese numero de envio en cualquier lote (menos salvo_lote).
        Con fecha, solo vale una hecha con esa misma fecha. Sin fecha, AP y EBU son unicos y vale la
        ultima. Los numeros de Reuters se reutilizan (el mismo Edit No vuelve cada pocos dias), asi que
        sin fecha vale la hecha reciente: la que es de los ultimos ya_hecha_dias dias (3 por defecto),
        por la fecha de la agencia o, en las fichas antiguas que no la guardaban, por el dia del lote.
        Con creado (cuando entro en MediaCentral, "dd/mm/aaaa HH:MM"), la ventana de dias se cuenta
        desde ese momento y no vale una hecha con fecha de agencia posterior a el."""
        limite = None
        momento = None
        if creado:
            try:
                momento = datetime.strptime(creado.strip()[:16], "%d/%m/%Y %H:%M")
            except ValueError:
                momento = None
        if not fecha and agencia_de(numero) == "Reuters":
            limite = (momento or datetime.now()) - timedelta(days=int(CFG.get("ya_hecha_dias") or 3))

        def fecha_agencia(f):
            try:
                return datetime.strptime((f.get("fecha") or "").strip(), "%d/%m/%Y")
            except ValueError:
                return None

        def reciente(lote, f):
            for texto, formato in ((f.get("fecha"), "%d/%m/%Y"), (lote.get("creado"), "%d/%m/%Y %H:%M")):
                try:
                    return datetime.strptime((texto or "").strip(), formato) >= limite.replace(hour=0, minute=0)
                except ValueError:
                    continue
            return False

        with self.lock:
            for lote in self.lotes:                           # el mas nuevo primero
                if lote["id"] == salvo_lote:
                    continue
                for f in lote["fichas"]:
                    if not (f["estado"] == "hecha" and f.get("numero") == numero and f.get("NAME")):
                        continue
                    if fecha and (f.get("fecha") or "") != fecha:
                        continue
                    if limite and not reciente(lote, f):
                        continue
                    if momento and limite:
                        fa = fecha_agencia(f)
                        if fa and fa > momento:               # de un envio posterior al que entro en MediaCentral
                            continue
                    return lote, f
        return None, None

    def siguiente_pendiente(self):
        """El lote pendiente mas antiguo, ya marcado "en curso" (asi nadie lo borra ni lo reencola
        entre que el worker lo coge y empieza con el)."""
        with self.lock:
            for lote in reversed(self.lotes):          # el mas antiguo primero
                if lote["estado"] == "pendiente":
                    lote["estado"] = "en curso"
                    return lote
        return None

    def resumen(self):
        with self.lock:
            return {
                "trabajando": self.trabajando,
                "modelo_parado": self.modelo_parado,
                "sesion_agencia": self.sesion_agencia,
                "pendientes": sum(1 for l in self.lotes for f in l["fichas"] if f["estado"] == "pendiente"),
                "ejemplos": len(listar_ejemplos()),
                "reglas": len(listar_reglas_extra()),
                "modelo": CFG.get("claude_model", ""),
                "criterio": CFG.get("reglas", "reglas.md"),
                "presentacion": CFG.get("presentacion", "mayusculas"),
                "correo_presentacion": CFG.get("correo_presentacion", "mayusculas"),
                "buzon": float(CFG.get("correo_buzon_minutos") or 0),
                "worker_vivo": WORKER.is_alive() if WORKER is not None else False,
            }


ESTADO = Estado()
WORKER = None          # el hilo de trabajo; se crea mas abajo, cuando existe la clase


def ficha_vacia(etiqueta, headline="", numero="", fecha="", creado=""):
    return {"id": secrets.token_hex(4), "etiqueta": etiqueta, "headline": headline,
            "numero": numero, "fecha": fecha, "creado": creado, "fila": None, "estado": "pendiente",
            "ENVIO": "", "NAME": "", "COMMENT": "", "RESTRICCIONES": "",
            "avisos": [], "alerta": "", "error": "", "aprobada": False, "segundos": 0, "normal": None,
            "para": ""}      # a quien mandarsela sola al terminar (viene del catalogador de la lista)


# ====================================================================== sesion de agencias
# Todo lo que toca el navegador (catalogar, comprobar sesion, abrir la ventana de login) lo hace el hilo del
# Worker: Playwright no admite que el navegador lo maneje mas de un hilo. Desde fuera solo se le encargan
# tareas con WORKER.encargar(...).
import queue


def comprobar_sesion():
    """Abre el navegador, entra en las agencias (Reuters, AP y EBU) y comprueba que la sesion sirve. Devuelve None si todo bien,
    o el texto del problema. Cierra el navegador al terminar. SOLO desde el hilo del Worker."""
    ex = Extractor(headless=CFG["headless"], oculto=CFG.get("navegador_oculto", True), canal=CFG.get("navegador", "auto"), ruta=CFG.get("navegador_ruta"),
                   miniaturas=0, espera_login=0, solo_texto=CFG.get("agencias_solo_texto", True),
                   reuters_xml=CFG.get("reuters_xml", True))
    try:
        ex.open()
        VISOR.conectar(ex._ctx, "comprobando la sesión")
        agencias = [("Reuters", BASE), ("AP", AP_BASE)]
        if CFG.get("ebu", True):
            agencias.append(("EBU", EBU_BASE))
        for agencia, url in agencias:
            ex._page.goto(url, wait_until="domcontentloaded")
            ex._page.wait_for_timeout(2500)
            ex._comprobar_acceso(agencia, url)
        return None
    except (NeedsLogin, AntiBot) as e:
        return str(e)
    except Exception as e:
        return f"No se pudo comprobar ({type(e).__name__}: {str(e).splitlines()[0][:120]})"
    finally:
        VISOR.desconectar()
        try:
            ex.close()
        except Exception:
            pass


def ventana_login(terminar, limite_minutos=10):
    """Abre Chrome con las agencias para iniciar sesion a mano y espera a que se cierre la ventana, a que
    terminar() devuelva True (boton del panel) o a que se agote el tope de tiempo. SOLO desde el hilo del Worker.

    IMPORTANTE: mientras esta ventana este abierta, el Worker esta parado aqui y la cola NO avanza. Por eso
    hay un tope: si nadie la cierra (o si el navegador se mata desde fuera y no nos enteramos), se sale sola."""
    oculto = bool(CFG.get("navegador_oculto", True)) and not CFG.get("headless")
    log("Login de agencias: abriendo Reuters, AP y EBU. " + ("Inicia sesion en la pestaña Navegador de la pagina y pulsa Ya he iniciado sesion."
        if oculto else "Inicia sesion en las pestañas y cierra la ventana (o pulsa el boton del panel)."))
    pendientes = ESTADO.resumen().get("pendientes", 0)
    if pendientes:
        log(f"Login de agencias: OJO, hay {pendientes} envio(s) en cola parados hasta que termine el login.")
    pw = ctx = None
    try:
        pw = sync_playwright().start()
        ctx, _ = abrir_contexto(pw, headless=False, canal=CFG.get("navegador", "auto"), ruta=CFG.get("navegador_ruta"),
                                oculto=oculto)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        VISOR.conectar(ctx, "inicio de sesión")
        # una agencia que no carga (caida, red lenta) no tumba el login de las demas: su pestaña se queda
        # abierta con el error y se puede recargar a mano
        for i, url in enumerate([BASE, AP_BASE] + ([EBU_BASE] if CFG.get("ebu", True) else [])):
            try:
                (page if i == 0 else ctx.new_page()).goto(url, timeout=45000)
            except Exception as e:
                log(f"Login de agencias: {url} no ha cargado ({str(e).splitlines()[0][:120]}); recargala en su pestaña")
        VISOR.conectar(ctx, "inicio de sesión")       # con las tres pestañas ya abiertas, se enseña la primera
        t0 = time.time()
        while not terminar():
            fin = time.time() + 1
            while time.time() < fin and not terminar():
                VISOR.bombear(100)                    # clics y teclas de la pestaña Navegador, e imagenes
            if not VISOR.activo:
                time.sleep(1)
            if limite_minutos and (time.time() - t0) > limite_minutos * 60:
                log(f"Login de agencias: {limite_minutos:g} minuto(s) sin terminar; cierro la ventana y sigo con la cola.")
                break
            try:
                paginas = ctx.pages
                if not paginas or all(p.is_closed() for p in paginas):   # ventana cerrada (o navegador muerto)
                    break
            except Exception:
                break
    except Exception as e:
        log(f"Login de agencias: fallo al abrir el navegador ({type(e).__name__}: {str(e).splitlines()[0][:160]})")
    finally:
        VISOR.desconectar()
        for cerrar in ((ctx.close if ctx else None), (pw.stop if pw else None)):
            try:
                if cerrar:
                    cerrar()
            except Exception:
                pass
        log("Login de agencias: ventana cerrada, sesion guardada en perfil_chromium.")


# ====================================================================== worker
class Worker(threading.Thread):
    """Un hilo que va sacando lotes pendientes y los procesa uno a uno."""

    def __init__(self):
        super().__init__(daemon=True)
        self.ex = None
        self.parar = False
        self.encargos = queue.Queue()       # tareas de navegador pedidas desde fuera: comprobar, login, cerrar
        self.login_activo = False
        self.login_terminar = False
        self.pausa_hasta = 0             # el modelo no atiende (limite de uso, caida): no se cogen lotes hasta entonces
        self.pausa_motivo = ""
        self.fallos_seguidos = 0         # redacciones fallidas una tras otra sin motivo reconocido
        self.aviso_sesion_mandado = False   # ya se ha avisado por correo de que Claude Code no tiene sesion

    # ---------------- encargos desde otros hilos
    def encargar(self, tipo, esperar=0):
        """Pide al Worker una tarea de navegador. Con esperar>0 bloquea hasta tener resultado (o hasta el tope)."""
        listo = threading.Event()
        caja = {"resultado": None}
        self.encargos.put((tipo, listo, caja))
        if esperar:
            listo.wait(esperar)
        return caja["resultado"] if listo.is_set() else "ocupado"

    def _atender_encargo(self, tipo, listo, caja):
        try:
            if tipo == "cerrar":
                self.cerrar_navegador()
                ESTADO.sesion_agencia = "sin abrir"
                caja["resultado"] = None
            elif tipo in ("comprobar", "comprobar_arranque"):
                self.cerrar_navegador()
                ESTADO.sesion_agencia = "comprobando"
                problema = comprobar_sesion()
                caja["resultado"] = problema
                if problema:
                    ESTADO.sesion_agencia = "caducada"
                    log(f"Sesion de agencias: {problema}")
                    if tipo == "comprobar_arranque" and CFG.get("login_al_arrancar", True):
                        self.encargos.put(("login", threading.Event(), {"resultado": None}))
                else:
                    ESTADO.sesion_agencia = "comprobada"
                    log("Sesion de agencias: correcta en Reuters, AP" + (" y EBU." if CFG.get("ebu", True) else "."))
            elif tipo == "login":
                self.cerrar_navegador()
                self.login_activo, self.login_terminar = True, False
                ESTADO.sesion_agencia = "login en curso"
                try:
                    ventana_login(lambda: self.login_terminar,
                                  float(CFG.get("login_espera_minutos", 10) or 0))
                finally:
                    self.login_activo = False
                    ESTADO.sesion_agencia = "sin abrir"
                caja["resultado"] = None
        except Exception as e:
            caja["resultado"] = f"{type(e).__name__}: {str(e).splitlines()[0][:160]}"
            log(f"Encargo {tipo}: {caja['resultado']}")
        finally:
            listo.set()

    # ---------------- navegador (solo para numeros de envio o fotogramas)
    def abrir_navegador(self):
        if self.ex is not None:
            return self.ex
        # fotogramas: los que se mandan al modelo (miniaturas) o, si son mas, los que se sacan solo para
        # reconocer la escena y, al aprobar la ficha, aprender de ella (escenas_fotogramas)
        para_escenas = int(CFG.get("escenas_fotogramas", 0) or 0) if CFG.get("escenas", True) else 0
        fotogramas = max(int(CFG.get("miniaturas", 0) or 0), para_escenas)
        if fotogramas and CFG.get("escenas", True):
            try:
                import escenas as _escenas
                if _escenas.hay_modelo():
                    _escenas.precargar()
            except ImportError as e:              # falta numpy/onnxruntime: se sigue sin clasificador
                log(f"Clasificador de escenas no disponible ({e.name}): pip install numpy onnxruntime pillow")
        self.ex = Extractor(headless=CFG["headless"], oculto=CFG.get("navegador_oculto", True), canal=CFG.get("navegador", "auto"),
                            ruta=CFG.get("navegador_ruta"), miniaturas=fotogramas, espera_login=0,
                            solo_texto=CFG.get("agencias_solo_texto", True), reuters_xml=CFG.get("reuters_xml", True))
        self.ex.open()
        VISOR.conectar(self.ex._ctx, "trabajo")
        ESTADO.sesion_agencia = "abierta"
        return self.ex

    def cerrar_navegador(self):
        if self.ex is not None:
            VISOR.desconectar()
            try:
                self.ex.close()
            except Exception:
                pass
            self.ex = None

    def sesion_caducada(self, motivo):
        ESTADO.sesion_agencia = "caducada"
        log(f"Sesion de agencia caducada o antibot: {motivo}")
        self.cerrar_navegador()

    # ---------------- bucle
    def run(self):
        log("Worker en marcha.")
        while not self.parar:
            try:
                self._vuelta()
            except Exception:
                # ultima red: si algo se escapa de la vuelta (incluido el propio manejo de errores),
                # se anota y se sigue; un worker muerto con el servidor vivo es lo peor que puede pasar
                log("Fallo inesperado del worker, se sigue:\n" + traceback.format_exc()[-1500:])
                time.sleep(5)

    def _vuelta(self):
        if VISOR.activo:
            VISOR.bombear(250)                       # el navegador de trabajo abierto y parado: se puede tocar
        try:
            # con el navegador a la vista, la espera la hace bombear (que atiende los clics al momento)
            encargo = self.encargos.get(timeout=0.01 if VISOR.activo else 2)   # primero los encargos de navegador
        except queue.Empty:
            encargo = None
        if encargo is not None:
            self._atender_encargo(*encargo)
            return
        if self.pausa_hasta and time.time() < self.pausa_hasta:
            return                                   # el modelo no atiende: se espera sin tocar la cola
        if self.pausa_hasta:
            self.pausa_hasta, self.pausa_motivo = 0, ""
            ESTADO.modelo_parado = ""
            log("Se vuelve a intentar con el modelo.")
        lote = ESTADO.siguiente_pendiente()
        if lote is None:
            return
        try:
            self.procesar_lote(lote)
        except Exception:
            log("Fallo general en el lote:\n" + traceback.format_exc()[-1500:])
            with ESTADO.lock:
                lote["estado"] = "error"
                lote["mensaje"] = "Fallo general del worker; mira la consola."
                for f in lote["fichas"]:                       # asi "Volver a intentarlo" las recoge
                    if f["estado"] in ("en curso", "pendiente"):
                        f["estado"], f["error"] = "error", "No llego a hacerse: fallo general del lote"
                ESTADO.trabajando = None
                ESTADO.guardar()

    def procesar_lote(self, lote):
        with ESTADO.lock:
            lote["estado"] = "en curso"          # siguiente_pendiente ya lo deja asi; se guarda
            ESTADO.guardar()
        log(f"Lote {lote['id']} ({lote['tipo']}): {len(lote['fichas'])} ficha(s)")
        self.procesar_numeros(lote)
        if self.pausa_hasta:
            # el modelo ha dejado de atender a mitad: el lote vuelve a la cola tal cual (lo hecho, hecho;
            # lo que queda, pendiente) y se retoma cuando pase la pausa, sin mandar ningun correo
            with ESTADO.lock:
                lote["estado"] = "pendiente"
                ESTADO.trabajando = None
                ESTADO.guardar()
            return
        with ESTADO.lock:
            if lote["estado"] == "en curso":
                lote["estado"] = "hecho"
            ESTADO.trabajando = None
            ESTADO.guardar()
        if lote.get("parado"):
            # parado a mano desde la pagina: ni respuesta al buzon ni reparto automatico,
            # que lo que haya salido se manda cuando se quiera con "Volver a repartir"
            log(f"Lote {lote['id']} parado a mano: {sum(1 for f in lote['fichas'] if f['estado'] == 'hecha')} ficha(s) hechas.")
            return
        log(f"Lote {lote['id']} terminado.")
        # Los correos de despues del lote: un fallo aqui no es un fallo del lote (las fichas estan hechas)
        try:
            if lote.get("responder_a"):
                responder_peticion(lote)
            if any(f.get("para") for f in lote["fichas"]):
                enviar_por_catalogador(lote)
            if (lote.get("reparto") or "").strip():
                if any(e.get("origen") == "reparto" and e.get("ok") for e in lote.get("envios") or []):
                    # ya se repartio una vez (esto es un reintento): no se manda todo otra vez;
                    # lo nuevo se manda a mano con "Volver a repartir" o "Enviar seleccion"
                    log("  reparto ya hecho antes: no se repite (usa Volver a repartir si hace falta)")
                else:
                    enviar_correos(lote)
        except Exception:
            log("Fallo al mandar los correos del lote:\n" + traceback.format_exc()[-1200:])

    def volcar_hecha(self, lote, ficha):
        """Si ese envio ya se catalogo en otro lote, se copia la ficha tal cual en vez de volver a
        entrar en la agencia y gastar una llamada al modelo. Devuelve True si se ha copiado."""
        origen, hecha = ESTADO.ya_hecha(ficha["numero"], ficha.get("fecha") or "", salvo_lote=lote["id"],
                                        creado=ficha.get("creado") or "")
        if not hecha:
            return False
        with ESTADO.lock:
            for c in CAMPOS:
                ficha[c] = hecha.get(c, "")
            ficha["normal"] = hecha.get("normal")
            ficha["avisos"] = list(hecha.get("avisos") or [])
            ficha["headline"] = hecha.get("headline", "")
            ficha["fecha"] = hecha.get("fecha", "") or ficha.get("fecha", "")
            ficha["aprobada"] = bool(hecha.get("aprobada"))
            ficha["alerta"] = hecha.get("alerta", "")
            ficha["script_paginas"] = hecha.get("script_paginas")
            ficha["fotogramas"] = hecha.get("fotogramas") or []
            ficha["borrador"] = hecha.get("borrador")
            ficha["estado"] = "hecha"
            ficha["segundos"] = 0
            ficha["copiada"] = origen.get("creado", "")          # de cuando es la que se copia
            ESTADO.guardar()
        log(f"  {ficha['etiqueta']}: ya estaba hecha ({origen.get('creado', '')}), se vuelca sin volver a redactar")
        return True

    def redactar_y_guardar(self, lote, ficha, datos, t0):
        try:
            campos, avisos, _ = redactar(datos, model=CFG["claude_model"], extra_args=CFG["claude_extra_args"],
                                         timeout=CFG["claude_timeout"], acortar=CFG.get("acortar_comment", True))
        except ModeloNoDisponible as e:
            self.pausar_modelo(ficha, e)
            return None, None
        except RedactorError as e:
            self.fallos_seguidos += 1
            if self.fallos_seguidos >= FALLOS_SEGUIDOS_PAUSA:
                # tres seguidas sin motivo reconocido: algo pasa con el modelo, no con las fichas
                self.pausar_modelo(ficha, ModeloNoDisponible(
                    f"{self.fallos_seguidos} redacciones seguidas han fallado ({str(e).splitlines()[0][:160]})", 5 * 60))
                self.fallos_seguidos = 0
                return None, None
            self.marcar_error(ficha, "Redaccion: " + str(e).splitlines()[0][:300])
            return None, None
        except Exception as e:
            self.marcar_error(ficha, f"{type(e).__name__}: {str(e).splitlines()[0][:300]}")
            return None, None
        self.fallos_seguidos = 0
        self.aviso_sesion_mandado = False
        avisos = list(avisos) + list(datos.get("avisos", []))
        with ESTADO.lock:
            for c in CAMPOS:
                ficha[c] = campos.get(c, "")
            if campos.get("NORMAL"):
                ficha["normal"] = campos["NORMAL"]        # viene de la misma llamada al modelo
            ficha["avisos"] = avisos
            ficha["fotogramas"] = huella_fotogramas(datos.get("miniaturas"))   # para entrenar al aprobarla
            ficha["borrador"] = {c: campos.get(c, "") for c in CAMPOS}         # lo del modelo, para aprender al aprobarla
            ficha["estado"] = "hecha"
            ficha["segundos"] = round(time.time() - t0)
            ESTADO.guardar()
        try:
            guardar_csv(campos, avisos)
        except OSError as e:                     # fichas.csv abierto en Excel, disco lleno...: la ficha ya esta
            log(f"  {ficha['etiqueta']}: no se ha podido anotar en fichas.csv ({type(e).__name__}); la ficha esta en la pagina")
        log(f"  {ficha['etiqueta']}: hecha en {ficha['segundos']} s")
        if quiere_normal() and not ficha.get("normal"):           # no vino en la generacion: segunda pasada
            t1 = time.time()
            if self.normalizar_ficha(ficha):
                log(f"  {ficha['etiqueta']}: version normal en {time.time() - t1:.0f} s")
        return campos, avisos

    def normalizar_ficha(self, ficha):
        """Version en escritura normal de NAME, COMMENT y RESTRICCIONES (segunda llamada al modelo)."""
        try:
            normal, avisos = presentar_normal({c: ficha.get(c, "") for c in ("NAME", "COMMENT", "RESTRICCIONES")})
        except RedactorError as e:
            log(f"  {ficha['etiqueta']}: no se pudo normalizar ({str(e).splitlines()[0][:120]})")
            return False
        with ESTADO.lock:
            ficha["normal"] = normal
            if avisos:
                ficha["avisos"] = list(ficha.get("avisos", [])) + avisos
            ESTADO.guardar()
        return True

    def asegurar_normal(self, fichas):
        """Antes de mandar en minusculas: la version normal de las fichas que no la tienen. Con el modelo; si
        no responde, sin el (las palabras en minuscula, siglas y comienzos de frase bien; los nombres propios
        tambien en minuscula)."""
        for f in fichas:
            if f.get("normal"):
                continue
            if not self.normalizar_ficha(f):
                normal, _ = version_normal(f, {})
                with ESTADO.lock:
                    f["normal"] = normal
                    ESTADO.guardar()
                log(f"  {f.get('etiqueta', '')}: version normal hecha sin el modelo (revisa nombres propios)")

    def marcar_error(self, ficha, mensaje):
        with ESTADO.lock:
            ficha["estado"] = "error"
            ficha["error"] = mensaje
            ESTADO.guardar()
        log(f"  {ficha['etiqueta']}: ERROR {mensaje}")

    # ---------------- lote de numeros de envio
    def _atender_pendientes(self):
        while True:
            try:
                encargo = self.encargos.get_nowait()
            except queue.Empty:
                return
            self._atender_encargo(*encargo)

    def avisar_sin_sesion(self, motivo):
        """Un correo a tu direccion (correo_copia) la primera vez que Claude Code pierde la sesion: trabajando
        en remoto, si no, nadie se entera hasta que faltan las fichas."""
        if self.aviso_sesion_mandado or not (CFG.get("correo_copia") and CFG.get("correo_usuario") and CFG.get("correo_clave")):
            return
        self.aviso_sesion_mandado = True
        texto = ("Catalogator no puede redactar: Claude Code ha perdido la sesion.\n\n"
                 f"Motivo: {motivo[:300]}\n\n"
                 "La cola esta en pausa y se vuelve a probar sola cada 10 minutos. Para arreglarlo:\n"
                 "1. En el equipo de Catalogator, en una ventana de comandos: claude setup-token\n"
                 "2. Entra con tu cuenta y copia el token que te da.\n"
                 "3. Pagina de Catalogator → Admin → Ajustes → Redaccion → Token de Claude Code: pegalo y Guardar.\n"
                 "Con eso la sesion dura un año. (O pon una clave de la API para que redacte con ella mientras.)\n")
        try:
            enviar(CFG, CFG["correo_copia"], "Catalogator: Claude Code sin sesion", texto)
            log(f"Aviso de sesion caducada mandado a {CFG['correo_copia']}")
        except Exception as ex:
            log(f"No se ha podido mandar el aviso de sesion caducada ({type(ex).__name__})")

    def pausar_modelo(self, ficha, e):
        """El modelo no atiende (limite de uso, caida, sesion de Claude Code caducada): la ficha vuelve a
        pendiente y el worker deja la cola quieta hasta que pase la espera, o hasta que alguien lo arregle."""
        with ESTADO.lock:
            ficha["estado"], ficha["error"] = "pendiente", ""
            ESTADO.guardar()
        if e.espera is None:
            # sin sesion de Claude Code: se vuelve a probar sola cada 10 minutos (y al guardar un token en Ajustes)
            self.pausa_hasta = time.time() + 10 * 60
            cuando = ("hasta que vuelva la sesion de Claude Code: se prueba sola cada 10 minutos. Para que no vuelva a "
                      "pasar, pon un token en Admin → Ajustes → Redaccion (claude setup-token)")
            self.avisar_sin_sesion(str(e))
        else:
            self.pausa_hasta = time.time() + e.espera
            cuando = f"hasta las {datetime.fromtimestamp(self.pausa_hasta):%H:%M}"
        self.pausa_motivo = str(e).splitlines()[0][:300]
        ESTADO.modelo_parado = f"{self.pausa_motivo} · en pausa {cuando}"
        log(f"MODELO NO DISPONIBLE: {self.pausa_motivo}\n  La cola se queda en pausa {cuando}.")

    def procesar_numeros(self, lote):
        for ficha in lote["fichas"]:
            if self.parar or self.pausa_hasta:
                return
            if ficha["estado"] != "pendiente":
                continue
            self._atender_pendientes()
            if not lote.get("rehacer") and self.volcar_hecha(lote, ficha):
                continue
            if ESTADO.sesion_agencia == "caducada":
                self.marcar_error(ficha, "Sesion de agencia caducada: pulsa Iniciar sesion agencias en el panel (o ejecuta login.py en casa)")
                continue
            with ESTADO.lock:
                ficha["estado"] = "en curso"
                ESTADO.trabajando = f"{lote['nombre']}: {ficha['etiqueta']}"
                ESTADO.guardar()
            t0 = time.time()
            try:
                datos = self.abrir_navegador().fetch(ficha["numero"], ficha["fecha"] or None,
                                                     creado=ficha.get("creado") or None)
            except (NeedsLogin, AntiBot) as e:
                self.sesion_caducada(e)
                self.marcar_error(ficha, f"{e}. Pulsa Iniciar sesion agencias en el panel (o ejecuta login.py en casa).")
                continue
            except NotFound as e:
                self.marcar_error(ficha, str(e))
                continue
            except ValueError as e:                # numero o fecha mal escritos: no es cosa del navegador
                self.marcar_error(ficha, str(e))
                continue
            except Exception as e:
                self.marcar_error(ficha, f"Extraccion: {type(e).__name__}: {str(e).splitlines()[0][:200]}")
                self.cerrar_navegador()
                continue
            with ESTADO.lock:
                ficha["headline"] = datos.get("headline", "")
                ficha["fecha"] = datos.get("fecha") or ficha.get("fecha", "")   # la real: con ella se reconoce despues
                ficha["alerta"] = datos.get("alerta", "")
                ficha["script_paginas"] = datos.get("script_paginas")
            if datos.get("alerta"):
                log(f"  {ficha['etiqueta']}: {datos['alerta']}")
            self.redactar_y_guardar(lote, ficha, datos, t0)


def enviar_correos(lote, reparto=None, formal=None):
    """Reparte y manda por correo las fichas hechas del lote. Guarda el resultado en lote['envios']."""
    texto = reparto if reparto is not None else lote.get("reparto", "")
    formal = lote.get("formal", False) if formal is None else formal
    sin_repartir = []
    try:
        envios, sin_repartir = enviar_lote(CFG, lote, texto, formal)
    except CorreoError as e:
        envios = [{"a": "", "etiqueta": "reparto", "fichas": [], "ok": False, "error": str(e)}]
    for e in envios:
        e["origen"] = "reparto"
    with ESTADO.lock:
        # se sustituyen solo los envios del reparto anterior: los de la lista (por catalogador) y la
        # respuesta al buzon se conservan
        lote["envios"] = [e for e in (lote.get("envios") or []) if e.get("origen") != "reparto"] + envios
        lote["sin_repartir"] = sin_repartir          # no se manda a nadie: se ve en la pagina y aqui
        lote["enviado"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        ESTADO.guardar()
    for e in envios:
        log(f"  correo a {e['etiqueta'] or e['a']}: " + ("enviado" if e["ok"] else "FALLO " + e["error"]))
        admin.anotar_correo("enviado", e["a"], e.get("etiqueta", ""), len(e.get("fichas") or []), lote, "reparto",
                            ok=e["ok"], error=e.get("error", ""), formal=formal)
    if sin_repartir:
        log(f"  sin repartir ({len(sin_repartir)}), quedan en la pagina: " + ", ".join(sin_repartir))
    return envios


def _direccion_de(destino):
    """La direccion a la que resuelve un destino (nombre de la agenda, 'yo' o direccion), o ''."""
    r = resolver_destino(destino, CFG.get("documentalistas") or {}, CFG.get("correo_copia") or "")
    return (r[1] if r else "").lower()


def _ya_enviada(ficha, destino):
    """True si esa ficha ya se le mando a ese destino (por direccion; los apuntes antiguos, por etiqueta)."""
    direccion = _direccion_de(destino)
    for e in ficha.get("enviada") or []:
        if e.get("direccion"):
            if e["direccion"].lower() == direccion:
                return True
        elif _norm(e.get("a", "")) == _norm(destino):
            return True
    return False


def _anotar_enviada(fichas, etiqueta, direccion):
    cuando = datetime.now().strftime("%d/%m/%Y %H:%M")
    with ESTADO.lock:
        for f in fichas:
            f.setdefault("enviada", []).append({"a": etiqueta, "direccion": direccion, "cuando": cuando})


def enviar_por_catalogador(lote):
    """Las fichas que la lista traia asignadas a alguien (columna Catalogador -> "catalogadores" de
    config.json) se le mandan solas al terminar el lote, cada persona las suyas. Las que son para
    "yo" no se mandan: se quedan en la pagina, que es lo acordado."""
    grupos = {}
    for f in lote["fichas"]:
        if f["estado"] == "hecha" and f.get("para") and not _ya_enviada(f, f["para"]):
            grupos.setdefault(f["para"], []).append(f)      # (al reintentar un lote no se repite lo mandado)
    envios = []
    for destino, fichas in grupos.items():
        if es_mio(CFG, destino):          # "yo", tu nombre de la agenda o tu direccion: no se manda
            log(f"  {len(fichas)} ficha(s) a tu nombre: se quedan en la pagina, no se mandan")
            continue
        try:
            etiqueta, direccion, formal = enviar_seleccion(CFG, fichas, destino, lote.get("nombre", ""),
                                                           formal=lote.get("formal", False))
            envios.append({"a": direccion, "etiqueta": etiqueta, "fichas": [f["etiqueta"] for f in fichas],
                           "ok": True, "error": "", "formal": formal, "origen": "lista"})
            log(f"  correo a {etiqueta} ({direccion}): {len(fichas)} ficha(s) de su lista" + (" · formal" if formal else ""))
            admin.anotar_correo("enviado", direccion, etiqueta, len(fichas), lote, "lista", formal=formal)
            _anotar_enviada(fichas, etiqueta, direccion)
        except CorreoError as e:
            envios.append({"a": destino, "etiqueta": destino, "fichas": [], "ok": False, "error": str(e),
                           "formal": False, "origen": "lista"})
            log(f"  correo a {destino}: FALLO {e}")
            admin.anotar_correo("enviado", destino, destino, len(fichas), lote, "lista", ok=False, error=str(e))
    with ESTADO.lock:
        lote["envios"] = (lote.get("envios") or []) + envios
        lote["enviado"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        ESTADO.guardar()


def _de_otro(ficha, destino):
    """True si la ficha tiene dueño (columna Catalogador de la lista) y no es quien pidio el lote."""
    para = ficha.get("para") or ""
    return bool(para) and _direccion_de(para) != _direccion_de(destino)


def responder_peticion(lote):
    """Devuelve al remitente las fichas del lote que pidio por correo, en el orden en que las mando.
    Las que la lista asigna a otra persona (columna Catalogador) no van en la respuesta: se las manda
    enviar_por_catalogador a su dueño. El remitente recibe las suyas y las que no tienen dueño.
    Tono segun es_formal: formal a quien no esta en la agenda, informal a la agenda (salvo correo_formal)."""
    destino = lote["responder_a"]
    # en un reintento solo van las fichas que aun no se le han mandado: las de la primera respuesta ya las tiene
    hechas = [f for f in lote["fichas"] if f["estado"] == "hecha" and not _ya_enviada(f, destino) and not _de_otro(f, destino)]
    fallidas = [f for f in lote["fichas"] if f["estado"] == "error" and not _de_otro(f, destino)]
    ajenas = [f for f in lote["fichas"] if _de_otro(f, destino)]
    if not hechas and not fallidas:
        log(f"  respuesta a {destino}: nada nuevo que mandar" + (f" ({len(ajenas)} ficha(s) de otros catalogadores van a sus dueños)" if ajenas else ""))
        return
    peticion = {"asunto": lote.get("asunto_origen", "")}
    nombre = nombre_saludo(CFG, "", destino)
    formal = es_formal(CFG, nombre, destino)
    # los AVISOS y el motivo tecnico de los fallos solo van si quien pidio eres tu, y nunca en formal
    detalle = es_para_mi(CFG, destino) and not formal
    lineas = []
    if lote.get("nota"):
        lineas.append(lote["nota"])
    if ajenas:
        mio = nombre_saludo(CFG, "", CFG.get("correo_copia") or "") or "el archivo"
        duenos = sorted({mio if es_mio(CFG, f.get("para") or "") else (f.get("para") or "").strip() for f in ajenas})
        lineas.append(f"{len(ajenas)} ficha(s) de la lista son de otros catalogadores ({', '.join(duenos)}) "
                      "y les llegan a ellos.")
    if fallidas:
        if detalle:
            lineas.append("No han salido: " + ", ".join(f"{f['etiqueta']} ({f.get('error','')[:70]})" for f in fallidas))
        else:
            lineas.append("No han salido: " + ", ".join(f["etiqueta"] for f in fallidas) + ".")
    if formal:
        cabecera = cabecera_fichas(len(hechas), False, formal=True)
    else:
        s = "" if len(hechas) == 1 else "s"
        cabecera = f"{len(hechas)} ficha{s} lista{s} para pegar." + (" Si alguna trae AVISOS, revisala con mas cuidado." if detalle else "")
    asunto = asunto_respuesta(peticion, hechas, fallidas, formal)
    envio = {"a": destino, "etiqueta": "respuesta", "fichas": [f["etiqueta"] for f in hechas],
             "ok": False, "error": "", "formal": formal, "origen": "respuesta"}
    try:
        if hechas:
            enviar_fichas(CFG, hechas, nombre, destino, asunto, forzar_formal=formal, cabecera=cabecera, lineas_extra=lineas)
        else:
            enviar(CFG, destino, asunto, "No ha salido ninguna ficha.\n" + "\n".join(lineas) + "\n")
        envio["ok"] = True
        _anotar_enviada(hechas, "respuesta", destino)
        log(f"  respuesta a {destino}: {len(hechas)} ficha(s)" + (" · formal" if formal else ""))
        admin.anotar_correo("enviado", destino, "respuesta", len(hechas), lote, "respuesta", formal=formal)
    except CorreoError as e:
        envio.update({"fichas": [], "error": str(e)})
        log(f"  respuesta a {destino}: FALLO {e}")
        admin.anotar_correo("enviado", destino, "respuesta", len(hechas), lote, "respuesta", ok=False, error=str(e))
    with ESTADO.lock:
        lote["envios"] = (lote.get("envios") or []) + [envio]
        ESTADO.guardar()


def avisar_sin_envios(peticion, nota):
    """Contesta a un correo cuya lista no ha dado ningun numero. Sin esto el remitente se queda
    esperando una respuesta que no va a llegar, sin saber que su fichero no servia."""
    nombre = nombre_saludo(CFG, "", peticion["de"])
    formal = es_formal(CFG, nombre, peticion["de"])
    saludo, despedida = adorno_para(CFG, nombre, formal)
    if formal:
        explicacion = "No se ha encontrado ningun numero de envio en el correo, por lo que no se ha catalogado nada."
        como = ("Los numeros pueden ir escritos en el propio correo o en una lista de MediaCentral adjunta (.csv o .xlsx), "
                "al principio de la columna Name: Reuters, 4 cifras; AP, 7; EBU, tipo 2026_10420363.")
    else:
        explicacion = "De ese correo no ha salido ningun numero de envio, asi que no he catalogado nada."
        como = ("La lista tiene que llevar el numero al principio de la columna Name (Reuters de 4 cifras, AP de 7 "
                "o EBU tipo 2026_10420363). Tambien vale escribir los numeros sueltos en el propio correo.")
    cuerpo = "\n\n".join(p for p in (saludo, explicacion, nota, como, despedida) if p) + "\n"
    html = cuerpo_aviso_html([explicacion, nota, como], saludo, despedida, formal) if CFG.get("correo_html", True) else None
    try:
        enviar(CFG, peticion["de"], f"{MARCA_CORREO} sin envios que catalogar", cuerpo, html=html)
        log(f"Buzon: aviso a {peticion['de']} (la lista no traia numeros)" + (" · formal" if formal else ""))
        admin.anotar_correo("recibido", peticion["de"], "", 0, None, "sin envios", asunto=peticion.get("asunto", ""),
                            detalle="no traia numeros: se le ha avisado")
    except CorreoError as e:
        log(f"Buzon: no se pudo avisar a {peticion['de']} ({e})")
        admin.anotar_correo("recibido", peticion["de"], "", 0, None, "sin envios", asunto=peticion.get("asunto", ""),
                            ok=False, error=f"no traia numeros y no se le ha podido avisar: {e}")


class Buzon(threading.Thread):
    """Mira la bandeja de la cuenta de catalogar cada X minutos y encola lo que llega por correo."""

    def __init__(self):
        super().__init__(daemon=True)
        self.parar = False
        self.ultimo = ""

    def run(self):
        minutos = float(CFG.get("correo_buzon_minutos") or 0)
        if minutos <= 0:
            # antes se salia en silencio y no habia forma de saber por que no llegaba nada
            log("Buzon: apagado. Para encenderlo, pon en config.json \"correo_buzon_minutos\": 5 y reinicia.")
            return
        if not (CFG.get("correo_usuario") and CFG.get("correo_clave")):
            log("Buzon: apagado. Faltan correo_usuario o correo_clave en config.json.")
            return
        log(f"Buzon: mirando el correo cada {minutos:g} minuto(s).")
        while not self.parar:
            try:
                peticiones, rechazados, errores = leer_buzon(CFG)
                for e in errores:
                    log(f"Buzon: correo que no se ha podido leer, {e} (se deja como leido)")
                for p in peticiones:
                    nota = nota_adjuntos(p)
                    if not p["numeros"]:
                        # el correo traia lista pero no ha salido ningun numero de ella: se contesta
                        # diciendo por que y no se encola nada, para no dejarlo esperando una respuesta
                        avisar_sin_envios(p, nota)
                        continue
                    # el titulo que trae la lista se guarda solo para reconocer la ficha en el panel:
                    # la fuente para redactar sigue siendo la ficha de la web de la agencia
                    titulos, destinos = {}, {}
                    creados = {}
                    for a in p["adjuntos"]:
                        for e in a["envios"]:
                            titulos.setdefault(e["numero"], e["etiqueta"])
                            if e.get("para"):
                                destinos.setdefault(e["numero"], e["para"])
                            if e.get("creado"):
                                creados.setdefault(e["numero"], e["creado"])
                    fichas = []
                    for n in p["numeros"]:
                        # la fecha escrita junto al numero manda; si no, la comun del correo si la hay
                        f = ficha_vacia(n, titulos.get(n, ""), n, p["fechas"].get(n) or p["fecha"], creados.get(n, ""))
                        f["para"] = destinos.get(n, "")
                        fichas.append(f)
                    nombre = f"correo de {p['nombre']}: " + ", ".join(p["numeros"][:3]) + (" ..." if len(p["numeros"]) > 3 else "")
                    lotes = crear_lotes("correo", nombre, fichas, responder_a=p["de"], asunto_origen=p["asunto"], nota=nota)
                    admin.anotar_correo("recibido", p["de"], p.get("nombre", ""), len(fichas), lotes[0], "peticion",
                                        asunto=p["asunto"], detalle=nota)
                    log(f"Buzon: {len(fichas)} envio(s) de {p['de']}" + (f" en {len(lotes)} lotes" if len(lotes) > 1 else "")
                        + (" (correo con mas de 2000 numeros: recortado)" if p["de_mas"] else "")
                        + (f" · {nota}" if nota else ""))
                if rechazados:
                    nuevo = ", ".join(sorted(set(rechazados)))
                    if nuevo != self.ultimo:
                        log(f"Buzon: correos ignorados de {nuevo} (remitente no permitido; se quedan sin leer)")
                        admin.anotar_correo("ignorado", nuevo, "", 0, None, "remitente no permitido")
                        self.ultimo = nuevo
            except BuzonError as e:
                log(f"Buzon: {e}")
            except Exception as e:
                log(f"Buzon: fallo inesperado ({type(e).__name__}: {str(e)[:120]})")
            for _ in range(int(minutos * 60)):
                if self.parar:
                    return
                time.sleep(1)


BUZON = Buzon()
WORKER = Worker()
import correo as _correo
_correo.ASEGURAR_NORMAL = WORKER.asegurar_normal     # antes de mandar en minusculas, la version normal con el modelo


def quiere_normal():
    """Si alguien ve o recibe las fichas en escritura normal (la pagina, los correos o una persona)."""
    return ("normalizado" in (CFG.get("presentacion", "mayusculas"), CFG.get("correo_presentacion", "mayusculas"))
            or hay_excepcion_normalizada(CFG))


# ====================================================================== autenticacion
SESIONES = set()
FALLOS = {}            # por direccion del cliente: {"n": intentos seguidos, "hasta": bloqueado hasta}


def _cliente(request):
    """Direccion de quien llama. Funnel entra por 127.0.0.1 y pone la real en X-Forwarded-For."""
    reenviada = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return reenviada or (request.client.host if request.client else "?")


async def _json(request):
    """El cuerpo JSON de la peticion, o 400 si no lo es (en vez de un 500 con traza en la consola)."""
    try:
        datos = await request.json()
    except Exception:
        raise HTTPException(400, "El cuerpo de la peticion no es JSON")
    if not isinstance(datos, dict):
        raise HTTPException(400, "El cuerpo de la peticion tiene que ser un objeto JSON")
    return datos
COOKIE = "catalogar_sesion"


def autenticado(request):
    return request.cookies.get(COOKIE) in SESIONES


@app.middleware("http")
async def exigir_clave(request: Request, call_next):
    ruta = request.url.path
    if ruta.startswith("/api/") and not autenticado(request):
        return JSONResponse({"error": "sin sesion"}, status_code=401)
    return await call_next(request)


@app.post("/login")
async def login(request: Request):
    quien = _cliente(request)
    fallos = FALLOS.setdefault(quien, {"n": 0, "hasta": 0.0})
    if time.time() < fallos["hasta"]:
        raise HTTPException(429, "Demasiados intentos; espera un momento")
    datos = await _json(request)
    clave = (datos.get("clave") or "").strip()
    # se compara en bytes: compare_digest no admite cadenas con ñ o tildes
    if not clave or not secrets.compare_digest(clave.encode("utf-8"), str(CFG["servidor_clave"]).encode("utf-8")):
        fallos["n"] += 1
        fallos["hasta"] = time.time() + min(2 ** fallos["n"], 60)
        if len(FALLOS) > 500:                                  # que no crezca sin fin con bots
            FALLOS.clear()
        log(f"Clave incorrecta ({quien})")
        raise HTTPException(401, "Clave incorrecta")
    FALLOS.pop(quien, None)
    token = secrets.token_urlsafe(32)
    SESIONES.add(token)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=request.url.scheme == "https",
                    max_age=60 * 60 * 24 * 30)
    return resp


@app.post("/logout")
async def logout(request: Request):
    SESIONES.discard(request.cookies.get(COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


# ====================================================================== pagina y API
@app.get("/", response_class=HTMLResponse)
def pagina():
    if not PANEL_PATH.exists():
        return HTMLResponse("<p>Falta panel/index.html</p>", status_code=500)
    # la huella va dentro de la pagina: si el servidor se actualiza, la pagina abierta lo ve y se recarga
    html = PANEL_PATH.read_text(encoding="utf-8").replace("__HUELLA_PANEL__", huella_panel())
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


_HUELLA = {"marca": None, "huella": ""}


def huella_panel():
    """Huella de panel/index.html (cambia con cada version que lo toca); se recalcula solo si cambia el fichero."""
    try:
        marca = PANEL_PATH.stat().st_mtime_ns
    except OSError:
        return ""
    if _HUELLA["marca"] != marca:
        import hashlib
        _HUELLA.update(marca=marca, huella=hashlib.sha1(PANEL_PATH.read_bytes()).hexdigest()[:12])
    return _HUELLA["huella"]


@app.get("/api/estado")
def estado():
    return ESTADO.resumen()


@app.get("/api/lotes")
def lotes():
    # se convierte a JSON dentro del cerrojo: fuera, el worker puede tocar una ficha mientras se
    # recorre y eso da un 500 intermitente ("dictionary changed size during iteration")
    with ESTADO.lock:
        cuerpo = json.dumps({"lotes": ESTADO.lotes[:MAX_LOTES_EN_PANEL], **ESTADO.resumen(), "panel": huella_panel()},
                            ensure_ascii=False)
    return Response(cuerpo, media_type="application/json")


def crear_lotes(tipo, nombre, fichas, **extra):
    """Crea el lote, o varios si pasa de lote_max_envios: lo que sobra se trocea en lotes seguidos
    (nombre (1/4), (2/4)...), nunca se descarta. Se crean en orden, asi que el 1 se hace primero y, si
    viene del buzon, cada trozo contesta en cuanto termina. Devuelve la lista de lotes."""
    tam = max(1, int(CFG.get("lote_max_envios") or 90))
    trozos = [fichas[i:i + tam] for i in range(0, len(fichas), tam)] or [[]]
    if len(trozos) == 1:
        return [ESTADO.nuevo_lote(tipo, nombre, fichas, **extra)]
    lotes = []
    for i, trozo in enumerate(trozos, 1):
        marca = f" ({i}/{len(trozos)})"
        datos = dict(extra)
        if datos.get("asunto_origen"):
            datos["asunto_origen"] = datos["asunto_origen"] + marca
        nota = datos.get("nota") or ""
        datos["nota"] = (nota + " · " if (nota and i == 1) else "") + \
            (f"Troceado en {len(trozos)} lotes de hasta {tam} ({len(fichas)} envios)" if i == 1 else f"Trozo {i} de {len(trozos)}")
        lotes.append(ESTADO.nuevo_lote(tipo, nombre + marca, trozo, **datos))
    log(f"{nombre}: {len(fichas)} envios, troceados en {len(trozos)} lotes de hasta {tam}")
    return lotes


def _lote_de(lotes):
    """{numero: id del lote} para quien tenga que seguir las fichas (el relleno de MediaCentral)."""
    return {f["numero"]: l["id"] for l in lotes for f in l["fichas"]}


def _ficha_para_fuera(f, lote):
    """Lo que necesita un programa de fuera (el relleno de MediaCentral) de una ficha."""
    claves = ("numero", "fecha", "creado", "estado", "error", "ENVIO", "NAME", "COMMENT", "RESTRICCIONES",
              "normal", "avisos", "alerta", "headline")
    return dict({k: f.get(k) for k in claves}, lote=lote["id"])


@app.get("/api/ficha")
def buscar_ficha(numero: str, fecha: str = "", lote: str = ""):
    """La ficha de un numero de envio, para el relleno de MediaCentral.
    Con lote: la de ese lote, este como este (para seguir una que se acaba de encargar).
    Sin lote: la hecha que vale para ese numero (misma regla que "ya hecha": un Reuters sin fecha solo si
    es de hoy); si no la hay, la que este en cola o en curso; si tampoco, {"estado": "no"}."""
    numero = numero.strip().upper()
    with ESTADO.lock:
        if lote:
            l = ESTADO.lote(lote)
            f = next((x for x in (l or {}).get("fichas", []) if x.get("numero") == numero), None)
            if f is None:
                raise HTTPException(404, "Ese numero no esta en ese lote")
            cuerpo = _ficha_para_fuera(f, l)
        else:
            l, f = ESTADO.ya_hecha(numero, fecha.strip())
            if f is None:
                l, f = next(((x, y) for x in ESTADO.lotes for y in x["fichas"]
                             if y.get("numero") == numero and y["estado"] in ("pendiente", "en curso")), (None, None))
            cuerpo = _ficha_para_fuera(f, l) if f else {"estado": "no", "numero": numero}
        texto = json.dumps(cuerpo, ensure_ascii=False)
    return Response(texto, media_type="application/json")


@app.post("/api/lotes/lista")
async def subir_lista(fichero: UploadFile = File(...), reparto: str = Form(""), fecha: str = Form(""),
                      formal: str = Form(""), rehacer: str = Form("")):
    """Lista de trabajo: el CSV o el Excel que se exporta de la busqueda de MediaCentral.
    Solo se saca de el que envios hay que hacer; las fichas se leen luego de la web de la agencia.
    Un Excel de fichas (columnas NAME y CONT) no va aqui: eso es python catalogar.py --excel."""
    nombre = Path(fichero.filename or "lista.csv").name
    if not nombre.lower().endswith((".csv", ".txt", ".tsv", ".xlsx")):
        raise HTTPException(400, "Solo se admiten listas .csv, .txt o .xlsx")
    contenido = await fichero.read()
    if len(contenido) > 20 * 1024 * 1024:
        raise HTTPException(400, "El fichero pasa de 20 MB")
    fecha = fecha.strip()
    if fecha and not FECHA_RE.match(fecha):
        raise HTTPException(400, "La fecha debe ser DD/MM/AAAA")
    try:
        envios, descartes, aviso = leer_lista(nombre, contenido)
    except ListaError as e:
        raise HTTPException(400, str(e))
    if reparto.strip():
        _comprobar_reparto(reparto)
    # el titulo de la lista es solo etiqueta para reconocer la ficha en el panel, nunca fuente
    fichas = []
    for e in envios:
        f = ficha_vacia(e["numero"], e["etiqueta"], e["numero"], fecha, e.get("creado", ""))
        f["para"] = e.get("para", "")
        fichas.append(f)
    nota = resumen_lista(envios, descartes) + ((". " + aviso) if aviso else "")
    lotes = crear_lotes("lista", nombre, fichas, nota=nota, rehacer=_es_formal(rehacer),
                        reparto=reparto.strip(), formal=_es_formal(formal) if reparto.strip() else False)
    log(f"Lista recibida: {nombre} · {nota}")
    return {"id": lotes[0]["id"], "lotes": len(lotes), "lote_de": _lote_de(lotes), "fichas": len(fichas),
            "aviso": aviso, "descartes": descartes[:20], "total_descartes": len(descartes)}


@app.post("/api/lotes/numeros")
async def subir_numeros(request: Request):
    """Caja de numeros de la pagina. Vale cualquier texto: "3213 2314", "3213,2314", "hazme el 7612 y el 7613",
    "AP4681323", "0611=06/09/2026". Se queda con los numeros de envio y cada uno va a su agencia."""
    datos = await _json(request)
    fecha_comun = (datos.get("fecha") or "").strip()
    if fecha_comun and not FECHA_RE.match(fecha_comun):
        raise HTTPException(400, "La fecha debe ser DD/MM/AAAA")
    trabajos, ignorados = leer_texto(datos.get("texto") or "")
    if not trabajos:
        raise HTTPException(400, "No he encontrado ningun numero de envio: Reuters lleva 4 cifras (0624), "
                                 "AP 7 (4681323) y EBU es tipo 2026_10420363"
                                 + (". Ignorado: " + "; ".join(ignorados) if ignorados else ""))
    trabajos = [(n, f or fecha_comun) for n, f in trabajos]
    reparto = (datos.get("reparto") or "").strip()
    if reparto:
        _comprobar_reparto(reparto)
    fichas = [ficha_vacia(n, "", n, f) for n, f in trabajos]
    nombre = ", ".join(n for n, _ in trabajos[:3]) + (" ..." if len(trabajos) > 3 else "")
    lotes = crear_lotes("numeros", nombre, fichas, rehacer=_es_formal(datos.get("rehacer")),
                        reparto=reparto or "", formal=_es_formal(datos.get("formal")) if reparto else False)
    agencias = resumen_agencias([n for n, _ in trabajos])
    log(f"Numeros recibidos: {nombre} ({agencias})" + (f" · ignorado: {'; '.join(ignorados)}" if ignorados else ""))
    return {"id": lotes[0]["id"], "lotes": len(lotes), "lote_de": _lote_de(lotes), "fichas": len(fichas),
            "agencias": agencias, "ignorados": ignorados}


def _es_formal(valor):
    """Una casilla de la pagina (Formal, Rehacer): llega como true/false (JSON) o "1"/"" (formulario)."""
    return str(valor).strip().lower() in ("1", "true", "si", "on")


def _comprobar_reparto(texto):
    reglas, errores = analizar_reparto(texto, CFG.get("documentalistas") or {}, CFG.get("correo_copia") or "")
    if errores:
        raise HTTPException(400, "Reparto mal escrito: " + " · ".join(errores))
    if not reglas:
        raise HTTPException(400, "El reparto esta vacio")


@app.get("/api/agenda")
def agenda():
    personas = CFG.get("documentalistas") or {}
    return {"documentalistas": personas, "copia": CFG.get("correo_copia") or "",
            "formales": [n for n, d in personas.items() if es_formal(CFG, n, d)],   # los de correo_formal
            "correo_configurado": bool(CFG.get("correo_servidor") and CFG.get("correo_usuario") and CFG.get("correo_clave"))}


@app.post("/api/lotes/{id_lote}/enviar")
async def enviar_lote_ahora(id_lote: str, request: Request):
    """Manda (o vuelve a mandar) las fichas hechas de un lote segun un reparto."""
    datos = await _json(request)
    lote = ESTADO.lote(id_lote)
    if lote is None:
        raise HTTPException(404, "No existe ese lote")
    if lote["estado"] == "en curso":
        raise HTTPException(409, "El lote todavia se esta catalogando")
    reparto = (datos.get("reparto") or lote.get("reparto") or "").strip()
    if not reparto:
        raise HTTPException(400, "Escribe un reparto")
    _comprobar_reparto(reparto)
    with ESTADO.lock:
        lote["reparto"] = reparto
        if "formal" in datos:
            lote["formal"] = _es_formal(datos["formal"])
        ESTADO.guardar()
    envios = enviar_correos(lote, reparto)
    return {"envios": envios}


@app.post("/api/enviar")
async def enviar_seleccion_api(request: Request):
    """Manda por correo una seleccion de fichas de cualquier lote: {fichas: [{lote, ficha}], a: "andrea", nota: "",
    formal: false}. "a" es un nombre de la agenda, "yo" o una direccion cualquiera (esa, siempre en formal)."""
    datos = await _json(request)
    destino = (datos.get("a") or "").strip()
    if not destino:
        raise HTTPException(400, "Falta a quien enviar")
    fichas, refs = [], []
    with ESTADO.lock:
        for ref in datos.get("fichas") or []:
            lote = ESTADO.lote(ref.get("lote", ""))
            f = next((x for x in lote["fichas"] if x["id"] == ref.get("ficha")), None) if lote else None
            if f and f["estado"] == "hecha":
                fichas.append(f); refs.append(f)
    if not fichas:
        raise HTTPException(400, "No hay fichas hechas en la seleccion")
    try:
        etiqueta, direccion, formal = enviar_seleccion(CFG, fichas, destino, (datos.get("nota") or "").strip(),
                                                       formal=_es_formal(datos.get("formal")))
    except CorreoError as e:
        raise HTTPException(502, str(e))
    _anotar_enviada(refs, etiqueta, direccion)
    with ESTADO.lock:
        ESTADO.guardar()
    log(f"Correo a {etiqueta} ({direccion}): {len(fichas)} ficha(s) seleccionadas" + (" · formal" if formal else ""))
    admin.anotar_correo("enviado", direccion, etiqueta, len(fichas), None, "seleccion", formal=formal)
    return {"a": etiqueta, "direccion": direccion, "fichas": len(fichas), "formal": formal}


@app.delete("/api/lotes/{id_lote}")
def borrar_lote(id_lote: str):
    with ESTADO.lock:
        lote = ESTADO.lote(id_lote)
        if lote is None:
            raise HTTPException(404, "No existe ese lote")
        if lote["estado"] == "en curso":
            raise HTTPException(409, "Ese lote se esta procesando ahora")
        ESTADO.lotes.remove(lote)
        ESTADO.guardar()
    return {"ok": True}


PARADA = "Parada a mano desde la pagina"


@app.post("/api/lotes/{id_lote}/parar")
def parar(id_lote: str):
    """Para un lote: lo que esta en cola no se hace. La ficha que se este redactando en ese momento
    termina (una llamada al modelo no se corta a medias, es un minuto). Lo parado se puede volver a
    poner en cola con Volver a intentarlo."""
    with ESTADO.lock:
        lote = ESTADO.lote(id_lote)
        if lote is None:
            raise HTTPException(404, "No existe ese lote")
        n = 0
        for f in lote["fichas"]:
            if f["estado"] == "pendiente":
                f["estado"], f["error"] = "error", PARADA
                n += 1
        lote["parado"] = True
        if lote["estado"] == "pendiente":            # aun no habia empezado: no hay nada que esperar
            lote["estado"] = "hecho"
        ESTADO.guardar()
    log(f"Lote {lote['id']} parado desde la pagina: {n} ficha(s) quedan sin hacer.")
    return {"paradas": n, "en_curso": any(f["estado"] == "en curso" for f in lote["fichas"])}


@app.post("/api/lotes/{id_lote}/reintentar")
def reintentar(id_lote: str):
    """Vuelve a poner en cola las fichas con error de un lote."""
    with ESTADO.lock:
        lote = ESTADO.lote(id_lote)
        if lote is None:
            raise HTTPException(404, "No existe ese lote")
        if lote["estado"] == "en curso":
            # si se pone en pendiente a medias, el worker lo recorre dos veces y manda los correos dos veces
            raise HTTPException(409, "El lote esta en curso: espera a que termine (o paralo) y reintenta despues")
        n = 0
        for f in lote["fichas"]:
            if f["estado"] == "error":
                f["estado"], f["error"] = "pendiente", ""
                n += 1
        if n or any(f["estado"] == "pendiente" for f in lote["fichas"]):
            lote["estado"] = "pendiente"
            lote["mensaje"] = ""
            lote["parado"] = False
        ESTADO.guardar()
    if WORKER is not None and WORKER.pausa_hasta:
        # Volver a intentarlo tambien levanta la pausa del modelo (limite, caida, login): se prueba ya
        WORKER.pausa_hasta = time.time()
        log("Pausa del modelo levantada a mano: se vuelve a intentar.")
    return {"reintentadas": n}


@app.post("/api/fichas/{id_lote}/{id_ficha}/buena")
async def aprobar(id_lote: str, id_ficha: str, request: Request):
    """Aprueba la ficha tal cual o con las correcciones que vengan en el cuerpo."""
    datos = await _json(request)
    with ESTADO.lock:
        lote = ESTADO.lote(id_lote)
        ficha = next((f for f in lote["fichas"] if f["id"] == id_ficha), None) if lote else None
        if ficha is None:
            raise HTTPException(404, "No existe esa ficha")
        campos = {c: " ".join(str(datos.get(c) or ficha.get(c) or "").split()) for c in CAMPOS}
        if not campos["RESTRICCIONES"]:
            campos["RESTRICCIONES"] = "SIN AVISO"
        try:
            n, aviso = anadir_ejemplo(campos)
        except ValueError as e:
            raise HTTPException(400, str(e))
        rehacer_normal = any(ficha.get(c) != campos[c] for c in CAMPOS)
        if rehacer_normal:
            ficha["normal"] = None                  # el texto ha cambiado: la version normal ya no vale
        for c in CAMPOS:
            ficha[c] = campos[c]
        ficha["aprobada"] = True
        ficha["avisos"] = validar(campos)
        ESTADO.guardar()
        foto = {"numero": ficha.get("numero"), "fecha": ficha.get("fecha"), "fotogramas": ficha.get("fotogramas")}
        borrador = ficha.get("borrador")
    log(f"Ficha aprobada: {campos['ENVIO'][:50]} (total {n})")
    # si se ha corregido, se estudia la correccion por si enseña una regla (Reglas → Sugerencias)
    aprendiendo = aprender.encolar(borrador, campos, campos["ENVIO"])
    # y la version en minusculas se rehace con el texto corregido, para quien la recibe asi
    if rehacer_normal and quiere_normal():
        threading.Thread(target=WORKER.normalizar_ficha, args=(ficha,), daemon=True).start()
    # sus fotogramas pasan a ser ejemplo de su tipo de acto (entrenar_escenas.py los recoge)
    try:
        copiados, motivo = guardar_para_entrenar(foto, campos)
        if copiados:
            log(f"  {copiados} fotograma(s) guardados para entrenar el reconocimiento de escenas")
        elif foto["fotogramas"]:
            log(f"  fotogramas no guardados para entrenar: {motivo}")
    except Exception as e:                       # entrenar es un extra: nunca estropea una aprobacion
        log(f"  no se han podido guardar los fotogramas para entrenar ({type(e).__name__})")
    return {"total": n, "aviso": aviso, "avisos": ficha["avisos"], "aprendiendo": aprendiendo}


@app.post("/api/fichas/{id_lote}/{id_ficha}/normalizar")
def normalizar(id_lote: str, id_ficha: str):
    """Pide al modelo la version en escritura normal de una ficha ya hecha."""
    with ESTADO.lock:
        lote = ESTADO.lote(id_lote)
        ficha = next((f for f in lote["fichas"] if f["id"] == id_ficha), None) if lote else None
        if ficha is None or ficha["estado"] != "hecha":
            raise HTTPException(404, "No existe esa ficha o todavia no esta hecha")
    if not WORKER.normalizar_ficha(ficha):
        raise HTTPException(502, "El modelo no ha devuelto la version normal; prueba de nuevo")
    return {"normal": ficha["normal"], "avisos": ficha["avisos"]}


@app.get("/api/reglas")
def reglas():
    return {"reglas": listar_reglas_extra()}


@app.post("/api/reglas")
async def nueva_regla(request: Request):
    datos = await _json(request)
    try:
        linea = anadir_regla(datos.get("texto") or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    log("Regla anadida")
    return {"regla": linea, "reglas": listar_reglas_extra()}


@app.delete("/api/reglas/{indice}")
def borrar_regla(indice: int):
    lista = listar_reglas_extra()
    if not 1 <= indice <= len(lista):
        raise HTTPException(404, f"No hay regla {indice}")
    cabecera = [l for l in REGLAS_EXTRA_PATH.read_text(encoding="utf-8").splitlines() if l.startswith("#")]
    quedan = [r for i, r in enumerate(lista, 1) if i != indice]
    REGLAS_EXTRA_PATH.write_text("\n".join(cabecera + quedan) + "\n", encoding="utf-8")
    return {"reglas": quedan}


@app.get("/api/ejemplos")
def ejemplos():
    return {"ejemplos": listar_ejemplos()}


@app.delete("/api/ejemplos/{numero}")
def borrar_ejemplo(numero: str):
    if not quitar_ejemplo(numero):
        raise HTTPException(404, "No hay ninguna ficha aprobada con ese numero")
    return {"ejemplos": listar_ejemplos()}


@app.post("/api/navegador/login")
def login_agencias():
    """Pide al Worker que abra en el PC de casa la ventana de inicio de sesion de las agencias."""
    if WORKER.login_activo:
        raise HTTPException(409, "Ya hay una ventana de inicio de sesion abierta")
    WORKER.encargar("login")
    ESTADO.sesion_agencia = "login pendiente" if ESTADO.trabajando else "login en curso"
    return {"ok": True, "nota": "Si hay un envio redactandose, la ventana se abre al terminar ese envio."}


@app.post("/api/navegador/login/terminar")
def login_terminar():
    """El usuario ya ha iniciado sesion: el Worker cierra la ventana y guarda el perfil."""
    if not WORKER.login_activo:
        raise HTTPException(409, "No hay ningun inicio de sesion en curso")
    WORKER.login_terminar = True
    return {"ok": True}


@app.post("/api/navegador/comprobar")
def comprobar_agencias():
    """Comprueba la sesion de las agencias sin catalogar nada (espera hasta 90 s si el Worker esta ocupado)."""
    if WORKER.login_activo:
        raise HTTPException(409, "Hay un inicio de sesion en curso")
    resultado = WORKER.encargar("comprobar", esperar=90)
    if resultado == "ocupado":
        raise HTTPException(503, "El Worker esta redactando; la comprobacion se hara en cuanto termine el envio actual")
    return {"ok": resultado is None, "problema": resultado}


@app.post("/api/navegador/reabrir")
def reabrir_navegador():
    """Cierra el navegador del Worker para que lo abra de nuevo con la sesion nueva."""
    WORKER.encargar("cerrar")
    return {"ok": True}


# ====================================================================== administracion (admin.py)
admin.iniciar(globals())
app.include_router(admin.router)
aprender.iniciar(globals())
app.include_router(aprender.router)
imagenes.iniciar(globals())
app.include_router(imagenes.router)


# ====================================================================== arranque
def main():
    global CFG
    ap = argparse.ArgumentParser(description="Servidor web de catalogar")
    ap.add_argument("--puerto", type=int, default=None)
    args = ap.parse_args()
    try:
        CFG = cargar_config()
    except ConfigError as e:
        print(e)
        return 1
    CFG.setdefault("servidor_puerto", 8765)
    CFG.setdefault("servidor_clave", "")
    configurar(CFG)
    paginas_word.configurar(CFG)
    listas.configurar(CFG)
    if len(CFG["servidor_clave"]) < 12:
        print("Pon en config.json una clave de al menos 12 caracteres: \"servidor_clave\": \"...\"")
        sys.exit(1)
    puerto = args.puerto or int(CFG["servidor_puerto"])
    COLA_DIR.mkdir(exist_ok=True)
    # deja claro en el arranque con que criterio se va a redactar
    ruta_reglas = Path(__file__).resolve().parent / CFG.get("reglas", "reglas.md")
    if ruta_reglas.exists():
        log(f"Criterio: {ruta_reglas.name} ({ruta_reglas.stat().st_size // 1024} KB) · "
            f"{len(listar_reglas_extra())} ajuste(s) en reglas_extra.md · {len(listar_ejemplos())} ficha(s) aprobada(s)")
    else:
        log(f"AVISO: no existe {ruta_reglas.name}; se usara reglas.md (la version antigua congelada). "
            f"Revisa la linea \"reglas\" de config.json.")
    WORKER.start()
    BUZON.start()
    if CFG.get("comprobar_sesion_al_arrancar", True):
        ESTADO.sesion_agencia = "comprobando"
        WORKER.encargar("comprobar_arranque")
    log(f"Servidor en http://127.0.0.1:{puerto}  (Ctrl+C para parar)")

    def _anunciar_direccion():
        time.sleep(3)                          # el lanzador publica con Tailscale justo antes de arrancar
        url, motivo = admin.direccion_fuera()
        log(f"Direccion para entrar desde fuera: {url}" if url else f"Sin direccion de fuera: {motivo}")
    threading.Thread(target=_anunciar_direccion, daemon=True).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=puerto, log_level="warning")
    finally:
        WORKER.parar = True
        BUZON.parar = True
        WORKER.cerrar_navegador()


if __name__ == "__main__":
    main()
