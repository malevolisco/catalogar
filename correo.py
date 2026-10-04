# -*- coding: utf-8 -*-
"""
correo.py - Reparto de fichas entre documentalistas y envio por correo.

Reparto: texto con una linea por persona, en cualquiera de estas formas
    andrea: 10                     -> diez fichas, las primeras que queden libres
    pepe: trump, eeuu, aranceles   -> las fichas cuyo NAME, COMMENT o titular contengan alguna de esas palabras
    resto: yo                      -> las que no case nadie (yo = la direccion de copia del config)
Primero se aplican las lineas por tema, en el orden escrito; despues las de cantidad; lo que quede va a "resto".
Los nombres se resuelven con la agenda "documentalistas" de config.json; tambien vale una direccion completa.

Correo: SMTP con STARTTLS (Gmail: smtp.gmail.com 587 con contrasena de aplicacion; Outlook.com: smtp-mail.outlook.com 587).
Claves de config.json: correo_servidor, correo_puerto, correo_usuario, correo_clave, correo_remitente (opcional),
correo_copia (tu direccion: es "yo" en el reparto), documentalistas.

Tu direccion (correo_copia) solo recibe correo cuando lo pides: con "yo" en el reparto, con Enviar
seleccion a "yo" o escribiendo tu al buzon. Nada de resumenes automaticos: lo que queda sin repartir
se ve en la pagina y en la consola. Los AVISOS del validador van solo a ti; a los demas les llegan las
fichas limpias, que un aviso sin contexto confunde mas de lo que ayuda.

Tono de cada correo (es_formal):
  - fuera de la agenda (una direccion suelta, alguien de rtve.es que escribe al buzon): formal, siempre;
  - en la agenda: informal, salvo quien tenga "formal": true en su bloque de la agenda (o figure en
    "correo_formal") o si se marca la casilla Formal de la pagina, que lo fuerza para todos los de ese envio.
Formal es: sin saludo ni despedida, asunto y cabecera "Lote completado", sin el pie de "generado
automaticamente" y sin AVISOS, tampoco a ti.

Todo lo que sale de aqui lleva un Message-ID propio (<catalogar.xxx@dominio>) y un asunto de los de
ASUNTOS_PROPIOS: con eso el buzon reconoce las respuestas y los reenvios de estos correos y no los
toma por peticiones de trabajo.
"""
import re
import uuid
import smtplib
import unicodedata
from email.message import EmailMessage
from email.utils import formataddr
from datetime import datetime

CAMPOS = ("ENVIO", "NAME", "COMMENT", "RESTRICCIONES")
SELLO_ID = "catalogar"      # el Message-ID de los correos enviados empieza por <catalogar.
# Como empiezan los asuntos de los correos de fichas (los del buzon llevan ademas su MARCA delante)
ASUNTOS_PROPIOS = ("Fichas de archivo:", "Lote completado:")

