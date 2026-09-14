# -*- coding: utf-8 -*-
"""
entrenar_escenas.py - Entrena el clasificador de escenas con tus propios fotogramas.

No hay que saber nada de aprendizaje automatico ni etiquetar cuadro por cuadro:
basta con arrastrar imagenes a carpetas con el nombre de la clase.

    escenas/
        rueda_de_prensa/      atril, carteleria del convocante, periodistas
        comparecencia_conjunta/   dos butacas o atril doble con dos banderas
        declaraciones_calle/  microfonos en mano, a la salida, de pie
        comparecencia/        estrado, hemiciclo, sala oficial
        entrevista/           dos personas frente a frente
        video_promocional/    producto sobre fondo neutro, animaciones
        recursos/             nadie hablando: calle, danos, paisaje, archivo

Las carpetas y sus nombres los decide usted: el script aprende las que encuentre.
Con 30 o 40 imagenes por clase ya funciona; con 10 tambien arranca, peor.

Uso:
    python entrenar_escenas.py
    python entrenar_escenas.py --carpeta escenas

Guarda escenas_modelo.npz, que es lo que usa escenas.py. Imprime al final una
estimacion honesta de acierto dejando fuera cada imagen por turnos.
"""
import argparse
from pathlib import Path

import numpy as np

import escenas

BASE_DIR = Path(__file__).resolve().parent
EXT = (".jpg", ".jpeg", ".png", ".webp")


def main():
    ap = argparse.ArgumentParser(description="Entrena el clasificador de escenas")
    ap.add_argument("--carpeta", default="escenas", help="carpeta con una subcarpeta por clase")
    ap.add_argument("--minimo", type=int, default=5, help="imagenes minimas para aceptar una clase")
    args = ap.parse_args()

    raiz = BASE_DIR / args.carpeta
    if not raiz.exists():
        print(f"No existe {raiz}.")
        print("Cree la carpeta y dentro una subcarpeta por cada clase, con sus imagenes.")
        print("Los fotogramas que ya tiene estan en miniaturas\\<numero>\\.")
        return

    clases, vectores_clase, cuentas = [], [], []
    todos_v, todos_y = [], []
    for sub in sorted(p for p in raiz.iterdir() if p.is_dir()):
        fotos = [str(p) for p in sorted(sub.iterdir()) if p.suffix.lower() in EXT]
        if len(fotos) < args.minimo:
            print(f"  {sub.name}: solo {len(fotos)} imagenes, se salta (minimo {args.minimo})")
            continue
        print(f"  {sub.name}: {len(fotos)} imagenes...", end="", flush=True)
        vs = escenas.vectores(fotos)
        if len(vs) == 0:
            print(" no se pudo leer ninguna")
            continue
        print(f" {len(vs)} leidas")
        idx = len(clases)
        clases.append(sub.name)
        centro = vs.mean(axis=0)
        vectores_clase.append(centro / (np.linalg.norm(centro) or 1.0))
        cuentas.append(len(vs))
        todos_v.append(vs)
        todos_y.append(np.full(len(vs), idx))

    if len(clases) < 2:
        print("\nHacen falta al menos dos clases con imagenes suficientes.")
        return

    centros = np.array(vectores_clase, dtype=np.float32)
    np.savez(BASE_DIR / "escenas_modelo.npz", clases=np.array(clases), centros=centros)
    print(f"\nGuardado escenas_modelo.npz con {len(clases)} clases y {sum(cuentas)} imagenes.")

    # estimacion honesta: se recalcula el centro sin la imagen que se esta probando
    V = np.vstack(todos_v)
    Y = np.concatenate(todos_y)
    aciertos = 0
    for i in range(len(V)):
        cs = []
        for c in range(len(clases)):
            sel = (Y == c)
            sel[i] = False
            if not sel.any():
                cs.append(np.zeros(V.shape[1], dtype=np.float32))
                continue
            m = V[sel].mean(axis=0)
            cs.append(m / (np.linalg.norm(m) or 1.0))
        if int(np.argmax(np.array(cs) @ V[i])) == Y[i]:
            aciertos += 1
    print(f"Acierto estimado: {aciertos}/{len(V)} = {aciertos / len(V):.0%}")
    print("Por debajo del 70% no se fie: haga falta mas variedad de ejemplos por clase,")
    print("o hay dos clases que se parecen demasiado y conviene fundirlas.")


if __name__ == "__main__":
    main()
