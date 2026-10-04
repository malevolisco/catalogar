# -*- coding: utf-8 -*-
"""
buzon.py - Entrada de trabajo por correo.

Cada pocos minutos mira la bandeja de la cuenta de Catalogacion por IMAP, coge los correos sin leer
de remitentes permitidos, saca los numeros de envio del asunto, del cuerpo y de los adjuntos
(.csv, .txt, .xlsx), y devuelve una lista de peticiones. Quien las cataloga y responde es el servidor.

Los adjuntos no se barren como texto suelto: se leen como tabla con listas.py, que coge el numero
del principio de la columna Name, salta las filas que ya tienen catalogador y no mira la columna de
fecha. Rastrear el fichero entero buscando cuatro cifras dejaria doce veces el 2026 de Created.

Permitidos: cualquier direccion de los dominios de "correo_dominios" (por defecto rtve.es y sus subdominios),
mas las direcciones de la agenda "documentalistas" y "correo_copia". Lo demas se deja sin leer y se anota.

No cuentan como peticion las respuestas ni los reenvios de los correos que manda la propia herramienta
(las fichas de un lote, las respuestas del buzon): se reconocen por el Message-ID que citan
(<catalogar.xxx@...>) o por el asunto (RE: Fichas de archivo..., RV: Lote completado..., la MARCA),
y se marcan como leidos sin hacer nada. Un "gracias" con las fichas citadas debajo no puede volver a
encolar esos numeros; para pedir trabajo hay que escribir un correo nuevo.

Claves de config.json: correo_imap (por defecto imap.gmail.com), correo_imap_puerto (993),
correo_usuario y correo_clave (las mismas del envio), correo_dominios, correo_buzon_minutos (0 = apagado),
correo_buzon_minutos. Un correo con muchos numeros no se recorta: el servidor lo trocea en lotes de
lote_max_envios.
"""
import re
import ssl
import html
import imaplib
import email
import email.policy
from email.header import decode_header, make_header
from email.utils import parseaddr

from listas import leer_lista, leer_texto, ListaError
from correo import SELLO_ID, ASUNTOS_PROPIOS

MARCA = "[Catalogación]"    # la que se pone en el asunto de las respuestas
# Marcas que hacen ignorar un correo: la actual y las antiguas, para que un correo de antes del
# cambio de nombre siga estando protegido y no se re-catalogue al responder a el.
MARCAS = (MARCA, "[catalogar]")
# Prefijos que ponen los gestores de correo al responder o reenviar (Outlook en español: RE:, RV:)
PREFIJOS_RE = re.compile(r"^(?:\s*(?:re|rv|fw|fwd|aw|tr|sv|rif|res|resp|respuesta|reenv|reenviar|"
                         r"respuesta autom[aá]tica|automatic reply|out of office|undeliverable|no entregado|"
                         r"mail delivery failed|delivery status notification(?: \(failure\))?)\s*(?:\[\d+\])?\s*:\s*)+", re.I)
# Los correos que rechaza el buzon se marcan con esta etiqueta IMAP para no volver a bajarlos entera
# en cada vuelta (siguen sin leer, a la vista en la bandeja)
MARCA_RECHAZADO = "CatalogarRechazado"
FECHA_SUELTA_RE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
EXT_LISTA = (".csv", ".txt", ".tsv", ".xlsx")
MAX_NUMEROS = 2000          # solo contra correos absurdos: lo normal se trocea en lotes en el servidor
IMAP_TIMEOUT = 60           # segundos sin respuesta del servidor antes de rendirse (y volver al minuto siguiente)
# Donde empieza lo que no es el mensaje: la firma, el correo citado debajo de una respuesta o de un reenvio
_CORTE_RE = re.compile(r"^(?:-- ?|>.*|_{5,}|-{3,} ?Original Message ?-{3,}|-{3,} ?Mensaje original ?-{3,}|"
                       r"(?:De|From|Von)\s*:.*|El .{6,80} escribi[oó]:|On .{6,80} wrote:|Enviado desde mi .*)$", re.M | re.I)
# Años escritos en prosa ("26 de septiembre de 2026", "en 2025"): no son envios de Reuters
_ANO_RE = re.compile(r"\b(?:de|del|en|año|ano)\s+(?:19|20)\d{2}\b", re.I)


class BuzonError(Exception):
    pass


