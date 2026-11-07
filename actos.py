# -*- coding: utf-8 -*-
"""
actos.py - Que clase de acto es el material hablado de un envio: rueda de prensa, declaraciones,
comparecencia, entrevista, intervencion... Es lo que decide el descriptor del NAME y del COMMENT.

Tres fuentes, de la mas fiable a la menos:
  1. El texto de la agencia (acto_por_texto): el shotlist suele decirlo con todas las letras
     (NEWS CONFERENCE, INTERVIEW, SPEAKING TO REPORTERS, ADDRESSING PARLIAMENT, VOX POP...). Se mira solo
     el titular, las datelines y las lineas de plano, no el STORY, que habla de otros dias y otros actos.
     Si no lo dice, se mira quien habla en cada SOUNDBITE (quien_habla): ciudadanos que cuentan lo vivido
     (RESIDENT, WITNESS, SURVIVOR...) son TESTIMONIOS; cuatro o mas opinando (VOTER, SHOPPER...), ENCUESTA.
     Alguien con cargo apunta a DECLARACIONES, pero es una pista floja (puede ser una intervencion o una
     entrevista que el texto no nombra): no fija el acto, va aparte como ficha["hablado"].
  2. Los fotogramas, con el clasificador de escenas.py entrenado con tus fichas aprobadas: solo
     desempata cuando el texto no dice nada, y solo en las clases en que ha demostrado acertar.
  3. Lo que ha escrito el modelo (clase_de_ficha), que se compara con las otras dos: si no cuadran,
     aviso al catalogador. Nunca se cambia la ficha sola.

La misma funcion clase_de_ficha sirve para etiquetar los fotogramas de las fichas aprobadas y
entrenar el clasificador sin tener que ordenar imagenes a mano (entrenar_escenas.py --desde-aprobadas).
"""
import re
import shutil
from pathlib import Path

from paginas_word import extraer_script

BASE_DIR = Path(__file__).resolve().parent
CARPETA_AUTO = BASE_DIR / "escenas_auto"          # aqui van los fotogramas de las fichas aprobadas
EXT_IMAGEN = (".jpg", ".jpeg", ".png", ".webp")

# clase (nombre de carpeta del clasificador) -> descriptor que lleva la ficha
DESCRIPTOR = {
    "rueda_de_prensa": "RUEDA DE PRENSA",
    "comparecencia_conjunta": "DECLARACIONES EN COMPARECENCIA CONJUNTA",
    "comparecencia": "COMPARECENCIA",
    "declaraciones": "DECLARACIONES",
    "entrevista": "ENTREVISTA",
    "intervencion": "INTERVENCION",
    "saludo_reunion": "SALUDO Y REUNION / SALUDO Y MUDO",
    "video_promocional": "VIDEO PROMOCIONAL",
    "recursos": "RECURSOS (sin declaraciones)",
}
# lo que se reconoce por el texto: las de arriba (que son tambien las clases de imagen) y dos que solo se leen
DESCRIPTOR_TEXTO = dict(DESCRIPTOR, testimonios="TESTIMONIOS", encuesta="ENCUESTA")
# como se dice cada material hablado en el COMMENT (delante de quien habla, no solo su nombre)
EXPRESION_COMMENT = {
    "rueda_de_prensa": "RUEDA DE PRENSA", "comparecencia_conjunta": "EN COMPARECENCIA CONJUNTA",
    "comparecencia": "COMPARECENCIA", "declaraciones": "DECLARACIONES", "entrevista": "ENTREVISTA",
    "intervencion": "INTERVENCION", "testimonios": "TESTIMONIOS", "encuesta": "ENCUESTA",
}

