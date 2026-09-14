# -*- coding: utf-8 -*-
"""
comparar_reglas.py - Compara dos ficheros de criterio (por ejemplo reglas_ligeras.md
y reglas_patrones.md) sobre los mismos envios.

Cada envio se extrae UNA sola vez y se redacta con los dos ficheros, en orden
alternado. Mide tiempos y avisos de validacion, y genera una pagina web para
elegir a ciegas: no se ve que version viene de que fichero hasta que pulsas
"Ver resultado".

Uso:
    python comparar_reglas.py 0624 0611 4681323
    python comparar_reglas.py --lote lote.txt
    python comparar_reglas.py --lote lote.txt --repes 2
    python comparar_reglas.py 0624 --a reglas.md --b reglas_ligeras.md
    python comparar_reglas.py --lote lote.txt --sin-acortar --debug

Salida (carpeta comparacion/):
    comparacion_AAAAMMDD_HHMM.html   pagina para juzgar a ciegas
    comparacion_AAAAMMDD_HHMM.json   datos en bruto
    comparacion_AAAAMMDD_HHMM.txt    todo revelado, para archivo

No toca config.json ni ningun fichero del proyecto.
"""
import re
import sys
import html
import json
import time
import random
import argparse
import statistics
from datetime import datetime
from pathlib import Path

from config import cargar_config
import redactor as R

BASE_DIR = Path(__file__).resolve().parent
SALIDA_DIR = BASE_DIR / "comparacion"

FECHA_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
ITEM_RE = re.compile(r"^(?:AP)?(\d{7}|\d{4})(?:[=@ ]\s*(\d{2}/\d{2}/\d{4}))?$", re.I)


# ---------------------------------------------------------------- entrada

def leer_trabajos(items, lote, fecha_comun):
    """Devuelve lista de (numero, fecha). Mismos formatos que catalogar.py."""
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
        if not m:
            print(f"Ignorado (formato no reconocido): {b}")
            continue
        trabajos.append((m.group(1), m.group(2)))
    if fecha_comun:
        trabajos = [(n, f or fecha_comun) for n, f in trabajos]
    return trabajos


# ---------------------------------------------------------------- redaccion

def redactar_con(fichero, ficha):
    """Redacta la ficha con ese fichero de reglas. Devuelve un dict con el resultado."""
    R.CFG["reglas"] = fichero
    t0 = time.time()
    try:
        campos, avisos, _ = R.redactar(ficha)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "segundos": round(time.time() - t0, 1),
                "name": "", "comment": "", "restricciones": "", "avisos": []}
    return {
        "ok": True,
        "error": None,
        "segundos": round(time.time() - t0, 1),
        "name": campos["NAME"],
        "comment": campos["COMMENT"],
        "restricciones": campos["RESTRICCIONES"],
        "avisos": list(avisos),
    }


# ---------------------------------------------------------------- resumen

def resumir(datos):
    """Estadisticas por fichero. Devuelve dict listo para imprimir o pintar."""
    out = {}
    for lado in ("a", "b"):
        segs, avis, nlen, clen, fallos = [], [], [], [], 0
        for c in datos["casos"]:
            r = c[lado]
            if not r["ok"]:
                fallos += 1
                continue
            segs.append(r["segundos"])
            avis.append(len(r["avisos"]))
            nlen.append(len(r["name"]))
            clen.append(len(r["comment"]))
        out[lado] = {
            "fichero": datos["fichero_" + lado],
            "n": len(segs),
            "fallos": fallos,
            "seg_mediana": round(statistics.median(segs), 1) if segs else 0,
            "seg_media": round(statistics.mean(segs), 1) if segs else 0,
            "seg_min": min(segs) if segs else 0,
            "seg_max": max(segs) if segs else 0,
            "seg_total": round(sum(segs), 0) if segs else 0,
            "avisos_media": round(statistics.mean(avis), 2) if avis else 0,
            "avisos_total": sum(avis),
            "sin_avisos": sum(1 for x in avis if x == 0),
            "name_media": round(statistics.mean(nlen)) if nlen else 0,
            "comment_media": round(statistics.mean(clen)) if clen else 0,
        }
    iguales_name = sum(1 for c in datos["casos"]
                       if c["a"]["ok"] and c["b"]["ok"] and c["a"]["name"] == c["b"]["name"])
    iguales_todo = sum(1 for c in datos["casos"]
                       if c["a"]["ok"] and c["b"]["ok"]
                       and c["a"]["name"] == c["b"]["name"] and c["a"]["comment"] == c["b"]["comment"])
    validos = sum(1 for c in datos["casos"] if c["a"]["ok"] and c["b"]["ok"])
    out["comparables"] = validos
    out["iguales_name"] = iguales_name
    out["iguales_todo"] = iguales_todo
    return out


