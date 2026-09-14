# -*- coding: utf-8 -*-
"""
login.py - Abre Reuters Connect y AP Newsroom en el navegador del script para que
inicies sesion a mano. La sesion queda guardada en ./perfil_chromium y la reutiliza
extractor.py. Repite este paso cuando el extractor avise de sesion caducada o de
verificacion antibot: en este ultimo caso, supera la verificacion en la ventana antes
de pulsar Intro.

Uso:
    python login.py
"""
from playwright.sync_api import sync_playwright
from extractor import abrir_contexto, PROFILE_DIR, BASE, AP_BASE
from config import cargar_config

SITIOS = [BASE, AP_BASE]


def main():
    cfg = cargar_config()
    with sync_playwright() as pw:
        ctx, navegador = abrir_contexto(pw, headless=False, canal=cfg.get("navegador", "auto"), ruta=cfg.get("navegador_ruta"))
        print(f"Navegador: {navegador}")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(SITIOS[0])
        for url in SITIOS[1:]:
            ctx.new_page().goto(url)
        print("Inicia sesion en las pestañas abiertas (Reuters Connect y AP Newsroom).")
        print("Si aparece una verificacion antibot, superala ahi mismo.")
        print("Cuando veas la portada con tu cuenta, vuelve aqui y pulsa Intro.")
        input()
        ctx.close()
    print("Sesion guardada en", PROFILE_DIR)


if __name__ == "__main__":
    main()
