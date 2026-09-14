# -*- coding: utf-8 -*-
"""
catalogar.py - Catalogacion de envios de Reuters Connect desde la consola, de uno
en uno o por lotes.

Uso:
    python catalogar.py 0624
    python catalogar.py 0624 06/09/2026
    python catalogar.py 0624 0611 0605 --fecha 06/09/2026        (fecha comun al lote)
    python catalogar.py 8999=30/08/2026 8783=29/08/2026 0624     (fecha por envio)
    python catalogar.py --lote lote.txt                          (un envio por linea, mismos formatos)
    python catalogar.py 0624 --debug --headed                    (capturas y navegador visible)

Salida: por cada envio las cuatro lineas (ENVIO, NAME, COMMENT, RESTRICCIONES) y los
avisos de forma. Todo se añade a fichas.csv y, por lote, a salida/lote_FECHA_HORA.txt.
"""
import re
import csv
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path

from config import cargar_config
from extractor import Extractor, NeedsLogin, NotFound, AntiBot
from redactor import redactar, formatear, RedactorError, configurar

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "fichas.csv"
SALIDA_DIR = BASE_DIR / "salida"

FECHA_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
ITEM_RE = re.compile(r"^(?:AP)?(\d{7}|\d{4})(?:[=@ ]\s*(\d{2}/\d{2}/\d{4}))?$", re.I)


def guardar_csv(campos, avisos):
    nuevo = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if nuevo:
            w.writerow(["fecha_hora", "ENVIO", "NAME", "COMMENT", "RESTRICCIONES", "AVISOS"])
        w.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            campos["ENVIO"], campos["NAME"], campos["COMMENT"], campos["RESTRICCIONES"],
            " | ".join(avisos),
        ])


def leer_trabajos(args):
    """Devuelve lista de (numero, fecha) a partir de argumentos y/o fichero de lote."""
    brutos = list(args.items)
    if args.lote:
        for linea in Path(args.lote).read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#"):
                brutos.append(linea)
    # compatibilidad con "0624 06/09/2026": una fecha suelta se aplica al numero anterior
    trabajos = []
    for b in brutos:
        b = b.strip()
        if FECHA_RE.match(b) and trabajos and trabajos[-1][1] is None:
            trabajos[-1] = (trabajos[-1][0], b)
            continue
        m = ITEM_RE.match(b)
        if not m:
            print(f"Ignorado (formato no reconocido): {b}")
            continue
        trabajos.append((m.group(1), m.group(2)))
    if args.fecha:
        trabajos = [(n, f or args.fecha) for n, f in trabajos]
    return trabajos


def procesar(ex, numero, fecha, cfg):
    t0 = time.time()
    ficha = ex.fetch(numero, fecha)
    t1 = time.time()
    campos, avisos, _ = redactar(
        ficha, model=cfg["claude_model"], extra_args=cfg["claude_extra_args"], timeout=cfg["claude_timeout"],
        acortar=cfg.get("acortar_comment", True)
    )
    t2 = time.time()
    avisos = list(avisos) + list(ficha.get("avisos", []))
    return campos, avisos, (t1 - t0, t2 - t1)


def main():
    ap = argparse.ArgumentParser(description="Cataloga uno o varios envios de Reuters Connect o AP Newsroom")
    ap.add_argument("items", nargs="*", help="Reuters 0624 (4 cifras) o AP 4681323 (7 cifras); fecha opcional: 0624=06/09/2026")
    ap.add_argument("--fecha", default=None, help="DD/MM/AAAA comun a todo el lote")
    ap.add_argument("--lote", default=None, help="fichero con un envio por linea")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    trabajos = leer_trabajos(args)
    if not trabajos:
        ap.error("no hay envios que procesar")
    cfg = cargar_config()
    configurar(cfg)
    headless = cfg["headless"] and not args.headed

    bloques, errores = [], 0
    inicio = time.time()
    if cfg.get("miniaturas", 0) and cfg.get("escenas", True):
        import escenas as _escenas
        _escenas.precargar()

    try:
        with Extractor(headless=headless, debug=args.debug, canal=cfg.get("navegador", "auto"),
                       miniaturas=cfg.get("miniaturas", 0), espera_login=cfg.get("espera_login", 60),
                       ruta=cfg.get("navegador_ruta")) as ex:
            for i, (numero, fecha) in enumerate(trabajos, 1):
                print(f"\n=== [{i}/{len(trabajos)}] envio {numero}{' del ' + fecha if fecha else ''} ===")
                try:
                    campos, avisos, (t_ext, t_red) = procesar(ex, numero, fecha, cfg)
                except (NeedsLogin, AntiBot) as e:
                    print(f"ERROR: {e}")
                    bloques.append(f"ENVIO: {numero}\n{e}\nLote interrumpido.")
                    errores += 1
                    break
                except (NotFound, RedactorError) as e:
                    print(f"ERROR: {e}")
                    bloques.append(f"ENVIO: {numero}\n{e}")
                    errores += 1
                    continue
                texto = formatear(campos, avisos)
                print(texto)
                print(f"[tiempos] extraccion {t_ext:.1f} s · redaccion {t_red:.1f} s")
                bloques.append(texto)
                guardar_csv(campos, avisos)
    except KeyboardInterrupt:
        print("\nInterrumpido.")

    if len(trabajos) > 1 and bloques:
        SALIDA_DIR.mkdir(exist_ok=True)
        ruta = SALIDA_DIR / f"lote_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
        ruta.write_text("\n\n".join(bloques) + "\n", encoding="utf-8")
        print(f"\nLote: {len(bloques)} bloque(s), {errores} error(es), {time.time() - inicio:.0f} s. Guardado en {ruta}")
    sys.exit(1 if errores and errores == len(trabajos) else 0)


if __name__ == "__main__":
    main()