# Reglas sobre el texto de la agencia, en orden: la primera que casa manda
REGLAS_TEXTO = [
    ("video_promocional", r"PROMOTIONAL VIDEO|PRODUCT VIDEO|COMPANY HANDOUT|CORPORATE VIDEO"),
    ("comparecencia_conjunta", r"JOINT (?:NEWS|PRESS) CONFERENCE|JOINT PRESSER|JOINT (?:PRESS )?STATEMENTS?\b|JOINT BRIEFING"),
    ("rueda_de_prensa", r"(?:NEWS|PRESS) CONFERENCE|PRESSER\b|(?:PRESS|NEWS|MEDIA) BRIEFING|PRESS BRIEFING"),
    ("encuesta", r"\bVOX[ -]?POPS?\b"),
    ("entrevista", r"INTERVIEW WITH|IN AN INTERVIEW|DURING AN INTERVIEW|\(INTERVIEW\)|\bINTERVIEW\b(?! (?:ROOM|REQUEST))|"
                   r"(?:SPEAKING|TALKING) TO (?:REUTERS|THE ASSOCIATED PRESS|AP|EBU)\b|TOLD (?:REUTERS|THE ASSOCIATED PRESS|AP)\b"),
    ("comparecencia", r"ADDRESS(?:ING|ES)? (?:PARLIAMENT|CONGRESS|THE SENATE|LAWMAKERS|THE HOUSE|THE BUNDESTAG|THE KNESSET|THE DUMA)|"
                      r"TESTIF(?:Y|YING|IES|IED)|(?:CONGRESSIONAL|SENATE|COMMITTEE|PARLIAMENTARY) HEARING|HEARING BEFORE|"
                      r"BEFORE (?:THE )?(?:COMMITTEE|PARLIAMENT|CONGRESS|SENATE)|"
                      r"PARLIAMENTARY (?:SESSION|QUESTIONS)|QUESTION TIME"),
    ("intervencion", r"(?:SPEECH|ADDRESS(?:ING|ES)?|SPEAKING|REMARKS) (?:AT|DURING|TO) (?:THE |A |AN )?"
                     r"(?:SUMMIT|CONFERENCE|RALLY|CONGRESS|FORUM|CEREMONY|GENERAL ASSEMBLY|CONVENTION|EVENT|MEETING OF|GALA)|"
                     r"\bRALLY\b|DELIVER(?:S|ING)? (?:A |HIS |HER )?SPEECH|\bSPEECH\b"),
    ("declaraciones", r"(?:SPEAKING|TALKING|REMARKS) TO (?:REPORTERS|THE MEDIA|MEDIA|JOURNALISTS|THE PRESS|PRESS)|"
                      r"DOORSTE?P|STATEMENT TO (?:THE )?(?:MEDIA|PRESS)|REPORTERS' QUESTIONS"),
    ("saludo_reunion", r"SHAK(?:E|ES|ING) HANDS|HANDSHAKE|BILATERAL MEETING|MEETS? WITH|MEETING WITH|TALKS WITH"),
]
HAY_VOZ_RE = re.compile(r"SOUNDBITE|\bSOT\b|\bSOTS\b|INTERVIEW WITH|\bSAYING\b", re.I)
LINEA_PLANO_RE = re.compile(r"^\s*(?:\d+\.|-)\s*(.+)$", re.M)
DATELINE_RE = re.compile(r"^(?:VIDEO SHOWS|SHOWS)\s*:?\s*(.+)$", re.M | re.I)

