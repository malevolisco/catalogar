# -*- coding: utf-8 -*-
"""
catalogar.py - Catalogacion de envios de Reuters Connect, AP Newsroom y EBU News Exchange desde la
consola, de uno en uno, por lotes o a partir de un Excel.

Uso por numero de envio (extraccion en la agencia):
    python catalogar.py 0624
    python catalogar.py 0624 06/09/2026
    python catalogar.py 0624 0611 0605 --fecha 06/09/2026        (fecha comun al lote)
    python catalogar.py 8999=30/08/2026 8783=29/08/2026 0624     (fecha por envio)
    python catalogar.py --lote lote.txt                          (un envio por linea, mismos formatos)
    python catalogar.py --lista "Search Results.csv"             (lista de trabajo de MediaCentral, .csv o .xlsx)
    python catalogar.py 0624 --debug --headed                    (capturas y navegador visible)

Uso por Excel (sin entrar en la agencia; columnas NAME, CONT y opcionalmente ENVIO, FECHA, SLUG):
    python catalogar.py --excel envios.xlsx
    python catalogar.py --excel envios.xlsx --salida resultado.xlsx
    python catalogar.py --excel envios.xlsx --reanudar            (salta las filas ya catalogadas)

Salida: por cada envio las cuatro lineas (ENVIO, NAME, COMMENT, RESTRICCIONES) y los
avisos de forma. Todo se anade a fichas.csv y, por lote, a salida/lote_FECHA_HORA.txt.
En modo Excel, ademas, una copia del libro con las columnas NAME_ARCHIVO, COMMENT,
RESTRICCIONES y AVISOS (<nombre>_catalogado.xlsx). Con "miniaturas" > 0 en config.json
y numero de envio en la fila, se abre el navegador solo para capturar fotogramas.
"""
import re
import csv
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path

from config import cargar_config, ConfigError
from listas import leer_lista, leer_texto, resumen as resumen_lista, ListaError
import paginas_word
import listas
from extractor import Extractor, NeedsLogin, NotFound, AntiBot
from redactor import redactar, formatear, RedactorError, configurar

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "fichas.csv"
SALIDA_DIR = BASE_DIR / "salida"

FECHA_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


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


CREADOS = {}      # numero -> cuando entro en MediaCentral (columna Created de la lista), si se sabe


def leer_trabajos(args):
    """Devuelve lista de (numero, fecha) a partir de argumentos y/o fichero de lote."""
    brutos = list(args.items)
    CREADOS.clear()
    if args.lote:
        for linea in Path(args.lote).read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#"):
                brutos.append(linea)
    if args.lista:
        ruta = Path(args.lista)
        envios, descartes, aviso = leer_lista(ruta.name, ruta.read_bytes())
        print(f"Lista {ruta.name}: {resumen_lista(envios, descartes)}")
        for d in descartes:
            print(f"  fuera  fila {d['fila']}: {d['motivo']}")
        if aviso:
            print("  " + aviso)
        brutos.extend(e["numero"] for e in envios)
        CREADOS.update({e["numero"]: e["creado"] for e in envios if e.get("creado")})
    # los numeros se leen como vengan: "0624 06/09/2026", "0624=06/09/2026", "3213,2314", "AP4681323"
    trabajos, ignorados = leer_texto("\n".join(brutos))
    for x in ignorados:
        print(f"Ignorado: {x}")
    if args.fecha:
        trabajos = [(n, f or args.fecha) for n, f in trabajos]
    return trabajos


def procesar(ex, numero, fecha, cfg):
    t0 = time.time()
    ficha = ex.fetch(numero, fecha, creado=CREADOS.get(numero))
    t1 = time.time()
    campos, avisos, _ = redactar(
        ficha, model=cfg["claude_model"], extra_args=cfg["claude_extra_args"], timeout=cfg["claude_timeout"],
        acortar=cfg.get("acortar_comment", True)
    )
    t2 = time.time()
    avisos = list(avisos) + list(ficha.get("avisos", []))
    if ficha.get("alerta"):
        campos["ALERTA"] = ficha["alerta"]          # el script no cabe en el archivo: se imprime con la ficha
    return campos, avisos, (t1 - t0, t2 - t1)


