# -*- coding: utf-8 -*-
"""
criterio.py - Cambios del catalogador sobre el criterio base (reglas_patrones.md), guardados aparte.

El criterio base viene con cada version de la app y se sustituye al actualizar. Lo que el catalogador
cambia desde la pagina (editar una linea, quitarla o añadir una nueva despues de otra) se guarda en
criterio_cambios.json, que es suyo y las actualizaciones no tocan. Al redactar, se compone el criterio
final: el base de la version instalada con esos cambios encima.

Cada linea del criterio es una regla y se reconoce por su texto original (una huella). Si una version
nueva cambia una linea que el catalogador habia tocado, su cambio se queda "suelto": no se aplica a
ciegas, la pagina lo enseña con la linea nueva mas parecida para que decida.

    {"reglas_patrones.md": {
        "editados": {huella: {"original": "...", "texto": "..."}},
        "borrados": {huella: {"original": "..."}},
        "nuevos":   [{"id": "n-...", "texto": "...", "despues_de": huella o "", "creado": "2026-10-04"}]
    }}
"""
import difflib
import hashlib
import json
import re
import secrets
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CAMBIOS_PATH = BASE_DIR / "criterio_cambios.json"


# ====================================================================== lineas del criterio
def _huella(texto):
    return hashlib.sha1(" ".join(texto.split()).encode("utf-8")).hexdigest()[:12]


def lineas(texto):
    """Las lineas con contenido del criterio, en orden: {id, texto, sangria, seccion, bloque}.
    seccion: la cabecera === ... === bajo la que esta. bloque: la primera linea de su parrafo
    (A) Hecho con planos..., B) Material hablado...), para agrupar en la pagina."""
    salida, vistas = [], {}
    seccion, bloque, anterior_vacia = "", "", True
    for cruda in texto.splitlines():
        if not cruda.strip():
            anterior_vacia = True
            continue
        limpia = cruda.strip()
        h = _huella(limpia)
        vistas[h] = vistas.get(h, 0) + 1
        ident = h if vistas[h] == 1 else f"{h}~{vistas[h]}"
        es_seccion = limpia.startswith("===") and limpia.endswith("===")
        if es_seccion:
            seccion, bloque = limpia.strip("= ").strip(), ""
        elif anterior_vacia:
            bloque = limpia[:90]
        salida.append({"id": ident, "texto": limpia, "sangria": cruda[:len(cruda) - len(cruda.lstrip())],
                       "seccion": seccion, "bloque": bloque, "es_seccion": es_seccion, "inicio_bloque": anterior_vacia})
        anterior_vacia = False
    return salida


# ====================================================================== cambios guardados
def _leer_todo():
    try:
        datos = json.loads(CAMBIOS_PATH.read_text(encoding="utf-8"))
        return datos if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        return {}


def _cambios(nombre):
    c = _leer_todo().get(nombre) or {}
    return {"editados": dict(c.get("editados") or {}), "borrados": dict(c.get("borrados") or {}),
            "nuevos": list(c.get("nuevos") or [])}


def _guardar(nombre, cambios):
    todo = _leer_todo()
    todo[nombre] = cambios
    tmp = CAMBIOS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(CAMBIOS_PATH)


def hay_cambios(nombre):
    c = _cambios(nombre)
    return bool(c["editados"] or c["borrados"] or c["nuevos"])


