# -*- coding: utf-8 -*-
"""
ap_json.py - Lee los datos de un envio de AP Newsroom tal como los recibe su pagina por detras
(la respuesta de /v1/nrsearch/search/item/details).

Trae lo mismo que la ficha, pero entero y ordenado: guion completo (SHOTLIST, planos, STORYLINE),
restricciones (rightsline), titular, slug, fecha de llegada, duracion, ID y fuente. extractor.py lo usa
en vez del texto de la pagina cuando la respuesta llega (ap_datos en config.json); si no, se queda con
la pagina, como antes.

    python ap_json.py datos.json 4689021      ensena el texto que veria el modelo
"""
import html
import json
import re
import sys
from datetime import datetime


def _parrafos(nitf):
    """El NITF del guion (<p>...</p>) en parrafos de texto."""
    t = re.sub(r"<br\s*/?>", "\n", nitf or "", flags=re.I)
    partes = re.split(r"</p\s*>", t, flags=re.I)
    salida = []
    for p in partes:
        p = html.unescape(re.sub(r"<[^>]+>", "", p)).replace("\xa0", " ")
        p = "\n".join(" ".join(l.split()) for l in p.split("\n")).strip()
        salida.append(p)
    while salida and not salida[-1]:
        salida.pop()
    return salida


def _duracion(ms):
    s = int(round(ms / 1000))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def item_de(respuesta, numero):
    """De una respuesta de item/details (dict o texto JSON), el _source del envio con ese numero, o None."""
    if isinstance(respuesta, (str, bytes)):
        try:
            respuesta = json.loads(respuesta)
        except ValueError:
            return None
    for it in (respuesta or {}).get("Items") or []:
        s = it.get("_source") or {}
        if str(numero) in (str(s.get("editorialid") or ""), str(s.get("referenceid") or "")):
            return s
    return None


def leer(fuente):
    """_source de AP -> dict con numero, slug, headline, fecha (dd/mm/aaaa), duracion, restricciones,
    texto (como el de la ficha). ValueError si no trae el guion."""
    guion = _parrafos((fuente.get("script") or {}).get("nitf"))
    if not any(guion):
        raise ValueError("Los datos de AP no traen el guion")
    numero = str(fuente.get("editorialid") or fuente.get("referenceid") or "")
    llegada = fuente.get("arrivaldatetime") or fuente.get("firstcreated") or ""
    fecha, llegada_txt = "", ""
    try:
        dt = datetime.strptime(llegada[:19], "%Y-%m-%dT%H:%M:%S")
        fecha, llegada_txt = dt.strftime("%d/%m/%Y"), dt.strftime("%b %d, %Y %H:%M (GMT)")
    except ValueError:
        pass
    durs = [r.get("totalduration") for r in fuente.get("renditions") or [] if isinstance(r.get("totalduration"), (int, float))]
    duracion = _duracion(max(durs)) if durs else ""
    fuentes = ", ".join(s.get("name", "") for s in fuente.get("sources") or [] if s.get("name"))
    headline = " ".join(str(fuente.get("headline") or "").split())
    slug = " ".join(str(fuente.get("title") or "").split())
    restricciones = " ".join(str(fuente.get("rightsline") or "").split())
    partes = [headline, ""] + guion + ["", "Video Metadata"]
    for etiqueta, valor in (("Slug", slug), ("Arrival Date", llegada_txt), ("Duration", duracion), ("ID", numero),
                            ("Source", fuentes), ("Restrictions", restricciones)):
        if valor:
            partes += [etiqueta, valor]
    texto = re.sub(r"\n{3,}", "\n\n", "\n".join(partes)).strip()
    return {"numero": numero, "slug": slug, "headline": headline, "fecha": fecha, "duracion": duracion,
            "restricciones": restricciones, "texto": texto}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    with open(sys.argv[1], encoding="utf-8") as f:
        datos = json.load(f)
    # vale la respuesta tal cual o el fichero de "Descargar datos de AP" (lista de {url, cuerpo})
    candidatos = [d["cuerpo"] for d in datos if isinstance(d, dict) and str(d.get("url", "")).endswith("item/details")] \
        if isinstance(datos, list) else [datos]
    for c in candidatos:
        fuente = item_de(c, sys.argv[2])
        if fuente:
            d = leer(fuente)
            print(f"ID {d['numero']} · {d['fecha']} · {d['slug']} · {d['duracion']}\n")
            print(d["texto"])
            break
    else:
        print("No hay detalles de ese numero en el fichero")
