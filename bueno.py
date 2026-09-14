# -*- coding: utf-8 -*-
"""
bueno.py - Marca una ficha como aprobada, para que sirva de ejemplo al redactor.

Uso:
    python bueno.py 4682902             aprueba la ultima ficha de ese envio tal cual salio
    python bueno.py 4682902 --editar    la abre en el Bloc de notas para corregirla antes de aprobar
    python bueno.py --pegar             pega una ficha a mano (la del atajo del navegador, por ejemplo)
    python bueno.py --pegar ficha.txt   la misma, leyendola de un fichero
    python bueno.py --listar            lista las fichas aprobadas
    python bueno.py --quitar 4682902    borra esa ficha aprobada

Antes de guardar pasa el validador y, si hay avisos, pregunta. Un ejemplo aprobado
pesa mas que una regla: lo que apruebe se repetira.
"""
import csv
import os
import sys
import argparse
import subprocess
import tempfile
from pathlib import Path

from redactor import anadir_ejemplo, listar_ejemplos, quitar_ejemplo, validar

BASE_DIR = Path(__file__).resolve().parent
CSV_PATH = BASE_DIR / "fichas.csv"
CAMPOS = ("ENVIO", "NAME", "COMMENT", "RESTRICCIONES")

PLANTILLA = """# Corrija lo que quiera, guarde el fichero y cierre el Bloc de notas.
# Las lineas que empiezan por # no se guardan.
# Deje cada campo en su linea; el COMMENT puede ocupar varias.
ENVIO: {ENVIO}
NAME: {NAME}
COMMENT: {COMMENT}
RESTRICCIONES: {RESTRICCIONES}
"""


def buscar_en_csv(numero):
    """Ultima fila del CSV cuyo ENVIO empieza por ese numero."""
    if not CSV_PATH.exists():
        return None
    numero = str(numero).strip()
    encontrada = None
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as f:
        for fila in csv.DictReader(f, delimiter=";"):
            envio = (fila.get("ENVIO") or "").strip()
            if envio.split("\u00b7")[0].strip().lstrip("0") == numero.lstrip("0"):
                encontrada = fila
    return encontrada


def parsear(texto):
    """Lee un bloque ENVIO/NAME/COMMENT/RESTRICCIONES. Tolera campos de varias lineas."""
    campos, actual = {}, None
    for linea in texto.splitlines():
        if linea.strip().startswith("#"):
            continue
        etiqueta = next((c for c in CAMPOS if linea.upper().startswith(c + ":")), None)
        if etiqueta:
            actual = etiqueta
            campos[actual] = linea.split(":", 1)[1].strip()
        elif actual and linea.strip():
            campos[actual] = (campos[actual] + " " + linea.strip()).strip()
    return {c: campos.get(c, "") for c in CAMPOS}


def editar(campos):
    """Abre la ficha en el editor del sistema y devuelve lo que quede al cerrarlo."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(PLANTILLA.format(**{c: campos.get(c, "") or "" for c in CAMPOS}))
        ruta = f.name
    editor = os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "nano")
    print(f"Abriendo {editor}. Corrija, guarde y cierre para continuar.")
    try:
        subprocess.run([editor, ruta], check=False)
    except FileNotFoundError:
        print(f"No encuentro el editor '{editor}'. Edite a mano este fichero y vuelva a lanzarlo:\n  {ruta}")
        sys.exit(2)
    texto = Path(ruta).read_text(encoding="utf-8")
    try:
        os.unlink(ruta)
    except OSError:
        pass
    return parsear(texto)


def guardar(campos, forzar=False):
    if not campos.get("NAME") or not campos.get("COMMENT"):
        print("Falta el NAME o el COMMENT: no se guarda.")
        sys.exit(2)
    avisos = validar(campos)
    if avisos:
        print("\nEl validador ve esto:")
        for a in avisos:
            print("  -", a)
        if not forzar:
            print("\nUn ejemplo aprobado se repite: si el aviso es correcto, corrija antes de guardar.")
            if input("Guardar igualmente? (s/N) ").strip().lower() not in ("s", "si", "s\u00ed"):
                print("No se ha guardado.")
                return
    n, aviso = anadir_ejemplo(campos)
    print(f"\nAprobada. Fichas de referencia: {n}.")
    print(f"  NAME: {campos['NAME']}")
    if aviso:
        print("Aviso:", aviso)


def main():
    ap = argparse.ArgumentParser(description="Aprueba fichas como ejemplo")
    ap.add_argument("numero", nargs="?", help="numero de envio ya catalogado")
    ap.add_argument("--editar", "-e", action="store_true", help="corregir antes de aprobar")
    ap.add_argument("--pegar", "-p", nargs="?", const="-", metavar="FICHERO",
                    help="aprobar una ficha escrita a mano; sin fichero, se abre el editor")
    ap.add_argument("--listar", "-l", action="store_true")
    ap.add_argument("--quitar", "-q", metavar="NUMERO")
    ap.add_argument("--forzar", "-f", action="store_true", help="guardar aunque haya avisos")
    args = ap.parse_args()

    if args.listar:
        ejemplos = listar_ejemplos()
        if not ejemplos:
            print("Sin fichas aprobadas.")
            return
        for i, e in enumerate(ejemplos, 1):
            print(f"{i}. {e['envio'] or e['numero']}\n   NAME: {e['name']}\n   COMMENT: {e['comment'][:120]}...")
        print(f"\nTotal: {len(ejemplos)}")
        return

    if args.quitar:
        print("Borrada." if quitar_ejemplo(args.quitar) else "No habia ninguna ficha aprobada con ese numero.")
        return

    if args.pegar:
        if args.pegar == "-":
            campos = editar({c: "" for c in CAMPOS})
        else:
            campos = parsear(Path(args.pegar).read_text(encoding="utf-8"))
        guardar(campos, args.forzar)
        return

    if not args.numero:
        ap.print_help()
        return

    fila = buscar_en_csv(args.numero)
    if not fila:
        print(f"No encuentro el envio {args.numero} en fichas.csv.")
        print("Si la ficha viene del atajo del navegador, use:  python bueno.py --pegar")
        sys.exit(2)
    campos = {c: (fila.get(c) or "").strip() for c in CAMPOS}
    if args.editar:
        campos = editar(campos)
    guardar(campos, args.forzar)


if __name__ == "__main__":
    main()
