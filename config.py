# -*- coding: utf-8 -*-
"""config.py - Carga config.json (copiar desde config.example.json).

La agenda "documentalistas" admite, por persona, la direccion a secas o un bloque con todo lo suyo:

    "documentalistas": {
      "Andrea": "andrea.apellido@rtve.es",
      "Javier": {"correo": "javier.apellido@rtve.es", "id": "I24082", "presentacion": "normalizado"},
      "Silvia": {"correo": "silvia.apellido@rtve.es", "formal": true}
    }

    correo        obligatorio
    id            su usuario de MediaCentral (columna Catalogador de las listas), uno o una lista
    formal        true: siempre en tono formal
    presentacion  "normalizado" o "mayusculas" si quiere las fichas distintas a correo_presentacion

Al cargar, el bloque se desdobla en las claves planas que usa el resto del programa (documentalistas
nombre -> correo, catalogadores id -> nombre, correo_formal, correo_presentacion_personas), que siguen
valiendo escritas a mano como antes.
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

DEFAULTS = {
    "github_repo": "",            # "usuario/catalogar"  (solo para worker.py)
    "github_token": "",           # token fine-grained con permiso Issues: read & write
    "poll_seconds": 20,
    "claude_model": "sonnet",
    "claude_extra_args": [],      # flags extra de claude -p; las herramientas y los turnos los fija redactor.py
    "claude_timeout": 240,
    "claude_thinking_tokens": 1024,   # limita el razonamiento de Claude Code (MAX_THINKING_TOKENS)
    "redactor": "claude_code",        # "claude_code" (Pro) o "api" (clave, centimos por envio, rapido)
    "claude_token": "",             # token de un año de Claude Code (claude setup-token): la sesion no caduca
    "api_key": "",
    "api_reserva": True,            # si Claude Code pierde la sesion y hay api_key, redactar con la API mientras
    "api_model": "claude-haiku-4-5-20251001",
    "api_max_tokens": 1200,     # con generar_normal son siete lineas: 700 se quedaba corto y cortaba el final
    "acortar_comment": True,    # segunda pasada automatica si el COMMENT supera el tope
    "script_max_paginas": 2,    # paginas de Word que admite el campo de script del archivo; mas, y salta ALERTA
    "documentalistas": {},      # agenda: nombre -> correo, o nombre -> {correo, id, formal, presentacion}
    "catalogadores": {},        # usuario de MediaCentral -> persona: {"I23785": "yo", "I24082": "javier"}
    "correo_formal": [],        # nombres o direcciones que reciben siempre en formal
    "correo_presentacion_personas": {},   # nombre -> "normalizado" | "mayusculas" (y siempre PERSONAS_NORMALIZADO)
    "reglas": "reglas_patrones.md",      # o "reglas_ligeras.md" (misma criterio, mitad de tamano)
    "espera_login": 60,        # segundos para iniciar sesion a mano antes de rendirse (0 = no esperar)
    "escenas": True,           # usar el clasificador local de escenas si esta entrenado
    "escenas_umbral": 0.6,     # parte minima de fotogramas que votan por la misma clase
    "escenas_fiabilidad_min": 0.85,  # acierto minimo de esa clase al entrenar para fiarse de ella
    "escenas_sin_imagenes": True,  # con etiqueta fiable, no mandar los fotogramas (ahorra cuota)
    "escenas_fotogramas": 6,   # fotogramas que se sacan para reconocer y aprender la escena, aunque no se manden al modelo
    "escenas_entrenar_auto": True,   # volver a entrenar solo cuando cambian las imagenes (pestaña Imagenes)
    "ejemplos_por_ficha": 8,   # fichas aprobadas que acompañan a cada envio: las mas parecidas a el
    "aprender_correcciones": True,   # al aprobar una ficha corregida, proponer la regla que enseña (Reglas → Sugerencias)
    "miniaturas": 0,            # 0 = apagado; N = fotogramas por envio que se bajan y se mandan al modelo
    "headless": False,          # ventana visible: evita que el antibot cambie de criterio entre login y uso
    "agencias_solo_texto": True,
    "reuters_xml": True,          # de Reuters, el texto del XML de la ficha (boton XML) en vez de leer la pagina   # de las agencias solo el texto: sin video, imagenes ni tipos de letra (carga antes)
    "navegador_oculto": True,   # ventana fuera de la pantalla (no es headless); sale sola si hay que iniciar sesion
    "navegador": "auto",        # auto | chrome | msedge | chromium | sistema (el instalado, para Raspberry)
    "navegador_ruta": "",       # ejecutable concreto, si hace falta (ej. /usr/bin/chromium)
    "ebu": True,                # EBU News Exchange activado
    "login_espera_minutos": 10, # cuanto se espera con la ventana de login abierta desde la pagina
    "comprobar_sesion_al_arrancar": True,
    "login_al_arrancar": True,
    "servidor_puerto": 8765,
    "servidor_clave": "",
    "presentacion": "mayusculas",         # como se ensenan las fichas en la pagina: mayusculas | normalizado
    "correo_presentacion": "mayusculas",  # y en los correos
    "generar_normal": True,               # pedir al modelo la version en escritura normal en la misma llamada
    "correo_servidor": "", "correo_puerto": 587, "correo_usuario": "", "correo_clave": "",
    "correo_remitente": "", "correo_copia": "",
    "correo_html": True,        # correos con version HTML ademas del texto plano
    "correo_saludos": True,     # saludo y despedida en los correos informales
    "correo_imap": "imap.gmail.com", "correo_imap_puerto": 993,
    "correo_dominios": ["rtve.es"],
    "correo_buzon_minutos": 0,  # 0 = buzon apagado
    "ya_hecha_dias": 3,         # un Reuters sin fecha ya hecho en estos ultimos dias se copia, no se redacta
    "lote_max_envios": 90,      # tamaño maximo de un lote: una lista o un correo con mas se trocea, no se descarta
}


class ConfigError(Exception):
    pass


PERSONAS_NORMALIZADO = {"javier": "normalizado"}


def desplegar_agenda(cfg):
    """Desdobla los bloques por persona de "documentalistas" en las claves planas. Lo escrito a mano en
    esas claves se conserva; lo del bloque se añade. Deja la agenda como nombre -> correo."""
    agenda = cfg.get("documentalistas") or {}
    if not isinstance(agenda, dict):
        raise ConfigError('config.json: "documentalistas" tiene que ser un bloque { "Nombre": ... }')
    planos = {}
    ids = dict(cfg.get("catalogadores") or {})
    formales = list(cfg.get("correo_formal") or [])
    # Javier recibe siempre en minusculas (fase de pruebas de la escritura normal), salvo que se diga otra cosa
    presentaciones = dict(PERSONAS_NORMALIZADO, **(cfg.get("correo_presentacion_personas") or {}))
    for nombre, datos in agenda.items():
        if isinstance(datos, str):
            planos[nombre] = datos.strip()
            continue
        if not isinstance(datos, dict) or not (datos.get("correo") or "").strip():
            raise ConfigError(f'config.json: a "{nombre}" en documentalistas le falta el "correo"')
        planos[nombre] = datos["correo"].strip()
        usuarios = datos.get("id") or []
        for u in ([usuarios] if isinstance(usuarios, str) else usuarios):
            if u.strip():
                ids.setdefault(u.strip().upper(), nombre)
        if datos.get("formal") and nombre not in formales:
            formales.append(nombre)
        if datos.get("presentacion"):
            presentaciones.setdefault(nombre, datos["presentacion"])
    cfg["documentalistas"] = planos
    cfg["catalogadores"] = ids
    cfg["correo_formal"] = formales
    cfg["correo_presentacion_personas"] = presentaciones
    return cfg


def cargar_config():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError as e:
            raise ConfigError(f"config.json no es un JSON valido: {e.msg} en la linea {e.lineno}, columna {e.colno}")
    return desplegar_agenda(cfg)