# ====================================================================== componer
def componer(nombre, texto):
    """El criterio con los cambios del catalogador aplicados. Respeta las lineas en blanco del base."""
    c = _cambios(nombre)
    if not (c["editados"] or c["borrados"] or c["nuevos"]):
        return texto
    nuevos_tras = {}
    for n in c["nuevos"]:
        nuevos_tras.setdefault(n.get("despues_de") or "", []).append(n)
    existentes = {l["id"] for l in lineas(texto)}
    salida, vistas = [], {}
    # nuevos al principio del todo (despues_de vacio) y los que se han quedado sin su linea de referencia
    for n in nuevos_tras.get("", []):
        salida.append(n["texto"])
    for cruda in texto.splitlines():
        if not cruda.strip():
            salida.append(cruda)
            continue
        limpia = cruda.strip()
        h = _huella(limpia)
        vistas[h] = vistas.get(h, 0) + 1
        ident = h if vistas[h] == 1 else f"{h}~{vistas[h]}"
        sangria = cruda[:len(cruda) - len(cruda.lstrip())]
        if ident in c["borrados"]:
            pass
        elif ident in c["editados"]:
            salida.append(sangria + " ".join(c["editados"][ident]["texto"].split()))
        else:
            salida.append(cruda)
        for n in nuevos_tras.get(ident, []):
            salida.append(sangria + " ".join(n["texto"].split()))
    sueltos = [n for clave, lista in nuevos_tras.items() if clave and clave not in existentes for n in lista]
    if sueltos:
        salida.append("")
        salida.extend(" ".join(n["texto"].split()) for n in sueltos)
    # una linea quitada entre dos parrafos deja dos huecos seguidos: se dejan en uno
    return re.sub(r"\n{3,}", "\n\n", "\n".join(salida)) + ("\n" if texto.endswith("\n") else "")


# ====================================================================== vista para la pagina
def vista(nombre, texto):
    """Lo que necesita la pagina: cada linea con su estado, los nuevos en su sitio y los cambios sueltos."""
    c = _cambios(nombre)
    base = lineas(texto)
    existentes = {l["id"]: l for l in base}
    nuevos_tras = {}
    for n in c["nuevos"]:
        nuevos_tras.setdefault(n.get("despues_de") or "", []).append(n)
    filas = []

    def fila_nueva(n, ref):
        return {"id": n["id"], "texto": n["texto"], "original": "", "estado": "nuevo",
                "seccion": ref.get("seccion", "") if ref else "", "bloque": ref.get("bloque", "") if ref else "",
                "sangria": ref.get("sangria", "") if ref else "", "es_seccion": False, "inicio_bloque": False}

    for n in nuevos_tras.get("", []):
        filas.append(fila_nueva(n, base[0] if base else None))
    for l in base:
        estado, actual = "base", l["texto"]
        if l["id"] in c["borrados"]:
            estado = "borrado"
        elif l["id"] in c["editados"]:
            estado, actual = "editado", c["editados"][l["id"]]["texto"]
        filas.append(dict(l, texto=actual, original=l["texto"], estado=estado))
        for n in nuevos_tras.get(l["id"], []):
            filas.append(fila_nueva(n, l))

    # cambios que ya no encajan: la version nueva ha cambiado (o quitado) la linea original
    sueltos = []
    textos_base = [l["texto"] for l in base]
    for tipo in ("editados", "borrados"):
        for ident, d in c[tipo].items():
            if ident in existentes:
                continue
            parecida = difflib.get_close_matches(d.get("original", ""), textos_base, n=1, cutoff=0.5)
            ref = next((l for l in base if parecida and l["texto"] == parecida[0]), None)
            sueltos.append({"id": ident, "tipo": tipo[:-1], "original": d.get("original", ""),
                            "texto": d.get("texto", ""), "parecida": ref["texto"] if ref else "",
                            "parecida_id": ref["id"] if ref else ""})
    for clave, lista in nuevos_tras.items():
        if clave and clave not in existentes:
            for n in lista:
                sueltos.append({"id": n["id"], "tipo": "nuevo", "original": "", "texto": n["texto"],
                                "parecida": "", "parecida_id": ""})
    resumen = {"editadas": sum(1 for i in c["editados"] if i in existentes),
               "borradas": sum(1 for i in c["borrados"] if i in existentes),
               "nuevas": len(c["nuevos"]), "sueltos": len(sueltos), "total": len(base)}
    return {"fichero": nombre, "lineas": filas, "sueltos": sueltos, "resumen": resumen}


