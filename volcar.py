"""volcar.py - Saca a fichero todo lo que la herramienta ve de un envio, para poder mirarlo.

No usa el volcado interno del extractor (que se pierde entero si falla la captura de pantalla):
escribe el texto y el HTML por su cuenta, en UTF-8, y avisa en pantalla de lo que ha guardado.

    python volcar.py 2656                  el mas reciente con ese numero
    python volcar.py 2656 14/09/2026       el de esa fecha
    python volcar.py 4683007               AP funciona igual

Deja en la carpeta debug\\:
    <numero>_volcado.txt   datos de la ficha + el texto tal cual lo lee el redactor
    <numero>_pagina.html   el HTML de la pagina, para ver donde esta cada cosa

El _volcado.txt lo entiende tambien demo_local.py: con esos ficheros se redacta sin abrir el
navegador, que es lo que hace falta para probar un modelo local o ensenar la herramienta sin red.
"""
import io
import json
import sys
from pathlib import Path

# La consola de Windows usa cp1252 y revienta con ideogramas o con el espacio ancho U+3000.
for flujo in ("stdout", "stderr"):
    try:
        getattr(sys, flujo).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
DEBUG_DIR = BASE_DIR / "debug"
SEPARADOR = "----- TEXTO QUE RECIBE EL REDACTOR -----"


def escribir_volcado(ficha, ruta):
    """Guarda una ficha extraida (el dict que devuelve Extractor.fetch) en un _volcado.txt."""
    texto = ficha.get("texto") or ""
    cabecera = {k: v for k, v in ficha.items() if k not in ("texto", "miniaturas")}
    with io.open(ruta, "w", encoding="utf-8") as f:
        f.write(json.dumps(cabecera, ensure_ascii=False, indent=2))
        f.write(f"\n\n{SEPARADOR}\n\n")
        f.write(texto)
        f.write("\n")


def leer_volcado(ruta):
    """Lee un _volcado.txt y devuelve la ficha tal como la recibiria el redactor."""
    contenido = Path(ruta).read_text(encoding="utf-8")
    if SEPARADOR not in contenido:
        raise ValueError(f"{Path(ruta).name} no es un volcado de volcar.py (falta el separador)")
    cabecera, texto = contenido.split(SEPARADOR, 1)
    ficha = json.loads(cabecera)
    ficha["texto"] = texto.strip("\n")
    return ficha


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    numero = sys.argv[1].strip()
    fecha = sys.argv[2].strip() if len(sys.argv) > 2 else None

    sys.path.insert(0, str(BASE_DIR))
    from extractor import Extractor

    try:
        from config import cargar_config
        cfg = cargar_config()
    except Exception:
        cfg = {}

    DEBUG_DIR.mkdir(exist_ok=True)
    ruta_txt = DEBUG_DIR / f"{numero}_volcado.txt"
    ruta_html = DEBUG_DIR / f"{numero}_pagina.html"

    print(f"Abriendo el navegador para el envio {numero}" + (f" del {fecha}" if fecha else "") + "...")
    try:
        with Extractor(headless=False, debug=True, canal=cfg.get("navegador", "auto"),
                       ruta=cfg.get("navegador_ruta"), miniaturas=0) as ex:
            ficha = ex.fetch(numero, fecha)
            # el HTML se pide aparte, y si falla no se lleva por delante el texto
            try:
                ruta_html.write_text(ex._page.content(), encoding="utf-8")
                html_ok = True
            except Exception as e:
                ruta_html.write_text(f"no se pudo guardar el HTML: {e}", encoding="utf-8")
                html_ok = False
    except Exception as e:
        print(f"\nERROR: {type(e).__name__}: {str(e).splitlines()[0]}")
        if "already in use" in str(e) or "existing browser session" in str(e):
            print("El perfil lo tiene cogido otro Chromium: cierra la ventana 'catalogar - servidor'")
            print("y las ventanas de Chrome que haya abierto, y vuelve a intentarlo.")
        return 2

    escribir_volcado(ficha, ruta_txt)
    texto = ficha.get("texto") or ""

    # resumen en pantalla: lo justo para saber si la ficha viene completa o cortada
    marcas = [m for m in ("SHOTLIST", "SHOWS", "STORY", "RESTRICTIONS", "Video Metadata",
                          "Edit No", "Duration", "SOUNDBITE") if m in texto]
    print()
    print(f"  agencia   {ficha.get('agencia')}")
    print(f"  fecha     {ficha.get('fecha')}  rev {ficha.get('rev')}")
    print(f"  headline  {(ficha.get('headline') or '')[:90]}")
    print(f"  texto     {len(texto)} caracteres, {len(texto.splitlines())} lineas")
    print(f"  marcas    {', '.join(marcas) if marcas else 'NINGUNA (la ficha viene vacia o cortada)'}")
    for aviso in ficha.get("avisos") or []:
        print(f"  aviso     {aviso}")
    print()
    print(f"  guardado  {ruta_txt}")
    print(f"  guardado  {ruta_html}" + ("" if html_ok else "  (vacio: no se pudo leer el HTML)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