# quien habla en una linea de SOUNDBITE: lo que va detras de la marca, sin el idioma, hasta SAYING o los dos puntos
# (Reuters: SOUNDBITE (English) RESIDENT ALI HASSAN, SAYING: / AP: SOUNDBITE (English) Ali Hassan, resident: /
#  EBU: SOT Ali Hassan, resident)
SOUNDBITE_RE = re.compile(r"\(?\b(?:SOUNDBITES?|SOTS?)\b\)?\s*:?\s*(.*)$", re.M | re.I)
# ciudadanos que cuentan lo que han vivido o visto
VIVIDO_RE = re.compile(
    r"\b(?:RESIDENTS?|WITNESS(?:ES)?|EYEWITNESS(?:ES)?|SURVIVORS?|VICTIMS?|RELATIVES?|FAMILY MEMBERS?|"
    r"(?:MOTHER|FATHER|SON|DAUGHTER|BROTHER|SISTER|WIFE|HUSBAND|WIDOW(?:ER)?|UNCLE|AUNT|COUSIN|GRAND(?:MOTHER|FATHER)|"
    r"NEPHEW|NIECE|PARENTS?|FRIEND|NEIGHBOU?R) OF|NEIGHBOU?RS?|VILLAGERS?|DISPLACED|REFUGEES?|EVACUEES?|PATIENTS?|"
    r"INJURED|HOMEOWNERS?|LOCALS?|UNIDENTIFIED|(?:LOCAL |YOUNG |ELDERLY |OLD )?(?:MAN|WOMAN|BOY|GIRL))\b")
# ciudadanos que dan su opinion (si son cuatro o mas, encuesta)
OPINION_RE = re.compile(
    r"\b(?:VOTERS?|SHOPPERS?|FANS?|SUPPORTERS?|PASSERS?-?BY|PASSERSBY|COMMUTERS?|CONSUMERS?|CUSTOMERS?|TOURISTS?|"
    r"VISITORS?|PEDESTRIANS?|CITIZENS?)\b")
# palabras de cargo o de quien habla en nombre de algo: con una de ellas no es un ciudadano cualquiera, aunque la
# descripcion lleve tambien RESIDENT o VICTIMS (RESIDENT COORDINATOR, LAWYER FOR THE VICTIMS)
CARGO_RE = re.compile(
    r"\b(?:MINISTER|PRESIDENT|PREMIER|CHANCELLOR|SPOKES(?:MAN|WOMAN|PERSON)|SECRETARY|DIRECTOR|CHIEF|HEAD|MAYOR|"
    r"GOVERNOR|OFFICIALS?|LAWYER|ATTORNEY|PROSECUTOR|JUDGE|POLICE|OFFICER|COMMANDER|GENERAL|AMBASSADOR|ENVOY|CEO|"
    r"CHAIR(?:MAN|WOMAN|PERSON)?|MANAGER|COACH|PLAYER|ACTOR|ACTRESS|SINGER|ARTIST|PROFESSOR|ANALYST|EXPERT|"
    r"RESEARCHER|SCIENTIST|LEADER|SENATOR|LAWMAKER|MP|DEPUTY|COORDINATOR|REPRESENTATIVE|COUNCIL(?:LOR|OR)?|"
    r"ADVISE[RO]|FOUNDER|ACTIVIST|CAMPAIGNER|ORGANISER|ORGANIZER|CANDIDATE|KING|QUEEN|PRINCE|PRINCESS|POPE|"
    r"DOCTOR|SURGEON|NURSE|RESCUER|FIREFIGHTER|SOLDIER|COMMISSIONER|DELEGATE|OWNER|PRIEST|IMAM|RABBI|BISHOP)\b")
MIN_ENCUESTA = 4
# cargo de verdad (publico o de representacion): con el, aunque diga VOTER o RESIDENT, no es un ciudadano cualquiera.
# Mas corto que CARGO_RE: "BUSINESS OWNER" o "STUDENT" no quitan a nadie de ser un votante que opina
CARGO_FUERTE_RE = re.compile(
    r"\b(?:MINISTER|PRESIDENT|PREMIER|CHANCELLOR|SPOKES(?:MAN|WOMAN|PERSON)|SECRETARY|MAYOR|GOVERNOR|OFFICIALS?|"
    r"SENATOR|LAWMAKER|MP|CANDIDATE|AMBASSADOR|ENVOY|COMMANDER|GENERAL|CHIEF|DIRECTOR|CEO|COORDINATOR|"
    r"REPRESENTATIVE|LAWYER|ATTORNEY|PROSECUTOR|JUDGE|POLICE|KING|QUEEN|PRINCE|PRINCESS|POPE)\b")