# ====================================================================== operaciones
def _original(texto, ident):
    return next((l["texto"] for l in lineas(texto) if l["id"] == ident), None)


def editar(nombre, texto_base, ident, nuevo):
    """Cambia una linea. Si es una de las nuevas, cambia su texto; si es del base, guarda la edicion.
    Volver a dejarla igual que el original equivale a restaurarla."""
    nuevo = " ".join(str(nuevo or "").split())
    if not nuevo:
        raise ValueError("La regla no puede quedar vacia: para quitarla, usa Quitar")
    c = _cambios(nombre)
    for n in c["nuevos"]:
        if n["id"] == ident:
            n["texto"] = nuevo
            _guardar(nombre, c)
            return
    original = _original(texto_base, ident)
    if original is None:
        raise KeyError("Esa linea ya no esta en el criterio")
    c["borrados"].pop(ident, None)
    if nuevo == " ".join(original.split()):
        c["editados"].pop(ident, None)
    else:
        c["editados"][ident] = {"original": original, "texto": nuevo}
    _guardar(nombre, c)


def quitar(nombre, texto_base, ident):
    c = _cambios(nombre)
    antes = len(c["nuevos"])
    c["nuevos"] = [n for n in c["nuevos"] if n["id"] != ident]
    if len(c["nuevos"]) == antes:
        original = _original(texto_base, ident)
        if original is None:
            raise KeyError("Esa linea ya no esta en el criterio")
        c["editados"].pop(ident, None)
        c["borrados"][ident] = {"original": original}
    _guardar(nombre, c)


def restaurar(nombre, ident):
    """Deshace el cambio sobre una linea del base (vuelve el texto original), o descarta un cambio suelto."""
    c = _cambios(nombre)
    c["editados"].pop(ident, None)
    c["borrados"].pop(ident, None)
    c["nuevos"] = [n for n in c["nuevos"] if n["id"] != ident]
    _guardar(nombre, c)


def anadir(nombre, texto_base, despues_de, texto):
    texto = " ".join(str(texto or "").split())
    if not texto:
        raise ValueError("Regla vacia")
    c = _cambios(nombre)
    if despues_de and _original(texto_base, despues_de) is None and not any(n["id"] == despues_de for n in c["nuevos"]):
        raise KeyError("No encuentro la linea despues de la que añadirla")
    # despues de una nueva: se cuelga de la misma linea del base, justo detras de ella
    ref = next((n for n in c["nuevos"] if n["id"] == despues_de), None)
    nuevo = {"id": "n-" + secrets.token_hex(4), "texto": texto,
             "despues_de": ref["despues_de"] if ref else (despues_de or ""), "creado": date.today().isoformat()}
    if ref:
        c["nuevos"].insert(c["nuevos"].index(ref) + 1, nuevo)
    else:
        c["nuevos"].append(nuevo)
    _guardar(nombre, c)
    return nuevo["id"]


def recolocar(nombre, texto_base, ident, sobre):
    """Un cambio suelto pasa a aplicarse sobre otra linea del base (la nueva version de la que tocaba)."""
    c = _cambios(nombre)
    original = _original(texto_base, sobre)
    if original is None:
        raise KeyError("Esa linea ya no esta en el criterio")
    for n in c["nuevos"]:                   # las nuevas que colgaban de esa linea se van con el cambio
        if n.get("despues_de") == ident:
            n["despues_de"] = sobre
    if ident in c["editados"]:
        d = c["editados"].pop(ident)
        c["borrados"].pop(sobre, None)
        c["editados"][sobre] = {"original": original, "texto": d["texto"]}
    elif ident in c["borrados"]:
        c["borrados"].pop(ident)
        c["editados"].pop(sobre, None)
        c["borrados"][sobre] = {"original": original}
    else:
        n = next((n for n in c["nuevos"] if n["id"] == ident), None)
        if n is None:
            raise KeyError("No hay ningun cambio suelto con ese id")
        n["despues_de"] = sobre
    _guardar(nombre, c)
