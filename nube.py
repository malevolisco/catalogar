# -*- coding: utf-8 -*-
"""
nube.py - Catalogator por correo sin ningun equipo encendido.

Lo arranca el timbre (nube_timbre.gs) o, de respaldo, GitHub cada pocas horas (.github/workflows/nube.yml).
Mira la bandeja de la cuenta de Catalogator y, por cada correo nuevo mandado a la direccion con +csv
(catalogator+csv@gmail.com) con un CSV o un Excel de MediaCentral adjunto, redacta cada fila con su Cont. o
Comments como texto de la agencia y contesta igual que contesta Catalogator (correo.enviar_fichas).

Respaldo de la Raspberry: un correo a la direccion normal que sigue sin leer a los 15 minutos (la Raspberry
no esta o no funciona) se contesta igual, si trae un CSV con texto de agencia. Antes de redactar se marca
leido, para que la Raspberry no lo conteste otra vez si vuelve. Si no hay nada que la nube sepa hacer (solo
numeros, sin texto), se deja sin leer para la Raspberry.

Redacta con el mismo codigo que la Raspberry (redactor.py, situaciones.py) y con lo que la Raspberry ha
aprendido: lo lee del ultimo correo "Cerebro para la nube" (cerebro.py). Sin ese correo redacta con las
reglas y las aprobadas que vienen con el programa.

Lo que no hace: entrar en las agencias. El texto tiene que venir en la columna Cont. (manda) o Comments.

Lo que necesita, como secretos del repositorio en GitHub (nunca en el codigo):
    CATALOGATOR_CORREO        la direccion de la cuenta (la misma de correo_usuario)
    CATALOGATOR_CLAVE         su contraseña de aplicacion de Gmail
    CATALOGATOR_CLAUDE_TOKEN  el token de un año de la suscripcion de Claude (claude setup-token), el mismo
                              de Admin: redacta Claude Code con la suscripcion, sin pagar por uso
    (en su lugar, si se prefiere pagar por uso: CATALOGATOR_API_KEY de la API de Claude o
     CATALOGATOR_OPENAI_KEY de ChatGPT)

El registro de GitHub es publico: aqui solo se escriben cuentas (correos, filas, fichas), nunca textos,
nombres ni direcciones. El detalle va en el correo de respuesta.
"""
import contextlib
import email
import email.policy
import imaplib
import io
import os
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import cerebro                                    # noqa: E402
from buzon import (es_propio, permitido, _texto, _remitente, asunto_respuesta, es_para_nube,   # noqa: E402
                   MARCA, MARCA_RECHAZADO)
from listas import (_filas_csv, _filas_xlsx, _sin_acentos, NUMERO_INICIAL_RE, CAB_CREADO, leer_creado,   # noqa: E402
                    agencia_de, texto_agencia, ListaError, CAB_CONT)

HECHO = "CatalogatorNubeHecho"      # marca IMAP de los correos ya contestados desde la nube
EXT = (".csv", ".tsv", ".txt", ".xlsx")
CAB_NOMBRE = ("name", "nombre", "titulo", "title")
CAB_COMENTARIO = ("comments", "comment", "comentario", "comentarios", "texto", "script", "shotlist", "story")
HILOS = 3
MAX_FILAS = 300


# ---------------------------------------------------------------------- filas del fichero
def _columna(normal, nombres):
    exacta = next((i for i, c in enumerate(normal) if c in nombres), None)
    if exacta is not None:
        return exacta
    return next((i for i, c in enumerate(normal) if any(c.startswith(n) for n in nombres)), None)