def abrir_extractor(cfg, args):
    headless = cfg["headless"] and not args.headed
    return Extractor(headless=headless, debug=args.debug, canal=cfg.get("navegador", "auto"),
                     miniaturas=cfg.get("miniaturas", 0), espera_login=cfg.get("espera_login", 60),
                     ruta=cfg.get("navegador_ruta"))


def ruta_lote():
    SALIDA_DIR.mkdir(exist_ok=True)
    return SALIDA_DIR / f"lote_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"


# ====================================================================== modo Excel
def procesar_excel(args, cfg):
    from excel import LibroExcel, ExcelError
    try:
        libro = LibroExcel(args.excel, args.salida, reanudar=args.reanudar)
    except ExcelError as e:
        print(f"ERROR: {e}")
        return 1

    filas = libro.filas()
    if not filas:
        print("El Excel no tiene filas con datos debajo de la cabecera.")
        return 1
    hechas = [f for f in filas if f["hecha"]] if args.reanudar else []
    saltadas = [f for f in filas if not f["hecha"] and f["motivo"]]
    pendientes = [f for f in filas if not f["motivo"] and not (args.reanudar and f["hecha"])]
    print(f"Excel: {len(filas)} filas con datos; {len(pendientes)} a catalogar"
          + (f", {len(hechas)} ya hechas" if hechas else "")
          + (f", {len(saltadas)} incompletas" if saltadas else "") + ".")
    for f in saltadas:
        print(f"  fila {f['fila']}: se salta, {f['motivo']}")
        libro.escribir_error(f["fila"], f["motivo"])
    if not pendientes:
        libro.guardar()
        print("No queda nada por hacer.")
        return 0

    # navegador solo si hay fotogramas que capturar y numeros de envio con que buscarlos
    quiere_fotogramas = int(cfg.get("miniaturas", 0) or 0) > 0
    con_numero = [f for f in pendientes if f["ficha"]["numero"]]
    ex = None
    if quiere_fotogramas and con_numero:
        print(f"Fotogramas activados: se abrira el navegador para {len(con_numero)} envio(s) con numero.")
        if cfg.get("escenas", True):
            import escenas as _escenas
            _escenas.precargar()
        try:
            ex = abrir_extractor(cfg, args)
            ex.open()
        except Exception as e:
            print(f"AVISO: no se pudo abrir el navegador ({type(e).__name__}: {str(e).splitlines()[0][:160]}). "
                  "Se redacta sin fotogramas.")
            ex = None
    elif quiere_fotogramas:
        print("Fotogramas activados en config.json pero ninguna fila trae numero de envio: se redacta sin ellos.")

    bloques, errores, fallidas = [], 0, []
    inicio = time.time()
    ruta = ruta_lote()

    def volcar():
        if bloques:
            ruta.write_text("\n\n".join(bloques) + "\n", encoding="utf-8")

    try:
        for i, f in enumerate(pendientes, 1):
            ficha = f["ficha"]
            fila = f["fila"]
            etiqueta = ficha["numero"] or f"fila {fila}"
            print(f"\n=== [{i}/{len(pendientes)}] {etiqueta} · {ficha['headline'][:70]} ===")
            t0 = time.time()
            if ex is not None and ficha["numero"]:
                try:
                    agencia = ex.fetch(ficha["numero"], ficha["fecha"] or args.fecha or None)
                    ficha["miniaturas"] = agencia.get("miniaturas") or []
                    for k in ("fecha", "slug", "rev", "url"):       # datos que el Excel no trae
                        if not ficha.get(k) and agencia.get(k):
                            ficha[k] = agencia[k]
                    if not ficha["miniaturas"]:
                        ficha["avisos"].append("El visor no dio fotogramas")
                except (NeedsLogin, AntiBot) as e:
                    print(f"AVISO: {e}. Se sigue sin fotogramas para el resto del lote.")
                    ficha["avisos"].append("Sin fotogramas: sesion o antibot")
                    try:
                        ex.close()
                    except Exception:
                        pass
                    ex = None
                except NotFound as e:
                    print(f"AVISO: {e}. Se redacta sin fotogramas.")
                    ficha["avisos"].append("Sin fotogramas: envio no encontrado en la agencia")
                except Exception as e:
                    print(f"AVISO: fallo al capturar fotogramas ({type(e).__name__}). Se redacta sin ellos.")
                    ficha["avisos"].append("Sin fotogramas: fallo del navegador")
            t1 = time.time()
            try:
                campos, avisos, _ = redactar(
                    ficha, model=cfg["claude_model"], extra_args=cfg["claude_extra_args"],
                    timeout=cfg["claude_timeout"], acortar=cfg.get("acortar_comment", True))
            except RedactorError as e:
                msg = str(e).splitlines()[0][:200]
                print(f"ERROR: {msg}")
                bloques.append(f"ENVIO: {etiqueta}\n{msg}")
                libro.escribir_error(fila, msg)
                libro.guardar()
                fallidas.append(fila)
                errores += 1
                volcar()
                continue
            except Exception as e:
                msg = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"
                print(f"ERROR inesperado {msg}\n  Se salta esta fila y sigue el lote.")
                bloques.append(f"ENVIO: {etiqueta}\n{msg}")
                libro.escribir_error(fila, msg)
                libro.guardar()
                fallidas.append(fila)
                errores += 1
                volcar()
                continue
            t2 = time.time()
            avisos = list(avisos) + list(ficha.get("avisos", []))
            texto = formatear(campos, avisos)
            print(texto)
            print(f"[tiempos] fotogramas {t1 - t0:.1f} s · redaccion {t2 - t1:.1f} s")
            bloques.append(texto)
            guardar_csv(campos, avisos)
            libro.escribir(fila, campos, avisos)
            libro.guardar()                   # se guarda tras cada fila: nunca se pierde el lote
            volcar()
    except KeyboardInterrupt:
        print("\nInterrumpido.")
    except Exception as e:
        print(f"\nFallo general ({type(e).__name__}): {e}")
    finally:
        if ex is not None:
            try:
                ex.close()
            except Exception:
                pass
        libro.guardar()

    volcar()
    print(f"\nExcel: {len(bloques)} bloque(s), {errores} error(es), {time.time() - inicio:.0f} s.")
    print(f"Resultado en {libro.destino}" + (f" y copia de texto en {ruta}" if bloques else ""))
    if fallidas:
        print("Fallaron las filas: " + " ".join(str(x) for x in fallidas))
        print(f"Para repetir solo esas:  python catalogar.py --excel {args.excel} --reanudar")
    return 1 if errores and errores == len(pendientes) else 0


