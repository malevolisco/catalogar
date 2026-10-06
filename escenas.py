# -*- coding: utf-8 -*-
"""
escenas.py - Clasificador local de escenas para los fotogramas de un envio.

Funciona entero en el PC, sin internet (salvo la descarga del modelo la primera vez)
y sin gastar cuota de Claude. Convierte cada fotograma en una lista de numeros con
CLIP y lo compara con los centros de las clases que usted mismo ha entrenado con
entrenar_escenas.py.

No decide la ficha: devuelve una etiqueta y una confianza (la parte de fotogramas que
votan por ella). El redactor solo la usa si la confianza pasa de escenas_umbral y la
clase ha demostrado al entrenar un acierto de al menos escenas_fiabilidad_min.

Requisitos:  pip install onnxruntime pillow numpy
El modelo se descarga solo la primera vez a modelos/ (escenas_modelo: ligero, unos 90 MB, o completo, 352 MB)
"""
import json
import sys
import threading
import urllib.request
from pathlib import Path

import numpy as np

BASE_DIR = Path(__file__).resolve().parent
MODELO_DIR = BASE_DIR / "modelos"
CENTROS = BASE_DIR / "escenas_modelo.npz"
CLASES_PROPIAS = BASE_DIR / "escenas_clases.json"    # categorias creadas en la pestaña Imagenes: {clase: descriptor}
# dos versiones del mismo modelo: la ligera (cuantizada a 8 bits) ocupa la cuarta parte, gasta mucha menos
# memoria y reconoce cada fotograma bastante mas rapido (lo que se nota en la Raspberry), con muy poca perdida
URL_BASE = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/onnx/"
VARIANTES = {"ligero": ("clip_vision_ligero.onnx", URL_BASE + "vision_model_quantized.onnx", "unos 90 MB"),
             "completo": ("clip_vision.onnx", URL_BASE + "vision_model.onnx", "352 MB")}
_variante = {"cargada": None}

# normalizacion propia de CLIP
MEDIA = np.array([0.48145466, 0.45782750, 0.40821073], dtype=np.float32)
DESV = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
LADO = 224

_sesion = None
_cerrojo = threading.Lock()       # la redaccion y el entrenamiento desde la pagina pueden cargarlo a la vez


def variante_pedida():
    """La version del modelo que pide config.json (escenas_modelo): ligero (por defecto) o completo."""
    try:
        from config import cargar_config
        v = cargar_config().get("escenas_modelo", "ligero")
    except Exception:
        v = "ligero"
    return v if v in VARIANTES else "ligero"


def variante_en_uso():
    return _variante["cargada"] or variante_pedida()


def _descargar_modelo(variante):
    fichero, url, tam = VARIANTES[variante]
    MODELO_DIR.mkdir(exist_ok=True)
    destino = MODELO_DIR / fichero
    print(f"Descargando el modelo de vision {variante} ({tam}). Solo ocurre la primera vez...")

    def progreso(bloques, tam, total):
        if total > 0:
            hecho = min(bloques * tam, total)
            sys.stdout.write(f"\r  {hecho / 1e6:.0f} de {total / 1e6:.0f} MB")
            sys.stdout.flush()

    tmp = destino.with_suffix(".parcial")
    urllib.request.urlretrieve(url, tmp, progreso)
    tmp.rename(destino)
    print("\n  Listo.")


def _cargar():
    global _sesion
    with _cerrojo:
        if _sesion is not None:
            return _sesion
        import onnxruntime
        variante = variante_pedida()
        ruta = MODELO_DIR / VARIANTES[variante][0]
        if not ruta.exists():
            try:
                _descargar_modelo(variante)
            except Exception as e:
                if variante == "completo":
                    raise
                print(f"No se ha podido bajar el modelo ligero ({type(e).__name__}); se usa el completo")
                variante = "completo"
                ruta = MODELO_DIR / VARIANTES[variante][0]
                if not ruta.exists():
                    _descargar_modelo(variante)
        _sesion = onnxruntime.InferenceSession(str(ruta), providers=["CPUExecutionProvider"])
        _variante["cargada"] = variante
        return _sesion


def clases_propias():
    """Categorias creadas por el catalogador en la pestaña Imagenes: {clase: como se dice en la ficha}."""
    try:
        datos = json.loads(CLASES_PROPIAS.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in datos.items()} if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        return {}


def descriptor(clase):
    """Como se dice esa clase en una ficha: el descriptor del tipo de acto o el que se puso al crearla."""
    from actos import DESCRIPTOR
    return DESCRIPTOR.get(clase) or clases_propias().get(clase) or clase.replace("_", " ").upper()


def _preparar(ruta):
    """Imagen -> tensor 1x3x224x224 como lo espera CLIP."""
    from PIL import Image
    im = Image.open(ruta).convert("RGB")
    ancho, alto = im.size
    escala = LADO / min(ancho, alto)
    im = im.resize((max(LADO, round(ancho * escala)), max(LADO, round(alto * escala))), Image.BICUBIC)
    ancho, alto = im.size
    izq, arriba = (ancho - LADO) // 2, (alto - LADO) // 2
    im = im.crop((izq, arriba, izq + LADO, arriba + LADO))
    x = np.asarray(im, dtype=np.float32) / 255.0
    x = (x - MEDIA) / DESV
    return np.transpose(x, (2, 0, 1))[None, ...].astype(np.float32)