def imprimir_resumen(datos):
    r = resumir(datos)
    print("\n" + "=" * 70)
    print("RESUMEN OBJETIVO")
    print("=" * 70)
    cab = f"{'':22} {'A':>18} {'B':>18}"
    print(cab)
    print(f"{'fichero':22} {r['a']['fichero']:>18} {r['b']['fichero']:>18}")
    filas = [
        ("redacciones", "n"), ("fallos", "fallos"),
        ("segundos mediana", "seg_mediana"), ("segundos media", "seg_media"),
        ("segundos min", "seg_min"), ("segundos max", "seg_max"),
        ("segundos total", "seg_total"),
        ("avisos por ficha", "avisos_media"), ("avisos totales", "avisos_total"),
        ("fichas sin avisos", "sin_avisos"),
        ("NAME caracteres", "name_media"), ("COMMENT caracteres", "comment_media"),
    ]
    for etiqueta, clave in filas:
        print(f"{etiqueta:22} {str(r['a'][clave]):>18} {str(r['b'][clave]):>18}")
    print("-" * 70)
    print(f"Comparaciones validas: {r['comparables']}")
    print(f"NAME identico en las dos versiones: {r['iguales_name']} de {r['comparables']}")
    print(f"NAME y COMMENT identicos: {r['iguales_todo']} de {r['comparables']}")
    if r["comparables"] and r["iguales_todo"] == r["comparables"]:
        print("Los dos ficheros dan exactamente lo mismo: la eleccion es indiferente.")
    print("=" * 70)


# ---------------------------------------------------------------- salidas

def escribir_txt(datos, ruta):
    L = []
    L.append(f"Comparacion de criterio - {datos['fecha']}")
    L.append(f"A = {datos['fichero_a']}   B = {datos['fichero_b']}")
    L.append(f"Modelo: {datos['modelo']}   Acortar COMMENT: {datos['acortar']}")
    L.append("")
    for c in datos["casos"]:
        L.append("=" * 70)
        titulo = f"ENVIO {c['numero']}"
        if c["fecha_envio"]:
            titulo += f" del {c['fecha_envio']}"
        if datos["repes"] > 1:
            titulo += f"  (pasada {c['pasada']})"
        L.append(titulo)
        if c["headline"]:
            L.append(f"headline: {c['headline']}")
        L.append("")
        for lado in ("a", "b"):
            r = c[lado]
            L.append(f"--- {lado.upper()} = {datos['fichero_' + lado]}  ({r['segundos']} s) ---")
            if not r["ok"]:
                L.append(f"ERROR: {r['error']}")
            else:
                L.append(f"NAME: {r['name']}")
                L.append(f"COMMENT: {r['comment']}")
                L.append(f"RESTRICCIONES: {r['restricciones']}")
                L.append(f"AVISOS: {' | '.join(r['avisos']) if r['avisos'] else 'ninguno'}")
            L.append("")
    r = resumir(datos)
    L.append("=" * 70)
    L.append("RESUMEN")
    for lado in ("a", "b"):
        d = r[lado]
        L.append(f"{lado.upper()} {d['fichero']}: {d['n']} redacciones, mediana {d['seg_mediana']} s, "
                 f"{d['avisos_media']} avisos por ficha, {d['sin_avisos']} fichas limpias, {d['fallos']} fallos")
    L.append(f"NAME identico: {r['iguales_name']} de {r['comparables']}")
    ruta.write_text("\n".join(L) + "\n", encoding="utf-8")


def bloque_ficha(r):
    if not r["ok"]:
        return f'<p class="err">ERROR: {html.escape(r["error"])}</p>'
    p = []
    p.append(f'<p><b>NAME</b><br><span class="campo">{html.escape(r["name"])}</span> '
             f'<span class="chars">{len(r["name"])} c · {len(r["name"].split())} p</span></p>')
    p.append(f'<p><b>COMMENT</b><br><span class="campo">{html.escape(r["comment"])}</span> '
             f'<span class="chars">{len(r["comment"])} c</span></p>')
    p.append(f'<p><b>RESTRICCIONES</b><br><span class="campo">{html.escape(r["restricciones"])}</span></p>')
    avisos = ("<br>".join(html.escape(a) for a in r["avisos"])) if r["avisos"] else "ninguno"
    p.append(f'<div class="oculto"><p class="meta">{r["segundos"]} s</p>'
             f'<p class="avisos"><b>Avisos:</b><br>{avisos}</p></div>')
    return "\n".join(p)