# ====================================================================== modo numero de envio
def procesar_numeros(args, cfg):
    trabajos = leer_trabajos(args)
    if not trabajos:
        print("ERROR: no hay envios que procesar")
        return 2

    if args.reanudar:
        hechos = set()
        if CSV_PATH.exists():
            with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
                for fila in csv.DictReader(f, delimiter=";"):
                    hechos.add((fila.get("ENVIO") or "").split("\u00b7")[0].strip().lstrip("0"))
        antes = len(trabajos)
        trabajos = [(n, f) for n, f in trabajos if n.lstrip("0") not in hechos]
        print(f"Reanudar: {antes - len(trabajos)} ya catalogados, quedan {len(trabajos)}.")
        if not trabajos:
            print("No queda nada por hacer.")
            return 0

    bloques, errores, fallidos = [], 0, []
    inicio = time.time()
    ruta = ruta_lote()

    def volcar():
        if bloques:
            ruta.write_text("\n\n".join(bloques) + "\n", encoding="utf-8")
    if cfg.get("miniaturas", 0) and cfg.get("escenas", True):
        import escenas as _escenas
        _escenas.precargar()

    try:
        with abrir_extractor(cfg, args) as ex:
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
                    fallidos.append(numero)
                    errores += 1
                    volcar()
                    continue
                except Exception as e:
                    print(f"ERROR inesperado ({type(e).__name__}): {str(e).splitlines()[0][:200]}")
                    print("  Se salta este envio y sigue el lote.")
                    bloques.append(f"ENVIO: {numero}\nERROR {type(e).__name__}: {str(e).splitlines()[0][:200]}")
                    fallidos.append(numero)
                    errores += 1
                    volcar()
                    continue
                texto = formatear(campos, avisos)
                print(texto)
                print(f"[tiempos] extraccion {t_ext:.1f} s · redaccion {t_red:.1f} s")
                bloques.append(texto)
                guardar_csv(campos, avisos)
                volcar()                      # se guarda tras cada ficha: nunca se pierde el lote
    except KeyboardInterrupt:
        print("\nInterrumpido.")
    except Exception as e:
        print(f"\nFallo general ({type(e).__name__}): {e}")

    if len(trabajos) > 1 and bloques:
        volcar()
        print(f"\nLote: {len(bloques)} bloque(s), {errores} error(es), {time.time() - inicio:.0f} s. Guardado en {ruta}")
        if fallidos:
            print(f"Fallaron {len(fallidos)}: " + " ".join(fallidos))
            print("Para repetir solo esos:  python catalogar.py " + " ".join(fallidos))
            print("Para seguir el lote donde se corto:  python catalogar.py --lote lote.txt --reanudar")
    return 1 if errores and errores == len(trabajos) else 0


