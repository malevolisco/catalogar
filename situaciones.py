# -*- coding: utf-8 -*-
"""
situaciones.py - Lo que la agencia dice del material, leido del texto de la ficha con reglas fijas.

El modelo escribe RESTRICCIONES a partir de la tabla del criterio, pero nadie comprobaba que lo que
decia la pagina y lo que decia la ficha coincidieran. Aqui se busca en el texto, con expresiones
fijas y caso por caso, lo que cambia el uso del material (embargo, no archivar, no usar en España,
credito obligatorio, limites de tiempo...) y lo que cambia como se cataloga (archivo, mudo, en bruto,
imagenes generadas por ordenador, obituario...). Con eso:

  1. se le dan pistas al modelo (ficha["pistas"], van en el prompt como "detectado automaticamente");
  2. se cruza despues con lo que ha escrito (cruzar_restricciones): falta algo que la pagina dice,
     o dice algo que la pagina no respalda -> aviso;
  3. lo que todo el mundo tiene que ver va a ficha["alerta"] (como la del script cortado); lo demas,
     a ficha["avisos"], que solo lee el catalogador.

Las expresiones estan sacadas de fichas reales de Reuters, AP y EBU. Cuando una restriccion tiene
formula en el criterio (reglas_patrones.md, seccion 5), aqui se usa la misma, para que el cruce sea
literal.

    from situaciones import detectar, cruzar_restricciones
    detectar(ficha)                     # deja ficha["situaciones"], ["pistas"], ["alerta"], ["avisos"], ["acto"]
    cruzar_restricciones(texto_restricciones, ficha["situaciones"])   # -> avisos

    python situaciones.py debug\\2656_volcado.txt     para ver que detecta en un envio guardado
"""
import re
import sys

from actos import DESCRIPTOR, acto_por_texto
from paginas_word import extraer_script

# ---------------------------------------------------------------- trozos del texto donde mirar
RESTR_REUTERS_RE = re.compile(r"Full Restriction Details\s*\n(.*?)\n\s*Details\b", re.S)
RESTR_EBU_RE = re.compile(r"^RESTRICTIONS \(EBU[^\n]*\n(.*)$", re.S | re.M)
RESTR_AP_RE = re.compile(r"^(?:Usage Terms|Restrictions|Eds Notes)\s*:?\s*\n?(.*?)(?=\n(?:Video Metadata|Story No|Duration|Details|Restrictions|Eds Notes|Usage Terms)\b|\Z)", re.S | re.M)
SHOWS_RE = re.compile(r"^(?:VIDEO SHOWS|SHOWS)\s*:?\s*(.+)$", re.M | re.I)
# el formulario de "denunciar contenido" de Reuters contiene literalmente "Graphic Content": fuera
COLA_RE = re.compile(r"\n(?:Report content|Report this item).*$", re.S | re.I)
FECHA_ANTIGUA_RE = re.compile(r"\((?:[A-Z]+\s+\d{1,2},\s*)?((?:19|20)\d\d)\)")


def trozos(ficha):
    """Devuelve las partes del texto en mayusculas: restricciones (los cuadros), shows (datelines),
    script (shotlist y story), cabecera (titular y slug) y todo."""
    texto = COLA_RE.sub("", (ficha.get("texto") or "").replace("\r", ""))
    bloques = []
    for rx in (RESTR_REUTERS_RE, RESTR_EBU_RE, RESTR_AP_RE):
        for m in rx.finditer(texto):
            bloques.append(m.group(1))
    shows = "\n".join(m.group(1) for m in SHOWS_RE.finditer(texto))
    # titular, slug y las dos primeras lineas del texto (donde Reuters pone REFILE-, KILL, OBIT-)
    cabecera = "\n".join(str(ficha.get(k) or "") for k in ("headline", "slug")) + "\n" + "\n".join(texto.splitlines()[:2])
    return {
        "restricciones": "\n".join(bloques).upper(),
        "shows": shows.upper(),
        "script": extraer_script(texto).upper(),
        "cabecera": cabecera.upper(),
        "todo": texto.upper(),
        "agencia": (ficha.get("agencia") or "").upper(),
    }


