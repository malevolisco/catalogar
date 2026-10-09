# -*- coding: utf-8 -*-
"""
redactor.py - Envia el texto de la ficha y las reglas a Claude y devuelve la ficha (ENVIO, NAME, COMMENT, RESTRICCIONES)
normalizadas y validadas.

Tres motores ("cerebros"), elegidos con "redactor" en config.json:
  claude_code  Claude Code en modo no interactivo (claude -p) con la cuenta Pro. Sin coste por ficha.
  api          API de Anthropic con clave (api_key). Centimos por ficha, mas rapido, reglas cacheadas.
  openai       ChatGPT por la API de OpenAI (openai_key), o el de Azure si openai_url apunta a un despliegue
               de Azure OpenAI. Mismo criterio, mismos ejemplos y mismas comprobaciones.

El modelo local (Ollama) no vive aqui: es una demo aparte, en la carpeta demo/, que sustituye
llamar_modelo desde fuera sin tocar este fichero.
"""
import os
import sys
import re
import shutil
import base64
import subprocess
import threading
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from actos import DESCRIPTOR as DESCRIPTOR_ACTO, comparar as comparar_acto
from situaciones import cruzar_restricciones
import criterio

BASE_DIR = Path(__file__).resolve().parent
REGLAS_PATH = BASE_DIR / "reglas.md"
REGLAS_EXTRA_PATH = BASE_DIR / "reglas_extra.md"
EJEMPLOS_PATH = BASE_DIR / "ejemplos.md"
WORKDIR = BASE_DIR / "claude_ws"   # carpeta vacia: Claude Code no debe leer nada del proyecto


def carpeta_trabajo():
    """La carpeta de Claude Code de este hilo: con varias fichas a la vez (hilos redaccion-N de servidor.py)
    cada una lleva sus fotogramas a la suya, para que una no borre los de otra."""
    nombre = threading.current_thread().name
    carpeta = WORKDIR / nombre if nombre.startswith("redaccion") else WORKDIR
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta

CAMPOS = ("ENVIO", "NAME", "COMMENT", "RESTRICCIONES")

# configuracion activa (la fija catalogar.py / worker.py con configurar(cfg))
CFG = {
    "redactor": "claude_code",            # "claude_code" (Pro, sin coste), "api" (clave, centimos, rapido) u "openai" (ChatGPT/Azure)
    "claude_model": "sonnet",
    "claude_extra_args": [],              # flags extra; herramientas y turnos los pone _cmd_claude
    "claude_timeout": 240,
    "claude_thinking_tokens": 1024,       # presupuesto de pensamiento de Claude Code (MAX_THINKING_TOKENS)
    "claude_token": "",                   # token de un año (claude setup-token): la sesion no caduca a los pocos dias
    "api_key": "",
    "api_reserva": True,                  # si Claude Code pierde la sesion y hay api_key, se redacta con la API mientras
    "api_model": "claude-haiku-4-5-20251001",
    "api_max_tokens": 1200,
    "openai_key": "",
    "openai_model": "gpt-5-mini",
    "openai_url": "",                     # vacio = OpenAI; o la direccion del despliegue de Azure OpenAI
    "openai_max_tokens": 6000,            # los modelos que razonan gastan parte en pensar antes de escribir
    "acortar_comment": True,
    "reglas": "reglas.md",                # fichero de reglas a usar (reglas.md o reglas_ligeras.md)
    "generar_normal": True,               # pedir en la misma llamada la version en escritura normal
    "miniaturas": 0,                      # fotogramas que se adjuntan al modelo (0 = ninguno)
    "escenas": True,                      # clasificador local de escenas, si esta entrenado
    "escenas_umbral": 0.6,                # parte minima de fotogramas que votan por la misma clase
    "escenas_fiabilidad_min": 0.85,       # acierto minimo medido al entrenar para fiarse de una clase
    "escenas_sin_imagenes": True,         # con etiqueta fiable, no adjuntar los fotogramas
    "escenas_fotogramas": 6,              # fotogramas que se sacan solo para el clasificador (no van al modelo)
    "ejemplos_por_ficha": 8,              # fichas aprobadas que acompañan a cada envio: las mas parecidas a el
}


def configurar(cfg):
    """Toma de config.json las claves que afectan al redactor."""
    for k in CFG:
        if k in cfg:
            CFG[k] = cfg[k]

CARGO_GENTILICIO_RE = re.compile(
    r"\b(PRESIDENTE|PRESIDENTA|PRIMER MINISTRO|PRIMERA MINISTRA|MINISTRO|MINISTRA|CANCILLER|ALCALDE|ALCALDESA|"
    r"REY|REINA|PRINCIPE|PRINCESA|GOBERNADOR|GOBERNADORA|SECRETARIO|SECRETARIA|PORTAVOZ|LIDER|EMBAJADOR|EMBAJADORA)\s+"
    r"(FRANCES|FRANCESA|ALEMAN|ALEMANA|ITALIANO|ITALIANA|ESPAÑOL|ESPAÑOLA|BRITANICO|BRITANICA|ESTADOUNIDENSE|RUSO|RUSA|"
    r"UCRANIANO|UCRANIANA|CHINO|CHINA|ISRAELI|IRANI|TURCO|TURCA|POLACO|POLACA|PORTUGUES|PORTUGUESA|ARGENTINO|ARGENTINA|"
    r"MEXICANO|MEXICANA|BRASILEÑO|BRASILEÑA|JAPONES|JAPONESA|INDIO|INDIA|SIRIO|SIRIA|PALESTINO|PALESTINA|EGIPCIO|EGIPCIA|"
    r"MARROQUI|SAUDI|CANADIENSE|AUSTRALIANO|AUSTRALIANA|HOLANDES|HOLANDESA|BELGA|SUECO|SUECA|NORUEGO|NORUEGA|DANES|DANESA|"
    r"FINLANDES|FINLANDESA|GRIEGO|GRIEGA|HUNGARO|HUNGARA|CHECO|CHECA|AUSTRIACO|AUSTRIACA|SUIZO|SUIZA|IRLANDES|IRLANDESA)\s+"
    r"[A-ZÑ][A-ZÑ\-]+\s+[A-ZÑ][A-ZÑ\-]+"
)

# Cargo delante del nombre en aposicion: EL PRESIDENTE DE FRANCIA, EMMANUEL MACRON (lo correcto es al reves)
CARGO_DELANTE_RE = re.compile(
    r"\b(PRESIDENTE|PRESIDENTA|PRIMER MINISTRO|PRIMERA MINISTRA|MINISTRO|MINISTRA|CANCILLER|ALCALDE|ALCALDESA|"
    r"GOBERNADOR|GOBERNADORA|PORTAVOZ|SECRETARIO|SECRETARIA|DIRECTOR|DIRECTORA|EMBAJADOR|EMBAJADORA|ACTIVISTA|"
    r"ABOGADO|ABOGADA|CANTANTE|ACTOR|ACTRIZ|ENTRENADOR|ENTRENADORA|JUGADOR|JUGADORA|PILOTO|CONSEJERO DELEGADO)"
    r"(?: (?:DE|DEL|DE LA|DE LOS) [A-ZÑ]+(?: [A-ZÑ]+){0,3})?, "
    r"(?!(?:SOBRE|EN|DURANTE|TRAS|CON|ANTE|Y|DE|DEL|INCLUYE|A|AL|POR|PARA|SIN|SEGUN|MOSTRANDO|JUNTO|ACOMPAÑADO|ACOMPAÑADA)\b)"
    r"([A-ZÑ]{2,}(?:-[A-ZÑ]+)?(?: [A-ZÑ]{2,}(?:-[A-ZÑ]+)?){1,2})(?=,|\.)")

VERBOS_INICIO = {
    # Solo formas que no son tambien sustantivo. CRITICA, RECHAZO, ANUNCIO, DENUNCIA y MUESTRA
    # quedan fuera a proposito: en estilo nominal son nombres (CRITICA A LOS REPUBLICANOS...).
    "ADVIERTE", "ACUSA", "PIDE", "DICE", "AFIRMA", "ANUNCIA", "SEÑALA", "ASEGURA",
    "RECHAZA", "CELEBRA", "EXPLICA", "DEFIENDE", "RECLAMA", "EXIGE",
    "CONFIRMA", "NIEGA", "RESPONDE", "ADVIRTIO", "ACUSO", "PIDIO", "DIJO", "AFIRMO",
    "SEÑALO", "ASEGURO", "DENUNCIO", "SE", "HAY", "ES", "SON", "ESTA", "ESTAN", "HA", "HAN",
}

# pais inicial de NAME -> raices de gentilicio que no deben repetirse en el resto del NAME
GENTILICIOS = {
    "EEUU": ("ESTADOUNIDENSE", "NORTEAMERICANO", "NORTEAMERICANA"),
    "ESPAÑA": ("ESPAÑOL", "ESPAÑOLA"),
    "SIRIA": ("SIRIO", "SIRIA"),
    "PALESTINA": ("PALESTINO", "PALESTINA"),
    "ISRAEL": ("ISRAELI",),
    "UCRANIA": ("UCRANIANO", "UCRANIANA"),
    "RUSIA": ("RUSO", "RUSA"),
    "FRANCIA": ("FRANCES", "FRANCESA"),
    "ALEMANIA": ("ALEMAN", "ALEMANA"),
    "ITALIA": ("ITALIANO", "ITALIANA"),
    "PORTUGAL": ("PORTUGUES", "PORTUGUESA"),
    "REINO": ("BRITANICO", "BRITANICA"),
    "IRAN": ("IRANI",),
    "IRAK": ("IRAQUI",),
    "CHINA": ("CHINO",),
    "JAPON": ("JAPONES", "JAPONESA"),
    "MEXICO": ("MEXICANO", "MEXICANA"),
    "ARGENTINA": ("ARGENTINO",),
    "BRASIL": ("BRASILEÑO", "BRASILEÑA"),
    "MARRUECOS": ("MARROQUI",),
    "TURQUIA": ("TURCO", "TURCA"),
    "KENIA": ("KENIANO", "KENIANA"),
    "NEPAL": ("NEPALI", "NEPALES"),
    "TAILANDIA": ("TAILANDES", "TAILANDESA"),
}


class RedactorError(Exception):
    pass


def criterio_vigente():
    """El criterio con el que se redacta: el base con los cambios del catalogador y sus ajustes al final."""
    ruta = BASE_DIR / CFG.get("reglas", "reglas.md")
    if not ruta.exists():
        ruta = REGLAS_PATH
    # el criterio base de la version instalada con los cambios del catalogador encima (criterio_cambios.json)
    base = criterio.componer(ruta.name, ruta.read_text(encoding="utf-8"))
    extra = []
    if REGLAS_EXTRA_PATH.exists():
        for linea in REGLAS_EXTRA_PATH.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#"):
                extra.append("- " + linea.lstrip("-• ").strip())
    if extra:
        base += ("\n\n=== AJUSTES DEL CATALOGADOR (prevalecen sobre todo lo anterior) ===\n\n" + "\n".join(extra) + "\n")
    return base


def _reglas():
    base = criterio_vigente()
    # la version en escritura normal se pide aqui, con el resto de instrucciones fijas (va en la parte
    # cacheada de la API y no contradice el "devuelve solo estas lineas" del preambulo)
    if CFG.get("generar_normal", True):
        base += (
            "\n\n=== VERSION EN ESCRITURA NORMAL ===\n\n"
            "Despues de las tres lineas, y solo despues, anade estas tres con el MISMO texto de NAME, COMMENT y "
            "RESTRICCIONES pasado a escritura normal del español: mayuscula solo al inicio de frase y en nombres propios "
            "(personas, lugares, instituciones, obras), minusculas en el resto, tildes y dieresis correctas, siglas en "
            "mayusculas (EEUU, ONU, OTAN, UE, AP, RTVE). Prohibido cambiar, añadir, quitar o reordenar palabras, cifras o "
            "signos: solo cambian mayusculas, minusculas y tildes.\n"
            "NAME_NORMAL: ...\nCOMMENT_NORMAL: ...\nRESTRICCIONES_NORMAL: ...\n"
        )
    return base


DURACION_RE = re.compile(r"Duration\s*:?\s*\n*\s*(\d{1,2}:\d{2}:\d{2})", re.I)


def duracion_de(texto):
    """Duracion del video tal como la da la pagina (Reuters y AP la ponen en los metadatos), o ''."""
    m = DURACION_RE.search(texto or "")
    return m.group(1) if m else ""


