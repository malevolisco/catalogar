# -*- coding: utf-8 -*-
"""
medir_dudas.py - Cuenta en cuantos envios de un lote harian falta fotogramas.

Solo extrae y lee el shotlist: no redacta, no captura imagenes y no gasta cuota.
Clasifica cada envio en:

  RESUELTO   el shotlist dice el contexto (rueda de prensa, atril, discurso, sala...)
  DUDA-ACTO  hay SOUNDBITE pero el shotlist no dice en que contexto se habla
  DUDA-PROMO material cedido por quien protagoniza: puede ser un video preparado
  SIN VOZ    no hay SOUNDBITE, no hay nada que decidir

Uso:
    python medir_dudas.py --lote lote.txt
    python medir_dudas.py 1554=09/09/2026 4683554 4683547
"""
import re
import argparse
from pathlib import Path

from config import cargar_config

FECHA_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
ITEM_RE = re.compile(r"^(?:AP)?(\d{7}|\d{4})(?:[=@ ]\s*(\d{2}/\d{2}/\d{4}))?$", re.I)

# El shotlist dice donde y en que acto se habla: no hace falta ver nada.
CONTEXTO = (r"PRESS CONFERENCE|NEWS CONFERENCE|PRESSER|BRIEFING|PODIUM|LECTERN|DOORSTOP|"
            r"MEDIA AVAILABILITY|GAGGLE|INTERVIEW|SPEECH|ADDRESS|REMARKS AT|RALLY|SUMMIT|"
            r"HEARING|TESTIMONY|PARLIAMENT|CONGRESS|SENATE|COURT|SIGNING CEREMONY|"
            r"JOINT STATEMENT|NEWS BRIEFING|SPEAKING TO REPORTERS|TALKING TO REPORTERS")
PROMO = (r"PROMOTIONAL VIDEO|PRODUCT VIDEO|COMPANY HANDOUT|CORPORATE VIDEO|PRESS RELEASE VIDEO|"
         r"PRODUCT LAUNCH|MANDATORY CREDIT|COURTESY")


def leer_trabajos(items, lote):
    brutos = list(items)
    if lote:
        for linea in Path(lote).read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#"):
                brutos.append(linea)
    trabajos = []
    for b in brutos:
        b = b.strip()
        if FECHA_RE.match(b) and trabajos and trabajos[-1][1] is None:
            trabajos[-1] = (trabajos[-1][0], b)
            continue
        m = ITEM_RE.match(b)
        if m:
            trabajos.append((m.group(1), m.group(2)))
    return trabajos


def clasificar(texto):
    t = (texto or "").upper()
    if "SOUNDBITE" not in t:
        return ("SIN VOZ", "")
    if re.search(PROMO, t) and not re.search(rf"\b(?:{CONTEXTO})\b", t):
        m = re.search(PROMO, t)
        return ("DUDA-PROMO", m.group(0))
    m = re.search(rf"\b(?:{CONTEXTO})\b", t)
    if m:
        return ("RESUELTO", m.group(0))
    return ("DUDA-ACTO", "el shotlist no dice donde se habla")


def main():
    ap = argparse.ArgumentParser(description="Cuenta envios que necesitarian fotogramas")
    ap.add_argument("items", nargs="*")
    ap.add_argument("--lote", default=None)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    trabajos = leer_trabajos(args.items, args.lote)
    if not trabajos:
        ap.error("no hay envios que analizar")

    cfg = cargar_config()
    from extractor import Extractor, NeedsLogin, NotFound, AntiBot

    cuenta = {"RESUELTO": 0, "DUDA-ACTO": 0, "DUDA-PROMO": 0, "SIN VOZ": 0}
    filas = []
    with Extractor(headless=cfg["headless"], debug=args.debug, canal=cfg.get("navegador", "auto"),
                   ruta=cfg.get("navegador_ruta")) as ex:
        for numero, fecha in trabajos:
            try:
                ficha = ex.fetch(numero, fecha)
            except (NeedsLogin, AntiBot) as e:
                print(f"ERROR: {e}")
                break
            except Exception as e:
                print(f"{numero}: no se pudo extraer ({type(e).__name__})")
                continue
            clase, motivo = clasificar(ficha.get("texto"))
            cuenta[clase] += 1
            filas.append((numero, clase, motivo, (ficha.get("headline") or "")[:46]))
            print(f"{numero:>8}  {clase:<11} {motivo[:34]:<34} {filas[-1][3]}")

    total = sum(cuenta.values())
    if not total:
        return
    dudas = cuenta["DUDA-ACTO"] + cuenta["DUDA-PROMO"]
    print("\n" + "=" * 62)
    for k in ("RESUELTO", "DUDA-ACTO", "DUDA-PROMO", "SIN VOZ"):
        print(f"{k:<12} {cuenta[k]:>3}  {cuenta[k] / total:>5.0%}")
    print("-" * 62)
    print(f"Necesitarian fotogramas: {dudas} de {total} ({dudas / total:.0%})")
    print(f"A 6 fotogramas por envio dudoso: {dudas * 6} imagenes por lote de {total}")
    print(f"Capturando siempre serian {total * 6}.")


if __name__ == "__main__":
    main()