def fichas_de_tabla(filas):
    """Las filas de la tabla como fichas para redactar: (fichas, descartes). Cada ficha lleva numero, fecha,
    slug y headline sacados de Name (4359 USA-MAMDANI LULA-PRESSER: MAMDANI PRAISES ...) y como texto de la
    agencia lo que traiga Cont. o, si no, Comments. descartes: etiquetas de las filas sin texto suficiente."""
    if not filas:
        return [], []
    normal = [_sin_acentos(c or "") for c in filas[0]]
    i_nombre, i_texto = _columna(normal, CAB_NOMBRE), _columna(normal, CAB_COMENTARIO)
    i_creado = _columna(normal, CAB_CREADO)
    i_cont = next((i for i, c in enumerate(normal) if c in CAB_CONT), None)       # exacta: «Cont.» manda
    if i_nombre is None or (i_texto is None and i_cont is None):
        raise ListaError("El fichero no tiene las columnas Name y Comments")
    fichas, descartes = [], []
    for fila in filas[1:MAX_FILAS + 1]:
        celda = lambda i: (fila[i] if i is not None and i < len(fila) else "") or ""
        nombre = celda(i_nombre).strip()
        texto = texto_agencia(celda(i_cont)) or celda(i_texto).strip()
        if not nombre and not texto:
            continue
        m = NUMERO_INICIAL_RE.match(nombre)
        numero = m.group(1) if m else ""
        if numero and len(numero) < 4:
            numero = numero.zfill(4)
        resto = nombre[m.end():].strip(" -·") if m else nombre
        slug, _, headline = resto.partition(": ")
        if not headline:
            slug, headline = "", resto
        etiqueta = numero or nombre[:40] or f"fila {len(fichas) + len(descartes) + 2}"
        if not texto_agencia(texto):
            descartes.append(etiqueta)
            continue
        creado = leer_creado(celda(i_creado))
        fichas.append({
            "etiqueta": etiqueta, "numero": numero, "agencia": agencia_de(numero).upper() if numero else "",
            "fecha": creado.split(" ")[0] if creado else "", "slug": slug.strip(), "headline": headline.strip(),
            "texto": texto,
        })
    return fichas, descartes


def adjuntos_de(msg):
    """[(nombre, bytes)] de los ficheros de tabla del correo."""
    salida = []
    for parte in msg.walk():
        nombre = parte.get_filename() or ""
        if nombre.lower().endswith(EXT):
            salida.append((nombre, parte.get_payload(decode=True) or b""))
    return salida


def fichas_del_correo(msg):
    fichas, descartes, problemas = [], [], []
    for nombre, datos in adjuntos_de(msg):
        try:
            filas = _filas_xlsx(datos) if nombre.lower().endswith(".xlsx") else _filas_csv(datos)
            f, d = fichas_de_tabla(filas)
            fichas += f
            descartes += d
        except ListaError as e:
            problemas.append(f"{nombre}: {e}")
    return fichas, descartes, problemas


# ---------------------------------------------------------------------- redaccion
def redactar_ficha(ficha):
    """La ficha redactada con el mismo camino que la Raspberry (detectar situaciones y redactar)."""
    from situaciones import detectar
    from redactor import redactar
    try:
        detectar(ficha)
    except Exception as e:
        ficha.setdefault("avisos", []).append(f"No se pudieron detectar las situaciones ({type(e).__name__})")
    campos, avisos, _ = redactar(ficha)
    ficha.update({c: campos.get(c, "") for c in ("ENVIO", "NAME", "COMMENT", "RESTRICCIONES")})
    ficha["normal"] = campos.get("NORMAL")
    ficha["avisos"] = list(ficha.get("avisos") or []) + list(avisos)
    return ficha


def redactar_todas(fichas):
    """(hechas, fallidas) en el orden del fichero. Tres a la vez, como las colas de la Raspberry."""
    def una(f):
        for intento in range(2):
            try:
                return redactar_ficha(f)
            except Exception as e:
                f["error"] = f"{type(e).__name__}: {str(e).splitlines()[0][:120] if str(e) else ''}"
                time.sleep(5 * (intento + 1))
        return f
    with ThreadPoolExecutor(HILOS) as pool:
        listas = list(pool.map(una, fichas))
    hechas = [f for f in listas if f.get("NAME")]
    fallidas = [f for f in listas if not f.get("NAME")]
    return hechas, fallidas


