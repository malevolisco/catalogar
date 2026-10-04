# -*- coding: utf-8 -*-
"""
paginas_word.py - Cuantas paginas ocupa un script pegado en un Word en blanco.

El campo de script del archivo se corta pasadas dos paginas de Word, asi que no vale contar
caracteres: hay que reproducir la maquetacion. Se simula lo que hace el Word en español con su
plantilla por defecto (A4, margenes de 2,5 cm arriba y abajo y 3 cm a los lados, Calibri 11,
interlineado 1,08 y 8 pt tras cada parrafo) con las anchuras reales de la fuente: Calibri en
Windows o Carlito (misma metrica) en Linux. Sin ninguna de las dos, una anchura media, que se
desvia un 5 % arriba o abajo. Comprobado contra un Word real: acierta la pagina en la que cae el
corte con una linea de margen.

Cada agencia pega distinto. Reuters pega linea a linea (un parrafo por plano, con el espacio de 8 pt
detras de cada uno), y se mide asi, solo el script. AP pega el texto entero en UN solo parrafo (titular
de aviso "Editors / Producers...", SHOTLIST, STORYLINE y el pie "Clients are reminded..." seguidos), que
cabe mucho mas en dos paginas: se mide todo el texto junto, como un parrafo, y se suman los caracteres
del aviso y el pie fijos de AP si el texto extraido no los trae (ap_script_extra_caracteres, 360).

Claves de config.json (opcionales): script_max_paginas (2), word_margen_vertical_cm (2.5),
word_margen_horizontal_cm (3), ap_script_extra_caracteres (360).

    from paginas_word import medir, alerta_script
    m = medir(texto)                     # {"paginas": 2.3, "caracteres": 9100, "lineas": 112}
    alerta_script(ficha)                 # pone ficha["alerta"] si el script no cabe

    python paginas_word.py debug\\4359_volcado.txt    para comprobar un envio contra Word
"""
import re
import sys
from pathlib import Path

# --- la pagina de Word, en puntos (1 cm = 28,35 pt) ---
CM = 28.35
A4_ANCHO, A4_ALTO = 595.3, 841.9
CFG = {"script_max_paginas": 2.0, "word_margen_vertical_cm": 2.5, "word_margen_horizontal_cm": 3.0,
       "ap_script_extra_caracteres": 360.0}
# lo fijo que AP pone delante y detras del script y que se pega con el (si el texto extraido no lo trae)
AP_FIJO_RE = re.compile(r"Editors\s*/\s*Producers|Clients are reminded", re.I)
CUERPO = 11.0                                  # Calibri 11
INTERLINEADO = 1.08
ESPACIO_PARRAFO = 8.0                          # "Despues: 8 pto"
ALTURA_LINEA = CUERPO * 1.2207 * INTERLINEADO  # Calibri: (ascent 1950 + descent 550) / 2048 em -> 14,5 pt
ANCHO_MEDIO = CUERPO * 0.49                    # si no hay fuente: media de Calibri en texto de agencia

# Donde empieza y donde acaba el script dentro del texto que saca el extractor
INICIO_RE = re.compile(r"^[^A-Za-z\n]{0,6}(VIDEO SHOWS|SHOWS|SHOTLIST|STORY(?:LINE)?|DOPESHEET)\b", re.M)
# "RESTRICTIONS SUMMARY:" es la primera linea del shotlist de AP (va dentro del script, no lo cierra)
FIN_RE = re.compile(r"^(Details|DETAILS|Restrictions(?!\s+SUMMARY)|RESTRICTIONS(?!\s+SUMMARY)|Video Metadata|Edit No|Usage Terms|"
                    r"Story No|Show Scene List|Video Transcript|VIEW LESS|VIEW MORE|FREE TO ME|Download|"
                    r"Add To Collection|Share Item|Disclaimer)\b", re.M)

_ANCHOS = None      # cache: {caracter: anchura en pt} o {} si no hay fuente


def configurar(cfg):
    """Toma de config.json las claves que afectan a la medida."""
    for k in CFG:
        if k in cfg and cfg[k] not in (None, ""):
            CFG[k] = float(cfg[k])


def ancho_texto():
    return A4_ANCHO - 2 * CFG["word_margen_horizontal_cm"] * CM     # 425 pt con 3 cm


def alto_texto():
    return A4_ALTO - 2 * CFG["word_margen_vertical_cm"] * CM        # 700 pt con 2,5 cm


def _rutas_fuente():
    yield from Path(r"C:\Windows\Fonts").glob("calibri.ttf")
    yield from (Path.home() / "AppData/Local/Microsoft/Windows/Fonts").glob("calibri.ttf")
    for base in ("/usr/share/fonts", "/usr/local/share/fonts", str(Path.home() / ".fonts")):
        yield from Path(base).rglob("Carlito-Regular.ttf")
        yield from Path(base).rglob("calibri.ttf")