def listar_ejemplos():
    """Fichas aprobadas guardadas en ejemplos.md, en orden. Cada una: numero, envio, name, comment, restricciones."""
    if not EJEMPLOS_PATH.exists():
        return []
    texto = "\n".join(l for l in EJEMPLOS_PATH.read_text(encoding="utf-8").splitlines() if not l.startswith("#"))
    salida = []
    for bloque in re.split(r"\n\s*\n", texto):
        if "NAME:" not in bloque:
            continue
        campos = {}
        actual = None
        for linea in bloque.strip().splitlines():
            m = re.match(r"^\s*(ENVIO|NAME|COMMENT|RESTRICCIONES)\s*:\s*(.*)$", linea, re.I)
            if m:
                actual = m.group(1).upper()
                campos[actual] = m.group(2).strip()
            elif actual and linea.strip():
                campos[actual] += " " + linea.strip()
        if not campos.get("NAME"):
            continue
        envio = campos.get("ENVIO", "")
        num = envio.split("·")[0].strip() if envio else ""
        salida.append({
            "numero": num,
            "envio": envio,
            "name": campos.get("NAME", ""),
            "comment": campos.get("COMMENT", ""),
            "restricciones": campos.get("RESTRICCIONES", "SIN AVISO"),
        })
    return salida


def anadir_ejemplo(campos):
    """Guarda una ficha aprobada en ejemplos.md. Si ya hay una con ese numero, la sustituye.
    Devuelve (numero_de_ejemplos, None); el segundo valor queda por compatibilidad."""
    envio = (campos.get("ENVIO") or "").strip()
    num = envio.split("·")[0].strip() if envio else ""
    campos = dict(campos, RESTRICCIONES=limpiar_restricciones(campos.get("RESTRICCIONES") or "SIN AVISO"))
    if not campos.get("NAME") or not campos.get("COMMENT"):
        raise ValueError("La ficha no tiene NAME o COMMENT")
    if num:
        quitar_ejemplo(num)
    bloque = (f"ENVIO: {envio}\nNAME: {campos['NAME'].strip()}\n"
              f"COMMENT: {campos['COMMENT'].strip()}\n"
              f"RESTRICCIONES: {(campos.get('RESTRICCIONES') or 'SIN AVISO').strip()}\n")
    if not EJEMPLOS_PATH.exists():
        EJEMPLOS_PATH.write_text("# Fichas aprobadas\n", encoding="utf-8")
    with EJEMPLOS_PATH.open("a", encoding="utf-8") as f:
        f.write("\n" + bloque)
    # no hay tope: en cada envio solo entran las mas parecidas (elegir_ejemplos)
    return len(listar_ejemplos()), None


def quitar_ejemplo(numero):
    """Borra la ficha aprobada de ese numero. Devuelve True si borro algo."""
    numero = str(numero).strip()
    ejemplos = listar_ejemplos()
    quedan = [e for e in ejemplos if e["numero"] != numero]
    if len(quedan) == len(ejemplos):
        return False
    cabecera = [l for l in EJEMPLOS_PATH.read_text(encoding="utf-8").splitlines() if l.startswith("#")]
    bloques = [f"ENVIO: {e['envio']}\nNAME: {e['name']}\nCOMMENT: {e['comment']}\nRESTRICCIONES: {e['restricciones']}\n"
               for e in quedan]
    EJEMPLOS_PATH.write_text("\n".join(cabecera) + "\n" + ("\n" + "\n".join(bloques) if bloques else ""), encoding="utf-8")
    return True


def anadir_regla(texto):
    """Añade una regla a reglas_extra.md con la fecha. Devuelve la linea escrita."""
    from datetime import date
    texto = " ".join(str(texto).split()).strip().lstrip("-• ")
    if not texto:
        raise ValueError("Regla vacia")
    linea = f"{texto}  ({date.today().isoformat()})"
    with REGLAS_EXTRA_PATH.open("a", encoding="utf-8") as f:
        f.write(linea + "\n")
    return linea


def listar_reglas_extra():
    if not REGLAS_EXTRA_PATH.exists():
        return []
    return [l.strip() for l in REGLAS_EXTRA_PATH.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#")]


# ====================================================================== fichas aprobadas parecidas
# Palabras que no distinguen un envio de otro (ingles de las agencias, español de las fichas)
VACIAS = set("""
THE AND FOR WITH FROM THAT THIS THESE THOSE ARE WAS WERE HAS HAVE HAD NOT BUT ITS HIS HER THEIR THEY SAID SAYS SAYING
ABOUT AFTER BEFORE OVER INTO ONTO WHO WHICH WHEN WHERE WHILE WILL WOULD CAN COULD ALSO MORE THAN BEEN BEING ONE TWO
VIDEO SHOWS SHOW SHOT SHOTS STORY SOUNDBITE ENGLISH SPANISH FRENCH NATS NATURAL SOUND REUTERS ASSOCIATED PRESS EBU
ACCESS RESTRICTIONS DURATION SOURCE LOCATION DATE MONDAY TUESDAY WEDNESDAY THURSDAY FRIDAY SATURDAY SUNDAY
JANUARY FEBRUARY MARCH APRIL MAY JUNE JULY AUGUST SEPTEMBER OCTOBER NOVEMBER DECEMBER WIDE MEDIUM CLOSE VARIOUS
DEL LOS LAS CON POR PARA QUE UNA UNO SUS SOBRE ENTRE TRAS ANTE DESDE HASTA SIN MAS COMO ESTE ESTA ESTOS ESTAS
PLANOS PLANO GENERAL GENERALES DETALLE VARIOS DIVERSOS IMAGENES DECLARACIONES AVISO
""".split())


def _palabras(texto):
    t = unicodedata.normalize("NFKD", (texto or "").upper())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    # raiz de seis letras: ZELENSKIY (agencia) y ZELENSKI (ficha) cuentan como la misma palabra
    return {w[:6] for w in re.findall(r"[A-Z0-9]{3,}", t) if w not in VACIAS and not w.isdigit()}


# ====================================================================== salud de las fichas aprobadas
USO_PATH = BASE_DIR / "cola" / "ejemplos_uso.json"
DIAS_SIN_USO = 30            # una aprobada que lleva este tiempo sin elegirse para ningun envio, "sin uso"
PARECIDO_REPETIDA = .6       # palabras en comun (Jaccard) a partir de las que dos aprobadas son la misma


def _leer_uso():
    import json
    try:
        datos = json.loads(USO_PATH.read_text(encoding="utf-8"))
        return datos if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        return {}


_CERROJO_USO = threading.Lock()


def registrar_uso(numeros):
    """Apunta que esas aprobadas han acompañado a un envio (para saber cuales no se usan nunca)."""
    with _CERROJO_USO:                      # varias fichas a la vez: que no se pisen al escribir
        _registrar_uso(numeros)


def _registrar_uso(numeros):
    import json
    uso = _leer_uso()
    hoy = datetime.now().strftime("%Y-%m-%d")
    uso.setdefault("_desde", hoy)
    for n in numeros:
        if n:
            d = uso.setdefault(n, {"veces": 0, "ultimo": ""})
            d["veces"] += 1
            d["ultimo"] = hoy
    try:
        USO_PATH.parent.mkdir(exist_ok=True)
        tmp = USO_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(uso, ensure_ascii=False), encoding="utf-8")
        tmp.replace(USO_PATH)
    except OSError:
        pass


_SALUD = {"marca": None, "datos": {}}


def salud_ejemplos():
    """Estado de cada aprobada, por numero: {estado, motivos, veces, ultimo}.
       repetida  hay otra aprobada mas nueva casi igual (mismo pais al frente y la mayoria de palabras en comun)
       revisar   el validador de ahora le pone avisos: puede arrastrar algo que el criterio ya corrige
       sin_uso   en DIAS_SIN_USO dias no se ha elegido nunca para acompañar a un envio
       valiosa   lo demas
    Las repetidas ya no entran en la redaccion; las de revisar entran solo si no hay otras igual de parecidas."""
    try:
        marca = (EJEMPLOS_PATH.stat().st_mtime_ns, USO_PATH.stat().st_mtime_ns if USO_PATH.exists() else 0)
    except OSError:
        return {}
    if _SALUD["marca"] == marca:
        return _SALUD["datos"]
    ejemplos = listar_ejemplos()
    uso = _leer_uso()
    desde = uso.get("_desde")
    dias_mirando = (datetime.now() - datetime.strptime(desde, "%Y-%m-%d")).days if desde else 0
    bolsas = [_palabras(e["name"] + " " + e["comment"]) for e in ejemplos]
    datos = {}
    for i, e in enumerate(ejemplos):
        clave = e["numero"] or f"#{i + 1}"
        motivos = []
        repetida_de = None
        pais = (e["name"].split() or [""])[0]
        for j in range(len(ejemplos) - 1, i, -1):                 # las de despues son mas nuevas
            otra = ejemplos[j]
            if (otra["name"].split() or [""])[0] != pais:
                continue
            union = bolsas[i] | bolsas[j]
            if union and len(bolsas[i] & bolsas[j]) / len(union) >= PARECIDO_REPETIDA:
                repetida_de = otra["numero"] or f"#{j + 1}"
                break
        try:
            avisos = validar({"ENVIO": e["envio"], "NAME": e["name"], "COMMENT": e["comment"],
                              "RESTRICCIONES": e["restricciones"]})
        except Exception:
            avisos = []
        u = uso.get(e["numero"]) or {}
        if repetida_de:
            estado = "repetida"
            motivos.append(f"Casi igual que la {repetida_de}, más nueva")
        elif avisos:
            estado = "revisar"
            motivos += avisos[:3]
        elif dias_mirando >= DIAS_SIN_USO and not u.get("veces"):
            estado = "sin_uso"
            motivos.append(f"En {dias_mirando} días no ha acompañado a ningún envío")
        else:
            estado = "valiosa"
        datos[clave] = {"estado": estado, "motivos": motivos, "veces": u.get("veces", 0), "ultimo": u.get("ultimo", ""),
                        "repetida_de": repetida_de}
    _SALUD.update(marca=marca, datos=datos)
    return datos


def elegir_ejemplos(ficha, ejemplos=None, n=None):
    """Las fichas aprobadas mas parecidas a este envio, para que acompañen a la redaccion sin cargar todas.
    Parecido: palabras en comun (nombres, lugares, slug y titular de la agencia, que las aprobadas guardan
    en ENVIO), pesando mas las raras. Si hay menos parecidas que n, se completa con las mas recientes."""
    ejemplos = listar_ejemplos() if ejemplos is None else ejemplos
    n = int(CFG.get("ejemplos_por_ficha", 8) if n is None else n)
    if n <= 0 or not ejemplos:
        return []
    if len(ejemplos) <= n:
        return list(ejemplos)
    import math
    buscadas = _palabras(" ".join([ficha.get("slug") or "", ficha.get("headline") or "",
                                   (ficha.get("texto") or "")[:2500]]))
    bolsas = [_palabras(" ".join([e["envio"], e["name"], e["comment"]])) for e in ejemplos]
    df = {}
    for b in bolsas:
        for w in b:
            df[w] = df.get(w, 0) + 1
    total = len(ejemplos)
    salud = salud_ejemplos()
    estado = lambda i: (salud.get(ejemplos[i]["numero"] or f"#{i + 1}") or {}).get("estado")
    puntos = []
    for i, b in enumerate(bolsas):
        if estado(i) == "repetida":              # su version mas nueva ya enseña lo mismo
            continue
        comunes = buscadas & b
        p = sum(math.log((total + 1) / df[w]) for w in comunes) / math.sqrt(max(len(b), 1))
        if estado(i) == "revisar":
            p *= .5                              # entra solo si no hay otra igual de parecida
        puntos.append((p, i))
    elegidos = [i for p, i in sorted(puntos, key=lambda x: (-x[0], -x[1])) if p > 0][:n]
    for i in range(total - 1, -1, -1):          # el resto, las mas recientes (y sanas)
        if len(elegidos) >= n:
            break
        if i not in elegidos and estado(i) not in ("repetida", "revisar"):
            elegidos.append(i)
    return [ejemplos[i] for i in elegidos]


def bloque_ejemplos(ficha):
    elegidos = elegir_ejemplos(ficha)
    if not elegidos:
        return ""
    registrar_uso([e["numero"] for e in elegidos])
    total = len(listar_ejemplos())
    bloques = [f"ENVIO: {e['envio']}\nNAME: {e['name']}\nCOMMENT: {e['comment']}\nRESTRICCIONES: {e['restricciones']}"
               for e in elegidos]
    return ("=== FICHAS APROBADAS POR EL CATALOGADOR"
            + (f" (las {len(elegidos)} mas parecidas a este envio, de {total})" if total > len(elegidos) else "")
            + " ===\n\n"
            "Sirven para el tono, el orden y la medida. Cuando una ficha aprobada contradiga una regla del "
            "criterio, manda la regla: las fichas se aprobaron enteras, no frase a frase, y pueden arrastrar "
            "detalles que el criterio ya corrige.\n\n" + "\n\n".join(bloques) + "\n\n")


