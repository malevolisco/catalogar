# -*- coding: utf-8 -*-
"""config.py - Carga config.json (copiar desde config.example.json)."""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULTS = {
    "github_repo": "",            # "usuario/catalogar"  (solo para worker.py)
    "github_token": "",           # token fine-grained con permiso Issues: read & write
    "poll_seconds": 20,
    "claude_model": "sonnet",
    "claude_extra_args": ["--max-turns", "1", "--disallowedTools", "Bash,Read,Write,Edit,MultiEdit,Glob,Grep,WebSearch,WebFetch,Task,NotebookEdit"],
    "claude_timeout": 240,
    "claude_thinking_tokens": 1024,   # limita el razonamiento de Claude Code (MAX_THINKING_TOKENS)
    "redactor": "claude_code",        # "claude_code" (Pro) o "api" (clave, centimos por envio, rapido)
    "api_key": "",
    "api_model": "claude-haiku-4-5-20251001",
    "api_max_tokens": 700,
    "acortar_comment": True,    # segunda pasada automatica si el COMMENT supera el tope
    "reglas": "reglas.md",      # o "reglas_ligeras.md" (misma criterio, mitad de tamano)
    "espera_login": 60,        # segundos para iniciar sesion a mano antes de rendirse (0 = no esperar)
    "escenas": True,           # usar el clasificador local de escenas si esta entrenado
    "escenas_umbral": 0.6,     # confianza minima para hacerle caso
    "escenas_sin_imagenes": True,  # con etiqueta fiable, no mandar los fotogramas (ahorra cuota)
    "miniaturas": 6,            # 0 = apagado; N = fotogramas por envio que se bajan y se mandan al modelo
    "headless": False,          # ventana visible: evita que el antibot cambie de criterio entre login y uso
    "navegador": "auto",        # auto | chrome | msedge | chromium | sistema (el instalado, para Raspberry)
    "navegador_ruta": "",       # ejecutable concreto, si hace falta (ej. /usr/bin/chromium)
}


def cargar_config():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return cfg