# ---------------------------------------------------------------- las reglas
# Cada una: clave, donde mirar, expresion, y que hacer con lo encontrado. "formula" es la frase de
# RESTRICCIONES (con {x} donde va lo capturado); "alerta" y "aviso" son textos para el catalogador.
# "clave_cruce" es la palabra por la que se reconoce esa formula en lo que escribe el modelo.

def _r(patron, extra=0):
    return re.compile(patron, re.I | extra)


REGLAS = [
    # --- restricciones con formula (orden de la tabla del criterio)
    dict(clave="credito", donde=("restricciones", "shows", "script"),
         rx=_r(r"\b(?:MUST\s+(?:ON[- ]SCREEN\s+)?(?:COURTESY|CREDIT)|MANDATORY\s+(?:ON[- ]SCREEN\s+)?(?:CREDIT|COURTESY)|"
               r"PLEASE\s+CREDIT|COURTESY\s+OF|CLIENTS?\s+(?:ARE\s+)?REQUIRED[^.\n]{0,40}?CREDIT|"
               r"MUST\s+BE\s+CREDITED\s+(?:TO|AS)|ON[- ]SCREEN\s+CREDIT(?:\s+(?:TO|FOR))?|^CREDIT)\s*:?\s*([A-Z0-9][^)\n.;]{1,60})", re.M),
         formula="ROTULAR CORTESIA DE ''{x}''", cruce=r"CORTESIA"),
    dict(clave="max_duracion", donde=("restricciones",),
         rx=_r(r"\b(?:NO\s+(?:USE\s+)?MORE\s+THAN|MAX(?:IMUM)?(?:\s+USE)?(?:\s+OF)?|(?:USE\s+)?LIMITED\s+TO|UP\s+TO)\s+(\d+)\s*(SECONDS?|SECS?|MINUTES?|MINS?)\b"),
         formula="NO USAR MAS DE {x}", cruce=r"NO USAR MAS DE"),
    dict(clave="no_archivo", donde=("restricciones",),
         rx=_r(r"\b(?:NO\s+(?:ARCHIV(?:E|AL|ING)|LIBRARY)|NOT\s+FOR\s+ARCHIV|MUST\s+BE\s+(?:DELETED|DESTROYED)|DELETE\s+AFTER)\b"),
         formula="NO ARCHIVAR", cruce=r"NO ARCHIVAR(?! PASADOS)",
         alerta="ALERTA: La agencia dice que este material NO se puede archivar."),
    dict(clave="archivo_dias", donde=("restricciones",),
         rx=_r(r"NO\s+USE\s*/\s*ARCHIVE\s+AFTER\s+(\d+)\s+DAYS"),
         formula="NO ARCHIVAR PASADOS {x} DIAS", cruce=r"NO ARCHIVAR PASADOS"),
    dict(clave="caduca_horas", donde=("restricciones",),
         rx=_r(r"\b(?:NO\s+USE\s+AFTER|USE\s+WITHIN|EXPIRES?\s+(?:AFTER|IN)|VALID\s+FOR)\s+(\d+)\s*(HOURS?|HRS?|DAYS?)\b"),
         formula="NO USAR PASADAS {x}", cruce=r"NO USAR PASAD"),
    dict(clave="caduca_fecha", donde=("restricciones",),
         rx=_r(r"\b(?:NO\s+USE\s+AFTER|EXPIRES?\s*(?:ON|:)?|VALID\s+UNTIL|NOT\s+FOR\s+USE\s+AFTER)\s+(\d{1,2}\s+[A-Z]{3,9}\.?\s+20\d\d(?:[ ,]+\d{1,2}[:.]\d{2}(?:\s*(?:GMT|UTC|BST|CET|ET))?)?)"),
         formula="NO USAR DESPUES DEL {x}", cruce=r"NO USAR DESPUES"),
    dict(clave="un_uso", donde=("restricciones",),
         rx=_r(r"\b(?:ONE|SINGLE)\s+(?:USE|TRANSMISSION)\s+ONLY\b"),
         formula="UN SOLO USO", cruce=r"UN SOLO USO"),
    dict(clave="veces", donde=("restricciones",),
         rx=_r(r"\b(\d+)\s+(?:USES?|TRANSMISSIONS?|PLAYS?)\s+(?:PER|IN|EVERY|WITHIN)\s+(\d+)\s+(HOURS?|DAYS?)"),
         formula="NO EMITIR MAS DE {x}", cruce=r"NO EMITIR MAS DE"),
    dict(clave="reventa", donde=("restricciones",),
         rx=_r(r"\bNO(?:T\s+FOR)?\s+(?:RESALE|SALES?|SYNDICATION|REDISTRIBUTION|THIRD[- ]PARTY\s+SALES?)\b"),
         formula="NO REVENDER NI CEDER A TERCEROS", cruce=r"NO REVENDER", salvo_agencia="EBU"),
    dict(clave="embargo", donde=("restricciones", "shows", "cabecera"),
         rx=_r(r"\b(?:EMBARGO(?:ED)?\s*(?:UNTIL|TILL?|TO)?|HOLD\s+(?:FOR\s+RELEASE(?:\s+UNTIL)?|TIL{1,2})|NOT\s+FOR\s+(?:USE|RELEASE|PUBLICATION)\s+(?:BEFORE|UNTIL|PRIOR\s+TO))\b\s*:?\s*([^\n.;]{3,60})"),
         formula="EMBARGADO HASTA {x}", cruce=r"EMBARGAD",
         alerta="ALERTA: Envio embargado ({x})."),
    dict(clave="espana", donde=("restricciones", "shows"),
         rx=_r(r"\b(?:PART\s+)?(?:NO\s+(?:USE|ACCESS)|NOT\s+(?:FOR\s+USE|AVAILABLE)|CANNOT\s+BE\s+USED)\b[^.\n]{0,60}?\b(?:IN\s+)?(SPAIN|SPANISH|IBERIA[N]?)\b"),
         formula="NO USAR EN ESPAÑA", cruce=r"NO USAR EN ESPAÑA",
         alerta="ALERTA: NO USAR EN ESPAÑA segun la agencia."),
    dict(clave="rtve", donde=("restricciones",),
         rx=_r(r"^RTVE:\s*(.+)$", re.M),
         formula=None, cruce=None,
         alerta="ALERTA: EBU trae una linea que nombra a RTVE: {x}"),
    dict(clave="europa", donde=("restricciones", "shows"),
         rx=_r(r"\b(?:PART\s+)?(?:NO\s+(?:USE|ACCESS)|NOT\s+(?:FOR\s+USE|AVAILABLE))\b[^.\n]{0,60}?\b(?:IN\s+)?(EUROPE(?:AN)?|EU|WORLDWIDE)\b"),
         formula="NO USAR EN EUROPA", cruce=r"NO USAR EN EUROPA",
         alerta="ALERTA: NO USAR EN EUROPA segun la agencia."),
    dict(clave="no_digital", donde=("restricciones",),
         rx=_r(r"\b(?:DIGITAL|ONLINE|WEB|INTERNET)\s*:\s*NO\s+USE\b|\bNO\s+(?:DIGITAL|ONLINE|INTERNET|WEB)(?:\s+USE)?\b|\bBROADCAST\s+ONLY\b"),
         formula="NO DIGITAL", cruce=r"\bNO DIGITAL\b"),
    dict(clave="solo_digital", donde=("restricciones",),
         rx=_r(r"\bBROADCASTERS?\s*:\s*NO\s+USE\b|\b(?:DIGITAL|ONLINE)\s+ONLY\b|\bNO\s+(?:BROADCAST|TV)\s+USE\b"),
         formula="NO EMITIR. SOLO DIGITAL", cruce=r"SOLO DIGITAL"),
    dict(clave="redes", donde=("restricciones",),
         rx=_r(r"\bNO\s+(?:USE\s+ON\s+)?SOCIAL\s+MEDIA\b"),
         formula="NO USAR EN REDES SOCIALES", cruce=r"REDES SOCIALES"),
    dict(clave="musica", donde=("restricciones", "script"),
         rx=_r(r"\b(?:CONTAINS?\s+MUSIC|MUSIC\s+(?:RIGHTS|NOT\s+CLEARED|CLEARANCE)|CLEAR\s+(?:ALL\s+)?(?:MUSIC\s+)?RIGHTS|COPYRIGHTED\s+MUSIC)\b"),
         formula="CONTIENE MUSICA. VERIFICAR DERECHOS", cruce=r"CONTIENE MUSICA"),
    dict(clave="no_reeditar", donde=("restricciones",),
         rx=_r(r"\b(?:MUST\s+NOT\s+BE\s+(?:EDITED|ALTERED|CUT)|NO\s+RE-?EDIT|DO\s+NOT\s+(?:EDIT|ALTER)|USE\s+IN\s+FULL)\b"),
         formula="NO REEDITAR", cruce=r"NO REEDITAR"),
    dict(clave="esta_noticia", donde=("restricciones",),
         rx=_r(r"\b(?:IN\s+CONNECTION\s+WITH|ONLY\s+(?:FOR|WITH)|FOR\s+USE\s+WITH)\s+THIS\s+(?:STORY|NEWS|EVENT|ITEM)\b"),
         formula="EMITIR SOLO EN EL CONTEXTO DE ESTA NOTICIA", cruce=r"CONTEXTO DE ESTA NOTICIA"),
    dict(clave="promocion", donde=("restricciones",),
         rx=_r(r"\bNO(?:T\s+FOR)?\s+(?:COMMERCIAL|ADVERTISING|PROMOTIONAL|MARKETING)\s+(?:USE|PURPOSES)?\b"),
         formula="NO USAR EN PROMOCION NI PUBLICIDAD", cruce=r"NO USAR EN PROMOCION"),
    dict(clave="informativos", donde=("restricciones",),
         rx=_r(r"\bNEWS\s+ACCESS\s+ONLY\b"),
         formula="SOLO USO EN INFORMATIVOS", cruce=r"SOLO USO EN INFORMATIVOS"),
    dict(clave="deportes", donde=("restricciones",),
         rx=_r(r"\bNO\s+USE\s+(?:ON|IN)\s+SPORTS?\s+(?:CHANNELS?|PROGRAMMES?|PROGRAMS?|SHOWS?)\b"),
         formula="NO USAR EN PROGRAMAS NI CANALES EXCLUSIVOS DE DEPORTES", cruce=r"CANALES EXCLUSIVOS DE DEPORTES",
         alerta="ALERTA: Derechos deportivos: leer el cuadro Restrictions completo."),
    dict(clave="derechos_deportivos", donde=("restricciones",),
         rx=_r(r"\bSEE\s+RESTRICTIONS\b|\bDORNA\b|\bUEFA\b|\bFIFA\b|\bLALIGA\b|\bRIGHTS\s+HOLDER"),
         formula=None, cruce=None,
         alerta="ALERTA: Derechos deportivos: leer el cuadro Restrictions completo."),
    # --- lo que cambia como se cataloga (avisos al catalogador)
    # solo como aviso de la agencia, no como palabra de la noticia ("two people killed", "troops withdrawn",
    # "ambassador recalled"): KILL como marca al principio de una linea del titular o slug, MANDATORY KILL,
    # "this item has been killed/withdrawn", o esas frases en el bloque de restricciones
    dict(clave="retirado", donde=("restricciones", "cabecera"),
         rx=_r(r"^\W*(?:MANDATORY\s+)?KILL\s*[:\-]|\bMANDATORY\s+KILL\b|"
               r"\b(?:ITEM|STORY|VIDEO|CLIP|SCRIPT)\s+(?:HAS\s+BEEN|IS|WAS)\s+(?:KILLED|WITHDRAWN|RECALLED|CANCELLED)\b|"
               r"\bPLEASE\s+DISREGARD\b|\bDO\s+NOT\s+(?:USE|PUBLISH)\s+THIS\b|\bKILL\s+ADVISORY\b", re.M),
         formula=None, cruce=None,
         alerta="ALERTA: La agencia ha RETIRADO o anulado este envio (KILL): no catalogar sin comprobar."),
    dict(clave="correccion", donde=("cabecera",),
         rx=_r(r"\b(?:REFILE|CORRECTED|CORRECTS|CORRECTION|CLARIFI(?:ES|CATION)|ADVISORY)\b"),
         formula=None, cruce=None,
         aviso="Envio corregido o reenviado (REFILE/CORRECTION): comprobar si sustituye a una ficha anterior"),
    dict(clave="archivo", donde=("cabecera", "shows"),
         rx=_r(r"\b(?:FILE|ARCHIVE|ARCHIVAL|FILE\s+(?:FOOTAGE|PICTURES|VIDEO))\b"),
         formula=None, cruce=None,
         aviso="Material de archivo (FILE): el NAME lleva ARCHIVO tras el pais (patron D); si solo son algunos planos, INCLUYE IMAGENES DE ARCHIVO"),
    dict(clave="mudo", donde=("script", "cabecera"),
         rx=_r(r"\(MUTE\)|\+\+\s*(?:PART\s+)?MUTE|\bNO\s+(?:SOUND|AUDIO)\b|\bSILENT\b"),
         formula=None, cruce=None,
         aviso="Material mudo (MUTE): valorar la marca SIN SONIDO"),
    dict(clave="audio", donde=("script", "cabecera"),
         rx=_r(r"\bAUDIO\s+AS\s+INCOMING\b|\bPOOR\s+AUDIO\b|\bAUDIO\s+(?:DROPS?|PROBLEMS?|ISSUES?)\b"),
         formula=None, cruce=None,
         aviso="La agencia avisa de problemas de audio"),
    dict(clave="explicito", donde=("script", "cabecera", "restricciones"),
         rx=_r(r"\bGRAPHIC\s+(?:CONTENT|IMAGES|FOOTAGE)\b|\bDISTURBING\b|\bDISTRESSING\b|\bVIEWER\s+DISCRETION\b|\bFLASHING\s+(?:IMAGES|LIGHTS)\b|\bSTROBE\b"),
         formula=None, cruce=None,
         aviso="La agencia avisa de contenido explicito o destellos"),
    dict(clave="bruto", donde=("cabecera",),
         rx=_r(r"\b(?:RUSHES|UNEDITED|RAW\s+(?:VIDEO|FOOTAGE|FEED)|LIVE\s+(?:FEED|SIGNAL|COVERAGE))\b"),
         formula=None, cruce=None,
         aviso="Material en bruto o directo: RECURSOS sin editar, revisar el shotlist"),
    dict(clave="ia", donde=("script", "cabecera", "restricciones"),
         rx=_r(r"\bAI[- ]GENERATED\b|\bARTIFICIAL\s+INTELLIGENCE\b|\bDIGITALLY\s+(?:ALTERED|MANIPULATED)\b|\bCOMPUTER[- ]GENERATED\b|\bCGI\b|\bDEEPFAKE\b"),
         formula=None, cruce=None,
         aviso="Imagenes generadas o alteradas digitalmente: el COMMENT lleva la marca IMAGENES GENERADAS POR INTELIGENCIA ARTIFICIAL"),
    dict(clave="pool", donde=("shows", "script"),
         rx=_r(r"\b(?:[A-Z]+\s+)?POOL\b|\bHOST\s+(?:BROADCASTER|TV)\b"),
         formula=None, cruce=None,
         aviso="Señal pool: comprobar si exige credito"),
    dict(clave="obituario", donde=("cabecera",),
         rx=_r(r"\bOBIT(?:UARY)?\b|\bDIES\b|\bDEAD\s+AT\s+\d|\bDEATH\s+OF\b|\bPASSE[SD]\s+AWAY\b|\bLYING\s+IN\s+STATE\b|\bSTATE\s+FUNERAL\b"),
         formula=None, cruce=None,
         aviso="Posible obituario: si trae material de archivo, patron ARCHIVO ... CON MOTIVO DE SU MUERTE"),
    dict(clave="tercero", donde=("cabecera", "todo"),
         rx=_r(r"^PAID CONTENT FROM (.+)$", re.M),
         formula=None, cruce=None,
         aviso="Proveedor externo ({x}): suele exigir credito, comprobar ROTULAR CORTESIA"),
]

