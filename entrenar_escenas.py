# -*- coding: utf-8 -*-
"""
entrenar_escenas.py - Entrena el reconocimiento de escenas (rueda de prensa, declaraciones,
entrevista...) con los fotogramas de tus propios envios.

De donde saca los ejemplos, sin que tengas que ordenar nada a mano:

    escenas_auto/<clase>/     los fotogramas de cada ficha que apruebas en la pagina ("Buena").
                              La clase sale del descriptor de la ficha aprobada (RUEDA DE PRENSA,
                              DECLARACIONES...). Se llena solo desde que el servidor lleva esta version.
    escenas/<clase>/          (opcional) imagenes que quieras anadir tu a mano, con el mismo
                              nombre de carpeta: rueda_de_prensa, comparecencia_conjunta,
                              comparecencia, declaraciones, entrevista, intervencion,
                              saludo_reunion, video_promocional, recursos.

Uso (con el servidor parado o en marcha, da igual):
    python entrenar_escenas.py                    entrena con lo que haya en las dos carpetas
    python entrenar_escenas.py --desde-aprobadas  antes recoge los fotogramas de las fichas que ya
                                                  aprobaste antes de esta version (cola/estado.json)

Al final imprime, clase por clase, cuantas veces acierta cuando dice esa clase, probandolo
con cada envio que no ha visto (se aparta el envio entero, no un fotograma suelto, que eso
engana). Ese acierto se guarda en escenas_modelo.npz y el redactor solo se fia de las clases
que llegan a escenas_fiabilidad_min (0.85 por defecto): las demas se aprenden, pero no se usan
hasta que tengan ejemplos suficientes. Conviene volver a entrenar cada semana o cada 100
fichas aprobadas; tarda poco, porque los fotogramas ya vistos se guardan en escenas_cache.npz.
"""
import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

import escenas
from actos import (CARPETA_AUTO, EXT_IMAGEN, siguen_igual, clase_de_ficha, copiar_para_entrenar,
                   clave_envio)

BASE_DIR = Path(__file__).resolve().parent
CACHE = BASE_DIR / "escenas_cache.npz"
ESTADO = BASE_DIR / "cola" / "estado.json"
MINIATURAS = BASE_DIR / "miniaturas"
GRUPO_RE = re.compile(r"^(.+_(?:\d{8}|sinfecha))_\d+$")   # numero_aaaammdd_NN -> un envio
MIN_DICHOS = 5          # veces minimas que la clase se ha dicho en la prueba para darle fiabilidad


# ---------------------------------------------------------------------- recoger las aprobadas
def desde_aprobadas():
    """Copia a escenas_auto/ los fotogramas de las fichas aprobadas que aun no esten. Para las fichas de
    antes de esta version (sin huella de fotogramas) se usa la carpeta miniaturas/<numero>/ solo si sus
    imagenes se sacaron en el dia siguiente a crear el lote; si hay varias fichas con ese numero, se queda
    la del lote mas reciente que encaje."""
    if not ESTADO.exists():
        print(f"No existe {ESTADO}: no hay fichas aprobadas que recoger.")
        return
    try:
        lotes = json.loads(ESTADO.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"No se puede leer {ESTADO} ({type(e).__name__}).")
        return
    candidatas = defaultdict(list)     # numero -> [(creado, ficha)]
    ya, sin_clase, copiadas, sin_fotos = 0, 0, 0, 0
    for lote in lotes if isinstance(lotes, list) else []:
        try:
            creado = datetime.strptime(lote.get("creado", ""), "%d/%m/%Y %H:%M")
        except ValueError:
            continue
        for f in lote.get("fichas") or []:
            if not f.get("aprobada"):
                continue
            clase = clase_de_ficha(f.get("NAME"), f.get("COMMENT"))
            if not clase:
                sin_clase += 1
                continue
            if list(CARPETA_AUTO.glob(f"{clase}/{clave_envio(f.get('numero'), f.get('fecha'))}_*")):
                ya += 1
                continue
            if f.get("fotogramas"):
                rutas = siguen_igual(f["fotogramas"])
                if rutas:
                    copiadas += 1 if copiar_para_entrenar(clase, f.get("numero"), f.get("fecha"), rutas) else 0
                else:
                    sin_fotos += 1
                continue
            candidatas[str(f.get("numero") or "")].append((creado, clase, f))
    for numero, lista in candidatas.items():
        carpeta = MINIATURAS / numero
        fotos = sorted(p for p in carpeta.glob("*") if p.suffix.lower() in EXT_IMAGEN) if numero and carpeta.is_dir() else []
        if not fotos:
            sin_fotos += len(lista)
            continue
        cuando = datetime.fromtimestamp(max(p.stat().st_mtime for p in fotos))
        encajan = [c for c in lista if c[0] - timedelta(minutes=5) <= cuando <= c[0] + timedelta(days=1)]
        if not encajan:
            sin_fotos += len(lista)
            continue
        _, clase, f = max(encajan, key=lambda c: c[0])
        copiadas += 1 if copiar_para_entrenar(clase, numero, f.get("fecha"), fotos) else 0
        sin_fotos += len(lista) - 1
    print(f"Fichas aprobadas recogidas ahora: {copiadas}  (ya estaban: {ya}; sin fotogramas validos: {sin_fotos}; "
          f"de archivo o resumen, que no se aprenden: {sin_clase})")


