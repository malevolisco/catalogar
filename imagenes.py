# -*- coding: utf-8 -*-
"""
imagenes.py - Pestaña Imagenes: enseñar al reconocimiento de escenas desde la pagina.

    categorias      los tipos de acto de actos.py (rueda de prensa, declaraciones...) y las que cree el
                    catalogador (escenas_clases.json: clase -> como se dice en la ficha)
    imagenes        las suyas (escenas/<clase>/, subidas o pegadas desde la pagina) y las de las fichas
                    aprobadas (escenas_auto/<clase>/, que se guardan solas al aprobar)
    entrenar        entrenar_escenas.entrenar() en segundo plano: con el boton o sola, cuando cambian las
                    imagenes (escenas_entrenar_auto). Lo ya visto se guarda en escenas_cache.npz y solo se
                    calculan las nuevas; en una Raspberry Pi 4, alrededor de medio segundo por imagen.

servidor.py llama a iniciar(globals()) y monta router.
"""
import collections
import io
import json
import re
import secrets
import threading
import time
import unicodedata
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

BASE_DIR = Path(__file__).resolve().parent
ORIGENES = {"escenas": "tuyas", "escenas_auto": "de fichas aprobadas"}
EXT = (".jpg", ".jpeg", ".png", ".webp")
ENTRENO_PATH = BASE_DIR / "escenas_entreno.json"      # el resultado del ultimo entrenamiento, para la pagina
LADO_MAX = 640                                         # las subidas se guardan como mucho a este tamaño
NOMBRE_RE = re.compile(r"^[\w.-]{1,120}$")
CLASE_RE = re.compile(r"^[a-z0-9_]{2,40}$")
ESPERA_TRAS_CAMBIO = 180                               # el entrenamiento solo espera a que se acabe de subir

router = APIRouter()
S = {}
ESTADO = {"en_marcha": False, "inicio": "", "lineas": collections.deque(maxlen=60), "error": ""}
_cerrojo = threading.Lock()


def iniciar(globales):
    S.clear()
    S["_g"] = globales
    threading.Thread(target=_vigilar, name="entrenar-escenas", daemon=True).start()


def g(nombre):
    return S["_g"][nombre]


def _log(texto):
    try:
        g("log")(texto)
    except Exception:
        print(texto, flush=True)


def _disponible():
    """(True, "") si se puede reconocer y entrenar en este equipo (numpy, onnxruntime y pillow instalados)."""
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        import PIL  # noqa: F401
        return True, ""
    except Exception as e:
        return False, f"Falta un componente ({e.name if hasattr(e, 'name') else e}); reinstala con requirements.txt"


# ====================================================================== categorias
def _clases_propias():
    import escenas
    return escenas.clases_propias()


def _guardar_propias(datos):
    import escenas
    escenas.CLASES_PROPIAS.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


def _imagenes(origen, clase):
    carpeta = BASE_DIR / origen / clase
    if not carpeta.is_dir():
        return []
    return [p for p in carpeta.iterdir() if p.suffix.lower() in EXT and p.is_file()]


def _ejemplos(rutas, origen):
    """Ejemplos que cuentan para entrenar: envios (fotogramas agrupados) o imagenes sueltas."""
    from entrenar_escenas import GRUPO_RE
    grupos = set()
    for p in rutas:
        m = GRUPO_RE.match(p.stem)
        grupos.add(m.group(1) if m else f"{origen}/{p.stem}")
    return len(grupos)


def _todas_las_clases():
    from actos import DESCRIPTOR
    propias = _clases_propias()
    nombres = list(DESCRIPTOR) + [c for c in propias if c not in DESCRIPTOR]
    for origen in ORIGENES:
        raiz = BASE_DIR / origen
        if raiz.is_dir():
            nombres += sorted(p.name for p in raiz.iterdir() if p.is_dir() and p.name not in nombres)
    return nombres, propias