def main():
    ap = argparse.ArgumentParser(description="Cataloga envios de Reuters Connect o AP Newsroom por numero o desde un Excel")
    ap.add_argument("items", nargs="*", help="Reuters 0624 (4 cifras) o AP 4681323 (7 cifras); fecha opcional: 0624=06/09/2026")
    ap.add_argument("--fecha", default=None, help="DD/MM/AAAA comun a todo el lote (o a las filas del Excel sin fecha)")
    ap.add_argument("--lote", default=None, help="fichero con un envio por linea")
    ap.add_argument("--lista", default=None, help="lista de trabajo de MediaCentral (.csv o .xlsx): coge el numero que abre cada fila")
    ap.add_argument("--excel", default=None, help="libro .xlsx con columnas NAME y CONT (y ENVIO, FECHA, SLUG opcionales)")
    ap.add_argument("--salida", default=None, help="ruta del Excel de salida (por defecto <nombre>_catalogado.xlsx)")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--reanudar", action="store_true",
                    help="salta lo ya catalogado: fichas.csv por numero, o filas con NAME_ARCHIVO en el Excel de salida")
    args = ap.parse_args()

    if args.excel and (args.items or args.lote or args.lista):
        ap.error("--excel no se combina con numeros de envio, --lote ni --lista")
    if args.salida and not args.excel:
        ap.error("--salida solo tiene sentido con --excel")

    try:
        cfg = cargar_config()
    except ConfigError as e:
        print(e)
        return 1
    configurar(cfg)
    paginas_word.configurar(cfg)
    listas.configurar(cfg)
    if args.lista and not Path(args.lista).exists():
        ap.error(f"no existe el fichero {args.lista}")
    if args.excel:
        sys.exit(procesar_excel(args, cfg))
    try:
        sys.exit(procesar_numeros(args, cfg))
    except ListaError as e:
        print(f"La lista no sirve: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