def responder(cfg, destino, asunto_origen, hechas, fallidas, descartes, problemas):
    """La respuesta, con las mismas piezas que responder_peticion del servidor."""
    from correo import enviar, enviar_fichas, nombre_saludo, es_formal, es_para_mi, cabecera_fichas
    nombre = nombre_saludo(cfg, "", destino)
    formal = es_formal(cfg, nombre, destino)
    detalle = es_para_mi(cfg, destino) and not formal
    lineas = list(problemas)
    if descartes:
        lineas.append("Sin texto de la agencia en la columna Comments: " + ", ".join(descartes) + ".")
    if fallidas:
        if detalle:
            lineas.append("No han salido: " + ", ".join(f"{f['etiqueta']} ({f.get('error', '')[:70]})" for f in fallidas))
        else:
            lineas.append("No han salido: " + ", ".join(f["etiqueta"] for f in fallidas) + ".")
    if formal:
        cabecera = cabecera_fichas(len(hechas), False, formal=True)
    else:
        s = "" if len(hechas) == 1 else "s"
        cabecera = f"{len(hechas)} ficha{s} lista{s} para pegar." + (" Si alguna trae AVISOS, revisala con mas cuidado." if detalle else "")
    asunto = asunto_respuesta({"asunto": asunto_origen}, hechas, fallidas, formal)
    if hechas:
        enviar_fichas(cfg, hechas, nombre, destino, asunto, forzar_formal=formal, cabecera=cabecera, lineas_extra=lineas)
    else:
        enviar(cfg, destino, asunto, "No ha salido ninguna ficha.\n" + "\n".join(lineas) + "\n")


# ---------------------------------------------------------------------- buzon (por UID: no cambian al borrar)
DIAS_ATRAS = 7                      # correos de la ultima semana; lo anterior ya se ha contestado o caducado
FALLO = "CatalogatorNubeFallo"      # primer intento fallido: al segundo se contesta que no se ha podido
# respaldo de la Raspberry: correos a la direccion normal que nadie ha leido
ESPERA_RASPBERRY = 15 * 60          # sin leer tras 15 min: la Raspberry no esta (mira el correo cada pocos minutos)
RESPALDO_HORAS = 24                 # lo que lleva mas de un dia sin leer no se contesta (correos olvidados)
RESPALDO = "CatalogatorNubeRespaldo"        # cogido por la nube (ya leido: la Raspberry no lo toca)
NO_RESPALDO = "CatalogatorNubeNoRespaldo"   # mirado: nada que la nube sepa hacer, se queda para la Raspberry
CABECERAS = "(BODY.PEEK[HEADER.FIELDS (TO CC DELIVERED-TO X-ORIGINAL-TO)])"


def _buscar(m, *criterios):
    ok, datos = m.uid("SEARCH", None, *criterios)
    return (datos[0] or b"").split() if ok == "OK" and datos and datos[0] else []


def _bajar(m, uid, que="(BODY.PEEK[])"):
    ok, bruto = m.uid("FETCH", uid, que)
    if ok != "OK" or not bruto or not isinstance(bruto[0], tuple):
        return None
    return email.message_from_bytes(bruto[0][1], policy=email.policy.default)


def _llegada(m, uid):
    """Cuando llego el correo al buzon (segundos), o None."""
    ok, datos = m.uid("FETCH", uid, "(INTERNALDATE)")
    if ok != "OK" or not datos or not datos[0]:
        return None
    fecha = imaplib.Internaldate2tuple(datos[0] if isinstance(datos[0], bytes) else datos[0][0])
    return time.mktime(fecha) if fecha else None