# Territorios que NO afectan a España: si el modelo los mete en RESTRICCIONES, sobra
TERRITORIO_AJENO_RE = _r(r"\b(?:PART\s+)?(?:NO\s+(?:USE|ACCESS)|NOT\s+AVAILABLE)\s+(?:IN\s+|BY\s+)?([A-Z][A-Z ,/&]{2,60}?)(?:\s+MEDIA|\s+BROADCASTERS?|\.|\n|$)")
NUESTROS = ("SPAIN", "SPANISH", "IBERIA", "EUROPE", "EU", "WORLDWIDE")
# Cabezas de frase que admite RESTRICCIONES: lo que no empiece asi esta fuera de formula
CABEZAS_RE = _r(r"^(?:NO |ROTULAR CORTESIA DE ''|UN SOLO USO|EMBARGADO HASTA|SOLO EMISION|SOLO DIGITAL|SOLO USO|"
                r"CONTIENE MUSICA|VERIFICAR DERECHOS|EMITIR SOLO EN EL CONTEXTO|SIN AVISO)")
# NO USAR EN [territorio]: solo España y Europa afectan a RTVE; lo demas se ignora segun el criterio
TERRITORIO_EN_FICHA_RE = _r(r"^NO USAR EN (?!ESPAÑA\b|EUROPA\b|DIGITAL\b|REDES\b|PROMOCION\b|PROGRAMAS\b)(.+)$")
AGENCIAS_PROPIAS = ("REUTERS", "AP", "ASSOCIATED PRESS", "EBU")