def _texto(cabecera):
    """Cabecera como texto de una linea: decodificada (=?utf-8?...?=) y sin los saltos con que los
    servidores pliegan las largas; un salto dentro del asunto rompe el correo de respuesta."""
    try:
        texto = str(make_header(decode_header(str(cabecera or ""))))
    except Exception:
        texto = str(cabecera or "")
    return " ".join(texto.split())


def _remitente(msg):
    """(nombre, direccion) del From. Con la politica moderna el nombre 'Apellido, Nombre' con acentos
    no se confunde con dos direcciones."""
    cab = msg["From"]
    direcciones = getattr(cab, "addresses", None)
    if direcciones:
        return direcciones[0].display_name or "", direcciones[0].addr_spec or ""
    return parseaddr(_texto(cab))


def es_propio(asunto, in_reply_to="", references="", msg=None):
    """True si el correo es respuesta o reenvio de uno que mando la herramienta (cita un Message-ID
    nuestro, o su asunto sin los RE:/RV: es uno de los nuestros) o si lo ha escrito una maquina
    (respuesta automatica de "fuera de la oficina", aviso de no entregado): a esos no se les contesta,
    que se contestan solos y se monta un bucle."""
    citados = f"{in_reply_to or ''} {references or ''}"
    if f"<{SELLO_ID}." in citados:
        return True
    if msg is not None:
        auto = str(msg.get("Auto-Submitted") or "").lower()
        if auto and auto != "no":
            return True
        if msg.get("X-Auto-Response-Suppress") or str(msg.get("Precedence") or "").lower() in ("bulk", "auto_reply", "junk"):
            return True
        if msg.get_content_type() == "multipart/report":
            return True
    limpio = PREFIJOS_RE.sub("", asunto or "").strip()
    return any(marca in limpio for marca in MARCAS) or limpio.startswith(ASUNTOS_PROPIOS)


def permitido(direccion, cfg):
    """True si el remitente puede encolar trabajo."""
    d = (direccion or "").strip().lower()
    if not d or "@" not in d:
        return False
    if d == (cfg.get("correo_usuario") or "").lower():
        return False                                   # nunca a uno mismo: evita bucles
    if d == (cfg.get("correo_remitente") or "").lower():
        return False
    dominio = d.split("@")[1]
    dominios = cfg.get("correo_dominios") or ["rtve.es"]
    for dom in ([dominios] if isinstance(dominios, str) else dominios):
        dom = dom.strip().lower().lstrip("@")
        if dominio == dom or dominio.endswith("." + dom):
            return True
    conocidas = {v.lower() for v in (cfg.get("documentalistas") or {}).values()}
    if cfg.get("correo_copia"):
        conocidas.add(cfg["correo_copia"].lower())
    return d in conocidas


def _sin_firma(cuerpo):
    """El cuerpo hasta la firma o el correo citado: lo de debajo (fechas, telefonos, fichas anteriores)
    no es parte de la peticion."""
    m = _CORTE_RE.search(cuerpo or "")
    return cuerpo[:m.start()] if m else (cuerpo or "")


def _trabajos(texto):
    """[(numero, fecha o "")] del asunto y del cuerpo, escritos como vengan; sin repetir y en orden.
    El mismo lector que la caja de numeros de la pagina (listas.leer_texto). Un numero con su fecha
    detras ("0624 del 25/09/2026") la conserva; los demas van sin fecha y el servidor les pone la
    comun del correo si la hay."""
    vistos, salida = set(), []
    for numero, fecha in leer_texto(_ANO_RE.sub(" ", texto))[0]:
        if numero not in vistos:
            vistos.add(numero)
            salida.append((numero, fecha or ""))
    return salida


def _de_adjunto(parte):
    """Lee un adjunto que sea lista de trabajo. Devuelve None si no lo es (una imagen, un pdf,
    la firma del remitente), y si lo es un diccionario:

        {"nombre": "Search Results.csv", "numeros": [...], "envios": [...],
         "descartes": [...], "aviso": "", "error": ""}

    Un fichero ilegible no tumba el correo: vuelve con "error" puesto para poder contestarlo.
    """
    nombre = (parte.get_filename() or "").strip()
    if not nombre.lower().endswith(EXT_LISTA):
        return None
    datos = parte.get_payload(decode=True) or b""
    if not datos:
        return None
    base = {"nombre": nombre, "numeros": [], "envios": [], "descartes": [], "aviso": "", "error": ""}
    try:
        envios, descartes, aviso = leer_lista(nombre, datos)
    except ListaError as e:
        base["error"] = str(e)
        return base
    except Exception as e:                         # fichero corrupto o rarisimo: se contesta, no se cae
        base["error"] = f"no se ha podido leer el fichero ({type(e).__name__})"
        return base
    base.update({"numeros": [e["numero"] for e in envios], "envios": envios,
                 "descartes": descartes, "aviso": aviso})
    return base