# ---------------------------------------------------------------------- vectores con cache
def _cargar_cache():
    if not CACHE.exists():
        return {}
    try:
        d = np.load(CACHE, allow_pickle=False)
        return {k: v for k, v in zip(d["claves"].tolist(), d["vectores"])}
    except Exception:
        return {}       # cache rota: se recalcula todo


def _clave(p):
    st = p.stat()
    return f"{p.resolve()}|{st.st_mtime_ns}|{st.st_size}"


def vectores_con_cache(rutas, decir=print):
    """Un vector por imagen legible (en el mismo orden, saltando las que fallen) y sus rutas."""
    cache = _cargar_cache()
    claves = [_clave(p) for p in rutas]
    faltan = [p for p, k in zip(rutas, claves) if k not in cache]
    if faltan:
        decir(f"  calculando {len(faltan)} imagen(es) nuevas (las demas ya estaban en la cache)...")
        for p in faltan:
            vs = escenas.vectores([str(p)])
            if len(vs):
                cache[_clave(p)] = vs[0]
        vivas = set(claves)             # la cache solo guarda lo que sigue existiendo
        cache = {k: v for k, v in cache.items() if k in vivas}
        if cache:
            np.savez(CACHE, claves=np.array(list(cache.keys())), vectores=np.array(list(cache.values()), dtype=np.float32))
    V, ok = [], []
    for p, k in zip(rutas, claves):
        if k in cache:
            V.append(cache[k])
            ok.append(p)
    return (np.array(V, dtype=np.float32) if V else np.zeros((0, 1), dtype=np.float32)), ok


# ---------------------------------------------------------------------- entrenar y medir
def _centros(V, Y, n_clases, fuera=None):
    cs = np.zeros((n_clases, V.shape[1]), dtype=np.float32)
    for c in range(n_clases):
        sel = (Y == c) if fuera is None else ((Y == c) & ~fuera)
        if sel.any():
            m = V[sel].mean(axis=0)
            cs[c] = m / (np.linalg.norm(m) or 1.0)
    return cs


def medir(V, Y, G, clases):
    """Aparta cada envio entero, entrena sin el y lo clasifica votando sus fotogramas como en el uso real
    (sin el primero y el ultimo si tiene 4 o mas). Devuelve por clase (acierto al decirla, veces dicha,
    envios reales de la clase, aciertos)."""
    dichos, buenos, reales = np.zeros(len(clases)), np.zeros(len(clases)), np.zeros(len(clases))
    for g in np.unique(G):
        fuera = (G == g)
        idx = np.flatnonzero(fuera)
        if len(idx) >= 4:
            idx = idx[1:-1]
        real = int(Y[fuera][0])
        cs = _centros(V, Y, len(clases), fuera)
        if not cs[real].any():
            continue                     # era el unico envio de su clase: no se puede probar
        i, _, _ = escenas.votar(V[idx], cs)
        dichos[i] += 1
        reales[real] += 1
        buenos[i] += (i == real)
    return dichos, buenos, reales