def es_ciudadano(quien):
    """True si quien habla es un ciudadano sin cargo (votante, vecino, testigo...): sus declaraciones son testimonios."""
    quien = (quien or "").upper()
    return bool((VIVIDO_RE.search(quien) or OPINION_RE.search(quien)) and not CARGO_FUERTE_RE.search(quien))


def _quien_habla(guion):
    """Las descripciones de quien habla en cada SOUNDBITE o SOT, en mayusculas ("" si la linea no lo dice)."""
    salida = []
    for resto in SOUNDBITE_RE.findall(guion):
        quien = re.sub(r"\([^)]*\)", " ", resto.upper())             # idioma y demas parentesis
        quien = re.split(r"\bSAYING\b|:|[\"“”«]", quien)[0]                # lo que dice, fuera
        salida.append(" ".join(quien.replace(",", " ").split()))
    return salida


def quien_habla(ficha):
    """(clase, evidencia) segun quien habla en los SOUNDBITE del shotlist: "testimonios", "encuesta",
    "declaraciones" (alguien con cargo: pista floja) o (None, ""). Una linea con solo el nombre no dice nada."""
    quienes = [q for q in _quien_habla(extraer_script(ficha.get("texto") or "")) if q]
    if not quienes:
        return None, ""
    con_cargo = [q for q in quienes if CARGO_RE.search(q)]
    vivido = [q for q in quienes if VIVIDO_RE.search(q) and q not in con_cargo]
    opinion = [q for q in quienes if OPINION_RE.search(q) and q not in con_cargo and q not in vivido]
    if con_cargo:
        return "declaraciones", f"habla alguien con cargo: {con_cargo[0][:80]}"
    if len(opinion) >= MIN_ENCUESTA and not vivido:
        return "encuesta", f"{len(opinion)} ciudadanos opinando: {opinion[0][:60]}"
    if vivido:
        return "testimonios", f"ciudadanos que cuentan lo vivido: {vivido[0][:80]}"
    return None, ""


def _texto_acto(ficha):
    """Titular, slug, datelines y lineas de plano: donde la agencia dice que es cada cosa."""
    texto = ficha.get("texto") or ""
    guion = extraer_script(texto)
    partes = [ficha.get("headline") or "", ficha.get("slug") or ""]
    partes += DATELINE_RE.findall(texto)
    partes += LINEA_PLANO_RE.findall(guion)
    return "\n".join(partes).upper()


def acto_por_texto(ficha):
    """(clase, evidencia) segun el texto de la agencia, o (None, "") si no lo dice.
    Sin ninguna voz en el shotlist y con planos: recursos."""
    texto = _texto_acto(ficha)
    hay_voz = bool(HAY_VOZ_RE.search(texto))
    for clase, patron in REGLAS_TEXTO:
        if clase == "saludo_reunion" and hay_voz:
            continue                                  # si alguien habla, no es un saludo mudo
        m = re.search(patron, texto)
        if m:
            return clase, m.group(0).strip()
    if not hay_voz and LINEA_PLANO_RE.search(extraer_script(ficha.get("texto") or "")):
        return "recursos", "ningun SOUNDBITE en el shotlist"
    if hay_voz:
        clase, evidencia = quien_habla(ficha)
        if clase in ("testimonios", "encuesta"):
            return clase, evidencia
    return None, ""