# Saludos y despedidas de los correos de fichas: se elige uno al azar por correo.
# Editables a gusto; se apagan con "correo_saludos": false en config.json.
SALUDOS = [
    "{nombre}, ahí van. He visto cosas que no creerías; casi todas eran declaraciones de Trump.",
    "{nombre}, van las fichas. Las mejores fichas de la historia, tremendas, todo el mundo lo dice.",
    "{nombre}, van las fichas. Lo he catalogado todo menos la reunión bilateral, que sigue sin comunicado conjunto.",
    "Buenas, {nombre}. Lote hecho, sin sanciones ni aranceles.",
    "{nombre}, van las de hoy: tres ruedas de prensa, dos declaraciones y un Kremlin que no confirma ni desmiente.",
    "{nombre}, aquí tienes el lote. Ha costado menos que un alto el fuego y dura más.",
    "Buenas, {nombre}. Van las fichas. Fuentes cercanas al bot aseguran que están bien; fuentes diplomáticas piden cautela.",
    "{nombre}, lote hecho. Si alguna no cuadra, no hace falta llamar a consultas al embajador: me lo dices y la repito.",
    "{nombre}, ahí van. Las he leído dos veces, que es más de lo que puede decir el Consejo de Seguridad de sus resoluciones.",
    "{nombre}, van las fichas, cada una con su país delante. En esta oficina las fronteras sí se respetan.",
    "{nombre}, hecho. Hoy ha habido más helicóptero que declaraciones, y se nota en los COMMENT.",
    "{nombre}, van las fichas. La ONU las calificaría de urgentes y pediría contención a todas las partes.",
    "Buenas, {nombre}. Lote entregado. Bruselas lo estudiará con atención y responderá en las próximas semanas.",
    "{nombre}, lote listo. Ni Macron ha hecho hoy tantas declaraciones como fichas te mando.",
    "{nombre}, van las fichas. Han pasado más controles que un periodista en Pyongyang.",
    "{nombre}, aquí está el lote. Lo he negociado con Reuters y con AP y, por una vez, hay acuerdo.",
    "{nombre}, hecho. Todo catalogado, incluido lo desmentido a mediodía y confirmado por la tarde.",
    "{nombre}, fichas listas. Más rápidas que un primer ministro británico.",
    "{nombre}, van las fichas. He pasado la motosierra de Milei por los COMMENT largos.",
    "{nombre}, lote hecho, con menos idas y vueltas que el Brexit.",
    "{nombre}, ahí van. Hubo cumbre, foto de familia y ningún acuerdo, pero las fichas sí están.",
    "{nombre}, van las fichas. Ni el G7 ni el G20: esto es el G1, y lo he hecho yo solo.",
    "{nombre}, aquí están. Léelas con calma, que no hace falta una mesa tan larga como la del Kremlin.",
    "{nombre}, van las fichas. Ni la Casa Blanca firma tantas órdenes ejecutivas en un día.",
    "{nombre}, lote hecho. Hasta el cónclave tardó más en ponerse de acuerdo.",
    "{nombre}, ahí van. Si pregunta el Kremlin, estas fichas nunca han existido.",
    "{nombre}, van las fichas. Aranceles del 200 % a las fichas extranjeras; estas son de la casa.",
    "{nombre}, lote listo. En Bruselas lo llamarían acuerdo de mínimos; aquí lo llamamos lunes.",
    "{nombre}, ahí van. Han cruzado más fronteras que un enviado especial de Washington.",
]
DESPEDIDAS = [
    "I am awash in dilemmas and deficiencies. Let me find my footing, and I will attend to your requests.",
    "All those moments will be lost in time, like tears in rain. Not these files: they are archived.",
    "I'm sorry, Dave. I'm afraid there are no more files.",
    "I'll be back.",
    "Hasta la vista, baby.",
    "Sayonara, baby.",
    "Come with me if you want more files.",
    "May the Force be with you.",
    "Live long and prosper.",
    "So long, and thanks for all the fish.",
    "Winter is coming. So is tomorrow's batch.",
    "Valar dohaeris: all bots must serve.",
    "Not today.",
    "Dracarys.",
    "The night is dark and full of restrictions.",
    "You know nothing, Jon Snow.",
    "May the odds be ever in your favor.",
    "This is the way.",
    "I am inevitable. So is tomorrow's batch.",
    "To infinity and beyond.",
    "Houston, we have no more files.",
    "Roads? Where we're going, we don't need roads.",
    "The name's Catalogación. Bot Catalogación.",
    "Shaken, not stirred.",
    "Here's looking at you, kid.",
    "After all, tomorrow is another batch.",
    "Just keep cataloguing.",
    "Why so serious? It's only metadata.",
    "Game over, man. Game over.",
    "As you wish.",
    "I am the bot who knocks.",
    "This is fine. Everything is fine.",
    "The truth is out there. The files are in your inbox.",
    "End of transmission.",
    "See you at the moviola.",
]


