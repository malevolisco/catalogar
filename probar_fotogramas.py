# -*- coding: utf-8 -*-
"""
probar_fotogramas.py - Comprueba si se pueden capturar fotogramas del reproductor.

No reproduce el video: salta a N puntos repartidos por la duracion y captura lo que
se ve. Sirve para saber si el reproductor deja capturar o devuelve negro por
proteccion de contenido, que es lo que decide si esta via existe o no.

Uso:
    python probar_fotogramas.py 1554=09/09/2026
    python probar_fotogramas.py 4683554 --puntos 6

Guarda las capturas en fotogramas_prueba/<numero>/ e imprime el tamaño de cada una.
Un PNG casi negro pesa muy poco y todos pesan casi lo mismo: esa es la señal de que
el reproductor esta protegido. Mirelas usted igualmente, que es lo definitivo.
"""
import re
import sys
import argparse
from pathlib import Path

from config import cargar_config

BASE_DIR = Path(__file__).resolve().parent
SALIDA = BASE_DIR / "fotogramas_prueba"

ITEM_RE = re.compile(r"^(?:AP)?(\d{7}|\d{4})(?:[=@ ]\s*(\d{2}/\d{2}/\d{4}))?$", re.I)

JS_BUSCAR = """() => {
  const v = document.querySelector('video');
  if (!v) return null;
  return {dur: v.duration || 0, w: v.videoWidth || 0, h: v.videoHeight || 0,
          src: (v.currentSrc || v.src || '').slice(0, 120)};
}"""

JS_SALTAR = """async (t) => {
  const v = document.querySelector('video');
  if (!v) return 'sin-video';
  try { v.pause(); } catch (e) {}
  v.currentTime = t;
  return await new Promise(r => {
    const ok = () => r('ok');
    v.addEventListener('seeked', ok, {once: true});
    setTimeout(() => r('timeout'), 6000);
  });
}"""


def marco_con_video(page):
    """Devuelve el frame que contiene el <video>: puede estar dentro de un iframe."""
    for marco in [page] + list(page.frames):
        try:
            if marco.evaluate(JS_BUSCAR):
                return marco
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser(description="Prueba si el reproductor deja capturar fotogramas")
    ap.add_argument("item", help="0624, 1554=09/09/2026, 4683554...")
    ap.add_argument("--puntos", type=int, default=6, help="fotogramas repartidos por la duracion")
    ap.add_argument("--headless", action="store_true", help="sin ventana (por defecto se ve)")
    args = ap.parse_args()

    m = ITEM_RE.match(args.item.strip())
    if not m:
        ap.error("formato no reconocido")
    numero, fecha = m.group(1), m.group(2)

    cfg = cargar_config()
    from extractor import Extractor

    destino = SALIDA / numero
    destino.mkdir(parents=True, exist_ok=True)
    for viejo in destino.glob("*.png"):
        viejo.unlink()

    print(f"Abriendo el envio {numero}{' del ' + fecha if fecha else ''}...")
    with Extractor(headless=args.headless, debug=True, canal=cfg.get("navegador", "auto"),
                   ruta=cfg.get("navegador_ruta")) as ex:
        ficha = ex.fetch(numero, fecha)
        print(f"Ficha: {ficha.get('headline', '')[:70]}")
        page = ex._page
        page.wait_for_timeout(2500)          # dar tiempo a que monte el reproductor

        marco = marco_con_video(page)
        if marco is None:
            print("\nNo hay ningun elemento <video> en la pagina.")
            print("Puede que el reproductor solo se monte al pulsar play, o que use otra tecnologia.")
            print("Deje la ventana abierta, pulse play a mano y vuelva a lanzar esto.")
            sys.exit(2)

        info = marco.evaluate(JS_BUSCAR)
        print(f"Video encontrado: {info['dur']:.0f} s, {info['w']}x{info['h']}")
        if not info["dur"]:
            print("La duracion sale a cero: el video aun no ha cargado metadatos.")
            sys.exit(2)

        elemento = marco.query_selector("video")
        pesos = []
        for i in range(args.puntos):
            t = info["dur"] * (i + 0.5) / args.puntos
            estado = marco.evaluate(JS_SALTAR, t)
            marco.wait_for_timeout(400)
            f = destino / f"{i + 1:02d}_{int(t):03d}s.png"
            try:
                elemento.screenshot(path=str(f))
            except Exception as e:
                print(f"  {int(t):4d} s  ERROR al capturar: {e}")
                continue
            peso = f.stat().st_size
            pesos.append(peso)
            print(f"  {int(t):4d} s  salto={estado}  {peso / 1024:.0f} KB  {f.name}")

    print()
    if not pesos:
        print("No se capturo nada.")
        sys.exit(2)
    medio = sum(pesos) / len(pesos)
    dispersion = (max(pesos) - min(pesos)) / medio if medio else 0
    print(f"Tamaño medio {medio / 1024:.0f} KB, dispersion {dispersion:.0%}")
    if medio < 15000 and dispersion < 0.15:
        print("SOSPECHA: pesan poco y todas casi lo mismo. Es lo que pasa cuando el")
        print("reproductor esta protegido y devuelve negro. Abra la carpeta y confirmelo.")
    else:
        print("BUENA SEÑAL: pesan lo suficiente y varian entre si, que es lo propio de")
        print("fotogramas distintos. Abra la carpeta y confirme que son del video.")
    print(f"\nCapturas en: {destino}")


if __name__ == "__main__":
    main()