def clase_de_ficha(name, comment):
    """La clase de acto que dice una ficha (la del modelo, o una aprobada), o None si no es de las
    que se pueden aprender de la imagen (archivo, resumenes deportivos, recopilatorios)."""
    name, comment = (name or "").upper(), (comment or "").upper()
    if re.search(r"\bARCHIVO\b|\bRESUMEN\b|\bRECOPILA", name):
        return None                                   # material mezclado: no enseña una sola escena
    if "VIDEO PROMOCIONAL" in comment:
        return "video_promocional"
    if "EN COMPARECENCIA CONJUNTA" in comment:
        return "comparecencia_conjunta"
    if re.search(r"SALUDOS? Y (?:MUDOS?|REUNION)", name + " " + comment):
        return "saludo_reunion"
    hablados = {"RUEDA DE PRENSA": "rueda_de_prensa", "COMPARECENCIA": "comparecencia",
                "INTERVENCION": "intervencion", "ENTREVISTA": "entrevista", "DECLARACIONES": "declaraciones"}
    en_name = [(name.find(h), c) for h, c in hablados.items() if h in name]
    if en_name:
        return min(en_name)[1]                        # el primer descriptor del NAME
    tras_lugar = re.sub(r"^[^.]*\.\s*", "", comment, count=1)
    en_comment = [(tras_lugar.find(h), c) for h, c in hablados.items() if h in tras_lugar]
    if en_comment and min(en_comment)[0] == 0:
        return min(en_comment)[1]
    if re.match(r"(RECURSOS|VISTAS AEREAS|SECUELAS|DAÑOS|MOMENTO|IMAGENES DE|LLEGADA|PHOTOCALL|ALFOMBRA)", tras_lugar) \
            and not any(h in comment for h in hablados):
        return "recursos"
    return None


def comparar(ficha, campos, imagen=None):
    """Avisos si el acto que dice la ficha no cuadra con el texto de la agencia o con la imagen.
    imagen = (clase, confianza, fiable) del clasificador, o None."""
    avisos = []
    escrito = clase_de_ficha(campos.get("NAME"), campos.get("COMMENT"))
    por_texto, evidencia = (ficha.get("acto") or {}).get("clase"), (ficha.get("acto") or {}).get("evidencia", "")
    # recursos delante e INCLUYE TESTIMONIOS o ENCUESTA detras es correcto: la ficha "es" recursos sin contradecir
    compatibles = (por_texto == "recursos" and escrito == "saludo_reunion") or \
        (por_texto in ("testimonios", "encuesta") and escrito == "recursos")
    if por_texto and escrito and por_texto != escrito and not compatibles:
        avisos.append(f"El shotlist dice {DESCRIPTOR_TEXTO[por_texto]} (\"{evidencia}\") y la ficha dice {DESCRIPTOR[escrito]}")
    comment = (campos.get("COMMENT") or "").upper()
    expresion = EXPRESION_COMMENT.get(por_texto)
    if expresion and expresion not in comment:
        avisos.append(f"El shotlist dice {DESCRIPTOR_TEXTO[por_texto]} y el COMMENT no lo pone: va {expresion} delante "
                      "de quien habla, no solo su nombre")
    elif not por_texto and ficha.get("hablado") and not any(e in comment for e in EXPRESION_COMMENT.values()):
        avisos.append("En el shotlist habla alguien con cargo y el COMMENT no dice que es (DECLARACIONES DE, "
                      "ENTREVISTA A, INTERVENCION DE...): va delante de quien habla, no solo su nombre")
    if imagen and imagen[2] and escrito and imagen[0] in DESCRIPTOR and imagen[0] != escrito and imagen[0] != por_texto:
        avisos.append(f"Por la imagen parece {DESCRIPTOR.get(imagen[0], imagen[0])} (confianza {imagen[1]:.2f}) "
                      f"y la ficha dice {DESCRIPTOR[escrito]}: revisar")
    return avisos


# ====================================================================== fotogramas para entrenar
def huella_fotogramas(rutas):
    """Lo que se guarda en la ficha al redactarla: que fotogramas eran y como estaban. Al aprobarla se
    comprueba que siguen igual, porque miniaturas/<numero>/ se sobrescribe si se vuelve a sacar ese
    numero (Reuters repite numeros de un dia para otro) y entonces ya no son de esta ficha."""
    huella = []
    for r in rutas or []:
        try:
            st = Path(r).stat()
        except OSError:
            continue
        huella.append({"ruta": str(r), "mtime": st.st_mtime_ns, "tam": st.st_size})
    return huella