def escribir_html(datos, ruta):
    r = resumir(datos)
    tarjetas = []
    for i, c in enumerate(datos["casos"]):
        izq, der = (("a", "b") if c["izquierda"] == "a" else ("b", "a"))
        titulo = f"Envio {html.escape(c['numero'])}"
        if c["fecha_envio"]:
            titulo += f" del {html.escape(c['fecha_envio'])}"
        if datos["repes"] > 1:
            titulo += f" · pasada {c['pasada']}"
        head = html.escape(c["headline"] or c["slug"] or "")
        tarjetas.append(f"""
<section class="caso" data-i="{i}" data-izq="{izq}" data-der="{der}">
  <h2>{titulo}</h2>
  <p class="head">{head}</p>
  <div class="par">
    <div class="col"><h3>Version 1 <span class="clave oculto">({html.escape(datos['fichero_' + izq])})</span></h3>
      {bloque_ficha(c[izq])}</div>
    <div class="col"><h3>Version 2 <span class="clave oculto">({html.escape(datos['fichero_' + der])})</span></h3>
      {bloque_ficha(c[der])}</div>
  </div>
  <div class="voto">
    <button data-v="izq">Prefiero la 1</button>
    <button data-v="der">Prefiero la 2</button>
    <button data-v="igual">Iguales</button>
    <button data-v="ninguna">Ninguna sirve</button>
    <span class="marca"></span>
  </div>
</section>""")

    filas = ""
    for etiqueta, clave in [("redacciones", "n"), ("fallos", "fallos"),
                            ("segundos mediana", "seg_mediana"), ("segundos min", "seg_min"),
                            ("segundos max", "seg_max"), ("segundos total", "seg_total"),
                            ("avisos por ficha", "avisos_media"), ("fichas sin avisos", "sin_avisos"),
                            ("NAME caracteres", "name_media"), ("COMMENT caracteres", "comment_media")]:
        filas += f"<tr><td>{etiqueta}</td><td>{r['a'][clave]}</td><td>{r['b'][clave]}</td></tr>"

    tabla = (f'<table><tr><th>medida</th><th>{html.escape(datos["fichero_a"])}</th>'
             f'<th>{html.escape(datos["fichero_b"])}</th></tr>{filas}</table>')

    doc = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<title>Comparacion de criterio {datos['fecha']}</title>