def vectores(rutas):
    """Devuelve un vector normalizado por imagen. Las que fallen se saltan."""
    ses = _cargar()
    entrada = ses.get_inputs()[0].name
    salida = []
    for r in rutas:
        try:
            lote = _preparar(r)
        except Exception:
            continue
        v = ses.run(None, {entrada: lote})[0]
        v = np.asarray(v, dtype=np.float32).reshape(-1)
        n = np.linalg.norm(v)
        if n > 0:
            salida.append(v / n)
    return np.array(salida, dtype=np.float32) if salida else np.zeros((0, 1), dtype=np.float32)


def hay_modelo():
    return CENTROS.exists()


_centros = {"marca": None, "clases": [], "centros": None, "fiabilidad": {}, "variante": "completo"}


def _modelo():
    """Clases, centros y fiabilidad por clase del escenas_modelo.npz, releido solo si ha cambiado."""
    marca = CENTROS.stat().st_mtime_ns
    if _centros["marca"] != marca:
        datos = np.load(CENTROS, allow_pickle=True)
        clases = [str(c) for c in datos["clases"]]
        fiab = {}
        if "fiabilidad" in datos.files:                # los modelos entrenados antes no la tienen
            fiab = {c: float(f) for c, f in zip(clases, datos["fiabilidad"])}
        # los entrenados antes de haber dos versiones del modelo son del completo
        var = str(datos["variante"]) if "variante" in datos.files else "completo"
        _centros.update(marca=marca, clases=clases, centros=datos["centros"].astype(np.float32), fiabilidad=fiab, variante=var)
    return _centros


def fiabilidad(clase):
    """Acierto medido de esa clase al entrenar (0-1), o None si el modelo no lo trae."""
    if not clase or not CENTROS.exists():
        return None
    return _modelo()["fiabilidad"].get(clase)


def precargar(verboso=True):
    """Descarga y carga el modelo antes de empezar un lote, para que no se pare a
    mitad. Devuelve True si el clasificador queda listo para usarse."""
    if not CENTROS.exists():
        return False
    try:
        primera = not (MODELO_DIR / VARIANTES[variante_pedida()][0]).exists()
        _cargar()
        if verboso and primera:
            print("Clasificador de escenas listo.")
        return True
    except Exception as e:
        if verboso:
            print(f"Clasificador de escenas no disponible ({type(e).__name__}): se sigue sin el.")
        return False


def votar(vs, centros):
    """Cada fotograma vota por su clase mas cercana. Devuelve (indice ganador, parte de votos, votos).
    Empate: gana la clase mas parecida de media."""
    sims = vs @ centros.T                              # fotogramas x clases
    votos = np.bincount(sims.argmax(axis=1), minlength=len(centros))
    maximo = votos.max()
    empatadas = np.flatnonzero(votos == maximo)
    i = int(empatadas[np.argmax(sims.mean(axis=0)[empatadas])])
    return i, float(maximo) / len(vs), votos


def clasificar(rutas):
    """Clasifica el conjunto de fotogramas de un envio.

    Devuelve (etiqueta, confianza, detalle) o (None, 0.0, motivo) si no puede.
    Vota cada fotograma por separado y descarta el primero y el ultimo, que suelen ser negros,
    cortinillas o el plano de otra cosa: la confianza es la parte de fotogramas que coinciden.
    """
    if not CENTROS.exists():
        return None, 0.0, "sin entrenar"
    rutas = list(rutas or [])
    if len(rutas) >= 4:
        rutas = rutas[1:-1]
    if not rutas:
        return None, 0.0, "sin fotogramas"
    m = _modelo()
    vs = vectores(rutas)                                # carga el modelo: ya se sabe que version esta en uso
    if m.get("variante", "completo") != variante_en_uso():
        return None, 0.0, "entrenado con la otra version del modelo: falta volver a entrenar"
    if len(vs) == 0:
        return None, 0.0, "no se pudo leer ningun fotograma"
    i, parte, votos = votar(vs, m["centros"])
    orden = [j for j in np.argsort(-votos) if votos[j]][:3]
    detalle = ", ".join(f"{m['clases'][j]} {votos[j]}/{len(vs)}" for j in orden)
    return m["clases"][i], parte, detalle


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        print("Uso: python escenas.py miniaturas\\4683554")
        sys.exit(1)
    carpeta = Path(sys.argv[1])
    fotos = sorted([p for p in carpeta.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png")])
    if not fotos:
        print(f"No hay imagenes en {carpeta}")
        sys.exit(1)
    etiqueta, confianza, detalle = clasificar([str(p) for p in fotos])
    print(f"{len(fotos)} fotogramas")
    print(f"Escena: {etiqueta}   confianza {confianza:.2f}")
    print(f"Reparto: {detalle}")
    f = fiabilidad(etiqueta)
    if f is not None:
        print(f"Acierto de esa clase al entrenar: {f:.0%}")