def adorno_para(cfg, nombre, formal=False):
    """(saludo, despedida) al azar, con el nombre del destinatario. Nada si el correo es formal
    o si "correo_saludos" esta a false en config.json."""
    if formal or not cfg.get("correo_saludos", True):
        return "", ""
    import random
    n = (nombre or "").strip() or "colega"
    return random.choice(SALUDOS).format(nombre=n[0].upper() + n[1:]), random.choice(DESPEDIDAS)


class CorreoError(Exception):
    pass


# ====================================================================== reparto
def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).upper()


DIRECCION_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def es_mio(cfg, destino):
    """True si ese destino (nombre de la agenda, 'yo' o direccion) es la direccion de copia: lo que va
    para uno mismo no se manda, se queda en la pagina."""
    resuelto = resolver_destino(destino, cfg.get("documentalistas") or {}, cfg.get("correo_copia") or "")
    return bool(resuelto) and es_para_mi(cfg, resuelto[1])


def resolver_destino(nombre, agenda, copia):
    """Nombre de la agenda, 'yo', o direccion completa -> (etiqueta, direccion). None si no se sabe."""
    n = (nombre or "").strip()
    if not n:
        return None
    if _norm(n) in ("YO", "MI", "COPIA"):
        return ("yo", copia) if copia else None
    if "@" in n:
        return (n.split("@")[0], n) if DIRECCION_RE.match(n) else None
    for clave, direccion in (agenda or {}).items():
        if _norm(clave) == _norm(n):
            return (clave, direccion)
    return None


def error_destino(destino):
    """Por que no vale un destinatario, dicho de forma que se sepa que hacer."""
    if "@" in (destino or ""):
        return f"'{destino}' no es una direccion de correo valida"
    return f"no conozco a '{destino}' (anadelo a documentalistas en config.json o pon su direccion completa)"


def _claves(*claves):
    """Formas por las que se puede nombrar a alguien en config.json: nombre de la agenda, direccion
    completa o lo que va antes de la arroba; sin tildes ni mayusculas."""
    salida = set()
    for c in claves:
        c = (c or "").strip()
        if c:
            salida.add(_norm(c))
            if "@" in c:
                salida.add(_norm(c.split("@")[0]))
    return salida


def registrado(cfg, direccion):
    """True si la direccion esta en la agenda (documentalistas) o es la tuya (correo_copia)."""
    d = (direccion or "").strip().lower()
    conocidas = {v.strip().lower() for v in (cfg.get("documentalistas") or {}).values()}
    return bool(d) and (d in conocidas or d == (cfg.get("correo_copia") or "").strip().lower())


def es_formal(cfg, etiqueta, direccion, forzar=False):
    """Tono del correo para este destinatario. Formal si se fuerza con la casilla de la pagina, si la
    direccion no esta en la agenda, o si la persona figura en "correo_formal" (por nombre de la agenda,
    direccion o parte anterior a la arroba). Informal solo para la agenda, que es lo predeterminado."""
    if forzar or not registrado(cfg, direccion):
        return True
    formales = {_norm(x) for x in (cfg.get("correo_formal") or [])}
    return bool(formales & _claves(etiqueta, direccion))


def nombre_saludo(cfg, etiqueta, direccion):
    """El nombre con que se saluda: el de la agenda si la direccion esta en ella (tambien para "yo"
    y para quien escribe al buzon); si no, la etiqueta; y si tampoco, lo que va antes del punto de
    la direccion ("andrea.garcia@rtve.es" -> "andrea")."""
    d = (direccion or "").strip().lower()
    for clave, dir_agenda in (cfg.get("documentalistas") or {}).items():
        if d and dir_agenda.strip().lower() == d:
            return clave
    e = (etiqueta or "").strip()
    if e and _norm(e) not in ("YO", "MI", "COPIA") and "@" not in e:
        return e
    return re.split(r"[._\-+]", d.split("@")[0])[0] if d else ""


def es_para_mi(cfg, direccion):
    """True si la direccion es la tuya (correo_copia): la unica que recibe los AVISOS."""
    copia = (cfg.get("correo_copia") or "").strip().lower()
    return bool(copia) and (direccion or "").strip().lower() == copia