def siguen_igual(huella):
    rutas = []
    for h in huella or []:
        try:
            st = Path(h["ruta"]).stat()
        except (OSError, KeyError, TypeError):
            return []
        if st.st_mtime_ns != h.get("mtime") or st.st_size != h.get("tam"):
            return []
        rutas.append(Path(h["ruta"]))
    return rutas


def clave_envio(numero, fecha):
    """numero_aaaammdd: nombre de grupo de los fotogramas de un envio (para no evaluar con ellos mismos)."""
    num = re.sub(r"[^\w-]", "", str(numero or "")) or "sin_numero"
    partes = (fecha or "").strip().split("/")
    fch = "".join(reversed(partes)) if len(partes) == 3 and all(x.isdigit() for x in partes) else "sinfecha"
    return f"{num}_{fch}"


def copiar_para_entrenar(clase, numero, fecha, rutas, carpeta=CARPETA_AUTO):
    """Copia los fotogramas a escenas_auto/<clase>/<numero>_<fecha>_<i>.jpg. Si ese envio ya estaba en
    otra clase (se aprobo antes con otro descriptor), se quita de alli: manda la ultima aprobacion.
    Devuelve cuantos se han copiado."""
    clave = clave_envio(numero, fecha)
    carpeta = Path(carpeta)
    if carpeta.exists():
        for viejo in carpeta.glob(f"*/{clave}_*"):
            try:
                viejo.unlink()
            except OSError:
                pass
    if not clase or not rutas:
        return 0
    destino = carpeta / clase
    destino.mkdir(parents=True, exist_ok=True)
    n = 0
    for i, r in enumerate(rutas, 1):
        r = Path(r)
        if r.suffix.lower() not in EXT_IMAGEN:
            continue
        try:
            shutil.copy2(r, destino / f"{clave}_{i:02d}{r.suffix.lower()}")
            n += 1
        except OSError:
            continue
    return n


def guardar_para_entrenar(ficha, campos):
    """Al aprobar una ficha: sus fotogramas pasan a ser ejemplo de su tipo de acto.
    Devuelve (copiados, motivo si no se ha copiado nada)."""
    clase = clase_de_ficha(campos.get("NAME"), campos.get("COMMENT"))
    if not clase:
        copiar_para_entrenar(None, ficha.get("numero"), ficha.get("fecha"), [])   # por si antes tenia otra
        return 0, "la ficha no es de un tipo de acto que se aprenda (archivo, resumen...)"
    rutas = siguen_igual(ficha.get("fotogramas"))
    if rutas:
        return copiar_para_entrenar(clase, ficha.get("numero"), ficha.get("fecha"), rutas), ""
    # los originales ya no estan, pero si se copiaron en una aprobacion anterior se cambian de clase
    movidos = mover_a_clase(clase, ficha.get("numero"), ficha.get("fecha"))
    if movidos:
        return movidos, ""
    if not ficha.get("fotogramas"):
        return 0, "la ficha no tiene fotogramas guardados"
    return 0, "los fotogramas de ese numero se han sobrescrito con otro envio"


def mover_a_clase(clase, numero, fecha, carpeta=CARPETA_AUTO):
    """Pasa a <clase> los fotogramas ya copiados de ese envio que esten en otra clase. Devuelve cuantos hay
    ahora en <clase>."""
    clave = clave_envio(numero, fecha)
    carpeta = Path(carpeta)
    if not carpeta.exists():
        return 0
    destino = carpeta / clase
    n = 0
    for viejo in sorted(carpeta.glob(f"*/{clave}_*")):
        if viejo.parent != destino:
            destino.mkdir(parents=True, exist_ok=True)
            try:
                viejo.replace(destino / viejo.name)
            except OSError:
                continue
        n += 1
    return n
