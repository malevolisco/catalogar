# -*- coding: utf-8 -*-
"""
importar_sesion.py - Carga en este equipo la sesion exportada con exportar_sesion.py.
Se ejecuta en la Raspberry (o en cualquier equipo sin pantalla) despues de copiar sesion.json.

Uso:
    python3 importar_sesion.py
    python3 importar_sesion.py --fichero sesion.json --comprobar
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from extractor import abrir_contexto, PROFILE_DIR, BASE, AP_BASE
from config import cargar_config

BASE_DIR = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description="Importa la sesion de las agencias desde un fichero")
    ap.add_argument("--fichero", default="sesion.json")
    ap.add_argument("--comprobar", action="store_true", help="abre Reuters y AP y dice si la sesion vale")
    args = ap.parse_args()
    origen = BASE_DIR / args.fichero
    if not origen.exists():
        print(f"No encuentro {origen}. Copia aqui el sesion.json exportado en el PC.")
        return
    estado = json.loads(origen.read_text(encoding="utf-8"))
    cfg = cargar_config()
    with sync_playwright() as pw:
        ctx, navegador = abrir_contexto(pw, headless=True, canal=cfg.get("navegador", "auto"), ruta=cfg.get("navegador_ruta"))
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        cookies = estado.get("cookies", [])
        if cookies:
            ctx.add_cookies(cookies)
        # localStorage por origen
        for origen_datos in estado.get("origins", []):
            url = origen_datos.get("origin")
            items = origen_datos.get("localStorage", [])
            if not url or not items:
                continue
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.evaluate("""items => { for (const it of items) { try { localStorage.setItem(it.name, it.value); } catch (e) {} } }""", items)
            except Exception as e:
                print(f"Aviso: no se pudo restaurar localStorage de {urlparse(url).netloc}: {e}")
        print(f"Importadas {len(cookies)} cookies y {len(estado.get('origins', []))} origenes en {PROFILE_DIR} (navegador: {navegador}).")

        if args.comprobar:
            for url, nombre in ((BASE, "Reuters Connect"), (AP_BASE, "AP Newsroom")):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=40000)
                    page.wait_for_timeout(3000)
                    texto = page.locator("body").inner_text()[:3000].lower()
                    if "password" in texto or "sign in" in texto or "log in" in texto:
                        print(f"  {nombre}: parece que pide login. Vuelve a exportar la sesion en el PC.")
                    else:
                        print(f"  {nombre}: sesion valida.")
                except Exception as e:
                    print(f"  {nombre}: no se pudo comprobar ({e})")
        ctx.close()


if __name__ == "__main__":
    main()