def cabecera_fichas(n, con_avisos, cuando=None, formal=False):
    """Frase que abre el correo de fichas. Los AVISOS solo se mencionan a quien los recibe.
    En formal, impersonal y sin instrucciones: es para quien lee, no para quien pega."""
    cuando = cuando or datetime.now().strftime("%d/%m/%Y %H:%M")
    s = "" if n == 1 else "s"
    if formal:
        return f"Lote completado: {n} ficha{s} catalogada{s} el {cuando[:10]}."
    texto = f"{n} ficha{s} catalogada{s} el {cuando}. Cada una lleva sus cuatro lineas listas para pegar"
    return texto + ("; si tiene AVISOS, revisala con mas cuidado." if con_avisos else ".")


def asunto_fichas(n, detalle="", formal=False):
    """Asunto de los correos de fichas. En formal, "Lote completado"."""
    s = "" if n == 1 else "s"
    base = f"{ASUNTOS_PROPIOS[1]} {n} ficha{s}" if formal else f"{ASUNTOS_PROPIOS[0]} {n} envio{s}"
    return base + (f" ({detalle})" if detalle else "")


def presentacion_para(cfg, *claves):
    """Modo de escritura del correo para un destinatario concreto.

    Manda la excepcion de "correo_presentacion_personas" (se busca por nombre de la agenda, por
    direccion completa o por la parte anterior a la arroba, sin distinguir tildes ni mayusculas);
    si esa persona no figura, se usa el general de "correo_presentacion".

        "correo_presentacion": "mayusculas",
        "correo_presentacion_personas": { "javier": "normalizado" }

    El dia que minusculas pase a ser lo normal, basta con poner "correo_presentacion":
    "normalizado" y vaciar la lista de excepciones."""
    por_persona = cfg.get("correo_presentacion_personas") or {}
    if por_persona:
        buscar = _claves(*claves)
        for clave, modo in por_persona.items():
            if _norm(clave) in buscar:
                return modo or cfg.get("correo_presentacion", "mayusculas")
    return cfg.get("correo_presentacion", "mayusculas")


def hay_excepcion_normalizada(cfg):
    """True si alguna persona tiene excepcion a escritura normal (para saber si hace falta generarla)."""
    return any((m or "") == "normalizado" for m in (cfg.get("correo_presentacion_personas") or {}).values())


def analizar_reparto(texto, agenda, copia):
    """Convierte el texto de reparto en reglas. Devuelve (reglas, errores)."""
    reglas, errores = [], []
    for linea in (texto or "").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        if ":" not in linea:
            errores.append(f"'{linea}': falta el signo ':' (nombre: cantidad o palabras)")
            continue
        nombre, valor = linea.split(":", 1)
        nombre, valor = nombre.strip(), valor.strip()
        if _norm(nombre) == "RESTO":
            destino = resolver_destino(valor, agenda, copia)
            if not destino:
                errores.append(f"'{linea}': {error_destino(valor)}")
                continue
            reglas.append({"tipo": "resto", "destino": destino})
            continue
        destino = resolver_destino(nombre, agenda, copia)
        if not destino:
            errores.append(f"'{linea}': {error_destino(nombre)}")
            continue
        if re.fullmatch(r"\d+", valor):
            reglas.append({"tipo": "cantidad", "destino": destino, "n": int(valor)})
        elif valor:
            palabras = [p.strip() for p in re.split(r"[,;]", valor) if p.strip()]
            reglas.append({"tipo": "tema", "destino": destino, "palabras": palabras})
        else:
            reglas.append({"tipo": "cantidad", "destino": destino, "n": 10 ** 6})   # sin valor: todas las que queden
    return reglas, errores