def _valor(m, regla):
    """Lo capturado por la regla, limpio, para meterlo en la formula."""
    grupos = [g for g in m.groups() if g] if m.groups() else []
    x = " ".join(g.strip() for g in grupos)
    if regla["clave"] == "credito":
        x = re.sub(r"\s*[-–]\s*NO\s+ACCESS.*$", "", x).strip(" .:;")   # "(CNS - NO ACCESS ...)" -> CNS
    if regla["clave"] in ("max_duracion", "caduca_horas"):
        n, u = m.group(1), m.group(2).upper()
        u = "SEGUNDOS" if u.startswith("SEC") else "MINUTOS" if u.startswith("MIN") else "HORAS" if u.startswith(("HOUR", "HR")) else "DIAS"
        x = f"{n} {u}"
    if regla["clave"] == "veces":
        n, cada, u = m.group(1), m.group(2), m.group(3).upper()
        x = f"{n} VECES CADA {cada} {'HORAS' if u.startswith('HOUR') else 'DIAS'}"
    return x


def detectar(ficha):
    """Busca las situaciones en el texto de la ficha y deja en ella:
       ficha["situaciones"] = {"esperadas": [frases de RESTRICCIONES], "claves": [...], "evidencia": {clave: texto}}
       ficha["pistas"]      = texto para el prompt
       ficha["alerta"]      = alertas nuevas unidas a la que hubiera (" | ")
       ficha["avisos"]      += avisos nuevos
    Devuelve el diccionario de situaciones."""
    t = trozos(ficha)
    esperadas, claves, evidencia, alertas, avisos = [], [], {}, [], []
    vistos = set()
    for regla in REGLAS:
        if regla.get("salvo_agencia") and t["agencia"] == regla["salvo_agencia"]:
            continue
        for donde in regla["donde"]:
            m = regla["rx"].search(t[donde])
            if not m:
                continue
            x = _valor(m, regla)
            if regla["clave"] == "tercero" and any(a in x for a in AGENCIAS_PROPIAS):
                continue
            marca = (regla["clave"], x.upper())
            if marca in vistos:
                break
            vistos.add(marca)
            claves.append(regla["clave"])
            evidencia[regla["clave"]] = m.group(0).strip()[:120]
            if regla.get("formula"):
                esperadas.append(regla["formula"].format(x=x) + ".")
            if regla.get("alerta"):
                alertas.append(regla["alerta"].format(x=x))
            if regla.get("aviso"):
                avisos.append(regla["aviso"].format(x=x))
            break                                   # una vez por regla (la primera aparicion)
    # planos con fecha antigua en la dateline: archivo aunque no diga FILE
    fecha = ficha.get("fecha") or ""
    try:
        ano_ficha = int(fecha.split("/")[2])
        anos = {int(a) for a in FECHA_ANTIGUA_RE.findall(t["shows"])}
        if anos and min(anos) < ano_ficha - 1 and "archivo" not in claves:
            claves.append("archivo")
            evidencia["archivo"] = f"dateline de {min(anos)} en un envio de {ano_ficha}"
            avisos.append(f"Planos con fecha de {min(anos)} en un envio de {ano_ficha}: material de archivo (patron D o INCLUYE IMAGENES DE ARCHIVO)")
    except (ValueError, IndexError):
        pass
    # territorios ajenos: se anotan para el cruce, no se piden
    ajenos = []
    for m in TERRITORIO_AJENO_RE.finditer(t["restricciones"] + "\n" + t["shows"]):
        terr = m.group(1).strip()
        if not any(n in terr for n in NUESTROS) and terr not in ajenos:
            ajenos.append(terr)
    situaciones = {"esperadas": esperadas, "claves": claves, "evidencia": evidencia, "territorios_ajenos": ajenos}
    # tipo de acto (rueda de prensa, declaraciones, entrevista...) segun el shotlist
    clase, pista = acto_por_texto(ficha)
    if clase:
        situaciones["acto"] = {"clase": clase, "evidencia": pista[:120]}
    ficha["acto"] = situaciones.get("acto")
    ficha["situaciones"] = situaciones
    ficha["pistas"] = pistas(situaciones)
    if alertas:
        ficha["alerta"] = " | ".join([a for a in [ficha.get("alerta") or ""] if a] + alertas)
    if avisos:
        ficha.setdefault("avisos", []).extend(avisos)
    return situaciones