def entrenar(carpetas=("escenas", "escenas_auto"), minimo=3, decir=print):
    """Entrena con las imagenes de las carpetas y guarda escenas_modelo.npz. Devuelve un resumen:
    {"ok", "motivo", "clases": [{clase, envios, dicha, acierto, se_usa, nota}], "global", "imagenes", "envios"}.
    La usa este script y la pestaña Imagenes de la pagina (imagenes.py)."""
    por_clase = defaultdict(list)            # clase -> [(ruta, grupo)]
    for nombre in carpetas:
        raiz = BASE_DIR / nombre
        if not raiz.is_dir():
            continue
        for sub in sorted(p for p in raiz.iterdir() if p.is_dir()):
            for p in sorted(sub.iterdir()):
                if p.suffix.lower() in EXT_IMAGEN:
                    m = GRUPO_RE.match(p.stem)
                    # las de escenas_auto (y las subidas como "mismo video") se agrupan por envio;
                    # las demas puestas a mano, cada una es un ejemplo
                    por_clase[sub.name].append((p, m.group(1) if m else f"{nombre}/{sub.name}/{p.stem}"))
    if not por_clase:
        return {"ok": False, "motivo": "No hay imagenes todavia: aprueba fichas o sube imagenes a una categoria."}

    clases, rutas, ys, gs, saltadas = [], [], [], [], []
    for clase in sorted(por_clase):
        grupos = {g for _, g in por_clase[clase]}
        if len(grupos) < minimo:
            decir(f"  {clase}: {len(grupos)} ejemplo(s), se salta hasta tener {minimo}")
            saltadas.append({"clase": clase, "envios": len(grupos), "dicha": 0, "acierto": None, "se_usa": False,
                             "nota": f"faltan ejemplos ({len(grupos)} de {minimo})"})
            continue
        idx = len(clases)
        clases.append(clase)
        for p, g in por_clase[clase]:
            rutas.append(p)
            ys.append(idx)
            gs.append(g)
    if len(clases) < 2:
        return {"ok": False, "motivo": f"Hacen falta al menos dos categorias con {minimo} ejemplos o mas.",
                "clases": saltadas}

    decir(f"Leyendo {len(rutas)} imagenes de {len(clases)} clases...")
    V, ok = vectores_con_cache(rutas, decir)
    donde = {p: i for i, p in enumerate(rutas)}
    Y = np.array([ys[donde[p]] for p in ok])
    G = np.array([gs[donde[p]] for p in ok])
    if len(V) == 0:
        return {"ok": False, "motivo": "No se ha podido leer ninguna imagen.", "clases": saltadas}

    dichos, buenos, reales = medir(V, Y, G, clases)
    fiab = np.where(dichos >= MIN_DICHOS, buenos / np.maximum(dichos, 1), 0.0)
    centros = _centros(V, Y, len(clases))
    np.savez(BASE_DIR / "escenas_modelo.npz", clases=np.array(clases), centros=centros,
             fiabilidad=fiab.astype(np.float32))

    umbral = 0.85
    try:
        from config import cargar_config
        umbral = float(cargar_config().get("escenas_fiabilidad_min", umbral))
    except Exception:
        pass
    filas = []
    for c, nombre in enumerate(clases):
        envios = len(np.unique(G[Y == c]))
        if dichos[c] < MIN_DICHOS:
            se_usa, nota = False, f"faltan pruebas ({int(dichos[c])} de {MIN_DICHOS})"
        else:
            se_usa = bool(fiab[c] >= umbral)
            nota = "se usa" if se_usa else f"acierta menos del {umbral:.0%}"
        filas.append({"clase": nombre, "envios": int(envios), "dicha": int(dichos[c]),
                      "acierto": float(buenos[c] / dichos[c]) if dichos[c] else None, "se_usa": se_usa, "nota": nota})
    total = int(dichos.sum())
    return {"ok": True, "clases": filas + saltadas, "umbral": umbral, "entrenadas": len(clases),
            "global": float(buenos.sum() / total) if total else None,
            "imagenes": int(len(V)), "envios": int(len(np.unique(G)))}


def main():
    ap = argparse.ArgumentParser(description="Entrena el reconocimiento de escenas")
    ap.add_argument("--desde-aprobadas", action="store_true",
                    help="recoge antes los fotogramas de las fichas aprobadas de cola/estado.json")
    ap.add_argument("--carpetas", nargs="+", default=["escenas", "escenas_auto"],
                    help="carpetas con una subcarpeta por clase (se juntan las del mismo nombre)")
    ap.add_argument("--minimo", type=int, default=3, help="envios minimos para aceptar una clase")
    args = ap.parse_args()

    if args.desde_aprobadas:
        desde_aprobadas()
    r = entrenar(args.carpetas, args.minimo)
    if not r["ok"]:
        print(r["motivo"])
        return
    print(f"\nGuardado escenas_modelo.npz ({r['entrenadas']} clases, "
          f"{r['envios']} envios, {r['imagenes']} imagenes).")
    print(f"\n{'clase':<24}{'envios':>7}{'dicha':>7}{'acierto':>9}   se usa")
    for c in r["clases"]:
        acierto = f"{c['acierto']:.0%}" if c["acierto"] is not None else "-"
        print(f"{c['clase']:<24}{c['envios']:>7}{c['dicha']:>7}{acierto:>9}   {'SI' if c['se_usa'] else 'no (' + c['nota'] + ')'}")
    if r["global"] is not None:
        print(f"\nAcierto global probando cada envio sin haberlo visto: {r['global']:.0%}")
    print("Las clases que no se usan se siguen aprendiendo: vuelve a entrenar cuando haya mas fichas aprobadas.")


if __name__ == "__main__":
    main()