def repartir(fichas, reglas):
    """Asigna fichas a destinatarios. Devuelve (asignacion, sin_asignar).
    asignacion: lista de (etiqueta, direccion, [fichas]) en el orden de las reglas."""
    libres = [f for f in fichas if f.get("estado") == "hecha"]
    asignacion = {}
    orden = []

    def dar(destino, ficha):
        clave = destino[1]
        if clave not in asignacion:
            asignacion[clave] = (destino[0], destino[1], [])
            orden.append(clave)
        asignacion[clave][2].append(ficha)

    for r in [r for r in reglas if r["tipo"] == "tema"]:
        claves = [_norm(p) for p in r["palabras"]]
        for f in list(libres):
            pajar = _norm(" ".join([f.get("NAME", ""), f.get("COMMENT", ""), f.get("headline", ""), f.get("ENVIO", "")]))
            if any(k in pajar for k in claves):
                dar(r["destino"], f)
                libres.remove(f)
    for r in [r for r in reglas if r["tipo"] == "cantidad"]:
        for f in libres[:r["n"]]:
            dar(r["destino"], f)
        libres = libres[r["n"]:]
    resto = next((r for r in reglas if r["tipo"] == "resto"), None)
    if resto and libres:
        for f in libres:
            dar(resto["destino"], f)
        libres = []
    return [asignacion[k] for k in orden], libres


# ====================================================================== correo
def _ver(f, c, modo):
    """Texto de un campo segun el modo: 'normalizado' usa la version en escritura normal si la ficha la tiene."""
    if c != "ENVIO" and modo == "normalizado" and f.get("normal") and f["normal"].get(c):
        return f["normal"][c]
    return f.get(c, "")


def cuerpo_fichas(fichas, cabecera="", modo="mayusculas", saludo="", despedida="", con_avisos=False):
    """Texto plano del correo. Los AVISOS del validador solo van si con_avisos (es decir, para ti)."""
    partes = ([saludo] if saludo else []) + ([cabecera.strip()] if cabecera else [])
    for f in fichas:
        lineas = [f"{c}: {_ver(f, c, modo)}" for c in CAMPOS]
        if f.get("alerta"):
            lineas.append(f["alerta"])                 # el script no cabe: lo ve todo el mundo
        if con_avisos and f.get("avisos"):
            lineas.append("AVISOS: " + " | ".join(f["avisos"]))
        partes.append("\n".join(lineas))
    if despedida:
        partes.append(despedida)
    return "\n\n".join(partes) + "\n"


FUENTE = "'Segoe UI',Arial,Helvetica,sans-serif"
MONO = "Consolas,Menlo,'Courier New',monospace"


