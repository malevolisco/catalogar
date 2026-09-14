# -*- coding: utf-8 -*-
"""
exportar_sesion.py - Guarda la sesion de Reuters y AP en sesion.json para llevarla a otro
equipo (por ejemplo la Raspberry). Se ejecuta en el PC donde ya funciona login.py.

Uso:
    python exportar_sesion.py
    python exportar_sesion.py --salida sesion.json
"""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright
from extractor import abrir_contexto, PROFILE_DIR, BASE, AP_BASE
from config import cargar_config

BASE_DIR = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description="Exporta la sesion de las agencias a un fichero")
    ap.add_argument("--salida", default="sesion.json")
    args = ap.parse_args()
    if not PROFILE_DIR.exists():
        print("No hay perfil todavia: ejecuta antes python login.py")
        return
    cfg = cargar_config()
    with sync_playwright() as pw:
        ctx, navegador = abrir_contexto(pw, headless=True, canal=cfg.get("navegador", "auto"), ruta=cfg.get("navegador_ruta"))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # visitar los dos sitios para que el localStorage de cada origen entre en el volcado
        for url in (BASE, AP_BASE):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
            except Exception as e:
                print(f"Aviso: no se pudo abrir {url}: {e}")
        estado = ctx.storage_state()
        ctx.close()
    destino = BASE_DIR / args.salida
    destino.write_text(json.dumps(estado, ensure_ascii=False), encoding="utf-8")
    n_c = len(estado.get("cookies", []))
    n_o = len(estado.get("origins", []))
    print(f"Sesion guardada en {destino} ({n_c} cookies, {n_o} origenes).")
    print("Copiala a la Raspberry y ejecuta alli: python3 importar_sesion.py")
    print("ATENCION: este fichero da acceso a tus cuentas. No lo subas a GitHub ni lo compartas.")


if __name__ == "__main__":
    main()