def pendientes(m, ahora=None):
    """[(uid, respaldo)] de los correos aun sin contestar: los de la ultima semana mandados a la direccion +csv
    (respaldo False) y los de la direccion normal que la Raspberry no ha leido en ESPERA_RASPBERRY (respaldo
    True; tambien los que ya cogio la nube y fallaron una vez). Se mira la cabecera de cada uno (la busqueda de
    Gmail no se lleva bien con el + de las direcciones)."""
    ahora = ahora or time.time()
    desde = time.strftime("%d-%b-%Y", time.gmtime(ahora - DIAS_ATRAS * 86400))
    salida, vistos = [], set()
    for uid in _buscar(m, "SINCE", desde, "UNKEYWORD", HECHO):
        cab = _bajar(m, uid, CABECERAS)
        if cab is not None and es_para_nube(cab):
            salida.append((uid, False))
            vistos.add(uid)
    for uid in _buscar(m, "KEYWORD", RESPALDO, "UNKEYWORD", HECHO):
        if uid not in vistos:
            salida.append((uid, True))
            vistos.add(uid)
    ayer = time.strftime("%d-%b-%Y", time.gmtime(ahora - RESPALDO_HORAS * 3600))
    for uid in _buscar(m, "SINCE", ayer, "UNSEEN", "UNKEYWORD", HECHO, "UNKEYWORD", MARCA_RECHAZADO,
                       "UNKEYWORD", NO_RESPALDO):
        if uid in vistos:
            continue
        llegada = _llegada(m, uid)
        if llegada is None or not ESPERA_RASPBERRY <= ahora - llegada <= RESPALDO_HORAS * 3600:
            continue
        cab = _bajar(m, uid, CABECERAS)
        if cab is not None and not es_para_nube(cab):
            salida.append((uid, True))
    return salida


def cargar_cerebro(m):
    """La configuracion y las aprobadas del ultimo correo "Cerebro para la nube". {} si no hay. Los
    anteriores sobran: se quitan de la bandeja (Gmail los deja en Todos)."""
    uids = _buscar(m, "SUBJECT", '"Cerebro para la nube"')
    for uid in reversed(uids):
        msg = _bajar(m, uid)
        if msg is None:
            continue
        for parte in msg.walk():
            if parte.get_filename() == cerebro.ADJUNTO:
                cfg = cerebro.desempaquetar(parte.get_payload(decode=True))
                viejos = uids[:uids.index(uid)]
                if viejos:
                    m.uid("STORE", b",".join(viejos), "+FLAGS", "(\\Deleted)")
                    m.expunge()
                return cfg
    return {}


def atender(m, uid, cfg, respaldo=False):
    """Un correo: redacta sus filas y contesta. Si falla, se reintenta en la vuelta siguiente; si falla
    otra vez, se le dice al remitente y no se vuelve a intentar.

    respaldo: el correo era para la Raspberry y no lo ha leido. Solo se coge si es una peticion de alguien
    permitido con alguna fila con texto de agencia; entonces se marca leido antes de redactar (la Raspberry
    ya no lo toca). Si no, se marca NO_RESPALDO y se deja sin leer para ella."""
    from correo import enviar
    msg = _bajar(m, uid)
    if msg is None:
        return
    asunto = _texto(msg["Subject"])
    _, de = _remitente(msg)
    if respaldo and uid not in _buscar(m, "KEYWORD", RESPALDO):
        propio = es_propio(asunto, msg.get("In-Reply-To"), msg.get("References"), msg)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                con_texto = bool(fichas_del_correo(msg)[0])
            except Exception:
                con_texto = False
        if propio or not permitido(de, cfg) or not con_texto:
            print("  respaldo: nada que la nube sepa hacer, se deja para la Raspberry")
            m.uid("STORE", uid, "+FLAGS", f"({NO_RESPALDO})")
            return
        m.uid("STORE", uid, "+FLAGS", f"(\\Seen {RESPALDO})")
        print("  respaldo: la Raspberry no lo ha leido")
    if es_propio(asunto, msg.get("In-Reply-To"), msg.get("References"), msg):
        print("  correo propio o automatico: se salta")
    elif not permitido(de, cfg):
        print("  remitente no permitido: se salta")
    else:
        t0 = time.time()
        try:
            # lo que escriban por dentro los modulos (textos de la ficha) no va al registro publico
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                fichas, descartes, problemas = fichas_del_correo(msg)
                hechas, fallidas = redactar_todas(fichas)
                responder(cfg, de, asunto, hechas, fallidas, descartes, problemas)
            print(f"  {len(fichas)} fila(s): {len(hechas)} hecha(s), {len(fallidas)} sin salir, "
                  f"{len(descartes)} sin texto, en {time.time() - t0:.0f} s")
        except Exception as e:
            print(f"  fallo con un correo ({type(e).__name__})")
            if uid not in _buscar(m, "KEYWORD", FALLO):
                m.uid("STORE", uid, "+FLAGS", f"({FALLO})")
                return                                      # se reintenta en la vuelta siguiente
            try:
                enviar(cfg, de, f"{MARCA} No se ha podido catalogar · {asunto}",
                       "No he podido catalogar la lista de este correo (dos intentos). " +
                       ("Vuelve a mandarla más tarde.\n" if respaldo else
                        "Vuelve a mandarla o pidela a Catalogator sin el +csv.\n"))
            except Exception:
                pass
    m.uid("STORE", uid, "+FLAGS", f"(\\Seen {HECHO})")