def _texto_parte(parte):
    """El texto de una parte, con su charset o, si es uno que Python no conoce, como UTF-8."""
    datos = parte.get_payload(decode=True) or b""
    try:
        return datos.decode(parte.get_content_charset() or "utf-8", "replace")
    except LookupError:
        return datos.decode("utf-8", "replace")


def _html_a_texto(codigo):
    """Texto de un cuerpo HTML: fuera estilos, scripts y etiquetas; los saltos de bloque se respetan
    para que la firma y el correo citado sigan empezando en linea propia."""
    t = re.sub(r"(?is)<(style|script|head)[^>]*>.*?</\1>", " ", codigo or "")
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|blockquote)>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    return "\n".join(" ".join(l.split()) for l in t.splitlines())


def _cuerpo(msg):
    """Devuelve (texto del correo, lista de adjuntos leidos con _de_adjunto). Se prefiere la parte de
    texto plano; si el correo solo trae HTML (moviles, Outlook "solo HTML"), se lee el HTML."""
    planos, htmls, adjuntos = [], [], []
    partes = [p for p in msg.walk() if p.get_content_maintype() != "multipart"] if msg.is_multipart() else [msg]
    for parte in partes:
        disp = (parte.get("Content-Disposition") or "").lower()
        if "attachment" in disp or parte.get_filename():
            leido = _de_adjunto(parte)
            if leido:
                adjuntos.append(leido)
        elif parte.get_content_type() == "text/plain":
            planos.append(_texto_parte(parte))
        elif parte.get_content_type() == "text/html":
            htmls.append(_texto_parte(parte))
    cuerpo = "\n".join(planos).strip() or "\n".join(_html_a_texto(h) for h in htmls)
    return cuerpo, adjuntos


def peticion_de(msg, tope=MAX_NUMEROS):
    """Lee un correo ya aceptado y devuelve la peticion, o None si no trae ni numeros ni lista:

        {de, nombre, asunto, numeros, fechas, fecha, de_mas, adjuntos, message_id}

    numeros va en orden y sin repetir; fechas es {numero: fecha} solo para los que la traen escrita al
    lado; fecha es la comun del correo (la unica DD/MM/AAAA que aparece suelta), o "" si no hay o hay
    varias distintas, que entonces solo valen las de cada numero."""
    nombre, de = _remitente(msg)
    asunto = _texto(msg["Subject"])
    cuerpo, adjuntos = _cuerpo(msg)
    texto = " ".join([asunto, _sin_firma(cuerpo)])
    trabajos = _trabajos(texto)
    numeros = [n for n, _ in trabajos]
    fechas = {n: f for n, f in trabajos if f}
    for a in adjuntos:                             # los de la lista van detras, sin repetir
        for n in a["numeros"]:
            if n not in numeros:
                numeros.append(n)
    sueltas = set(FECHA_SUELTA_RE.findall(texto)) - set(fechas.values())   # las de cada numero no son comunes
    if not (numeros or adjuntos):
        return None
    # con adjuntos se anota aunque no salga ningun numero: asi el servidor contesta explicando que
    # pasa con el fichero, en vez de dejar al remitente esperando
    return {"de": de, "nombre": nombre or de.split("@")[0], "asunto": asunto,
            "numeros": numeros[:tope], "de_mas": len(numeros) > tope,
            "fechas": fechas, "fecha": sueltas.pop() if len(sueltas) == 1 else "",
            "adjuntos": adjuntos, "message_id": str(msg.get("Message-ID", ""))}