def cuerpo_fichas_html(fichas, cabecera="", modo="mayusculas", saludo="", despedida="", con_avisos=False,
                       formal=False):
    """Version HTML del correo: una tarjeta por ficha, campos en bloques monoespaciados para
    que el copiar y pegar salga limpio. Todo con estilos en linea y tablas, que es lo unico
    que Outlook respeta. La version de texto plano viaja siempre al lado en el mismo correo."""
    import html as _html

    def e(txt):
        return _html.escape(str(txt or ""), quote=False)

    def bloque(etiqueta, valor, fondo="#f7f8fa", borde="#e7ebf0", color="#1c232b"):
        return (f'<tr><td style="padding:10px 16px 0 16px;">'
                f'<div style="font-family:{FUENTE};font-size:11px;font-weight:700;'
                f'letter-spacing:.8px;color:#8a94a0;">{etiqueta}</div>'
                f'<div style="font-family:{MONO};font-size:13px;line-height:1.5;color:{color};'
                f'background:{fondo};border:1px solid {borde};border-radius:6px;'
                f'padding:8px 10px;margin-top:4px;word-break:break-word;">{e(valor)}</div>'
                f'</td></tr>')

    tarjetas = []
    for f in fichas:
        envio = f.get("ENVIO", "")
        numero = e((envio.split("\u00b7")[0] if "\u00b7" in envio else envio).strip()[:40])
        filas = [
            f'<tr><td style="background:#eef3f9;border-bottom:1px solid #dfe4ea;'
            f'padding:10px 16px;font-family:{FUENTE};font-size:14px;font-weight:700;'
            f'color:#1d3a5f;border-radius:8px 8px 0 0;">Env\u00edo {numero}</td></tr>',
            bloque("ENVIO", envio),
            bloque("NAME", _ver(f, "NAME", modo)),
            bloque("COMMENT", _ver(f, "COMMENT", modo)),
        ]
        restr = _ver(f, "RESTRICCIONES", modo)
        if (restr or "").strip().upper().startswith("SIN AVISO"):
            filas.append(bloque("RESTRICCIONES", restr, fondo="#f2f8f2", borde="#d7e6d7", color="#2d5b2d"))
        else:
            filas.append(bloque("RESTRICCIONES", restr, fondo="#fdf3f2", borde="#ecccc8", color="#8a3b32"))
        if f.get("alerta"):
            filas.append(bloque("ALERTA", f["alerta"], fondo="#fdf3f2", borde="#ecccc8", color="#8a3b32"))
        if con_avisos and f.get("avisos"):
            filas.append(bloque("AVISOS \u00b7 revisar con m\u00e1s cuidado", " | ".join(f["avisos"]),
                                fondo="#fff8e6", borde="#eed9a0", color="#7a5c14"))
        filas.append('<tr><td style="padding-bottom:14px;"></td></tr>')
        tarjetas.append('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                        'style="border:1px solid #dfe4ea;border-radius:8px;background:#ffffff;'
                        'margin:0 0 14px 0;">' + "".join(filas) + "</table>")

    n = len(fichas)
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    intro = (f'<p style="font-family:{FUENTE};font-size:13px;line-height:1.55;color:#5a6572;'
             f'margin:0 0 16px 0;">{e(cabecera)}</p>') if cabecera else ""
    sal = (f'<p style="font-family:{FUENTE};font-size:15px;font-weight:600;color:#1c232b;'
           f'margin:0 0 8px 0;">{e(saludo)}</p>') if saludo else ""
    desp = (f'<p style="font-family:{FUENTE};font-size:13px;font-style:italic;color:#5a6572;'
            f'margin:6px 0 4px 0;">{e(despedida)}</p>') if despedida else ""
    return _marco_html(f"{n} ficha{'s' if n != 1 else ''} \u00b7 {fecha}", sal + intro + "".join(tarjetas) + desp, formal)


def _marco_html(derecha, contenido, formal=False):
    """El marco comun de los correos: cabecera azul con el titulo y un texto a la derecha, el
    contenido en una tarjeta blanca y, en informal, el pie de "generado automaticamente"."""
    titulo = "Lote completado" if formal else "Catalogación"
    pie = ("" if formal else
           f'<p style="font-family:{FUENTE};font-size:11px;color:#9aa4af;margin:4px 0 10px 0;">'
           'Generado automáticamente por Catalogación. Revisa cada ficha antes de archivarla.</p>')
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#eef1f4;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef1f4;">
<tr><td align="center" style="padding:24px 10px;">
<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;max-width:660px;">
  <tr><td style="background:#1d3a5f;border-radius:10px 10px 0 0;padding:16px 22px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
      <td style="font-family:{FUENTE};font-size:20px;font-weight:700;letter-spacing:1px;color:#ffffff;">{titulo}</td>
      <td align="right" style="font-family:{FUENTE};font-size:13px;color:#b9c6d8;">{derecha}</td>
    </tr></table>
  </td></tr>
  <tr><td style="background:#ffffff;border:1px solid #dfe4ea;border-top:0;border-radius:0 0 10px 10px;padding:18px 18px 8px 18px;">
    {contenido}
    {pie}
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