def _entreno():
    try:
        return json.loads(ENTRENO_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@router.get("/api/imagenes")
def resumen():
    import escenas
    ok, motivo = _disponible()
    nombres, propias = _todas_las_clases()
    ultimo = _entreno()
    por_clase = {c["clase"]: c for c in (ultimo.get("resultado") or {}).get("clases") or []}
    filas = []
    for clase in nombres:
        tuyas, auto = _imagenes("escenas", clase), _imagenes("escenas_auto", clase)
        r = por_clase.get(clase) or {}
        filas.append({"clase": clase, "descriptor": escenas.descriptor(clase), "propia": clase in propias,
                      "tuyas": len(tuyas), "de_fichas": len(auto),
                      "ejemplos": _ejemplos(tuyas, "escenas") + _ejemplos(auto, "escenas_auto"),
                      "acierto": r.get("acierto"), "se_usa": bool(r.get("se_usa")), "nota": r.get("nota") or ""})
    return {"disponible": ok, "motivo": motivo, "clases": filas,
            "entreno": {"en_marcha": ESTADO["en_marcha"], "inicio": ESTADO["inicio"], "error": ESTADO["error"],
                        "lineas": list(ESTADO["lineas"]), "fecha": ultimo.get("fecha", ""),
                        "global": (ultimo.get("resultado") or {}).get("global"),
                        "motivo": (ultimo.get("resultado") or {}).get("motivo", ""),
                        "pendiente": _firma() != ultimo.get("firma")},
            "auto": bool(g("CFG").get("escenas_entrenar_auto", True)),
            "minimo": 3}


@router.post("/api/imagenes/clases")
async def nueva_clase(request: Request):
    datos = await g("_json")(request)
    nombre = " ".join(str(datos.get("nombre") or "").split())
    descriptor = " ".join(str(datos.get("descriptor") or nombre).split()).upper()
    t = unicodedata.normalize("NFKD", nombre.lower())
    clase = re.sub(r"[^a-z0-9]+", "_", "".join(ch for ch in t if not unicodedata.combining(ch))).strip("_")[:40]
    if not CLASE_RE.match(clase or ""):
        raise HTTPException(400, "Ponle un nombre a la categoría (letras o números)")
    nombres, propias = _todas_las_clases()
    if clase in nombres:
        raise HTTPException(409, "Ya hay una categoría con ese nombre")
    propias[clase] = descriptor
    _guardar_propias(propias)
    (BASE_DIR / "escenas" / clase).mkdir(parents=True, exist_ok=True)
    _log(f"Imagenes: categoria nueva {clase} ({descriptor})")
    return {"clase": clase}


@router.put("/api/imagenes/clases/{clase}")
async def renombrar_clase(clase: str, request: Request):
    """Cambia como se dice en la ficha una categoria propia."""
    datos = await g("_json")(request)
    propias = _clases_propias()
    if clase not in propias:
        raise HTTPException(404, "Solo se puede cambiar una categoría creada por ti")
    descriptor = " ".join(str(datos.get("descriptor") or "").split()).upper()
    if not descriptor:
        raise HTTPException(400, "Escribe cómo se dice en la ficha")
    propias[clase] = descriptor
    _guardar_propias(propias)
    return {"ok": True}


@router.delete("/api/imagenes/clases/{clase}")
def borrar_clase(clase: str):
    propias = _clases_propias()
    if clase not in propias:
        raise HTTPException(404, "Solo se puede quitar una categoría creada por ti")
    if _imagenes("escenas", clase) or _imagenes("escenas_auto", clase):
        raise HTTPException(409, "La categoría tiene imágenes: quítalas o muévelas antes")
    propias.pop(clase)
    _guardar_propias(propias)
    for origen in ORIGENES:
        try:
            (BASE_DIR / origen / clase).rmdir()
        except OSError:
            pass
    return {"ok": True}


# ====================================================================== imagenes
def _ruta(origen, clase, nombre):
    if origen not in ORIGENES or not CLASE_RE.match(clase) or not NOMBRE_RE.match(nombre) or nombre.startswith("."):
        raise HTTPException(400, "Imagen no valida")
    ruta = BASE_DIR / origen / clase / nombre
    if not ruta.is_file():
        raise HTTPException(404, "Esa imagen ya no está")
    return ruta


@router.get("/api/imagenes/{clase}")
def imagenes_de(clase: str, limite: int = 300):
    if not CLASE_RE.match(clase):
        raise HTTPException(400, "Categoría no válida")
    lista = []
    for origen in ORIGENES:
        for p in _imagenes(origen, clase):
            st = p.stat()
            lista.append({"origen": origen, "nombre": p.name, "t": st.st_mtime,
                          "url": f"/api/imagenes/archivo/{origen}/{clase}/{p.name}?v={int(st.st_mtime)}"})
    lista.sort(key=lambda x: -x["t"])
    return {"clase": clase, "imagenes": lista[:limite], "total": len(lista)}


@router.get("/api/imagenes/archivo/{origen}/{clase}/{nombre}")
def archivo(origen: str, clase: str, nombre: str):
    return FileResponse(_ruta(origen, clase, nombre), headers={"Cache-Control": "private, max-age=86400"})


def _guardar_imagen(datos, destino):
    """Guarda la imagen reducida a LADO_MAX en jpg. Sin pillow, tal cual."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(datos)).convert("RGB")
        im.thumbnail((LADO_MAX, LADO_MAX))
        im.save(destino.with_suffix(".jpg"), "JPEG", quality=88)
        return True
    except ImportError:
        destino.write_bytes(datos)
        return True
    except Exception:
        return False


@router.post("/api/imagenes/{clase}/subir")
async def subir(clase: str, request: Request):
    """Imagenes para una categoria. mismo_video=1: son del mismo video y cuentan como un solo ejemplo (si no,
    el acierto que se mide al entrenar saldria inflado, probando con fotogramas casi iguales a los aprendidos)."""
    nombres, _ = _todas_las_clases()
    if clase not in nombres:
        raise HTTPException(404, "Esa categoría no existe")
    formulario = await request.form()
    ficheros = [f for f in formulario.getlist("ficheros") if hasattr(f, "read")]
    if not ficheros:
        raise HTTPException(400, "No llega ninguna imagen")
    mismo = str(formulario.get("mismo_video") or "") in ("1", "true", "on")
    carpeta = BASE_DIR / "escenas" / clase
    carpeta.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d%H%M%S") + secrets.token_hex(2)
    hoy = datetime.now().strftime("%Y%m%d")
    guardadas, malas = 0, []
    for i, f in enumerate(ficheros, 1):
        datos = await f.read()
        if len(datos) > 25 * 1024 * 1024:
            malas.append(f.filename or f"imagen {i}")
            continue
        # mismo video: subida<sello>_<aaaammdd>_<n>, que entrenar_escenas agrupa como un envio;
        # sueltas: un nombre sin ese patron, y cada una cuenta como un ejemplo
        nombre = f"subida{sello}_{hoy}_{i:02d}" if mismo else f"subida{sello}-{i:02d}"
        if _guardar_imagen(datos, carpeta / (nombre + ".jpg")):
            guardadas += 1
        else:
            malas.append(f.filename or f"imagen {i}")
    if guardadas:
        _log(f"Imagenes: {guardadas} subida(s) a {clase}" + (" (del mismo video)" if mismo else ""))
    return {"guardadas": guardadas, "malas": malas}


@router.post("/api/imagenes/mover")
async def mover(request: Request):
    datos = await g("_json")(request)
    ruta = _ruta(str(datos.get("origen") or ""), str(datos.get("clase") or ""), str(datos.get("nombre") or ""))
    a = str(datos.get("a") or "")
    nombres, _ = _todas_las_clases()
    if a not in nombres:
        raise HTTPException(404, "Esa categoría no existe")
    destino = BASE_DIR / ruta.parent.parent.name / a
    destino.mkdir(parents=True, exist_ok=True)
    ruta.replace(destino / ruta.name)
    return {"ok": True}


@router.delete("/api/imagenes/archivo/{origen}/{clase}/{nombre}")
def quitar(origen: str, clase: str, nombre: str):
    _ruta(origen, clase, nombre).unlink()
    return {"ok": True}


# ====================================================================== entrenar
def _firma():
    """Cuantas imagenes hay y la mas reciente: si cambia, hay algo nuevo que aprender."""
    n, ultima = 0, 0.0
    for origen in ORIGENES:
        raiz = BASE_DIR / origen
        if not raiz.is_dir():
            continue
        for p in raiz.glob("*/*"):
            if p.suffix.lower() in EXT:
                n += 1
                try:
                    ultima = max(ultima, p.stat().st_mtime)
                except OSError:
                    pass
    return f"{n}:{int(ultima)}"


def _ultimo_cambio():
    ultima = 0.0
    for origen in ORIGENES:
        raiz = BASE_DIR / origen
        if raiz.is_dir():
            for p in [raiz, *raiz.iterdir()]:
                try:
                    ultima = max(ultima, p.stat().st_mtime)
                except OSError:
                    pass
    return ultima


def entrenar_ahora(motivo="a mano"):
    """Lanza el entrenamiento en segundo plano. False si ya hay uno en marcha."""
    with _cerrojo:
        if ESTADO["en_marcha"]:
            return False
        ESTADO.update(en_marcha=True, inicio=datetime.now().strftime("%d/%m/%Y %H:%M"), error="")
        ESTADO["lineas"].clear()
    threading.Thread(target=_entrenar, args=(motivo,), name="entrenar", daemon=True).start()
    return True


def _entrenar(motivo):
    def decir(texto):
        ESTADO["lineas"].append(str(texto).strip())
        print("  [imagenes] " + str(texto).strip(), flush=True)
    firma = _firma()
    try:
        _log(f"Imagenes: entrenando el reconocimiento de escenas ({motivo})")
        import entrenar_escenas
        resultado = entrenar_escenas.entrenar(decir=decir)
        ENTRENO_PATH.write_text(json.dumps({"fecha": datetime.now().strftime("%d/%m/%Y %H:%M"), "firma": firma,
                                            "resultado": resultado}, ensure_ascii=False, indent=1), encoding="utf-8")
        if resultado.get("ok"):
            usadas = [c["clase"] for c in resultado["clases"] if c.get("se_usa")]
            _log(f"Imagenes: entrenado ({resultado['imagenes']} imagenes). Se usan: {', '.join(usadas) or 'ninguna todavia'}")
        else:
            _log(f"Imagenes: sin entrenar ({resultado.get('motivo')})")
    except Exception as e:
        ESTADO["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        _log(f"Imagenes: el entrenamiento ha fallado ({ESTADO['error']})")
        try:                                          # no se reintenta solo hasta que cambien las imagenes
            anterior = _entreno()
            anterior["firma"] = firma
            ENTRENO_PATH.write_text(json.dumps(anterior, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass
    finally:
        ESTADO["en_marcha"] = False


@router.post("/api/imagenes/entrenar")
def entrenar_boton():
    ok, motivo = _disponible()
    if not ok:
        raise HTTPException(503, motivo)
    if not entrenar_ahora():
        raise HTTPException(409, "Ya se está entrenando")
    return {"ok": True}


def _vigilar():
    """Entrena sola cuando han cambiado las imagenes (fichas aprobadas, subidas, quitadas) y hace unos
    minutos que nadie toca nada."""
    time.sleep(90)                                    # que arranque antes lo demas
    while True:
        try:
            if g("CFG").get("escenas_entrenar_auto", True) and not ESTADO["en_marcha"] and _disponible()[0] \
                    and _firma() != _entreno().get("firma") and time.time() - _ultimo_cambio() > ESPERA_TRAS_CAMBIO:
                entrenar_ahora("solo, porque hay imagenes nuevas")
        except Exception:
            pass
        time.sleep(600)
