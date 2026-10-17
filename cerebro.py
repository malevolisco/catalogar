# -*- coding: utf-8 -*-
"""
cerebro.py - Lo que Catalogator sabe, empaquetado para Catalogator en la nube (nube.py).

La version en la nube no tiene disco propio: cada vez que arranca lee de la bandeja de entrada el ultimo
correo "Cerebro para la nube" que la Raspberry se manda a si misma y redacta con eso. Asi aprende lo
mismo que la Raspberry, con un dia de retraso como mucho:

    ejemplos.md                  las fichas aprobadas
    reglas_extra.md              los ajustes del catalogador
    criterio_cambios.json        los cambios al criterio base
    cola/ejemplos_uso.json       que aprobadas se usan (para no elegir las repetidas o a revisar)
    config.json                  la configuracion SIN ninguna contraseña ni clave

Lo manda salud.py cada noche (nube_cerebro en config.json) y el boton "Mandar a la nube" de Admin.
"""
import io
import json
import re
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ASUNTO = "[Catalogación] Cerebro para la nube"
ADJUNTO = "cerebro.zip"
FICHEROS = ("ejemplos.md", "reglas_extra.md", "criterio_cambios.json", "cola/ejemplos_uso.json")
# por si un dia se añade una clave secreta sin marcarla como tal en admin.py
PARECE_SECRETO_RE = re.compile(r"clave|key|token|password|contrase|secret", re.I)


def config_sin_secretos(cfg):
    """La configuracion sin contraseñas, claves ni tokens."""
    try:
        from admin import SECRETOS
    except Exception:
        SECRETOS = set()
    return {k: v for k, v in (cfg or {}).items()
            if k not in SECRETOS and not PARECE_SECRETO_RE.search(k) and not k.startswith("_")}


def empaquetar(cfg, base=BASE_DIR):
    """El zip con lo que la nube necesita. Solo FICHEROS y la configuracion limpia."""
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre in FICHEROS:
            ruta = Path(base) / nombre
            if ruta.exists():
                z.write(ruta, nombre)
        z.writestr("config.json", json.dumps(config_sin_secretos(cfg), ensure_ascii=False, indent=1))
    return salida.getvalue()


def desempaquetar(datos, base=BASE_DIR):
    """Pone en su sitio lo que trae el zip (solo los nombres conocidos: nada de rutas raras). Devuelve la
    configuracion que trae, o {}."""
    cfg = {}
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        nombres = set(z.namelist())
        for nombre in FICHEROS:
            if nombre in nombres:
                ruta = Path(base) / nombre
                ruta.parent.mkdir(parents=True, exist_ok=True)
                ruta.write_bytes(z.read(nombre))
        if "config.json" in nombres:
            cfg = config_sin_secretos(json.loads(z.read("config.json").decode("utf-8")))
    return cfg


def mandar(cfg):
    """Se manda el cerebro a la propia cuenta de correo. Lanza CorreoError si no se puede."""
    from correo import enviar
    usuario = cfg.get("correo_usuario") or ""
    datos = empaquetar(cfg)
    enviar(cfg, usuario, ASUNTO,
           "Copia de lo que sabe Catalogator para la version en la nube (NUBE.md). "
           "No hace falta hacer nada con este correo.\n",
           adjuntos=[(ADJUNTO, datos)])
    return len(datos)
