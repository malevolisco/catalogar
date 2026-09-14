# -*- coding: utf-8 -*-
"""
redactor.py - Envia el texto de la ficha y las reglas a Claude Code en modo no
interactivo (claude -p) y devuelve las cuatro lineas normalizadas y validadas.

Requiere Claude Code instalado y con sesion iniciada (ejecuta `claude` una vez
en una consola y entra con tu cuenta Pro).
"""
import os
import re
import shutil
import base64
import subprocess
import unicodedata
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REGLAS_PATH = BASE_DIR / "reglas.md"
REGLAS_EXTRA_PATH = BASE_DIR / "reglas_extra.md"
EJEMPLOS_PATH = BASE_DIR / "ejemplos.md"
MAX_EJEMPLOS_AVISO = 12
WORKDIR = BASE_DIR / "claude_ws"   # carpeta vacia: Claude Code no debe leer nada del proyecto

CAMPOS = ("ENVIO", "NAME", "COMMENT", "RESTRICCIONES")

# configuracion activa (la fija catalogar.py / worker.py con configurar(cfg))
CFG = {
    "redactor": "claude_code",            # "claude_code" (Pro, sin coste) o "api" (clave, centimos, rapido)
    "claude_model": "sonnet",
    "claude_extra_args": ["--max-turns", "1"],
    "claude_timeout": 240,
    "claude_thinking_tokens": 1024,       # presupuesto de pensamiento de Claude Code (MAX_THINKING_TOKENS)
    "api_key": "",
    "api_model": "claude-haiku-4-5-20251001",
    "api_max_tokens": 700,
    "acortar_comment": True,
    "reglas": "reglas.md",                # fichero de reglas a usar (reglas.md o reglas_ligeras.md)
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


def _reglas():
    ruta = BASE_DIR / CFG.get("reglas", "reglas.md")
    base = (ruta if ruta.exists() else REGLAS_PATH).read_text(encoding="utf-8")
    extra = []
    if REGLAS_EXTRA_PATH.exists():
        for linea in REGLAS_EXTRA_PATH.read_text(encoding="utf-8").splitlines():
            linea = linea.strip()
            if linea and not linea.startswith("#"):
                extra.append("- " + linea.lstrip("-• ").strip())
    if extra:
        base += ("\n\n=== AJUSTES DEL CATALOGADOR (prevalecen sobre todo lo anterior) ===\n\n" + "\n".join(extra) + "\n")
    ejemplos = listar_ejemplos()
    if ejemplos:
        bloques = []
        for e in ejemplos:
            bloques.append(f"ENVIO: {e['envio']}\nNAME: {e['name']}\nCOMMENT: {e['comment']}\nRESTRICCIONES: {e['restricciones']}")
        base += ("\n\n=== FICHAS APROBADAS POR EL CATALOGADOR (el modelo a seguir) ===\n\n"
                 + "\n\n".join(bloques) + "\n")
    return base


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
    Devuelve (numero_de_ejemplos, aviso_o_None)."""
    envio = (campos.get("ENVIO") or "").strip()
    num = envio.split("·")[0].strip() if envio else ""
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
    n = len(listar_ejemplos())
    aviso = None
    if n > MAX_EJEMPLOS_AVISO:
        aviso = (f"Hay {n} fichas aprobadas: cada una alarga el prompt y la redaccion. "
                 f"Conviene quedarse con unas {MAX_EJEMPLOS_AVISO}, variadas por tipo.")
    return n, aviso


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


def construir_partes(ficha):
    """(system, user): las reglas van como system para poder cachearlas en la API."""
    datos = (
        f"numero: {ficha.get('numero', '')}\n"
        f"fecha: {ficha.get('fecha', '')}\n"
        f"revision: {ficha.get('rev', '')}\n"
        f"slug: {ficha.get('slug', '')}\n"
        f"headline: {ficha.get('headline', '')}\n"
    )
    user = (
        "=== DATOS DE CODIGO ===\n" + datos
        + "\n=== TEXTO DE LA FICHA ===\n" + ficha.get("texto", "")
        + "\n=== FIN ===\n\nDevuelve ahora las cuatro lineas.\n"
    )
    return _reglas(), user


def construir_prompt(ficha):
    datos = (
        f"numero: {ficha.get('numero', '')}\n"
        f"fecha: {ficha.get('fecha', '')}\n"
        f"revision: {ficha.get('rev', '')}\n"
        f"slug: {ficha.get('slug', '')}\n"
        f"headline: {ficha.get('headline', '')}\n"
    )
    return (
        _reglas()
        + "\n\n=== DATOS DE CODIGO ===\n" + datos
        + "\n=== TEXTO DE LA FICHA ===\n" + ficha.get("texto", "")
        + "\n=== FIN ===\n\nDevuelve ahora las cuatro lineas.\n"
    )


def _ruta_claude():
    """Ejecutable de Claude Code: variable CLAUDE_EXE, PATH, o la ruta del instalador nativo."""
    env = os.environ.get("CLAUDE_EXE")
    if env and Path(env).exists():
        return env
    exe = shutil.which("claude")
    if exe:
        return exe
    for cand in (Path.home() / ".local" / "bin" / "claude.exe", Path.home() / ".local" / "bin" / "claude"):
        if cand.exists():
            return str(cand)
    return "claude"


def _cmd_claude(model, extra_args):
    return [_ruta_claude(), "-p", "--model", model, "--output-format", "text"] + list(extra_args or [])


def llamar_claude(prompt, model="sonnet", extra_args=None, timeout=240):
    WORKDIR.mkdir(exist_ok=True)
    cmd = _cmd_claude(model, extra_args)
    env = dict(os.environ)
    if CFG.get("claude_thinking_tokens"):
        env["MAX_THINKING_TOKENS"] = str(CFG["claude_thinking_tokens"])
    try:
        r = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
            cwd=str(WORKDIR), timeout=timeout, env=env,
        )
    except FileNotFoundError:
        # en Windows, claude puede ser un .cmd que solo arranca a traves de la shell
        r = subprocess.run(
            " ".join(cmd), input=prompt, capture_output=True, text=True, encoding="utf-8",
            cwd=str(WORKDIR), timeout=timeout, shell=True, env=env,
        )
    except subprocess.TimeoutExpired:
        raise RedactorError(f"Claude Code no respondio en {timeout} s")
    if r.returncode != 0:
        raise RedactorError(f"Claude Code devolvio error {r.returncode}: {(r.stderr or '').strip()[:800]}")
    return r.stdout


def llamar_api(system, user, timeout=120):
    """Llamada directa a la API de Anthropic con las reglas cacheadas como system."""
    import requests
    if not CFG.get("api_key"):
        raise RedactorError("redactor=api pero falta api_key en config.json")
    cuerpo = {
        "model": CFG["api_model"],
        "max_tokens": int(CFG.get("api_max_tokens", 700)),
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
        raise RedactorError(f"API no accesible: {e}")
    if r.status_code != 200:
        raise RedactorError(f"API devolvio {r.status_code}: {r.text[:500]}")
    datos = r.json()
    return "".join(b.get("text", "") for b in datos.get("content", []) if b.get("type") == "text")


def llamar_modelo(system, user, timeout=None):
    """Enruta al backend configurado."""
    if CFG.get("redactor") == "api":
        return llamar_api(system, user, timeout=timeout or 120)
    return llamar_claude(system + "\n\n" + user, model=CFG["claude_model"], extra_args=CFG["claude_extra_args"],
                         timeout=timeout or CFG["claude_timeout"])


def parsear(salida):
    campos = {c: "" for c in CAMPOS}
    actual = None
    for linea in salida.splitlines():
        m = re.match(r"^\s*\**\s*(ENVIO|NAME|COMMENT|RESTRICCIONES)\s*\**\s*:\s*(.*)$", linea, re.I)
        if m:
            actual = m.group(1).upper()
            campos[actual] = m.group(2).strip()
        elif actual and linea.strip():
            campos[actual] = (campos[actual] + " " + linea.strip()).strip()
    return campos


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


PAISES_GUERRA = ("PALESTINA", "UCRANIA", "RUSIA", "ISRAEL")

HABLADOS = ("DECLARACIONES", "RUEDA DE PRENSA", "COMPARECENCIA", "INTERVENCION", "ENTREVISTA")

# Palabras que casi siempre delatan una N donde debia ir una Ñ.
ENE_PERDIDA_RE = re.compile(
    r"\b(ESPANA|ESPANOL|ESPANOLA|ESPANOLES|ESPANOLAS|ANO|ANOS|NINO|NINA|NINOS|NINAS|"
    r"DANO|DANOS|DANADO|DANADA|DANADOS|DANADAS|CANON|CANONES|MANANA|COMPANIA|COMPANIAS|"
    r"SENAL|SENALES|SENOR|SENORA|SENORES|MONTANA|MONTANAS|PEQUENO|PEQUENA|PEQUENOS|PEQUENAS|"
    r"ACOMPANADO|ACOMPANADA|ACOMPANADOS|ENSENANZA|SUENO|ENGANO|EXTRANO|EXTRANA|PUNO|BANO|CAMPANA|CAMPANAS)\b")

# Transcripciones inglesas que hay que españolizar.
TRANSCRIPCION_RE = re.compile(
    r"\b(ZELENSKIY|ZELENSKYY|MIKHAIL|MOHAMMED|MUHAMMAD|LVIV|KHARKIV|ODESSA|KHERSON|"
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


def validar(campos):
    avisos = []
    name, comment, restr = campos["NAME"], campos["COMMENT"], campos["RESTRICCIONES"]
    if not name:
        avisos.append("NAME vacio")
    else:
        if len(name) > 90:
            avisos.append(f"NAME de {len(name)} caracteres (max 90)")
        if len(name.split()) > 12:
            avisos.append(f"NAME de {len(name.split())} palabras (max 12)")
        if "GUERRA" in name and name.split()[0] not in PAISES_GUERRA:
            avisos.append(f"GUERRA en el NAME con {name.split()[0]} al frente: solo se usa con "
                          f"PALESTINA, UCRANIA, RUSIA o ISRAEL")
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
        if len(comment) < 100:
            avisos.append(f"COMMENT de {len(comment)} caracteres (minimo 100): falta gente, motivo o contenido de los planos")
        # IMAGENES DE ARCHIVO es formula obligatoria del patron de archivo: no es palabra poco llana
        m = re.search(r"\b(HALLADOS|HALLADAS|VIVIENDAS|MASIVO|DOCENTES|CONSISTORIO|IMAGENES DE (?!ARCHIVO|SATELITE))", comment)
        if m:
            avisos.append(f"Palabra poco llana ({m.group(1).strip()}): usa la corriente (ENCONTRADOS, CASAS, COLECTIVO, PROFESORES, AYUNTAMIENTO, RECURSOS DE)")
        if re.search(r"\b(EL TERRITORIO|EL PAIS|LA CIUDAD|EL MANDATARIO|EL DIRIGENTE|LA MANDATARIA|EL LIDER)\b", comment):
            avisos.append("Sustituto de cronica (EL TERRITORIO, EL PAIS, EL MANDATARIO...): repite el nombre propio")
        if not re.match(r"^[A-ZÑ][A-ZÑ0-9 (),.\-]*?\.(\s|$)", comment):
            avisos.append("COMMENT no empieza por LUGAR.")
        pais = pais_en_parentesis(comment)
        if pais:
            avisos.append(f"LUGAR con el pais entre parentesis ({pais}): los parentesis son solo para "
                          f"region, estado o provincia")
        if re.search(r"\b\d{1,2} DE [A-Z]+ DE (19|20)\d{2}\b", comment):
            avisos.append("COMMENT con fecha de calendario")
        if re.search(r"\b(HOY|AYER|ESTA SEMANA)\b", comment):
            avisos.append("COMMENT con referencia temporal relativa")
        if len(comment) > MAX_COMMENT:
            avisos.append(f"COMMENT de {len(comment)} caracteres (tope {MAX_COMMENT})")
    if restr and restr != "SIN AVISO" and "+++" not in restr:
        avisos.append("RESTRICCIONES sin formato +++")
    en_name = next((h for h in HABLADOS if h in name), None)
    en_comment = next((h for h in HABLADOS if h in comment), None)
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
        avisos.append(f"Transcripcion inglesa ({m.group(1)}): españolizar (ZELENSKI, MIJAIL, LEOPOLIS, JARKOV)")
    # anglicismo evitable
    m = ANGLICISMO_RE.search(todo)
    if m:
        avisos.append(f"Anglicismo evitable ({m.group(1).strip()}): traduce la parte generica (BASE ANDREWS, LAGO ONTARIO)")
    m = re.search(r"\bCOLAPSO\b", todo)
    if m:
        avisos.append("COLAPSO es falso amigo de COLLAPSE: se dice DERRUMBE o HUNDIMIENTO")
    for formula in ("TESTIMONIOS DE", "ENCUESTA A"):
        if formula in comment and f"INCLUYE {formula}" not in comment and not comment.startswith(formula):
            avisos.append(f"{formula} sin INCLUYE: el material hablado secundario entra por INCLUYE")
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


MAX_COMMENT = 600


def acortar_comment(comment, model="sonnet", extra_args=None, timeout=120):
    """Segunda pasada: reescribe un COMMENT demasiado largo. Devuelve el nuevo texto o el original si no mejora."""
    prompt = (
        f"Este es el COMMENT de una ficha de archivo audiovisual y tiene {len(comment)} caracteres. "
        f"Reescribelo en menos de {MAX_COMMENT - 50} caracteres conservando: el LUGAR inicial con su punto, "
        "las personas con nombre y cargo (acorta los cargos si hace falta, quita los testimonios secundarios antes que los nombres), "
        "el hecho principal y la frase INCLUYE si la hay. Estilo nominal, sin frases que empiecen por verbo, "
        "todo en mayusculas sin tildes, sin fechas, termina en punto. Devuelve solo el texto del COMMENT, sin etiqueta ni explicaciones.\n\n"
        + comment
    )
    try:
        salida = llamar_modelo("Eres un documentalista de archivo audiovisual. Respondes solo con el texto pedido.", prompt, timeout=timeout)
    except RedactorError:
        return comment
    nuevo = " ".join(salida.strip().split())
    nuevo = re.sub(r"^\**\s*COMMENT\s*\**\s*:\s*", "", nuevo, flags=re.I)
    nuevo = normalizar(nuevo)
    if nuevo and len(nuevo) < len(comment) and re.match(r"^[A-ZÑ][A-ZÑ0-9 ()\-]*\.", nuevo):
        return nuevo
    return comment


def adjuntar_imagenes(user, rutas):
    """Añade los fotogramas al mensaje. Con la API van como bloques de imagen; con Claude Code
    se copian al directorio de trabajo y se pasan por nombre de fichero."""
    instruccion = (
        f"\n\nADJUNTO {len(rutas)} FOTOGRAMAS del video, repartidos por su duracion y en orden. "
        "Mirelos antes de decidir el descriptor del material y antes de escribir el COMMENT, porque el shotlist "
        "muchas veces no dice en que acto se habla. Resuelva con ellos, en este orden:\n"
        "1. QUE CLASE DE ACTO ES. Atril con carteleria del convocante y periodistas sentados: RUEDA DE PRENSA. "
        "Dos personas sentadas en butacas o en un atril doble con las banderas de sus paises detras, hablando a la "
        "prensa: son DECLARACIONES dichas EN COMPARECENCIA CONJUNTA, que es el marco y no el descriptor, y es el acto en si, no algo posterior a una reunion. Una persona ante un "
        "organo, un estrado o un hemiciclo: COMPARECENCIA. Alguien hablando de pie en la calle o a la salida con "
        "los microfonos en mano delante: DECLARACIONES. Alguien tomando la palabra en un acto que va de otra cosa "
        "(mitin, congreso, entrega de premios, misa): INTERVENCION. Dos personas frente a frente con un "
        "entrevistador: ENTREVISTA.\n"
        "2. SI ES UN VIDEO PREPARADO. Producto sobre fondo neutro, animaciones, rotulacion de marca, planos de "
        "estudio encadenados: es VIDEO PROMOCIONAL, y entonces no se cataloga a quien habla.\n"
        "3. QUE SE VE DE VERDAD. Lugar, interior o exterior, cuanta gente hay y que hacen. Sirve para el INCLUYE "
        "y para no describir planos que no existen.\n"
        "4. MARCAS DEL MATERIAL. Texto sobreimpreso o mosca de cadena (INCLUYE ROTULOS), imagen vertical, vision "
        "nocturna, camara termica, vista de dron, camara de seguridad.\n"
        "Si lo que ve contradice al shotlist, mande lo que ve. No describa los fotogramas uno a uno ni los "
        "mencione en la ficha.")
    if CFG.get("redactor") == "api":
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
    WORKDIR.mkdir(exist_ok=True)
    for viejo in list(WORKDIR.glob("*.jpg")) + list(WORKDIR.glob("*.png")):
        try:
            viejo.unlink()
        except OSError:
            pass
    nombres = []
    for r in rutas:
        f = Path(r)
        if not f.exists():
            continue
        shutil.copyfile(f, WORKDIR / f.name)
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
    imgs = list(ficha.get("miniaturas") or [])[:tope] if tope else []
    if imgs and CFG.get("escenas", True):
        try:
            import escenas as _escenas
            if _escenas.hay_modelo():
                etiqueta, confianza, detalle = _escenas.clasificar(imgs)
                umbral = float(CFG.get("escenas_umbral", 0.6))
                if etiqueta and confianza >= umbral:
                    user += (f"\n\nESCENA RECONOCIDA POR EL CLASIFICADOR LOCAL: "
                             f"{etiqueta.replace('_', ' ').upper()} (confianza {confianza:.2f}). "
                             "Es lo que muestran los fotogramas del video. Uselo para elegir el descriptor "
                             "del material y para no describir planos que no existen. Si el shotlist dice "
                             "otra cosa, mande lo que se ve.")
                    if CFG.get("escenas_sin_imagenes", True):
                        imgs = []      # con etiqueta fiable no hace falta gastar cuota en imagenes
        except Exception:
            pass
    if imgs:
        user = adjuntar_imagenes(user, imgs)
    salida = llamar_modelo(system, user)
    campos = parsear(salida)
    if not campos["NAME"] and not campos["COMMENT"]:
        raise RedactorError("No se han encontrado las lineas NAME/COMMENT en la respuesta:\n" + salida[:1500])
    campos["NAME"] = normalizar(campos["NAME"])
    campos["COMMENT"] = normalizar(campos["COMMENT"])
    if acortar and len(campos["COMMENT"]) > MAX_COMMENT:
        campos["COMMENT"] = acortar_comment(campos["COMMENT"])
    campos["RESTRICCIONES"] = normalizar(campos["RESTRICCIONES"]) or "SIN AVISO"
    if not campos["ENVIO"]:
        campos["ENVIO"] = " · ".join(str(ficha.get(k, "")) for k in ("numero", "fecha", "slug", "headline"))
    avisos = validar(campos)
    texto_ficha = (ficha.get("texto") or "").upper()
    todo = campos["NAME"] + " " + campos["COMMENT"]
    hablado = next((h for h in HABLADOS if h in todo), None)
    hay_soundbite = "SOUNDBITE" in texto_ficha
    if hablado and not hay_soundbite:
        avisos.append(f"Dice {hablado} pero el shotlist no tiene SOUNDBITE: deberia ser RECURSOS ... CON MOTIVO DE")
    if hay_soundbite and not hablado:
        avisos.append("El shotlist tiene SOUNDBITE y la ficha no dice de que material hablado se trata "
                      "(DECLARACIONES, RUEDA DE PRENSA, COMPARECENCIA, INTERVENCION, ENTREVISTA)")
    if (hablado
            and re.search(r"SCREEN ?GRAB|SCREENSHOT|SOCIAL MEDIA POST|TRUTH SOCIAL|\bPOST ON\b|POSTED ON", texto_ficha)
            and not hay_soundbite):
        avisos.append("Es una captura de redes: PUBLICACION EN REDES SOCIALES, no DECLARACIONES")
    if re.search(r"AIRED ON|TV FOOTAGE|BROADCAST FOOTAGE|\bCCTV\b|\bCGTN\b|GRAPHICS|CAPTIONS|ON.SCREEN TEXT|BURN(T|ED).IN|LOWER THIRD|CHYRON|SUBTITLE", texto_ficha) and "ROTULOS" not in campos["COMMENT"]:
        avisos.append("Material emitido o con graficos y el COMMENT no dice INCLUYE ROTULOS")
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
    # conflicto del pais que encabeza el NAME
    encabeza = campos["NAME"].split()[0] if campos["NAME"].split() else ""
    if (encabeza in PAISES_GUERRA
            and re.search(r"\bWAR\b|\bWARTIME\b|\bCONFLICT\b|\bFRONTLINE\b|\bFRONT LINE\b|\bSHELLING\b|\bAIR ?STRIKE", texto_ficha)
            and "GUERRA" not in campos["NAME"]):
        avisos.append(f"El NAME empieza por {encabeza} y el script cita la guerra: si es la de ese pais, "
                      f"GUERRA va detras del pais")
    return campos, avisos, salida


def formatear(campos, avisos=None):
    lineas = [f"{c}: {campos.get(c, '')}" for c in CAMPOS]
    if avisos:
        lineas.append("AVISOS: " + " | ".join(avisos))
    return "\n".join(lineas)