def construir_partes(ficha):
    """(system, user): las reglas van como system para poder cachearlas en la API; las fichas aprobadas
    parecidas, en user, porque cambian con cada envio."""
    texto = ficha.get("texto", "")
    datos = (
        f"agencia: {ficha.get('agencia', '')}\n"
        f"numero: {ficha.get('numero', '')}\n"
        f"fecha: {ficha.get('fecha', '')}\n"
        f"slug: {ficha.get('slug', '')}\n"
        f"headline: {ficha.get('headline', '')}\n"
        f"duracion: {duracion_de(texto) or 'no consta'}\n"
    )
    pistas = ficha.get("pistas") or ""            # lo que situaciones.py ha detectado en el texto
    user = (
        bloque_ejemplos(ficha)
        + "=== DATOS DE CODIGO ===\n" + datos
        + (("\n=== DETECTADO AUTOMATICAMENTE EN EL TEXTO (compruebalo, no lo copies a ciegas) ===\n" + pistas + "\n") if pistas else "")
        + "\n=== TEXTO DE LA FICHA ===\n" + texto
        + "\n=== FIN ===\n\nDevuelve ahora las lineas pedidas.\n"
    )
    return _reglas(), user


def _ruta_claude():
    """Ejecutable de Claude Code: variable CLAUDE_EXE, PATH, o la ruta del instalador nativo."""
    env = os.environ.get("CLAUDE_EXE")
    if env and Path(env).exists():
        return env
    exe = shutil.which("claude")
    if exe:
        return exe
    # instalador nativo (~/.local/bin) o npm en Windows (%APPDATA%\\npm), que a veces no esta en el PATH
    # del programa aunque si en el de la ventana de comandos
    appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    for cand in (Path.home() / ".local" / "bin" / "claude.exe", Path.home() / ".local" / "bin" / "claude",
                 appdata / "npm" / "claude.cmd", appdata / "npm" / "claude"):
        if cand.exists():
            return str(cand)
    return "claude"


# "claude" no esta instalado: lo dice la shell de Windows (en español o en ingles) o la de Linux
NO_INSTALADO_RE = re.compile(r"no se reconoce como un comando|is not recognized as an internal|command not found|"
                             r"^\S*: \d*:? ?\S*claude\S*: not found$", re.I | re.M)


# Flags que pudiera traer claude_extra_args de un config.json antiguo y que fija el redactor: las
# herramientas y los turnos. La lista antigua nombraba MultiEdit, que Claude Code ya no tiene, y eso
# hacia que cada vez que el modelo intentaba usar una herramienta la llamada acabara en
# 'Permission deny rule "MultiEdit" matches no known tool' + 'Reached max turns (1)'.
FLAGS_FIJADOS = {"--tools", "--allowedTools", "--allowed-tools", "--disallowedTools", "--disallowed-tools",
                 "--max-turns"}
TURNOS_CON_IMAGENES = 4        # abrir los fotogramas con Read gasta turnos; sin herramientas basta uno


def _cmd_claude(model, extra_args, con_imagenes=False):
    """Orden de Claude Code. Sin herramientas y un solo turno (el modelo solo tiene que escribir las
    cuatro lineas: asi un intento de usar una herramienta no se come la llamada), o con Read y unos
    turnos mas cuando se le adjuntan fotogramas, que tiene que abrirlos."""
    limpios, saltar = [], False
    for a in list(extra_args or []):
        if saltar:
            saltar = False
            continue
        if a.split("=", 1)[0] in FLAGS_FIJADOS:
            saltar = "=" not in a          # "--flag valor": salta tambien el valor; "--flag=valor": ya va junto
            continue
        limpios.append(a)
    return ([_ruta_claude(), "-p", "--model", model, "--output-format", "text",
             "--tools", "Read" if con_imagenes else "",
             "--max-turns", str(TURNOS_CON_IMAGENES if con_imagenes else 1)] + limpios)