def leer(cfg, marcar_leidos=True):
    """Devuelve (peticiones, rechazados, errores): las peticiones nuevas (ver peticion_de), las
    direcciones no permitidas (sus correos se dejan sin leer, a la vista) y los correos que no se han
    podido leer ("de X: motivo"; se marcan leidos para no tropezar con ellos en cada vuelta).
    Cada correo se trata por separado: uno raro no impide leer los demas."""
    usuario = cfg.get("correo_usuario") or ""
    clave = cfg.get("correo_clave") or ""
    if not (usuario and clave):
        raise BuzonError("Faltan correo_usuario o correo_clave en config.json")
    servidor = cfg.get("correo_imap") or "imap.gmail.com"
    puerto = int(cfg.get("correo_imap_puerto") or 993)
    tope = MAX_NUMEROS
    peticiones, rechazados, errores = [], [], []
    try:
        with imaplib.IMAP4_SSL(servidor, puerto, ssl_context=ssl.create_default_context(),
                               timeout=IMAP_TIMEOUT) as m:
            m.login(usuario, clave)
            m.select("INBOX")
            ok, datos = m.search(None, "UNSEEN", "UNKEYWORD", MARCA_RECHAZADO)
            if ok != "OK":                                     # servidor sin etiquetas propias: como antes
                ok, datos = m.search(None, "UNSEEN")
            if ok != "OK":
                return [], [], []
            for num in (datos[0] or b"").split():
                ok, bruto = m.fetch(num, "(BODY.PEEK[])")     # PEEK: no marca leido todavia
                if ok != "OK" or not bruto or not bruto[0]:
                    continue
                de = ""
                try:
                    msg = email.message_from_bytes(bruto[0][1], policy=email.policy.default)
                    nombre, de = _remitente(msg)
                    if es_propio(_texto(msg["Subject"]), msg.get("In-Reply-To"), msg.get("References"), msg):
                        m.store(num, "+FLAGS", "\\Seen")          # respuesta a un correo nuestro: fuera, sin mas
                        continue
                    if not permitido(de, cfg):
                        rechazados.append(de or "(sin remitente)")
                        try:
                            m.store(num, "+FLAGS", f"({MARCA_RECHAZADO})")   # sigue sin leer, pero no se baja mas
                        except imaplib.IMAP4.error:
                            pass
                        continue
                    p = peticion_de(msg, tope)
                    if p:
                        peticiones.append(p)
                except Exception as e:                             # un correo raro: se anota y se sigue
                    errores.append(f"de {de or '(sin remitente)'}: {type(e).__name__}: {str(e)[:120]}")
                if marcar_leidos:
                    m.store(num, "+FLAGS", "\\Seen")
    except imaplib.IMAP4.error as e:
        texto = str(e)
        if "AUTHENTICATIONFAILED" in texto.upper() or "Invalid credentials" in texto:
            raise BuzonError("El servidor rechaza el usuario o la contrasena. En Gmail hace falta una contrasena de aplicacion, "
                             "y ademas IMAP activado en Configuracion - Reenvio y correo POP/IMAP")
        raise BuzonError(f"IMAP: {texto[:200]}")
    except OSError as e:
        raise BuzonError(f"No se pudo conectar con {servidor}:{puerto} ({type(e).__name__})")
    return peticiones, rechazados, errores


def nota_adjuntos(peticion):
    """Una o dos frases sobre los ficheros que venian en el correo, para meterlas en la respuesta.
    Cadena vacia si el correo no traia ninguna lista."""
    lineas = []
    for a in peticion.get("adjuntos") or []:
        if a["error"]:
            lineas.append(f"{a['nombre']}: no se ha podido usar. {a['error']}")
            continue
        texto = f"{a['nombre']}: {len(a['numeros'])} envio(s)"
        saltadas = [d for d in a["descartes"] if "ya la tiene" in d["motivo"]]
        sin_numero = [d for d in a["descartes"] if "sin numero" in d["motivo"]]
        repetidas = [d for d in a["descartes"] if "repetido" in d["motivo"]]
        detalle = []
        if saltadas:
            detalle.append(f"{len(saltadas)} ya con catalogador")
        if repetidas:
            detalle.append(f"{len(repetidas)} repetidas")
        if sin_numero:
            detalle.append(f"{len(sin_numero)} sin numero")
        if detalle:
            texto += " (fuera: " + ", ".join(detalle) + ")"
        if a["aviso"]:
            texto += ". " + a["aviso"]
        lineas.append(texto)
    return " · ".join(lineas)


def asunto_respuesta(peticion, hechas, fallidas, formal=False):
    """Asunto de la respuesta del buzon. Lleva siempre la MARCA delante: es lo que hace que el buzon
    ignore sus propias respuestas si vuelven, tambien las formales."""
    n = len(hechas)
    cuantas = f"{n} ficha{'s' if n != 1 else ''}"
    return f"{MARCA} " + (f"Lote completado: {cuantas}" if formal else cuantas) \
        + (f", {len(fallidas)} sin salir" if fallidas else "") \
        + (f" · {peticion['asunto']}" if peticion.get("asunto") else "")