def cuerpo_aviso_html(parrafos, saludo="", despedida="", formal=False):
    """Un correo de aviso sin fichas (por ejemplo, una lista de la que no ha salido ningun numero),
    con el mismo aspecto que los de fichas. parrafos: lista de textos; el primero va destacado."""
    import html as _html
    e = lambda s: _html.escape(str(s or ""), quote=False)
    partes = []
    if saludo:
        partes.append(f'<p style="font-family:{FUENTE};font-size:15px;font-weight:600;color:#1c232b;margin:0 0 8px 0;">{e(saludo)}</p>')
    for i, p in enumerate(parrafos):
        if not p:
            continue
        if i == 0:
            partes.append(f'<p style="font-family:{FUENTE};font-size:14px;line-height:1.55;color:#8a3b32;'
                          f'background:#fdf3f2;border:1px solid #ecccc8;border-radius:6px;padding:10px 12px;margin:0 0 14px 0;">{e(p)}</p>')
        else:
            partes.append(f'<p style="font-family:{FUENTE};font-size:13px;line-height:1.55;color:#5a6572;margin:0 0 12px 0;">{e(p)}</p>')
    if despedida:
        partes.append(f'<p style="font-family:{FUENTE};font-size:13px;font-style:italic;color:#5a6572;margin:6px 0 4px 0;">{e(despedida)}</p>')
    return _marco_html("sin envíos \u00b7 " + datetime.now().strftime("%d/%m/%Y %H:%M"), "".join(partes), formal)


