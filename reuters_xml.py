# -*- coding: utf-8 -*-
"""
reuters_xml.py - Lee el XML de un envio de Reuters Connect (boton "XML" de la ficha, formato NewsML-G2).

Trae lo mismo que la pagina, pero ordenado y sin depender de como este dibujada: Edit No, version,
slug, titular, fecha, guion completo (SHOWS, planos, STORY), restricciones y duracion. extractor.py lo
usa en vez del texto de la pagina cuando lo consigue (reuters_xml en config.json); si algo falla, se
queda con la pagina, como antes.

    python reuters_xml.py fichero.XML      ensena el texto que veria el modelo
"""
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

NS = {"n": "http://iptc.org/std/nar/2006-10-01/", "x": "http://www.w3.org/1999/xhtml"}


def _texto(el):
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def _duracion(segundos):
    s = int(round(segundos))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def leer(datos):
    """bytes o str del XML -> dict con numero, rev, slug, headline, fecha (dd/mm/aaaa), restricciones,
    duracion (hh:mm:ss), guion (lista de parrafos) y texto (como el de la pagina). ValueError si no es un
    XML de Reuters con guion."""
    try:
        raiz = ET.fromstring(datos)
    except ET.ParseError as e:
        raise ValueError(f"XML no valido ({e})")
    paquete = raiz.find(".//n:packageItem", NS)
    if paquete is None:
        raise ValueError("No es un XML de Reuters (sin packageItem)")
    meta = paquete.find("n:contentMeta", NS)
    numero = _texto(meta.find("n:altId[@type='idType:editNumber']", NS)) if meta is not None else ""
    slug = _texto(meta.find("n:slugline", NS)) if meta is not None else ""
    headline = _texto(meta.find("n:headline", NS)) if meta is not None else ""
    restricciones = _texto(paquete.find("n:rightsInfo/n:usageTerms", NS))
    creado = _texto(paquete.find("n:itemMeta/n:firstCreated", NS))
    fecha = ""
    try:
        fecha = datetime.strptime(creado[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        pass
    # el guion: el newsItem con texto (inlineXML), parrafo a parrafo
    guion = []
    for item in raiz.findall(".//n:newsItem", NS):
        cuerpo = item.find(".//n:inlineXML//x:body", NS)
        if cuerpo is None:
            continue
        guion = [" ".join("".join(p.itertext()).split()) for p in cuerpo.iter(f"{{{NS['x']}}}p")]
        guion = [p for p in guion if p]
        if guion:
            break
    if not guion:
        raise ValueError("El XML no trae el guion")
    # duracion: la mas larga de los videos (las versiones con y sin claqueta difieren unos segundos)
    durs = []
    for rc in raiz.iter(f"{{{NS['n']}}}remoteContent"):
        if (rc.get("contenttype") or "").startswith("video/") and rc.get("duration"):
            try:
                durs.append(float(rc.get("duration")))
            except ValueError:
                pass
    duracion = _duracion(max(durs)) if durs else ""
    version = paquete.get("version") or ""
    partes = [headline, ""] + [p + "\n" for p in guion]
    if restricciones:
        partes += ["Restrictions", "Full Restriction Details", restricciones, ""]
    partes += ["Details"]
    if duracion:
        partes += ["Duration", duracion]
    if numero:
        partes += ["Edit No", numero]
    if version:
        partes += ["Revision", version]
    return {"numero": numero, "rev": version, "slug": slug, "headline": headline, "fecha": fecha,
            "restricciones": restricciones, "duracion": duracion, "guion": guion,
            "texto": "\n".join(partes).strip()}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    with open(sys.argv[1], "rb") as f:
        d = leer(f.read())
    print(f"Edit No {d['numero']} · v{d['rev']} · {d['fecha']} · {d['slug']} · {d['duracion']}\n")
    print(d["texto"])