def configuracion():
    """La de los secretos de GitHub; None si faltan (la nube no esta puesta en marcha)."""
    usuario = os.environ.get("CATALOGATOR_CORREO", "").strip()
    clave = os.environ.get("CATALOGATOR_CLAVE", "").strip()
    token = os.environ.get("CATALOGATOR_CLAUDE_TOKEN", "").strip()
    api = os.environ.get("CATALOGATOR_API_KEY", "").strip()
    openai = os.environ.get("CATALOGATOR_OPENAI_KEY", "").strip()
    if not (usuario and clave and (token or api or openai)):
        return None
    return {"correo_usuario": usuario, "correo_clave": clave, "claude_token": token, "api_key": api,
            "openai_key": openai}


def motor(secretos):
    """Con el token, Claude Code con la suscripcion (como la Raspberry); si no, la API de pago o ChatGPT."""
    if secretos.get("claude_token"):
        return "claude_code"
    return "api" if secretos.get("api_key") else "openai"


def instalar_claude_code():
    """Claude Code no viene en el equipo de GitHub: se instala la primera vez que hace falta (unos segundos)."""
    import shutil
    import subprocess
    if shutil.which("claude"):
        return True
    r = subprocess.run(["npm", "install", "-g", "--silent", "@anthropic-ai/claude-code"],
                       capture_output=True, text=True, timeout=300)
    return r.returncode == 0 and bool(shutil.which("claude"))


def main():
    secretos = configuracion()
    if not secretos:
        print("Catalogator en la nube sin poner en marcha (faltan los secretos, ver NUBE.md)")
        return 0
    cfg = {"correo_servidor": "smtp.gmail.com", "correo_puerto": 587, "correo_imap": "imap.gmail.com",
           "correo_imap_puerto": 993}
    with imaplib.IMAP4_SSL(cfg["correo_imap"], cfg["correo_imap_puerto"], ssl_context=ssl.create_default_context(),
                           timeout=60) as m:
        m.login(secretos["correo_usuario"], secretos["correo_clave"])
        m.select("INBOX")
        uids = pendientes(m)
        if not uids:
            print("Sin correos nuevos")
            return 0
        aprendido = cargar_cerebro(m)
        cfg.update(aprendido)
        cfg.update(secretos)
        import redactor
        redactor.configurar(cfg)
        redactor.CFG["redactor"] = motor(secretos)
        redactor.CFG["miniaturas"] = 0
        if redactor.CFG["redactor"] == "claude_code" and not instalar_claude_code():
            print("No se ha podido instalar Claude Code: se reintenta en la vuelta siguiente")
            return 1
        print(f"{len(uids)} correo(s) nuevo(s); cerebro {'cargado' if aprendido else 'no encontrado: reglas de serie'}; "
              f"redacta {'Claude con la suscripcion' if redactor.CFG['redactor'] == 'claude_code' else redactor.CFG['redactor']}")
        for uid, respaldo in uids:
            atender(m, uid, cfg, respaldo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