def enviar(cfg, destinatario, asunto, cuerpo, html=None):
    """Manda un correo de texto plano y, si se le pasa, con su version HTML al lado. Lanza CorreoError si falla."""
    servidor = cfg.get("correo_servidor") or ""
    usuario = cfg.get("correo_usuario") or ""
    clave = cfg.get("correo_clave") or ""
    if not (servidor and usuario and clave):
        raise CorreoError("Faltan correo_servidor, correo_usuario o correo_clave en config.json")
    puerto = int(cfg.get("correo_puerto") or 587)
    remitente = cfg.get("correo_remitente") or usuario
    msg = EmailMessage()
    msg["From"] = formataddr(("Catalogación", remitente))
    msg["To"] = destinatario
    msg["Subject"] = " ".join((asunto or "").split())   # una linea: un salto en el asunto lo rechaza el EmailMessage
    # Message-ID reconocible: quien responda o reenvie este correo lo cita en In-Reply-To o References,
    # y el buzon lo deja pasar sin catalogar nada (buzon.es_propio)
    msg["Message-ID"] = f"<{SELLO_ID}.{uuid.uuid4().hex}@{remitente.split('@')[-1] or 'catalogar'}>"
    msg["Auto-Submitted"] = "auto-generated"     # que los "fuera de la oficina" de Outlook no contesten a la herramienta
    msg.set_content(cuerpo, charset="utf-8")
    if html:
        msg.add_alternative(html, subtype="html")   # el gestor de correo ensena esta; el texto plano viaja debajo
    try:
        with smtplib.SMTP(servidor, puerto, timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(usuario, clave)
            s.send_message(msg)
    except ValueError as e:                          # una cabecera que el correo no admite
        raise CorreoError(f"No se pudo componer el correo ({str(e)[:160]})")
    except smtplib.SMTPAuthenticationError:
        raise CorreoError("El servidor de correo rechaza el usuario o la contrasena. En Gmail hace falta una contrasena de aplicacion, no la normal")
    except (smtplib.SMTPException, OSError) as e:
        raise CorreoError(f"No se pudo enviar ({type(e).__name__}: {str(e)[:160]})")


def enviar_fichas(cfg, fichas, etiqueta, direccion, asunto, forzar_formal=False, cuando=None,
                  cabecera=None, lineas_extra=()):
    """Compone y manda un correo de fichas a una persona: decide el tono (es_formal), la presentacion,
    el saludo y los AVISOS (solo a ti y nunca en formal), y manda texto plano mas HTML. Es el unico
    sitio que monta correos de fichas: lo usan la seleccion, el reparto y la respuesta al buzon.
    cabecera=None pone la de siempre; lineas_extra van al final del cuerpo (notas, lo que no salio).
    Devuelve el tono usado (True = formal)."""
    formal = es_formal(cfg, etiqueta, direccion, forzar=forzar_formal)
    con_avisos = es_para_mi(cfg, direccion) and not formal
    if cabecera is None:
        cabecera = cabecera_fichas(len(fichas), con_avisos, cuando, formal)
    modo = presentacion_para(cfg, etiqueta, direccion)
    saludo, despedida = adorno_para(cfg, nombre_saludo(cfg, etiqueta, direccion), formal)
    lineas_extra = [l for l in lineas_extra if l]
    cuerpo = cuerpo_fichas(fichas, cabecera, modo, saludo, despedida, con_avisos=con_avisos)
    if lineas_extra:
        cuerpo += "\n" + "\n".join(lineas_extra) + "\n"
    html = None
    if cfg.get("correo_html", True):
        cabecera_html = cabecera + ((" " + " · ".join(lineas_extra)) if lineas_extra else "")
        html = cuerpo_fichas_html(fichas, cabecera_html, modo, saludo, despedida, con_avisos=con_avisos, formal=formal)
    enviar(cfg, direccion, asunto, cuerpo, html=html)
    return formal


def enviar_seleccion(cfg, fichas, destino, nota="", formal=False):
    """Manda una seleccion de fichas (de uno o varios lotes) a una persona de la agenda, a 'yo' o a una direccion.
    formal=True lo fuerza; si no, decide es_formal. Devuelve (etiqueta, direccion, formal)."""
    resuelto = resolver_destino(destino, cfg.get("documentalistas") or {}, cfg.get("correo_copia") or "")
    if not resuelto:
        motivo = error_destino(destino)
        raise CorreoError(motivo[:1].upper() + motivo[1:])
    etiqueta, direccion = resuelto
    hechas = [f for f in fichas if f.get("estado") == "hecha"]
    if not hechas:
        raise CorreoError("Ninguna de las fichas seleccionadas esta hecha")
    formal = es_formal(cfg, etiqueta, direccion, forzar=formal)
    formal = enviar_fichas(cfg, hechas, etiqueta, direccion, asunto_fichas(len(hechas), nota, formal), forzar_formal=formal)
    return etiqueta, direccion, formal


def enviar_lote(cfg, lote, reparto_texto, formal=False):
    """Reparte las fichas hechas de un lote y manda un correo a cada destinatario del reparto, y a nadie mas:
    tu direccion solo recibe si el reparto dice "yo". formal=True lo fuerza para todos; si no, cada
    destinatario lleva su tono (es_formal). Devuelve (envios, sin_repartir):
    envios = [{a, etiqueta, fichas, ok, error, formal}] y sin_repartir = etiquetas de las que no ha cogido nadie."""
    agenda = cfg.get("documentalistas") or {}
    copia = cfg.get("correo_copia") or ""
    reglas, errores = analizar_reparto(reparto_texto, agenda, copia)
    if errores:
        raise CorreoError("Reparto mal escrito: " + " · ".join(errores))
    if not reglas:
        raise CorreoError("El reparto esta vacio")
    # las fichas que ya tienen destinatario propio (columna Catalogador de la lista) no entran en el reparto
    asignacion, sin_asignar = repartir([f for f in lote["fichas"] if not f.get("para")], reglas)
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    envios = []
    for etiqueta, direccion, fichas in asignacion:
        tono = es_formal(cfg, etiqueta, direccion, forzar=formal)
        envio = {"a": direccion, "etiqueta": etiqueta, "fichas": [f.get("etiqueta") for f in fichas],
                 "ok": False, "error": "", "formal": tono}
        try:
            enviar_fichas(cfg, fichas, etiqueta, direccion, asunto_fichas(len(fichas), lote.get("nombre", ""), tono),
                          forzar_formal=tono, cuando=fecha)
            envio["ok"] = True
        except CorreoError as e:
            envio["error"] = str(e)
        envios.append(envio)
    return envios, [f.get("etiqueta", "") for f in sin_asignar]