def pistas(situaciones):
    """Lo que se le dice al modelo: las frases de RESTRICCIONES que se esperan y por que."""
    lineas = []
    for frase in situaciones["esperadas"]:
        lineas.append(f"- RESTRICCIONES: {frase}")
    for terr in situaciones.get("territorios_ajenos") or []:
        lineas.append(f"- Restriccion territorial que no afecta a España ({terr}): se ignora")
    for clave in ("archivo", "mudo", "ia", "bruto", "obituario", "correccion"):
        if clave in situaciones["claves"]:
            lineas.append(f"- Situacion: {clave} ({situaciones['evidencia'].get(clave, '')})")
    if not lineas and situaciones["claves"] == []:
        lineas.append("- Ninguna restriccion reconocida en el texto: si no ves otra, RESTRICCIONES es SIN AVISO")
    acto = situaciones.get("acto")
    if acto:
        lineas.append(f"- Tipo de acto segun el shotlist: {DESCRIPTOR[acto['clase']]} (\"{acto['evidencia']}\")")
    return "\n".join(lineas)


def cruzar_restricciones(restricciones, situaciones):
    """Avisos sobre lo que el modelo ha escrito en RESTRICCIONES comparado con lo detectado:
       falta una restriccion que la pagina dice; hay una que la pagina no respalda; una frase fuera
       de formula; SIN AVISO mezclado con otras frases; un territorio que no afecta a España."""
    avisos = []
    r = (restricciones or "").strip()
    if not situaciones:
        return avisos
    esperadas = situaciones.get("esperadas") or []
    claves = set(situaciones.get("claves") or [])
    evidencia = situaciones.get("evidencia") or {}
    # 1. falta lo detectado
    for regla in REGLAS:
        if regla["clave"] in claves and regla.get("cruce") and not re.search(regla["cruce"], r):
            frase = next((e for e in esperadas if re.search(regla["cruce"], e)), regla["formula"] or "")
            avisos.append(f"RESTRICCIONES: falta {frase} (la agencia dice \"{evidencia.get(regla['clave'], '')}\")")
    if r == "SIN AVISO":
        return avisos
    # 2. frases que sobran o estan fuera de formula
    frases = [f.strip() for f in re.split(r"\.\s*", r) if f.strip()]
    for frase in frases:
        if frase == "SIN AVISO":
            avisos.append("RESTRICCIONES mezcla SIN AVISO con otras frases")
            continue
        if not CABEZAS_RE.match(frase):
            avisos.append(f"RESTRICCIONES fuera de formula: \"{frase}\"")
            continue
        for regla in REGLAS:
            if regla.get("cruce") and re.search(regla["cruce"], frase) and regla["clave"] not in claves:
                avisos.append(f"RESTRICCIONES sin respaldo en el texto de la agencia: \"{frase}\"")
                break
        m = TERRITORIO_EN_FICHA_RE.match(frase)
        if m:
            avisos.append(f"RESTRICCIONES recoge un territorio que no afecta a España ({m.group(1)}): se ignora")
    return avisos


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    from volcar import leer_volcado
    for ruta in sys.argv[1:]:
        ficha = leer_volcado(ruta)
        s = detectar(ficha)
        print(f"== {ruta}")
        print("esperadas:", s["esperadas"])
        print("claves:", s["claves"])
        for k, v in s["evidencia"].items():
            print(f"  {k}: {v}")
        if s["territorios_ajenos"]:
            print("territorios ajenos:", s["territorios_ajenos"])
        if ficha.get("alerta"):
            print("ALERTA:", ficha["alerta"])
        if s.get("acto"):
            print("acto:", s["acto"])
        print("avisos:", ficha.get("avisos"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