def _matar(proceso):
    """Mata el proceso y lo que haya lanzado: en Windows claude es un .cmd que arranca node, y matar
    solo la shell deja a node vivo con las tuberias abiertas (y a la llamada esperando para siempre)."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proceso.pid)], capture_output=True, timeout=15)
        else:
            proceso.kill()
        proceso.communicate(timeout=15)
    except Exception:
        pass


class ModeloNoDisponible(RedactorError):
    """El modelo no puede atender ahora y no es cosa de esta ficha: limite de uso agotado, servicio
    saturado, sesion de Claude Code caducada. espera = segundos que conviene esperar antes de volver a
    intentarlo (None: hace falta que alguien haga algo, como volver a entrar)."""

    def __init__(self, mensaje, espera=None):
        super().__init__(mensaje)
        self.espera = espera


# Mensajes de Claude Code que significan "ahora no", no "esta ficha no": (patron, espera en segundos o None)
NO_DISPONIBLE = [
    (re.compile(r"hit your (?:usage )?limit|usage limit|out of (?:usage|credits)|limit (?:reached|exceeded)|"
                r"credit balance|quota", re.I), 15 * 60),
    (re.compile(r"rate limit|too many requests|\b429\b|overloaded|\b529\b|\b503\b|service unavailable|"
                r"internal server error|\b500\b|connection (?:error|refused|reset)|ECONNRE|ETIMEDOUT|"
                r"fetch failed|network error|socket hang up", re.I), 5 * 60),
    (re.compile(r"not logged in|please (?:run )?/?login|log in|authentication|unauthorized|invalid api key|"
                r"\b401\b|token (?:has )?expired|oauth", re.I), None),
]
RESET_RE = re.compile(r"reset(?:s)?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.I)


def _espera_hasta_reset(texto):
    """Si el mensaje dice a que hora se renueva el limite ("resets at 3am"), los segundos hasta entonces."""
    m = RESET_RE.search(texto or "")
    if not m:
        return None
    try:
        hora, minuto, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if ampm == "pm" and hora < 12:
            hora += 12
        if ampm == "am" and hora == 12:
            hora = 0
        ahora = datetime.now()
        objetivo = ahora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
        if objetivo <= ahora:
            objetivo += timedelta(days=1)
        return int((objetivo - ahora).total_seconds()) + 60
    except ValueError:
        return None


def clasificar_fallo(texto):
    """(espera, motivo) si el texto del fallo es un "ahora no" del modelo; None si es un fallo normal."""
    for rx, espera in NO_DISPONIBLE:
        m = rx.search(texto or "")
        if m:
            if espera is not None:
                espera = _espera_hasta_reset(texto) or espera
            return espera, m.group(0)
    return None


def llamar_claude(prompt, model="sonnet", extra_args=None, timeout=240, con_imagenes=False):
    carpeta = carpeta_trabajo()
    cmd = _cmd_claude(model, extra_args, con_imagenes)
    env = dict(os.environ)
    # una ANTHROPIC_API_KEY del sistema mandaria sobre la suscripcion (y cobraria): fuera
    env.pop("ANTHROPIC_API_KEY", None)
    if CFG.get("claude_token"):
        env["CLAUDE_CODE_OAUTH_TOKEN"] = str(CFG["claude_token"]).strip()
    if CFG.get("claude_thinking_tokens"):
        env["MAX_THINKING_TOKENS"] = str(CFG["claude_thinking_tokens"])

    def lanzar(orden, shell):
        # errors="replace": la consola de Windows en español escribe sus mensajes en cp850, y una
        # ruta con tilde en un error no debe convertirse en un UnicodeDecodeError que tape el error real
        return subprocess.Popen(orden, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", cwd=str(carpeta), shell=shell, env=env)

    try:
        proceso = lanzar(cmd, False)
    except FileNotFoundError:
        # en Windows, claude puede ser un .cmd que solo arranca a traves de la shell
        proceso = lanzar(subprocess.list2cmdline(cmd), True)
    try:
        salida, errores = proceso.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        _matar(proceso)
        raise RedactorError(f"Claude Code no respondio en {timeout} s")
    if proceso.returncode != 0:
        # el motivo puede venir por stderr o, con --output-format text, por stdout (limite de uso, login...)
        dicho = " | ".join(t.strip() for t in ((errores or ""), (salida or "")) if t.strip())
        if NO_INSTALADO_RE.search(dicho):
            # sin espera: no se arregla solo; si hay clave de la API de reserva, se redacta con ella
            raise ModeloNoDisponible("Claude Code no esta instalado en este equipo (o no se encuentra). Instalalo o "
                                     "elige otro cerebro en Admin → Ajustes → Redaccion", None)
        fallo = clasificar_fallo(dicho)
        if fallo:
            espera, motivo = fallo
            raise ModeloNoDisponible(f"Claude Code no puede atender ahora ({motivo}): {dicho[:400]}", espera)
        raise RedactorError(f"Claude Code devolvio error {proceso.returncode}: {dicho[:800] or 'sin mensaje'}")
    return salida


class _AvisosDelHilo:
    """Lista de avisos propia de cada hilo: con varias fichas a la vez, los avisos de una llamada no se
    mezclan con los de otra."""
    _local = threading.local()

    def _lista(self):
        if not hasattr(self._local, "lista"):
            self._local.lista = []
        return self._local.lista

    def append(self, x):
        self._lista().append(x)

    def clear(self):
        self._lista().clear()

    def __iter__(self):
        return iter(list(self._lista()))

    def __len__(self):
        return len(self._lista())


HILO = threading.local()              # datos de la ultima redaccion de este hilo (acortado: hubo segunda pasada)
AVISOS_LLAMADA = _AvisosDelHilo()     # avisos que deja la ultima llamada al modelo (respuesta cortada...); redactar los recoge


def llamar_api(system, user, timeout=120):
    """Llamada directa a la API de Anthropic con las reglas cacheadas como system."""
    import requests
    if not CFG.get("api_key"):
        raise RedactorError("redactor=api pero falta api_key en config.json")
    cuerpo = {
        "model": CFG["api_model"],
        "max_tokens": int(CFG.get("api_max_tokens", 1200)),
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
    }
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CFG["api_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json=cuerpo, timeout=timeout,
        )
    except requests.RequestException as e:
        raise ModeloNoDisponible(f"API no accesible: {e}", 5 * 60)
    if r.status_code != 200:
        if r.status_code in (401, 403):
            raise ModeloNoDisponible(f"API devolvio {r.status_code} (clave no valida o sin permiso): {r.text[:300]}", None)
        if r.status_code in (429, 500, 502, 503, 529):
            raise ModeloNoDisponible(f"API devolvio {r.status_code} (limite o servicio saturado): {r.text[:300]}",
                                     15 * 60 if r.status_code == 429 else 5 * 60)
        raise RedactorError(f"API devolvio {r.status_code}: {r.text[:500]}")
    datos = r.json()
    texto = "".join(b.get("text", "") for b in datos.get("content", []) if b.get("type") == "text")
    if datos.get("stop_reason") == "max_tokens":
        # cortada a medias: vale si las cuatro lineas estan enteras (el corte cayo en las _NORMAL);
        # si falta RESTRICCIONES, mejor fallar que dar por bueno un SIN AVISO
        if "RESTRICCIONES" not in parsear(texto)["_presentes"]:
            raise RedactorError(f"La API corto la respuesta en {cuerpo['max_tokens']} tokens antes de RESTRICCIONES: "
                                "sube api_max_tokens en config.json")
        AVISOS_LLAMADA.append(f"La API corto la respuesta en {cuerpo['max_tokens']} tokens (faltan las lineas _NORMAL): sube api_max_tokens")
    return texto


OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def _bloques_openai(user):
    """El mensaje con fotogramas (bloques al estilo de Anthropic) en el formato de OpenAI."""
    if isinstance(user, str):
        return user
    salida = []
    for b in user:
        if b.get("type") == "text":
            salida.append({"type": "text", "text": b["text"]})
        elif b.get("type") == "image":
            src = b["source"]
            salida.append({"type": "image_url", "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}})
    return salida


def llamar_openai(system, user, timeout=120):
    """ChatGPT por la API de OpenAI, o Azure OpenAI si openai_url es de Azure. OpenAI guarda en cache el
    principio repetido del mensaje (el criterio) por su cuenta."""
    import requests
    clave = CFG.get("openai_key")
    if not clave:
        raise RedactorError("redactor=openai pero falta openai_key en config.json")
    url = (CFG.get("openai_url") or "").strip() or OPENAI_URL
    azure = ".azure.com" in url or "api-version=" in url
    cuerpo = {
        "model": CFG.get("openai_model") or "gpt-5-mini",
        "max_completion_tokens": int(CFG.get("openai_max_tokens") or 6000),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": _bloques_openai(user)}],
    }
    cabeceras = {"content-type": "application/json"}
    cabeceras.update({"api-key": clave} if azure else {"authorization": f"Bearer {clave}"})
    try:
        r = requests.post(url, headers=cabeceras, json=cuerpo, timeout=timeout)
    except requests.RequestException as e:
        raise ModeloNoDisponible(f"OpenAI no accesible: {e}", 5 * 60)
    if r.status_code != 200:
        if r.status_code in (401, 403, 404):
            raise ModeloNoDisponible(f"OpenAI devolvio {r.status_code} (clave, modelo o direccion no validos): {r.text[:300]}", None)
        if r.status_code in (429, 500, 502, 503):
            raise ModeloNoDisponible(f"OpenAI devolvio {r.status_code} (limite o servicio saturado): {r.text[:300]}",
                                     15 * 60 if r.status_code == 429 else 5 * 60)
        raise RedactorError(f"OpenAI devolvio {r.status_code}: {r.text[:500]}")
    eleccion = (r.json().get("choices") or [{}])[0]
    texto = (eleccion.get("message") or {}).get("content") or ""
    if eleccion.get("finish_reason") == "length":
        if "RESTRICCIONES" not in parsear(texto)["_presentes"]:
            raise RedactorError(f"OpenAI corto la respuesta en {cuerpo['max_completion_tokens']} tokens antes de "
                                "RESTRICCIONES: sube openai_max_tokens en config.json")
        AVISOS_LLAMADA.append(f"OpenAI corto la respuesta (faltan las lineas _NORMAL): sube openai_max_tokens")
    return texto


CLAUDE_SIN_SESION = {"hasta": 0.0}   # Claude Code sin sesion: mientras, se va directo a la API de reserva
ESPERA_SESION = 10 * 60


def _reserva_api():
    return bool(CFG.get("api_reserva", True) and CFG.get("api_key"))


def llamar_modelo(system, user, timeout=None, con_imagenes=False):
    """Enruta al motor configurado en "redactor": claude_code, api u openai. Si Claude Code ha perdido la sesion
    y hay clave de la API (api_reserva), redacta con la API mientras tanto y vuelve a probar Claude Code
    cada 10 minutos."""
    import time as _t
    if CFG.get("redactor") == "api":
        return llamar_api(system, user, timeout=timeout or 120)
    if CFG.get("redactor") == "openai":
        return llamar_openai(system, user, timeout=timeout or 120)
    if _reserva_api() and _t.time() < CLAUDE_SIN_SESION["hasta"]:
        AVISOS_LLAMADA.append("Redactada con la API de reserva: Claude Code no tiene sesion")
        return llamar_api(system, user, timeout=timeout or 120)
    try:
        return llamar_claude(system + "\n\n" + user, model=CFG["claude_model"], extra_args=CFG["claude_extra_args"],
                             timeout=timeout or CFG["claude_timeout"], con_imagenes=con_imagenes)
    except ModeloNoDisponible as e:
        if e.espera is not None or not _reserva_api():
            raise
        CLAUDE_SIN_SESION["hasta"] = _t.time() + ESPERA_SESION
        print(f"Claude Code sin sesion ({str(e)[:120]}): se redacta con la API de reserva", flush=True)
        AVISOS_LLAMADA.append("Redactada con la API de reserva: Claude Code no tiene sesion")
        return llamar_api(system, user, timeout=timeout or 120)


def probar_modelo():
    """Una llamada minima para saber si el modelo atiende. (True, texto) o (False, motivo)."""
    try:
        if CFG.get("redactor") == "api":
            salida = llamar_api("Responde solo con la palabra OK.", "OK?", timeout=60)
        elif CFG.get("redactor") == "openai":
            salida = llamar_openai("Responde solo con la palabra OK.", "OK?", timeout=60)
        else:
            salida = llamar_claude("Responde solo con la palabra OK.", model=CFG["claude_model"],
                                   extra_args=CFG["claude_extra_args"], timeout=90)
            CLAUDE_SIN_SESION["hasta"] = 0.0
        return True, (salida or "").strip()[:80] or "OK"
    except RedactorError as e:
        return False, str(e).splitlines()[0][:300]


def linea_envio(ficha):
    """numero · fecha · slug · headline, con los datos del extractor (nunca los que escriba el modelo)."""
    return " · ".join(str(ficha.get(k) or "") for k in ("numero", "fecha", "slug", "headline"))


def parsear(salida):
    """Los campos de la respuesta del modelo. Un campo sigue en las lineas de debajo hasta la primera
    en blanco: lo que venga despues de un hueco (una nota, una explicacion) no es parte del campo.
    campos["_presentes"] dice que lineas ha escrito el modelo, para distinguir vacia de ausente."""
    campos = {c: "" for c in CAMPOS}
    presentes = set()
    actual = None
    for linea in salida.splitlines():
        m = re.match(r"^\s*\**\s*(ENVIO|NAME_NORMAL|COMMENT_NORMAL|RESTRICCIONES_NORMAL|NAME|COMMENT|RESTRICCIONES)\s*\**\s*:\s*(.*)$", linea, re.I)
        if m:
            actual = m.group(1).upper()
            presentes.add(actual)
            campos[actual] = m.group(2).strip().strip("*").strip()
        elif actual and linea.strip():
            campos[actual] = (campos[actual] + " " + linea.strip()).strip()
        else:
            actual = None                      # linea en blanco: se cierra el campo
    campos["_presentes"] = presentes
    return campos


# palabras acortadas con punto (PTE. PDTE. EXPDTE. GRAL. SRA.), que nunca van en la ficha
ABREVIATURA_RE = re.compile(r"\b(?:PTE|PDTE|PDTA|EXPTE|EXPDTE|VICEPTE|GRAL|SR|SRA|SRTA|DR|DRA|DPTO|AYTO|GOB|MIN|SECR|SEC|"
                            r"PROF|ADMON|CTRA|AVDA|ADJ|DTOR|DTORA|DIR|PRES)\.(?=\s|,|$)")


def normalizar(texto):
    """Mayusculas y sin tildes ni dieresis, conservando la Ñ."""
    out = []
    for ch in texto:
        if ch in "ñÑ":
            out.append("Ñ")
            continue
        desc = unicodedata.normalize("NFD", ch)
        out.append("".join(c for c in desc if unicodedata.category(c) != "Mn"))
    texto = "".join(out).upper().strip()
    # sin separadores de miles: 360.000 -> 360000 (no toca decimales tipo 1.5)
    while re.search(r"(\d)\.(\d{3})(?!\d)", texto):
        texto = re.sub(r"(\d)\.(\d{3})(?!\d)", r"\1\2", texto)
    return texto


# la agencia dice que la imagen lleva texto sobreimpreso (rotulos de verdad)
ROTULOS_EXPLICITO_RE = re.compile(
    r"GRAPHICS|CAPTIONS?\b|ON.SCREEN (?:TEXT|GRAPHICS?|CAPTIONS?)|BURN(?:T|ED).IN|LOWER.THIRDS?|CHYRONS?|SUBTITLE|"
    r"\bAS AIRED\b|WITH (?:STATION |TV |CHANNEL )?LOGO|\bTICKER\b|SUPERS\b|\bCCTV\b|SECURITY CAMERA|SURVEILLANCE (?:CAMERA|FOOTAGE)")
# material que suele llevarlos pero no seguro: emitido por una cadena
ROTULOS_PROBABLE_RE = re.compile(r"AIRED ON|TV FOOTAGE|BROADCAST FOOTAGE|TELEVISION FOOTAGE|STATE TV|STATE TELEVISION|\bCGTN\b|\bIRIB\b|\bKCNA\b|\bKRT\b")


def quitar_rotulos(comment):
    """Quita ROTULOS de la frase INCLUYE del COMMENT (solo o con mas cosas), dejando la lista bien unida."""
    def arreglar(m):
        lista = m.group(1)
        if lista.strip() == "ROTULOS":
            return ""
        lista = re.sub(r"^ROTULOS,\s*", "", lista)                 # ROTULOS, A Y B -> A Y B
        lista = re.sub(r"^ROTULOS\s+Y\s+", "", lista)              # ROTULOS Y A -> A
        lista = re.sub(r",\s*ROTULOS\s+Y\s+", " Y ", lista)        # A, ROTULOS Y B -> A Y B
        lista = re.sub(r"\s+Y\s+ROTULOS$", "", lista)              # A Y ROTULOS -> A
        if "," in lista and not re.search(r"\s+Y\s+", lista):     # A, B -> A Y B (se quito el ultimo)
            i = lista.rfind(",")
            lista = lista[:i] + " Y" + lista[i + 1:]
        return "INCLUYE " + lista.strip() + "."
    c = re.sub(r"INCLUYE ([^.]*\bROTULOS\b[^.]*)\.", arreglar, comment)
    return " ".join(c.split())


def quitar_cortesia(restricciones):
    """Quita las frases ROTULAR CORTESIA DE ''X'' de RESTRICCIONES. Devuelve (quitadas, resto)."""
    frases = [f.strip() for f in re.split(r"\.\s*", restricciones or "") if f.strip()]
    quitadas = [f for f in frases if "CORTESIA" in f]
    resto = [f for f in frases if "CORTESIA" not in f]
    return quitadas, (". ".join(resto) + "." if resto else "SIN AVISO")


def limpiar_restricciones(texto):
    """Quita los +++ del formato antiguo: cada bloque pasa a ser una o varias frases terminadas en punto,
    todas seguidas. '+++ A +++ +++ B. C +++' -> 'A. B. C.'  Lo que no lleve +++ se deja igual."""
    texto = (texto or "").strip()
    if "+++" not in texto:
        return texto
    bloques = [b.strip(" .") for b in texto.split("+++") if b.strip(" .")]
    return " ".join(b + "." for b in bloques)


def _huella(texto):
    """Palabras del texto en mayusculas sin tildes ni signos, para comparar versiones sin que cuente la ortografia."""
    return re.findall(r"[A-ZÑ0-9]+", normalizar(texto))


# ====================================================================== escritura normal (minusculas)
SIGLAS = set("""EEUU ONU OTAN UE AP RTVE EBU FMI OMS OIEA BCE PIB IPC NBA FIFA UEFA COI ATP WTA OPEP G7 G20 BRICS UCI
CNN BBC AFP EFE TVE PSOE PP VOX ERC PNV ONG ADN VIH COVID IA EM UNICEF UNESCO ACNUR OCDE OEA ASEAN DGT CIA FBI KGB
FSB OSCE NASA ESA SpaceX TV UCI""".upper().split())


def _forma_suelta(palabra, inicio_frase):
    """Una palabra de la ficha en escritura normal sin ayuda del modelo: siglas como estan, el resto en
    minuscula y con mayuscula inicial al empezar frase. Los nombres propios no se reconocen aqui."""
    nucleo = re.sub(r"[^\wÑñ]", "", palabra)
    if nucleo.upper() in SIGLAS or re.fullmatch(r"\d+[A-ZÑ]+|[A-ZÑ]+\d+|[IVXLC]{2,5}", nucleo or "x"):
        return palabra
    baja = palabra.lower()
    return baja[:1].upper() + baja[1:] if inicio_frase else baja


def fusionar_normal(original, candidato):
    """La version en escritura normal de un campo, palabra a palabra: de la que propone el modelo se toma
    cada palabra que coincide con la original (misma palabra, solo cambian mayusculas y tildes); las que el
    modelo cambio o no puso se pasan a minuscula aqui (_forma_suelta). Asi nunca se pierde el campo entero
    ni queda en mayusculas por una palabra. Devuelve (texto, palabras_resueltas_sin_modelo)."""
    import difflib
    orig = (original or "").split()
    cand = (candidato or "").split()
    clave = lambda w: "".join(_huella(w))
    co, cc = [clave(w) for w in orig], [clave(w) for w in cand]
    elegidas = [None] * len(orig)
    for bloque in difflib.SequenceMatcher(None, co, cc, autojunk=False).get_matching_blocks():
        for k in range(bloque.size):
            if co[bloque.a + k]:
                elegidas[bloque.a + k] = cand[bloque.b + k]
    signos = ".,:;!?()\"'«»"
    salida, sueltas = [], 0
    for i, w in enumerate(orig):
        inicio = i == 0 or bool(re.search(r"[.:;!?]$", orig[i - 1]))
        if elegidas[i] is not None:
            # del modelo solo la palabra; los signos de alrededor, los de la ficha (no se añade ni quita ninguno)
            nucleo = w.strip(signos)
            delante = w[:len(w) - len(w.lstrip(signos))]
            detras = w[len(delante) + len(nucleo):]
            salida.append(delante + (elegidas[i].strip(signos) or nucleo) + detras)
        else:
            salida.append(_forma_suelta(w, inicio))
            sueltas += bool(re.search(r"[A-ZÑ]", w))
    return " ".join(salida), sueltas


def version_normal(campos, propuesta):
    """Los tres campos en escritura normal a partir de lo que propone el modelo (puede faltar). Devuelve
    (normal, avisos)."""
    normal, avisos = {}, []
    for c in ("NAME", "COMMENT", "RESTRICCIONES"):
        normal[c], sueltas = fusionar_normal(campos.get(c, ""), " ".join((propuesta.get(c) or "").split()))
        if sueltas and propuesta.get(c):
            avisos.append(f"{c}: {sueltas} palabra(s) de la version en texto normal no venian bien del modelo y se han "
                          "pasado a minuscula sin el (revisa nombres propios)")
    return normal, avisos


def presentar_normal(campos, timeout=120):
    """Segunda pasada opcional: devuelve NAME, COMMENT y RESTRICCIONES en escritura normal (mayuscula inicial,
    nombres propios, tildes, siglas en mayusculas) sin cambiar ni una palabra. Si el modelo altera el texto,
    las palabras que cambio se pasan a minuscula sin el (fusionar_normal). Devuelve dict con los tres campos y
    una lista de avisos."""
    entrada = "\n".join(f"{c}: {campos.get(c, '')}" for c in ("NAME", "COMMENT", "RESTRICCIONES"))
    prompt = (
        "Estos tres textos son campos de una ficha de archivo audiovisual escritos en mayusculas sin tildes. "
        "Reescribelos en escritura normal del español: mayuscula solo al inicio de frase y en nombres propios "
        "(personas, lugares, instituciones, obras), minusculas en el resto, tildes y dieresis correctas, siglas "
        "en mayusculas (EEUU, ONU, OTAN, UE, AP, RTVE). En NAME, ademas, la primera palabra con mayuscula inicial. "
        "PROHIBIDO cambiar, añadir, quitar o reordenar palabras, cifras o signos: solo cambia mayusculas, minusculas y tildes. "
        "Devuelve exactamente tres lineas con el mismo formato, NAME:, COMMENT: y RESTRICCIONES:, sin nada mas.\n\n" + entrada
    )
    salida = llamar_modelo("Eres un corrector ortotipografico. Respondes solo con las tres lineas pedidas.", prompt, timeout=timeout)
    return version_normal(campos, parsear(salida))


# Deporte: para saber si la ficha es deportiva se mira el SLUG (en Reuters es SOCCER-..., TENNIS-...),
# que es la senal fiable, y el headline solo con marcas inequivocas (competiciones y siglas). Asi una
# noticia politica que mencione de pasada "football stadium" no cuenta como deporte.
DEPORTES = (
    # (marca en el slug, marca inequivoca en cualquier sitio, palabra que debe ir en el NAME)
    (r"\bMOTOGP\b|MOTO ?GP", r"\bMOTOGP\b|MOTO ?GP", "MOTOCICLISMO"),
    (r"FORMULA ?(1|ONE)|\bF1\b", r"FORMULA ?(1|ONE)\b|\bF1 (GP|RACE|GRAND PRIX)\b", "FORMULA 1"),
    (r"\bSOCCER\b|\bFOOTBALL\b", r"CHAMPIONS LEAGUE|EUROPA LEAGUE|PREMIER LEAGUE|LALIGA|LA LIGA\b|\bFIFA\b|\bUEFA\b|WORLD CUP QUALIFIER", "FUTBOL"),
    (r"\bTENNIS\b", r"\bUS OPEN\b|ROLAND GARROS|WIMBLEDON|\bATP\b|\bWTA\b|DAVIS CUP", "TENIS"),
    (r"BASKETBALL", r"\bNBA\b|EUROLEAGUE|EUROBASKET", "BALONCESTO"),
    (r"\bCYCLING\b", r"TOUR DE FRANCE|\bGIRO D|VUELTA A ESPANA", "CICLISMO"),
    (r"ATHLETICS", r"WORLD ATHLETICS|DIAMOND LEAGUE", "ATLETISMO"),
    (r"\bRUGBY\b", r"\bRUGBY\b", "RUGBY"),
    (r"\bGOLF\b", r"RYDER CUP|\bPGA\b|MASTERS AUGUSTA", "GOLF"),
    (r"\bSWIMMING\b|WATERPOLO|WATER POLO", r"WORLD AQUATICS", "NATACION"),
    (r"HANDBALL", r"HANDBALL", "BALONMANO"),
    (r"VOLLEYBALL", r"VOLLEYBALL", "VOLEIBOL"),
    (r"\bBOXING\b", r"\bBOXING\b|\bWBC\b|\bWBA\b", "BOXEO"),
    (r"\bJUDO\b|TAEKWONDO|KARATE", r"\bJUDO\b|TAEKWONDO", "ARTES MARCIALES"),
    (r"\bSKIING\b|SNOWBOARD|BIATHLON", r"\bSKIING\b|BIATHLON", "ESQUI"),
    (r"AMERICAN FOOTBALL|\bNFL\b", r"SUPER BOWL|\bNFL\b", "FUTBOL AMERICANO"),
    (r"BASEBALL", r"\bMLB\b|WORLD SERIES", "BEISBOL"),
    (r"\bHOCKEY\b", r"\bNHL\b|\bHOCKEY\b", "HOCKEY"),
)
PALABRAS_DEPORTE = r"\b(FUTBOL|TENIS|BALONCESTO|ARTES MARCIALES|CICLISMO|ATLETISMO|RUGBY|GOLF|NATACION|BALONMANO|VOLEIBOL|BOXEO|JUDO|TAEKWONDO|KARATE|ESQUI|BEISBOL|HOCKEY|MOTOCICLISMO|MOTOCROSS|AUTOMOVILISMO|WATERPOLO|PIRAGUISMO|REMO|GIMNASIA|HALTEROFILIA|ESGRIMA|TRIATLON|PADEL|BADMINTON|VELA|SURF)\b|FORMULA 1"


def deporte_de(envio, texto=None):
    """Devuelve el deporte de la ficha, o None. envio = la linea ENVIO (numero, fecha, slug, headline).
    Se mira el slug y el titular, no el texto entero: una noticia politica que cita a la FIFA no es
    una ficha de deporte."""
    trozos = [t.strip() for t in (envio or "").split("\u00b7")]
    slug = trozos[2].upper() if len(trozos) > 2 else ""
    cabecera = (envio or "").upper()
    for en_slug, inequivoca, palabra in DEPORTES:
        if (slug and re.search(en_slug, slug)) or re.search(inequivoca, cabecera):
            return palabra
    return None


PAISES_GUERRA = ("PALESTINA", "UCRANIA", "RUSIA", "ISRAEL", "JERUSALEN")
GUERRA_RE = re.compile(r"\bGUERRA\b(?! (MUNDIAL|CIVIL|FRIA|COMERCIAL|DE PRECIOS|ARANCELARIA))")
# lo que hace que un envio sea de la guerra en si (no basta con que la agencia cite "war" o "conflict")
COMBATE_RE = re.compile(r"\bSHELLING\b|\bAIR ?STRIKES?\b|\bAIRSTRIKES?\b|\bBOMBARD\w*|\bBOMBING\b|\bMISSILES?\b|"
                        r"\bDRONE (?:ATTACK|STRIKE)S?\b|\bARTILLERY\b|\bFRONT ?LINES?\b|\bTRENCH\w*|\bCOMBAT\b|"
                        r"\bFIGHTING\b|\bINCURSION\b|\bRAIDS?\b|\bOFFENSIVE\b|\bEXPLOSIONS?\b|\bROCKETS?\b|"
                        r"\bINTERCEPT\w*|\bSTRIKE ON\b|\bATTACK ON\b|\bDAMAGED?\b.{0,40}\b(?:ATTACK|STRIKE)", re.I)
MODA_RE = re.compile(r"FASHION (?:WEEK|SHOW)|\bRUNWAY\b|\bCATWALK\b|READY-TO-WEAR|HAUTE COUTURE|\bCOLLECTION\b.{0,60}"
                     r"\b(?:FASHION|DESIGNER|MODELS?)\b|\bMET GALA\b", re.I)

HABLADOS = ("DECLARACIONES", "RUEDA DE PRENSA", "COMPARECENCIA", "INTERVENCION", "ENTREVISTA")

# Palabras que casi siempre delatan una N donde debia ir una Ñ.
ENE_PERDIDA_RE = re.compile(
    r"\b(ESPANA|ESPANOL|ESPANOLA|ESPANOLES|ESPANOLAS|ANO|ANOS|NINO|NINA|NINOS|NINAS|"
    r"DANO|DANOS|DANADO|DANADA|DANADOS|DANADAS|MANANA|COMPANIA|COMPANIAS|"
    r"SENOR|SENORA|SENORES|MONTANA|MONTANAS|PEQUENO|PEQUENA|PEQUENOS|PEQUENAS|"
    r"ACOMPANADO|ACOMPANADA|ACOMPANADOS|ENSENANZA|SUENO|ENGANO|EXTRANO|EXTRANA|PUNO|BANO)\b")
# (SENAL, CANON y CAMPANA son palabras de verdad, SEÑAL, CAÑON y CAMPAÑA tambien: no se pueden distinguir)

# Transcripciones inglesas que hay que españolizar.
TRANSCRIPCION_RE = re.compile(
    r"\b(ZELENSKIY|ZELENSKYY|MIKHAIL|MOHAMMED|MUHAMMAD|LVIV|KHARKIV|ODESSA|KHERSON|DELHI|SHARAA|"
    r"ABDULLAH|HUSSEIN|TAYYIP|YEVGENY|SERGEI|DMITRY|ANDRIY|OLEKSANDR|KYIV|ZAPORIZHZHIA|"
    r"[A-ZÑ]{4,}SKIY|KH[A-ZÑ]{3,})\b")

# Anglicismos evitables: la parte generica de un nombre compuesto se traduce.
ANGLICISMO_RE = re.compile(
    r"\b(JOINT BASE|LAKE [A-ZÑ]|COUNTY|CHURCH|HIGH SCHOOL|UNIVERSITY OF|"
    r"AIRPORT|CENTRAL STATION|SUPREME COURT|CITY HALL|POLICE DEPARTMENT|FIRE DEPARTMENT|TOWN HALL)\b")

PAISES = {"ESTADOS UNIDOS", "EEUU", "ALEMANIA", "FRANCIA", "ITALIA", "REINO UNIDO", "ESPANA", "ESPAÑA",
          "PORTUGAL", "RUSIA", "UCRANIA", "CHINA", "JAPON", "INDIA", "BRASIL", "MEXICO", "ARGENTINA",
          "COLOMBIA", "CHILE", "PERU", "MARRUECOS", "EGIPTO", "ISRAEL", "PALESTINA", "SIRIA", "IRAN",
          "IRAK", "TURQUIA", "GRECIA", "POLONIA", "SUECIA", "NORUEGA", "ISLANDIA", "CANADA", "AUSTRALIA",
          "FINLANDIA", "DINAMARCA", "PAISES BAJOS", "BELGICA", "SUIZA", "AUSTRIA", "IRLANDA", "ECUADOR"}


def pais_en_parentesis(comment):
    """Devuelve el pais que aparezca dentro del parentesis del LUGAR, o None.
    Caza tanto (BRASIL) como (SAO PAULO, BRASIL)."""
    m = re.match(r"^[^.]{0,60}?\(([^)]+)\)", comment)
    if not m:
        return None
    dentro = [x.strip() for x in m.group(1).split(",")]
    return next((x for x in dentro if x in PAISES), None)


HABLANTE_RE = re.compile(
    r"(?:\(SOUNDBITE\)|\bSOUNDBITE)\s*\([A-Za-z ]+\)\s*([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,3})|"
    r"\bSOT\b[:\s]+([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,3})|"
    r"INTERVIEW WITH\s+([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+){0,3})")
ANONIMO_RE = re.compile(r"^(UNIDENTIFIED|UNNAMED|VOX|RESIDENT|LOCAL|MAN|WOMAN|PROTESTER|PASSER|SHOPPER|FAN|SUPPORTER|VOTER|WORKER|STUDENT|TOURIST|VILLAGER)", re.I)
CUTAWAY_RE = re.compile(r"CUTAWAY|GVS? OF (PRESS|PRESSER|NEWS CONFERENCE|JOURNALISTS|MEDIA|ROOM)|WIDE OF (PRESS|PRESSER|ROOM)|"
                        r"REPORTERS|JOURNALISTS|MEDIA IN ROOM|PODIUM|MICROPHONES|CAMERAS|WALKING (TO|UP TO) (THE )?PODIUM", re.I)


def hablantes(texto):
    """Nombres de quienes hablan segun el shotlist (Reuters/AP SOUNDBITE (Idioma) NOMBRE, ...; EBU SOT NOMBRE
    o INTERVIEW WITH NOMBRE), en mayusculas sin tildes y sin repetir; los anonimos (UNIDENTIFIED...) no."""
    salida = []
    for m in HABLANTE_RE.finditer(texto or ""):
        nombre = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if nombre and not ANONIMO_RE.match(nombre):
            n = normalizar(nombre)
            if n not in salida:
                salida.append(n)
    return salida


def planos_numerados(texto):
    """Lineas de plano del shotlist: numeradas (Reuters/AP) o con guion (EBU)."""
    return re.findall(r"^\s*(?:\d+\.|-)\s*(.+)$", texto or "", flags=re.M)


def planos_reales(texto):
    """Planos del shotlist que no son SOUNDBITE ni planos de sala (cutaways, periodistas, atril)."""
    return [p for p in planos_numerados(texto)
            if not re.search(r"SOUNDBITE|\bSOT\b|\bSAYING\b|\bSPEAKING\b", p, re.I) and not CUTAWAY_RE.search(p)]


def _sin_planos(comment):
    """True si el descriptor hablado va justo tras el LUGAR y en el COMMENT no se describe ninguna imagen
    (salvo la propia palabra INCLUYE, que es lo que se quiere detectar)."""
    tras_lugar = re.sub(r"^[^.]*\.\s*", "", comment, count=1)
    if not any(tras_lugar.startswith(h) for h in HABLADOS + ("EXTRACTO DE", "TESTIMONIOS DE", "ENCUESTA A")):
        return False
    return not re.search(r"\bPLANOS\b|\bRECURSOS\b|\bIMAGENES\b|\bVISTAS\b|\bFOTOS\b", comment)


def _solo_hablado(comment):
    """True si el COMMENT es solo material hablado: el descriptor va justo tras el LUGAR y no hay
    ningun hecho visible detras (ni INCLUYE con planos ni otra frase que describa imagenes)."""
    tras_lugar = re.sub(r"^[^.]*\.\s*", "", comment, count=1)
    if not any(tras_lugar.startswith(h) for h in HABLADOS + ("EXTRACTO DE", "TESTIMONIOS DE", "ENCUESTA A")):
        return False
    # si en algun punto se describen imagenes, el envio no es solo hablado
    return not re.search(r"\bINCLUYE\b|\bPLANOS\b|\bRECURSOS\b|\bIMAGENES\b|\bVISTAS\b|\bFOTOS\b", comment)


def validar(campos):
    avisos = []
    name, comment, restr = campos["NAME"], campos["COMMENT"], campos["RESTRICCIONES"]
    if not name:
        avisos.append("NAME vacio")
    else:
        if len(name) > TOPE_NAME:
            avisos.append(f"NAME de {len(name)} caracteres (tope {TOPE_NAME}; lo normal son {MAX_NAME})")
        if GUERRA_RE.search(name) and name.split()[0] not in PAISES_GUERRA:
            avisos.append(f"GUERRA en el NAME con {name.split()[0]} al frente: solo se usa con "
                          f"PALESTINA, UCRANIA, RUSIA, ISRAEL o JERUSALEN")
        if re.search(r"\bCON MOTIVO (?!DE\b|DEL\b)", name):
            avisos.append("CON MOTIVO sin su DE en el NAME")
        if re.search(r"\bRECURSOS\b", name):
            avisos.append("RECURSOS no va en el NAME: PAIS, lo que se ve y la noticia (RECURSOS DE queda para el COMMENT)")
    if re.search(r"\bSECUELAS?\b", name + " " + comment):
        avisos.append("SECUELAS no se usa: la palabra del archivo es DAÑOS (DAÑOS DE, DAÑOS TRAS)")
        if "CHAMPIONS" in name and "LIGA DE CAMPEONES" in comment:
            avisos.append("El NAME dice CHAMPIONS y el COMMENT LIGA DE CAMPEONES: una sola forma")
        if "," in name:
            avisos.append("NAME con coma: el NAME es una cadena de palabras clave sin puntuacion; "
                          "el cargo va al COMMENT")
        pal = name.split()
        pegadas = {a for a, b in zip(pal, pal[1:]) if a == b}   # DURAN DURAN, NUEVA NUEVA
        repes = [w for w in dict.fromkeys(pal) if len(w) > 3 and pal.count(w) > 1 and w not in pegadas]
        if repes:
            avisos.append(f"NAME repite la palabra {repes[0]}: cada palabra una sola vez")
        if re.search(r"[:;]", name):
            avisos.append("NAME con dos puntos o punto y coma")
        if re.search(r"\b(19|20)\d{2}\b", name) and "GRAN PREMIO" not in name and "TEMPORADA" not in name:
            avisos.append("NAME con un año: revisar si es el asunto")
        # efemerides y siglas con guion: van pegadas (11S, 11M, 7O, COVID)
        m = re.search(r"\b(\d{1,2}-[A-ZÑ]|COVID-\d+|[A-ZÑ]+-\d+)\b", name)
        if m:
            avisos.append(f"NAME con guion ({m.group(1)}): las efemerides y siglas van pegadas (11S, 11M, COVID)")
    if not comment:
        avisos.append("COMMENT vacio")
    else:
        if not comment.endswith("."):
            avisos.append("COMMENT no termina en punto")
        if ":" in comment:
            avisos.append("COMMENT con dos puntos")
        m = ABREVIATURA_RE.search(comment)
        if m:
            avisos.append(f"COMMENT con una palabra abreviada ({m.group(0).strip()}): se escribe entera")
        if len(comment) < 100:
            avisos.append(f"COMMENT de {len(comment)} caracteres (minimo 100): falta gente, motivo o contenido de los planos")
        # IMAGENES DE ARCHIVO es formula obligatoria del patron de archivo: no es palabra poco llana
        m = re.search(r"\b(HALLADOS|HALLADAS|VIVIENDAS|MASIVO|DOCENTES|CONSISTORIO|IMAGENES DE (?!ARCHIVO|SATELITE))", comment)
        if m:
            avisos.append(f"Palabra poco llana ({m.group(1).strip()}): usa la corriente (ENCONTRADOS, CASAS, COLECTIVO, PROFESORES, AYUNTAMIENTO, RECURSOS DE)")
        # como sujeto de frase (no dentro de LAS CALLES DE LA CIUDAD, que es literal)
        if re.search(r"(?:^|\. |\bY )(EL TERRITORIO|EL PAIS|LA CIUDAD|EL MANDATARIO|EL DIRIGENTE|LA MANDATARIA|EL LIDER)\b", comment):
            avisos.append("Sustituto de cronica (EL TERRITORIO, EL PAIS, EL MANDATARIO...): repite el nombre propio")
        lugar = comment.split(".")[0].strip()
        if (not re.match(r"^[A-ZÑ][A-ZÑ0-9 (),.\-]*?\.(\s|$)", comment) or len(lugar.split()) > 6
                or lugar.startswith(HABLADOS + ("RECURSOS", "EXTRACTO", "SALUDO", "RESUMEN", "VISTAS", "SECUELAS", "DAÑOS",
                                                "LLEGADA", "MOMENTO", "IMAGENES", "ARCHIVO", "PHOTOCALL", "ALFOMBRA",
                                                "TESTIMONIO", "ENCUESTA", "PUBLICACION", "FOTOS"))):
            avisos.append("COMMENT no empieza por LUGAR.")
        if re.search(r"\b(PRESUNT[OA]S?|SUPUEST[OA]S?)\b", comment):
            avisos.append("PRESUNTO o SUPUESTO: los cargos y acusaciones se enuncian como tales")
        if re.search(r"\b(EL|ESTE|ESTA|LOS) (LUNES|MARTES|MIERCOLES|JUEVES|VIERNES|SABADO|DOMINGO)\b", comment):
            avisos.append("Dia de la semana en el COMMENT: fuera las referencias temporales")
        if re.search(r"ELECCIONES DE MITAD DE MANDATO", comment):
            avisos.append("ELECCIONES DE MITAD DE MANDATO: se dice ELECCIONES DE MEDIO MANDATO")
        m = re.search(r"[%$€£]|\bKMS?\b", comment)
        if m:
            avisos.append(f"Simbolo o abreviatura ({m.group(0)}): POR CIENTO, DOLARES, EUROS, KILOMETROS en letra")
        m = re.search(r"\b(UN|UNA|DOS|TRES|CUATRO|CINCO|SEIS|SIETE|OCHO|NUEVE|DIEZ|ONCE|DOCE|QUINCE|VEINTE|TREINTA)\s+"
                      r"(AÑOS|MESES|DIAS|HORAS|EUROS|DOLARES|LIBRAS|METROS|KILOMETROS|POR CIENTO)\b", comment)
        if m:
            avisos.append(f"Cantidad con unidad en letra ({m.group(0)}): con unidad va siempre en cifra")
        m = CARGO_DELANTE_RE.search(comment)
        if m:
            avisos.append(f"Cargo delante del nombre ({m.group(0)[:50]}): NOMBRE APELLIDO, CARGO")
        tras = re.sub(r"^[^.]*\.\s*", "", comment, count=1)
        if tras.startswith("IMAGENES DE ARCHIVO") and "ARCHIVO" not in name:
            avisos.append("El COMMENT empieza por IMAGENES DE ARCHIVO y el NAME no lleva ARCHIVO tras el pais")
        if tras.startswith("RECURSOS DE") and any(h in comment for h in HABLADOS):
            avisos.append("RECURSOS DE es para planos sin declaraciones: si alguien habla, el descriptor es el del "
                          "hecho (DAÑOS DE, LLEGADA DE...) o el hablado")
        pais = pais_en_parentesis(comment)
        if pais:
            avisos.append(f"LUGAR con el pais entre parentesis ({pais}): los parentesis son solo para "
                          f"region, estado o provincia")
        if re.search(r"\b\d{1,2} DE [A-Z]+ DE (19|20)\d{2}\b", comment):
            avisos.append("COMMENT con fecha de calendario")
        if re.search(r"\b(HOY|AYER|ESTA SEMANA)\b", comment):
            avisos.append("COMMENT con referencia temporal relativa")
        if len(comment) > TOPE_COMMENT:
            avisos.append(f"COMMENT de {len(comment)} caracteres (objetivo {MAX_COMMENT}, margen hasta {TOPE_COMMENT})")
        m_inc = re.search(r"INCLUYE (.+)$", comment)
        if (_sin_planos(comment) and m_inc
                and not re.search(r"DECLARACIONES|TESTIMONIOS|ENCUESTA|RUEDA DE PRENSA|INTERVENCION|COMPARECENCIA|ENTREVISTA|ROTULOS|VERTICAL|SUBTITUL", m_inc.group(1))):
            avisos.append("El envio es solo material hablado: sobra el INCLUYE, no hay material secundario que listar")
        elif len(comment) > TOPE_SOLO_HABLADO and _solo_hablado(comment):
            avisos.append(f"COMMENT de {len(comment)} caracteres y el envio es solo material hablado "
                          f"(tope {TOPE_SOLO_HABLADO}): recorta lo que dice cada uno")
    if restr and restr != "SIN AVISO" and not restr.endswith("."):
        avisos.append("RESTRICCIONES: cada aviso termina en punto")
    # el descriptor es el primero que aparece en cada campo (por posicion, no por orden de la lista)
    en_name = min((h for h in HABLADOS if h in name), key=name.find, default=None)
    en_comment = min((h for h in HABLADOS if h in comment), key=comment.find, default=None)
    if en_name and en_comment and en_name != en_comment:
        avisos.append(f"El NAME dice {en_name} y el COMMENT dice {en_comment}: el descriptor del material "
                      f"es el mismo en los dos campos")
    todo = name + " " + comment
    if re.search(r"\b(MIL NOVECIENTOS|DOS MIL)\b", todo):
        avisos.append("Año escrito en letras: debe ir en cifra")
    m = re.search(r"\b(\d{7,})\b", todo)
    if m:
        avisos.append(f"Cantidad de {len(m.group(1))} cifras ({m.group(1)}): a partir del millon va el numero "
                      f"en cifra y la escala en letra (30 MILLONES, UN MILLON)")
    if re.search(r"\b(UN|DOS|TRES|CUATRO|CINCO|SEIS|SIETE|OCHO|NUEVE|DIEZ|VEINTE|TREINTA|CUARENTA|CINCUENTA|SESENTA|SETENTA|OCHENTA|NOVENTA|CIEN|CIENTO|DOSCIENTOS|TRESCIENTOS|QUINIENTOS)\b[A-ZÑ ]{0,30}\b(MIL|MILLON|MILLONES)\b", comment):
        avisos.append("Cantidad escrita en letras: debe ir en cifra sin puntos")
    # Ñ perdida
    m = ENE_PERDIDA_RE.search(todo)
    if m:
        avisos.append(f"Posible Ñ perdida ({m.group(1)}): en mayusculas se quitan las tildes, no la Ñ")
    # transcripcion inglesa sin españolizar
    m = TRANSCRIPCION_RE.search(todo)
    if m:
        avisos.append(f"Transcripcion inglesa ({m.group(1)}): españolizar (ZELENSKI, MIJAIL, LEOPOLIS, JARKOV, AL CHARAA)")
    # anglicismo evitable
    m = ANGLICISMO_RE.search(todo)
    if m:
        avisos.append(f"Anglicismo evitable ({m.group(1).strip()}): traduce la parte generica (BASE ANDREWS, LAGO ONTARIO)")
    m = re.search(r"\bCOLAPSO\b", todo)
    if m:
        avisos.append("COLAPSO es falso amigo de COLLAPSE: se dice DERRUMBE o HUNDIMIENTO")
    if re.search(r"\bELECCIONES INTERMEDIAS\b", todo):
        avisos.append("ELECCIONES INTERMEDIAS es traduccion literal de MIDTERM: se dice ELECCIONES DE MEDIO MANDATO")
    # tras el LUGAR (primera frase) TESTIMONIOS o ENCUESTA pueden ser el material principal; como secundario entran por INCLUYE
    tras_lugar = re.sub(r"^[^.]*\.\s*", "", comment, count=1)
    for formula in (r"TESTIMONIOS? DEL?\b", r"ENCUESTA A"):
        m = re.search(formula, comment)
        if m and not re.search(r"INCLUYE " + formula, comment) and not re.match(formula, tras_lugar):
            avisos.append(f"{m.group(0)} sin INCLUYE: el material hablado secundario entra por INCLUYE")
            break
    # pais inicial repetido como gentilicio en NAME
    if name:
        primera = name.split()[0]
        for gent in GENTILICIOS.get(primera, ()):
            if re.search(rf"\b{gent}(ES|AS|S|A)?\b", name[len(primera):]):
                avisos.append(f"NAME repite el pais inicial como gentilicio ({gent})")
                break
    frases = re.split(r"\.\s+", comment)
    # frases largas
    for frase in frases:
        if len(frase.split()) > 40:
            avisos.append(f"COMMENT con una frase de {len(frase.split())} palabras: dividir")
            break
    # dos conectores en la misma frase: el del acto y el del asunto
    for frase in frases:
        if re.search(r"\b(DURANTE|ANTE)\b", frase) and re.search(r"\bSOBRE\b", frase):
            avisos.append("Frase con dos conectores (DURANTE o ANTE, y SOBRE): uno por frase, divide en dos")
            break
    # el descriptor hablado lleva su conector
    if "INTERVENCION DE" in comment and not re.search(r"INTERVENCION DE[^.]*\b(DURANTE|EN)\b", comment):
        avisos.append("INTERVENCION sin su conector: INTERVENCION DE ... DURANTE el acto")
    if "COMPARECENCIA DE" in comment and not re.search(r"COMPARECENCIA DE[^.]*\bANTE\b", comment):
        avisos.append("COMPARECENCIA sin su conector: COMPARECENCIA DE ... ANTE el organo")
    # cargos en ingles sin traducir
    m = re.search(r"\b(GOVERNOR|PRIME MINISTER|MINISTER|SECRETARY|CHAIRMAN|CHAIRWOMAN|CEO|SPOKESMAN|SPOKESWOMAN|SPOKESPERSON|HEAD OF|LAWMAKER|CONGRESSMAN|ATTORNEY GENERAL|COMMISSIONER|MANAGER|COACH)\b", todo)
    if not m:
        # MAYOR solo como cargo en ingles (", MAYOR DE ..." o "MAYOR OF"); ESTADO MAYOR y PLAZA MAYOR son español
        m = re.search(r"(?:, |\bTHE )(MAYOR)\b(?! PARTE)(?! DE EDAD)|\b(MAYOR) OF\b", todo)
    if m:
        avisos.append(f"Cargo en ingles sin traducir ({m.group(1) or m.group(2)})")
    # cargo con gentilicio delante del nombre (PRESIDENTE FRANCES EMMANUEL MACRON)
    if re.search(CARGO_GENTILICIO_RE, todo):
        avisos.append("Cargo con gentilicio delante del nombre: debe ir NOMBRE APELLIDO, CARGO DE PAIS")
    # el verbo inicial solo esta prohibido en la primera frase real, la que sigue al LUGAR
    MARCA_RE = re.compile(r"^(INCLUYE ROTULOS|MATERIAL |VIDEO PROMOCIONAL|IMAGENES DE (CAMARA|VISION|DRON)|VIDEO EN VERTICAL)")
    for frase in frases[1:]:
        if MARCA_RE.match(frase.strip()):
            continue                      # las marcas del material no cuentan
        primera = frase.split()[0] if frase.split() else ""
        if primera in VERBOS_INICIO:
            avisos.append(f"La primera frase tras el LUGAR empieza por verbo ({primera}): ahi el estilo "
                          f"nominal es obligatorio")
        break                             # solo se comprueba esa
    return avisos


MAX_NAME = 65           # longitud normal del NAME (PAIS y lo esencial)
TOPE_NAME = 100         # margen maximo: por encima, aviso
TOPE_SOLO_HABLADO = 300   # envio que es solo material hablado, sin hecho visible: no da para mas
MAX_COMMENT = 400      # objetivo: a esto se recorta
TOPE_COMMENT = 500     # margen: por debajo de esto no se toca ni se avisa


def acortar_comment(comment, model="sonnet", extra_args=None, timeout=120, intentos=2):
    """Segunda pasada: reescribe un COMMENT demasiado largo. Insiste hasta 'intentos' veces
    y se queda con la version mas corta que sea valida. Si ninguna sirve, devuelve el original."""
    mejor = comment
    for intento in range(1, intentos + 1):
        objetivo = MAX_COMMENT - 50
        prompt = (
            f"Este es el COMMENT de una ficha de archivo audiovisual y tiene {len(mejor)} caracteres. "
            f"Reescribelo en MENOS DE {objetivo} CARACTERES, que es obligatorio, conservando: el LUGAR inicial "
            "con su punto, las personas con nombre y cargo completo, el hecho principal "
            "y la frase INCLUYE si la hay. Quita en este orden: 1) contexto del STORY (antecedentes, cifras "
            "generales, frase final de cronica, fechas), 2) el detalle de lo que dice cada persona, dejando quien es "
            "y el asunto, 3) adjetivos y matices. Nunca quites a una persona que habla, el LUGAR, el descriptor del "
            "material, un resultado deportivo ni una pieza del INCLUYE. Nunca abrevies ni acortes una palabra (PRESIDENTE, "
            "nunca PTE.; PRIMER MINISTRO, nunca PREMIER) ni uses dos puntos: se quitan frases, no letras. Estilo nominal, "
            "todo en mayusculas sin tildes pero con Ñ, sin fechas, termina en punto. Devuelve solo el texto del "
            "COMMENT, sin etiqueta ni explicaciones.\n\n" + mejor
        )
        try:
            salida = llamar_modelo("Eres un documentalista de archivo audiovisual. Respondes solo con el texto pedido.",
                                   prompt, timeout=timeout)
        except RedactorError:
            break
        cand = " ".join(salida.strip().split())
        cand = re.sub(r"^\**\s*COMMENT\s*\**\s*:\s*", "", cand, flags=re.I)
        cand = normalizar(cand)
        # mismo criterio de LUGAR que el validador: admite comas y puntos dentro del parentesis
        valido = bool(cand) and re.match(r"^[A-ZÑ][A-ZÑ0-9 (),.\-]*?\.(\s|$)", cand) and cand.endswith(".")
        # una version con palabras abreviadas o con dos puntos no vale: mejor el COMMENT largo que uno mutilado
        if valido and (re.search(r"[:;]", cand) or ABREVIATURA_RE.search(cand)):
            valido = False
        if valido and len(cand) < len(mejor):
            mejor = cand
            if len(mejor) <= TOPE_COMMENT:
                return mejor
        else:
            break
    return mejor


def adjuntar_imagenes(user, rutas):
    """Añade los fotogramas al mensaje. Con la API van como bloques de imagen; con Claude Code
    se copian al directorio de trabajo y se pasan por nombre de fichero."""
    instruccion = (
        f"\n\nADJUNTO {len(rutas)} FOTOGRAMAS del video, repartidos por su duracion y en orden. "
        "Miralos antes de decidir el descriptor del material y antes de escribir el COMMENT, porque el shotlist "
        "muchas veces no dice en que acto se habla. Resuelve con ellos, en este orden:\n"
        "1. QUE CLASE DE ACTO ES. Atril con carteleria del convocante y periodistas sentados: RUEDA DE PRENSA. "
        "Dos personas sentadas en butacas o en un atril doble con las banderas de sus paises detras, hablando a la "
        "prensa: son DECLARACIONES dichas EN COMPARECENCIA CONJUNTA, que es el marco y no el descriptor, y es el acto en si, no algo posterior a una reunion. Una persona ante un "
        "organo, un estrado o un hemiciclo: COMPARECENCIA. Alguien hablando de pie en la calle o a la salida con "
        "los microfonos en mano delante: DECLARACIONES. Alguien tomando la palabra en un acto que va de otra cosa "
        "(mitin, congreso, entrega de premios, misa): INTERVENCION. Dos personas frente a frente con un "
        "entrevistador: ENTREVISTA.\n"
        "2. SI ES UN VIDEO PREPARADO. Producto sobre fondo neutro, animaciones, rotulacion de marca, planos de "
        "estudio encadenados: es VIDEO PROMOCIONAL, y entonces no se cataloga a quien habla.\n"
        "3. QUE SE VE DE VERDAD. Lugar, interior o exterior, cuanta gente hay y que hacen. Sirve para no describir "
        "planos que no existen.\n"
        "4. MARCAS DEL MATERIAL. Texto sobreimpreso o mosca de cadena (INCLUYE ROTULOS), imagen vertical, vision "
        "nocturna, camara termica, vista de dron, camara de seguridad.\n"
        "Los fotogramas sirven solo para elegir entre RUEDA DE PRENSA, DECLARACIONES, COMPARECENCIA, INTERVENCION y "
        "ENTREVISTA cuando el shotlist no lo dice, y para las marcas del material. No añaden planos: en el COMMENT "
        "solo entra lo que esta en el shotlist. Si un fotograma contradice un plano del shotlist, manda el shotlist "
        "y no se menciona. No describas los fotogramas uno a uno ni los menciones en la ficha.")
    if CFG.get("redactor") in ("api", "openai"):
        bloques = [{"type": "text", "text": user + instruccion}]
        for r in rutas:
            f = Path(r)
            if not f.exists():
                continue
            medio = "image/png" if f.suffix.lower() == ".png" else "image/jpeg"
            bloques.append({"type": "image", "source": {
                "type": "base64", "media_type": medio,
                "data": base64.b64encode(f.read_bytes()).decode("ascii")}})
        return bloques
    carpeta = carpeta_trabajo()
    for viejo in list(carpeta.glob("*.jpg")) + list(carpeta.glob("*.png")):
        try:
            viejo.unlink()
        except OSError:
            pass
    nombres = []
    for r in rutas:
        f = Path(r)
        if not f.exists():
            continue
        shutil.copyfile(f, carpeta / f.name)
        nombres.append(f.name)
    if not nombres:
        return user
    return user + instruccion + "\n\nFotogramas en el directorio de trabajo: " + ", ".join(nombres)


def redactar(ficha, model=None, extra_args=None, timeout=None, acortar=None):
    """Devuelve (campos, avisos, salida_bruta). Los parametros sueltos se admiten por compatibilidad;
    la configuracion real es CFG (ver configurar)."""
    if model: CFG["claude_model"] = model
    if extra_args: CFG["claude_extra_args"] = extra_args
    if timeout: CFG["claude_timeout"] = timeout
    if acortar is not None: CFG["acortar_comment"] = acortar
    acortar = CFG["acortar_comment"]
    system, user = construir_partes(ficha)
    tope = int(CFG.get("miniaturas", 0) or 0)
    todas = list(ficha.get("miniaturas") or [])     # las que se hayan sacado (para el modelo o para el clasificador)
    imgs = todas[:tope] if tope else []             # las que se adjuntan al modelo
    imagen = None                                  # (clase, confianza, fiable) del clasificador de escenas
    if todas and CFG.get("escenas", True):
        try:
            import escenas as _escenas
            if _escenas.hay_modelo():
                etiqueta, confianza, detalle = _escenas.clasificar(todas)
                fiab = _escenas.fiabilidad(etiqueta)
                fiable = (bool(etiqueta) and confianza >= float(CFG.get("escenas_umbral", 0.6))
                          and (fiab is None or fiab >= float(CFG.get("escenas_fiabilidad_min", 0.85))))
                imagen = (etiqueta, confianza, fiable)
                ficha["escena"] = {"clase": etiqueta, "confianza": round(confianza, 2), "fiabilidad": fiab,
                                   "detalle": detalle, "fiable": fiable}
                propia = etiqueta not in DESCRIPTOR_ACTO       # categoria creada en la pestaña Imagenes
                if fiable and propia:
                    user += (f"\n\nLOS FOTOGRAMAS PARECEN, SEGUN EL CLASIFICADOR LOCAL: {_escenas.descriptor(etiqueta)} "
                             f"(lo ven {confianza:.0%} de los fotogramas). Es una pista de lo que se ve; en el COMMENT "
                             "solo entra lo que esta en el shotlist.")
                # el shotlist manda: la imagen solo se le cuenta al modelo cuando el texto no dice el acto
                elif fiable and not (ficha.get("acto") or {}).get("clase"):
                    user += (f"\n\nESCENA RECONOCIDA POR EL CLASIFICADOR LOCAL: "
                             f"{DESCRIPTOR_ACTO.get(etiqueta, etiqueta.replace('_', ' ').upper())} "
                             f"(la ven {confianza:.0%} de los fotogramas). "
                             "Es lo que muestran los fotogramas del video. Usalo solo para elegir el descriptor "
                             "del material (RUEDA DE PRENSA, DECLARACIONES, COMPARECENCIA, INTERVENCION, ENTREVISTA) "
                             "cuando el shotlist no lo dice. No añade planos: en el COMMENT solo entra lo que esta "
                             "en el shotlist.")
                if fiable and not propia and CFG.get("escenas_sin_imagenes", True):
                    imgs = []      # con etiqueta fiable no hace falta gastar cuota en imagenes
        except Exception:
            imagen = None
    if imgs:
        user = adjuntar_imagenes(user, imgs)
    AVISOS_LLAMADA.clear()
    salida = llamar_modelo(system, user, con_imagenes=bool(imgs))
    avisos_llamada = list(AVISOS_LLAMADA)
    campos = parsear(salida)
    presentes = campos.pop("_presentes")
    if not campos["NAME"] and not campos["COMMENT"]:
        raise RedactorError("No se han encontrado las lineas NAME/COMMENT en la respuesta:\n" + salida[:1500])
    campos["NAME"] = normalizar(campos["NAME"])
    campos["COMMENT"] = normalizar(campos["COMMENT"])
    HILO.acortado = bool(acortar and len(campos["COMMENT"]) > TOPE_COMMENT)
    if HILO.acortado:
        campos["COMMENT"] = acortar_comment(campos["COMMENT"])
    campos["RESTRICCIONES"] = limpiar_restricciones(normalizar(campos["RESTRICCIONES"])) or "SIN AVISO"
    # la linea ENVIO la compone el programa: el modelo podia cambiar el numero, la fecha o el slug
    campos["ENVIO"] = linea_envio(ficha)
    avisos = validar(campos) + avisos_llamada
    if "RESTRICCIONES" not in presentes:
        avisos.append("RESTRICCIONES: el modelo no ha escrito la linea (respuesta cortada?); se ha puesto SIN AVISO, "
                      "comprobar las restricciones en la agencia")
    # lo detectado en el texto frente a lo que ha escrito el modelo
    avisos += cruzar_restricciones(campos["RESTRICCIONES"], ficha.get("situaciones"))
    # ROTULAR CORTESIA solo cuando la agencia pide el credito (must credit, courtesy of, please credit...):
    # la fuente del material ("Source: IRIB", "DPA VIDEO - NO ACCESS GERMANY") no es un credito obligatorio
    if ficha.get("situaciones") and "CORTESIA" in campos["RESTRICCIONES"] \
            and "credito" not in (ficha["situaciones"].get("claves") or []):
        quitadas, campos["RESTRICCIONES"] = quitar_cortesia(campos["RESTRICCIONES"])
        if quitadas:
            avisos = [a for a in avisos if not ("sin respaldo" in a and "CORTESIA" in a)]   # ya lo dice el de abajo
            avisos.append("Quitado " + " / ".join(quitadas) + ": la agencia no pide credito obligatorio "
                          "(si lo pide con otras palabras, añadelo a mano)")
    # el tipo de acto que dice la ficha frente al del shotlist y al de la imagen
    avisos += comparar_acto(ficha, campos, imagen)
    # version en escritura normal generada en la misma llamada (generar_normal): palabra a palabra, las que
    # el modelo cambio se pasan a minuscula sin el; nunca queda un campo en mayusculas
    propuesta = {c: campos.pop(c + "_NORMAL", "") for c in ("NAME", "COMMENT", "RESTRICCIONES")}
    if any(propuesta.values()):
        campos["NORMAL"], avisos_normal = version_normal(campos, propuesta)
        avisos += avisos_normal
    texto_ficha = (ficha.get("texto") or "").upper()
    todo = campos["NAME"] + " " + campos["COMMENT"]
    hablado = next((h for h in HABLADOS if h in todo), None)
    # Reuters y AP marcan las declaraciones con SOUNDBITE; los shotlists de EBU usan SOT o INTERVIEW
    hay_soundbite = bool(re.search(r"SOUNDBITE|\bSOT\b|\bSOTS\b|INTERVIEW WITH", texto_ficha))
    # quien habla segun el shotlist tiene que estar en el COMMENT: son la clave de busqueda
    for nombre in hablantes(ficha.get("texto") or ""):
        apellido = nombre.split()[-1]
        if len(apellido) > 2 and apellido not in campos["COMMENT"]:
            avisos.append(f"Habla {nombre} segun el shotlist y no consta en el COMMENT")
    # planos: los de verdad son los numerados que no son SOUNDBITE ni planos de sala
    planos = planos_reales(ficha.get("texto") or "")
    if hay_soundbite and not planos and "INCLUYE" in campos["COMMENT"] and not re.search(
            r"INCLUYE (DECLARACIONES|TESTIMONIOS|ENCUESTA|RUEDA|INTERVENCION|COMPARECENCIA|ENTREVISTA|ROTULOS|FOTOS|PUBLICACION)", campos["COMMENT"]):
        avisos.append("Segun el shotlist el envio es solo hablado (sin planos de recurso, los de sala no cuentan): sobra el INCLUYE")
    if planos and _sin_planos(campos["COMMENT"]):
        avisos.append(f"El shotlist tiene {len(planos)} plano(s) de recurso y el COMMENT no los cita")
    if not planos_numerados(ficha.get("texto") or "") and "INCLUYE" in campos["COMMENT"]:
        avisos.append("INCLUYE sin shotlist: esos planos no constan en el envio")
    # el LUGAR sale de la dateline
    lugar = campos["COMMENT"].split(".")[0].strip()
    datelines = {d.strip() for d in re.findall(r"^(?:VIDEO SHOWS|SHOWS)\s*:?\s*([^(\n]+)\(", texto_ficha, flags=re.M)}
    if re.search(r"UNKNOWN LOCATION|UNDISCLOSED LOCATION|LOCATION NOT GIVEN|LOCATION UNKNOWN", texto_ficha) and lugar != "UBICACION DESCONOCIDA":
        avisos.append("La agencia no da el lugar: el LUGAR es UBICACION DESCONOCIDA")
    if len(datelines) > 1 and "VARIAS LOCALIZACIONES" not in lugar and "(" not in lugar:
        avisos.append(f"El envio tiene {len(datelines)} datelines distintas y el LUGAR es una sola ciudad: revisar si es VARIAS LOCALIZACIONES")
    if hablado and not hay_soundbite:
        avisos.append(f"Dice {hablado} pero el shotlist no tiene SOUNDBITE: deberia ser RECURSOS ... CON MOTIVO DE")
    if hay_soundbite and not hablado:
        avisos.append("El shotlist tiene SOUNDBITE y la ficha no dice de que material hablado se trata "
                      "(DECLARACIONES, RUEDA DE PRENSA, COMPARECENCIA, INTERVENCION, ENTREVISTA)")
    if (hablado
            and re.search(r"SCREEN ?GRAB|SCREENSHOT|SOCIAL MEDIA POST|TRUTH SOCIAL|\bPOST ON\b|POSTED ON", texto_ficha)
            and not hay_soundbite):
        avisos.append("Es una captura de redes: PUBLICACION EN REDES SOCIALES, no DECLARACIONES")
    # deporte: en toda ficha deportiva el NAME lo lleva detras del pais (patron G del criterio)
    deporte = deporte_de(campos.get("ENVIO", ""), texto_ficha)
    if deporte and not re.search(PALABRAS_DEPORTE, campos["NAME"]):
        avisos.append(f"La ficha es de deporte ({deporte}) y el NAME no lo dice: el deporte va detras del pais")

    # INCLUYE ROTULOS solo cuando la agencia dice que la imagen lleva texto (o el modelo lo ha visto en los
    # fotogramas). Que el material venga de una cadena (IRIB, CGTN, "aired on") no basta: suele llevar
    # rotulos, pero no siempre, y poniendolo de oficio salian falsos positivos.
    dice_rotulos = bool(ROTULOS_EXPLICITO_RE.search(texto_ficha))
    de_cadena = bool(ROTULOS_PROBABLE_RE.search(texto_ficha))
    if "ROTULOS" in campos["COMMENT"] and not dice_rotulos:
        if imgs:
            avisos.append("INCLUYE ROTULOS no lo dice la agencia (solo los fotogramas): comprobar en el video")
        else:
            campos["COMMENT"] = quitar_rotulos(campos["COMMENT"])
            avisos.append("Quitado INCLUYE ROTULOS: la agencia no dice que la imagen lleve texto sobreimpreso"
                          + (" (material de cadena: comprobar en el video)" if de_cadena else ""))
    elif dice_rotulos and "ROTULOS" not in campos["COMMENT"]:
        avisos.append("La agencia dice que la imagen lleva texto o graficos y el COMMENT no dice INCLUYE ROTULOS")
    elif de_cadena and "ROTULOS" not in campos["COMMENT"]:
        avisos.append("Material de cadena o de camara de seguridad: suele llevar rotulos, comprobar en el video")
    if (re.search(r"\bSTILLS?\b|STILL (PHOTO|IMAGE)|PHOTOGRAPHS?|\bPHOTOS\b", texto_ficha)
            and not re.search(r"\bFOTOS\b|FOTOGRAFIAS", campos["COMMENT"])):
        avisos.append("El envio trae fotos fijas y el COMMENT no las menciona: van en el INCLUYE")
    if re.search(r"VERTICAL (VIDEO|FORMAT)|\b9:16\b|PORTRAIT", texto_ficha) and "VERTICAL" not in campos["COMMENT"]:
        avisos.append("La ficha indica video vertical y el COMMENT no lo dice")
    # aficionado si, handout civil no: el cedido por un gobierno o institucion no se menciona
    if re.search(r"\bUGC\b|EYEWITNESS|AMATEUR|MOBILE PHONE|CELLPHONE|SOCIAL MEDIA VIDEO|VIDEO OBTAINED BY", texto_ficha) and "VIDEOAFICIONADO" not in campos["COMMENT"]:
        avisos.append("La ficha indica material de aficionado y el COMMENT no lo dice")
    # handout militar si se marca
    if (re.search(r"HANDOUT|DISTRIBUTED BY|RELEASED BY", texto_ficha)
            and re.search(r"\bMILITARY\b|\bARMY\b|\bNAVY\b|AIR FORCE|DEFENC?SE MINISTRY|MINISTRY OF DEFENC?SE|ARMED FORCES|\bIDF\b|PENTAGON", texto_ficha)
            and "MATERIAL MILITAR" not in campos["COMMENT"]):
        avisos.append("Material militar cedido y el COMMENT no dice MATERIAL MILITAR DISTRIBUIDO POR")
    # material promocional cedido por la propia empresa que sale
    promo = re.search(r"PROMOTIONAL VIDEO|PRODUCT VIDEO|COMPANY HANDOUT|CORPORATE VIDEO|PRESS RELEASE VIDEO|PRODUCT LAUNCH VIDEO", texto_ficha)
    if promo and "PROMOCIONAL" not in campos["COMMENT"]:
        avisos.append("El envio es material promocional y el COMMENT no dice VIDEO PROMOCIONAL DE")
    if promo and any(h in campos["COMMENT"] for h in HABLADOS):
        avisos.append("Video promocional con un descriptor hablado: en un video cedido por la empresa no se "
                      "cataloga a quien habla, se describe el video y lo que muestra")
    # tipo de camara
    for patron, formula in ((r"THERMAL", "CAMARA TERMICA"), (r"INFRA-?RED", "CAMARA INFRARROJA"),
                            (r"NIGHT ?VISION", "VISION NOCTURNA"), (r"\bDRONE\b|\bUAV\b", "DRON"),
                            (r"SECURITY CAMERA|SURVEILLANCE FOOTAGE", "CAMARA DE SEGURIDAD")):
        if re.search(patron, texto_ficha) and formula not in campos["COMMENT"]:
            avisos.append(f"La ficha indica {formula.lower()} y el COMMENT no lo dice")
            break
    # GUERRA solo para el material de la guerra en si (combates, bombardeos, frente), no para todo el pais
    encabeza = campos["NAME"].split()[0] if campos["NAME"].split() else ""
    combate = COMBATE_RE.search(texto_ficha)
    if encabeza in PAISES_GUERRA and combate and "GUERRA" not in campos["NAME"]:
        avisos.append(f"El NAME empieza por {encabeza} y el material es de combate ({combate.group(0).lower()[:40]}): "
                      f"si es la guerra de ese pais, GUERRA va detras del pais")
    if GUERRA_RE.search(campos["NAME"]) and not combate:
        avisos.append("GUERRA en el NAME pero el script no muestra combates, bombardeos, frente ni militares en "
                      "operaciones: GUERRA es solo para el material de la guerra en si")
    # moda
    if (MODA_RE.search(texto_ficha) and not re.search(r"MILITARY PARADE|PRIDE", texto_ficha)
            and not re.search(r"\bMODA\b", campos["NAME"])):
        avisos.append("El envio es de moda (desfile, pasarela, coleccion): MODA va en el NAME detras del pais")
    return campos, avisos, salida


def formatear(campos, avisos=None):
    lineas = [f"{c}: {campos.get(c, '')}" for c in CAMPOS]
    if campos.get("ALERTA"):
        lineas.append(campos["ALERTA"])
    if avisos:
        lineas.append("AVISOS: " + " | ".join(avisos))
    return "\n".join(lineas)
