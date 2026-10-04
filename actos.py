# -*- coding: utf-8 -*-
"""
actos.py - Que clase de acto es el material hablado de un envio: rueda de prensa, declaraciones,
comparecencia, entrevista, intervencion... Es lo que decide el descriptor del NAME y del COMMENT.

Tres fuentes, de la mas fiable a la menos:
  1. El texto de la agencia (acto_por_texto): el shotlist suele decirlo con todas las letras
     (NEWS CONFERENCE, INTERVIEW, SPEAKING TO REPORTERS, ADDRESSING PARLIAMENT...). Se mira solo el
     titular, las datelines y las lineas de plano, no el STORY, que habla de otros dias y otros actos.
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

# Reglas sobre el texto de la agencia, en orden: la primera que casa manda
REGLAS_TEXTO = [
    ("video_promocional", r"PROMOTIONAL VIDEO|PRODUCT VIDEO|COMPANY HANDOUT|CORPORATE VIDEO"),
    ("comparecencia_conjunta", r"JOINT (?:NEWS|PRESS) CONFERENCE|JOINT PRESSER|JOINT (?:PRESS )?STATEMENTS?\b|JOINT BRIEFING"),
    ("rueda_de_prensa", r"(?:NEWS|PRESS) CONFERENCE|PRESSER\b|(?:PRESS|NEWS|MEDIA) BRIEFING|PRESS BRIEFING"),
    ("entrevista", r"INTERVIEW WITH|IN AN INTERVIEW|DURING AN INTERVIEW|\(INTERVIEW\)|\bINTERVIEW\b(?! (?:ROOM|REQUEST))|"
                   r"(?:SPEAKING|TALKING) TO (?:REUTERS|THE ASSOCIATED PRESS|AP|EBU)\b|TOLD (?:REUTERS|THE ASSOCIATED PRESS)"),
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
    if por_texto and escrito and por_texto != escrito and not (por_texto == "recursos" and escrito == "saludo_reunion"):
        avisos.append(f"El shotlist dice {DESCRIPTOR[por_texto]} (\"{evidencia}\") y la ficha dice {DESCRIPTOR[escrito]}")
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
