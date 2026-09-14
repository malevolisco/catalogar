# -*- coding: utf-8 -*-
"""
escenas.py - Clasificador local de escenas para los fotogramas de un envio.

Funciona entero en el PC, sin internet (salvo la descarga del modelo la primera vez)
y sin gastar cuota de Claude. Convierte cada fotograma en una lista de numeros con
CLIP y lo compara con los centros de las clases que usted mismo ha entrenado con
entrenar_escenas.py.

No decide la ficha: devuelve una etiqueta y una confianza, y el redactor solo la usa
como pista cuando la confianza pasa del umbral.

Requisitos:  pip install onnxruntime pillow numpy
El modelo (352 MB) se descarga solo la primera vez a modelos/clip_vision.onnx
"""
import sys
import urllib.request
from pathlib import Path

import numpy as np

BASE_DIR = Path(__file__).resolve().parent
MODELO_DIR = BASE_DIR / "modelos"
MODELO_ONNX = MODELO_DIR / "clip_vision.onnx"
CENTROS = BASE_DIR / "escenas_modelo.npz"
URL_MODELO = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main/onnx/vision_model.onnx"

# normalizacion propia de CLIP
MEDIA = np.array([0.48145466, 0.45782750, 0.40821073], dtype=np.float32)
DESV = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
LADO = 224

_sesion = None


def _descargar_modelo():
    MODELO_DIR.mkdir(exist_ok=True)
    print("Descargando el modelo de vision (352 MB). Solo ocurre la primera vez...")

    def progreso(bloques, tam, total):
        if total > 0:
            hecho = min(bloques * tam, total)
            sys.stdout.write(f"\r  {hecho / 1e6:.0f} de {total / 1e6:.0f} MB")
            sys.stdout.flush()

    tmp = MODELO_ONNX.with_suffix(".parcial")
    urllib.request.urlretrieve(URL_MODELO, tmp, progreso)
    tmp.rename(MODELO_ONNX)
    print("\n  Listo.")


def _cargar():
    global _sesion
    if _sesion is not None:
        return _sesion
    import onnxruntime
    if not MODELO_ONNX.exists():
        _descargar_modelo()
    _sesion = onnxruntime.InferenceSession(str(MODELO_ONNX), providers=["CPUExecutionProvider"])
    return _sesion


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


def precargar(verboso=True):
    """Descarga y carga el modelo antes de empezar un lote, para que no se pare a
    mitad. Devuelve True si el clasificador queda listo para usarse."""
    if not CENTROS.exists():
        return False
    try:
        primera = not MODELO_ONNX.exists()
        _cargar()
        if verboso and primera:
            print("Clasificador de escenas listo.")
        return True
    except Exception as e:
        if verboso:
            print(f"Clasificador de escenas no disponible ({type(e).__name__}): se sigue sin el.")
        return False


def clasificar(rutas, temperatura=25.0):
    """Clasifica el conjunto de fotogramas de un envio.

    Devuelve (etiqueta, confianza, detalle) o (None, 0.0, motivo) si no puede.
    Promedia los vectores de todos los fotogramas: interesa la escena del envio,
    no la de cada cuadro suelto.
    """
    if not CENTROS.exists():
        return None, 0.0, "sin entrenar"
    if not rutas:
        return None, 0.0, "sin fotogramas"
    datos = np.load(CENTROS, allow_pickle=True)
    clases = list(datos["clases"])
    centros = datos["centros"].astype(np.float32)
    vs = vectores(rutas)
    if len(vs) == 0:
        return None, 0.0, "no se pudo leer ningun fotograma"
    medio = vs.mean(axis=0)
    medio /= (np.linalg.norm(medio) or 1.0)
    sim = centros @ medio
    exp = np.exp((sim - sim.max()) * temperatura)
    prob = exp / exp.sum()
    i = int(np.argmax(prob))
    orden = np.argsort(-prob)[:3]
    detalle = ", ".join(f"{clases[j]} {prob[j]:.2f}" for j in orden)
    return clases[i], float(prob[i]), detalle


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