<style>
 body {{ font: 15px/1.5 system-ui, Segoe UI, sans-serif; max-width: 1100px; margin: 0 auto; padding: 24px; color: #1a1a1a; }}
 h1 {{ font-size: 20px; margin-bottom: 4px; }}
 .sub {{ color: #666; margin-top: 0; }}
 .caso {{ border: 1px solid #ddd; border-radius: 6px; padding: 14px 18px; margin: 18px 0; }}
 .caso h2 {{ font-size: 15px; margin: 0; }}
 .head {{ color: #666; font-size: 13px; margin: 2px 0 12px; }}
 .par {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
 .col h3 {{ font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: #444; margin: 0 0 8px; }}
 .col p {{ margin: 0 0 10px; }}
 .campo {{ font-family: Consolas, monospace; font-size: 13px; }}
 .chars {{ color: #999; font-size: 11px; }}
 .avisos {{ font-size: 12px; color: #a33; }}
 .meta {{ font-size: 12px; color: #666; }}
 .err {{ color: #a00; }}
 .oculto {{ display: none; }}
 .voto {{ margin-top: 10px; border-top: 1px solid #eee; padding-top: 10px; }}
 button {{ font: inherit; padding: 5px 12px; margin-right: 6px; border: 1px solid #bbb; background: #fafafa; border-radius: 4px; cursor: pointer; }}
 button.sel {{ background: #1a1a1a; color: #fff; border-color: #1a1a1a; }}
 .marca {{ color: #666; font-size: 13px; margin-left: 8px; }}
 #barra {{ position: sticky; top: 0; background: #fff; padding: 10px 0; border-bottom: 1px solid #ddd; z-index: 2; }}
 #res {{ display: none; border: 2px solid #1a1a1a; border-radius: 6px; padding: 16px 20px; margin: 20px 0; }}
 table {{ border-collapse: collapse; font-size: 14px; margin-top: 10px; }}
 td, th {{ border: 1px solid #ddd; padding: 4px 10px; text-align: left; }}
 .grande {{ font-size: 17px; font-weight: 600; }}
</style></head><body>
<h1>Comparacion de criterio a ciegas</h1>
<p class="sub">{datos['fecha']} · {len(datos['casos'])} comparaciones · modelo {html.escape(str(datos['modelo']))}</p>
<div id="barra"><span id="cuenta">0 de {len(datos['casos'])} valoradas</span>
  <button id="ver" style="float:right">Ver resultado</button></div>
<div id="res"></div>
{''.join(tarjetas)}
<script>
const CLAVE = {json.dumps([{"izq": c["izquierda"], "der": ("b" if c["izquierda"] == "a" else "a")} for c in datos["casos"]])};
const FICH = {json.dumps({"a": datos["fichero_a"], "b": datos["fichero_b"]})};
const TABLA = {json.dumps(tabla)};
const votos = {{}};
document.querySelectorAll('.caso').forEach(sec => {{
  const i = sec.dataset.i;
  sec.querySelectorAll('.voto button').forEach(b => b.onclick = () => {{
    votos[i] = b.dataset.v;
    sec.querySelectorAll('.voto button').forEach(x => x.classList.remove('sel'));
    b.classList.add('sel');
    document.getElementById('cuenta').textContent =
      Object.keys(votos).length + ' de ' + CLAVE.length + ' valoradas';
  }});
}});
document.getElementById('ver').onclick = () => {{
  let a = 0, b = 0, ig = 0, no = 0;
  Object.keys(votos).forEach(i => {{
    const v = votos[i];
    if (v === 'igual') {{ ig++; return; }}
    if (v === 'ninguna') {{ no++; return; }}
    const cual = (v === 'izq') ? CLAVE[i].izq : CLAVE[i].der;
    if (cual === 'a') a++; else b++;
  }});
  document.querySelectorAll('.oculto').forEach(e => e.classList.remove('oculto'));
  document.querySelectorAll('.caso').forEach(sec => {{
    const i = sec.dataset.i, v = votos[i];
    if (!v) {{ sec.querySelector('.marca').textContent = 'sin valorar'; return; }}
    let t = 'iguales';
    if (v === 'ninguna') t = 'ninguna sirve';
    else if (v !== 'igual') t = 'elegida: ' + FICH[(v === 'izq') ? CLAVE[i].izq : CLAVE[i].der];
    sec.querySelector('.marca').textContent = t;
  }});
  const total = a + b;
  const r = document.getElementById('res');
  r.style.display = 'block';
  r.innerHTML = '<p class="grande">' + FICH.a + ': ' + a + ' · ' + FICH.b + ': ' + b +
    ' · iguales: ' + ig + ' · ninguna: ' + no + '</p>' +
    '<p>' + (total === 0 ? 'Sin preferencias marcadas.' :
      (a === b ? 'Empate: elige por tiempo y tamaño.' :
       'Preferido ' + (a > b ? FICH.a : FICH.b) + ' en ' + Math.max(a, b) + ' de ' + total + '.')) + '</p>' + TABLA;
  r.scrollIntoView();
}};
</script>
</body></html>"""
    ruta.write_text(doc, encoding="utf-8")


# ---------------------------------------------------------------- principal

def main():
    ap = argparse.ArgumentParser(description="Compara dos ficheros de reglas sobre los mismos envios")
    ap.add_argument("items", nargs="*", help="0624, 8999=30/08/2026, 4681323...")
    ap.add_argument("--lote", default=None, help="fichero con un envio por linea")
    ap.add_argument("--fecha", default=None, help="DD/MM/AAAA comun a todo el lote")
    ap.add_argument("--a", default="reglas_ligeras.md", help="primer fichero de reglas")
    ap.add_argument("--b", default="reglas_patrones.md", help="segundo fichero de reglas")
    ap.add_argument("--repes", type=int, default=1, help="pasadas por envio (2 mide tambien la variabilidad)")
    ap.add_argument("--sin-acortar", action="store_true", help="desactiva la segunda pasada del COMMENT largo")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    trabajos = leer_trabajos(args.items, args.lote, args.fecha)
    if not trabajos:
        ap.error("no hay envios que procesar")
    for f in (args.a, args.b):
        if not (BASE_DIR / f).exists():
            ap.error(f"no existe el fichero de reglas {f}")
    if args.a == args.b:
        ap.error("los dos ficheros de reglas son el mismo")

    cfg = cargar_config()
    R.configurar(cfg)
    if args.sin_acortar:
        R.CFG["acortar_comment"] = False

    ka, kb = (BASE_DIR / args.a).stat().st_size / 1024, (BASE_DIR / args.b).stat().st_size / 1024
    n = len(trabajos) * args.repes
    print(f"A = {args.a} ({ka:.1f} KB)   B = {args.b} ({kb:.1f} KB)")
    print(f"{len(trabajos)} envio(s) x {args.repes} pasada(s) = {n} comparaciones, {n * 2} redacciones.")
    print(f"Extraccion una sola vez por envio. Calcula entre {n * 2 // 4} y {n * 2 * 2} minutos.\n")

    from extractor import Extractor, NeedsLogin, NotFound, AntiBot

    datos = {
        "fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "fichero_a": args.a, "fichero_b": args.b, "repes": args.repes,
        "modelo": cfg["claude_model"] if cfg.get("redactor") != "api" else cfg.get("api_model"),
        "acortar": R.CFG["acortar_comment"],
        "casos": [],
    }
    SALIDA_DIR.mkdir(exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d_%H%M")
    ruta_json = SALIDA_DIR / f"comparacion_{sello}.json"
    ruta_html = SALIDA_DIR / f"comparacion_{sello}.html"
    ruta_txt = SALIDA_DIR / f"comparacion_{sello}.txt"

    headless = cfg["headless"] and not args.headed
    inicio = time.time()
    try:
        with Extractor(headless=headless, debug=args.debug, canal=cfg.get("navegador", "auto"),
                       miniaturas=cfg.get("miniaturas", 0), espera_login=cfg.get("espera_login", 60),
                       ruta=cfg.get("navegador_ruta")) as ex:
            for numero, fecha in trabajos:
                print(f"\n=== envio {numero}{' del ' + fecha if fecha else ''} ===")
                try:
                    t0 = time.time()
                    ficha = ex.fetch(numero, fecha)
                    print(f"extraido en {time.time() - t0:.1f} s")
                except (NeedsLogin, AntiBot) as e:
                    print(f"ERROR: {e}\nComparacion interrumpida.")
                    break
                except NotFound as e:
                    print(f"ERROR: {e}")
                    continue
                except Exception as e:
                    print(f"ERROR de extraccion ({type(e).__name__}): {str(e).splitlines()[0][:200]}")
                    print("  Se salta este envio. Para verlo: python catalogar.py "
                          f"{numero} --debug --headed")
                    continue
                for pasada in range(1, args.repes + 1):
                    # orden alternado: el fichero que va primero cambia en cada pasada
                    orden = ("a", "b") if (len(datos["casos"]) % 2 == 0) else ("b", "a")
                    res = {}
                    for lado in orden:
                        fichero = args.a if lado == "a" else args.b
                        r = redactar_con(fichero, ficha)
                        res[lado] = r
                        estado = "ERROR" if not r["ok"] else f"{len(r['avisos'])} avisos"
                        print(f"  {lado.upper()} {fichero}: {r['segundos']} s, {estado}")
                    datos["casos"].append({
                        "numero": numero, "fecha_envio": fecha or ficha.get("fecha", ""),
                        "slug": ficha.get("slug", ""), "headline": ficha.get("headline", ""),
                        "pasada": pasada,
                        "izquierda": random.choice(["a", "b"]),
                        "a": res["a"], "b": res["b"],
                    })
                    ruta_json.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    except KeyboardInterrupt:
        print("\nInterrumpido: se guarda lo hecho hasta ahora.")

    if not datos["casos"]:
        print("Sin resultados.")
        sys.exit(1)

    escribir_html(datos, ruta_html)
    escribir_txt(datos, ruta_txt)
    imprimir_resumen(datos)
    print(f"\nTiempo total: {time.time() - inicio:.0f} s")
    print(f"Abre esta pagina y elige a ciegas:\n  {ruta_html}")
    print(f"Datos: {ruta_json}\nTexto revelado: {ruta_txt}")


if __name__ == "__main__":
    main()