def _cargar_anchos():
    """Anchura de cada caracter a 11 pt, leida de la fuente. {} si no hay fuente o fontTools."""
    global _ANCHOS
    if _ANCHOS is not None:
        return _ANCHOS
    _ANCHOS = {}
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return _ANCHOS
    for ruta in _rutas_fuente():
        try:
            f = TTFont(str(ruta))
            upem = f["head"].unitsPerEm
            hmtx, cmap = f["hmtx"].metrics, f.getBestCmap()
            _ANCHOS = {chr(cp): hmtx[g][0] * CUERPO / upem for cp, g in cmap.items() if g in hmtx}
            _ANCHOS["\u00a0"] = _ANCHOS.get(" ", ANCHO_MEDIO)
            break
        except Exception:
            continue
    return _ANCHOS


def fuente_en_uso():
    """Para decirlo en pantalla: 'Calibri', 'Carlito' o 'anchura media'."""
    for ruta in _rutas_fuente():
        if ruta.exists():
            return "Calibri" if "calibri" in ruta.name.lower() else "Carlito"
    return "anchura media"


def _ancho(texto, anchos):
    if anchos:
        return sum(anchos.get(c, ANCHO_MEDIO) for c in texto)
    return len(texto) * ANCHO_MEDIO


def lineas_parrafo(parrafo, anchos):
    """Lineas que ocupa un parrafo con el ajuste de Word: corta en los espacios, y una palabra que
    no cabe entera en una linea se parte por letras."""
    if not parrafo.strip():
        return 1                                   # parrafo vacio: una linea en blanco
    lineas, actual = 1, 0.0
    espacio = _ancho(" ", anchos)
    for palabra in parrafo.split(" "):
        w = _ancho(palabra, anchos)
        if w > ancho_texto():                        # una tira sin espacios mas larga que la linea
            for c in palabra:
                wc = _ancho(c, anchos)
                if actual + wc > ancho_texto():
                    lineas, actual = lineas + 1, 0.0
                actual += wc
            actual += espacio
            continue
        sep = espacio if actual > 0 else 0.0
        if actual + sep + w > ancho_texto():
            lineas, actual = lineas + 1, w
        else:
            actual += sep + w
    return lineas


def extraer_script(texto):
    """La parte del texto de la ficha que se pega como script: desde SHOWS/SHOTLIST/STORY hasta antes
    de Details, Restrictions o Video Metadata. Si no encuentra las marcas, el texto entero."""
    t = texto or ""
    m = INICIO_RE.search(t)
    ini = m.start() if m else 0
    m = FIN_RE.search(t, ini + 1)
    fin = m.start() if m else len(t)
    return t[ini:fin].strip("\n")


def medir(texto, solo_script=True, agencia=""):
    """Paginas de Word que ocupa el texto (o su script) pegado en un documento en blanco.
    agencia="AP": el texto entero como un solo parrafo, que es como se pega el script de AP."""
    if (agencia or "").upper() == "AP":
        t = " ".join((texto or "").split())            # todo seguido, en un parrafo
        if not AP_FIJO_RE.search(t):
            t += " " + "x" * int(CFG["ap_script_extra_caracteres"])
    else:
        t = extraer_script(texto) if solo_script else (texto or "")
    t = t.replace("\r\n", "\n").replace("\t", "    ")
    # Al pegar desde el navegador, Word hace un parrafo por bloque y no deja lineas en blanco entre
    # ellos: las lineas vacias del texto extraido se quitan para medir lo mismo que se pega.
    t = re.sub(r"\n[ \u3000]*\n+", "\n", t).strip("\n")
    anchos = _cargar_anchos()
    parrafos = t.split("\n")
    lineas = sum(lineas_parrafo(p, anchos) for p in parrafos)
    alto = lineas * ALTURA_LINEA + len(parrafos) * ESPACIO_PARRAFO
    return {"paginas": round(alto / alto_texto(), 2), "caracteres": len(t), "lineas": lineas,
            "parrafos": len(parrafos)}


def alerta_script(ficha, max_paginas=None):
    """Mide el script de una ficha extraida. Deja ficha["script_paginas"] y, si pasa del tope,
    ficha["alerta"] con el enlace del envio. Devuelve la medida."""
    max_paginas = CFG["script_max_paginas"] if max_paginas is None else float(max_paginas)
    m = medir(ficha.get("texto") or "", agencia=ficha.get("agencia") or "")
    ficha["script_paginas"] = m["paginas"]
    if max_paginas and m["paginas"] > max_paginas:
        ficha["alerta"] = (f"ALERTA: Script cortado ({m['paginas']:g} paginas de Word, el archivo admite "
                           f"{max_paginas:g}). Copiar script de este link: {ficha.get('url') or '(sin enlace)'}")
    return m


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    from volcar import leer_volcado
    for ruta in sys.argv[1:]:
        ficha = leer_volcado(ruta)
        m = medir(ficha.get("texto") or "", agencia=ficha.get("agencia") or "")
        print(f"{Path(ruta).name}: {m['paginas']} paginas de Word · {m['caracteres']} caracteres · "
              f"{m['lineas']} lineas en {m['parrafos']} parrafos · medido con {fuente_en_uso()}"
              + (" · como un solo parrafo (AP)" if (ficha.get("agencia") or "").upper() == "AP" else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
