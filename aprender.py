# -*- coding: utf-8 -*-
"""
aprender.py - Que el criterio crezca con el uso sin que el prompt engorde.

    colocar una regla      una regla de "Mis reglas" pasa a su sitio del criterio base: el modelo propone
                           despues de que linea va, como queda redactada y que lineas del criterio deja
                           sobrando; el catalogador lo revisa y lo aplica (criterio.py lo guarda aparte).
    aprender al aprobar    cuando se aprueba una ficha corregida, se compara con lo que redacto el modelo y,
                           si la correccion enseña algo general, queda una regla sugerida en la pestaña Reglas
                           (cola/sugerencias.json). Nunca se añade sola: el catalogador la acepta o la descarta.

Las fichas aprobadas no necesitan nada aqui: en cada envio solo entran las mas parecidas
(redactor.elegir_ejemplos), asi que se pueden aprobar todas las que se quiera.

servidor.py llama a iniciar(globals()) y monta router.
"""
import difflib
import json
import queue
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

import criterio

BASE_DIR = Path(__file__).resolve().parent
SUGERENCIAS_PATH = BASE_DIR / "cola" / "sugerencias.json"
CONSOLIDADAS_PATH = BASE_DIR / "cola" / "consolidadas.json"   # reglas que han pasado solas al criterio
CAMPOS_FICHA = ("NAME", "COMMENT", "RESTRICCIONES")
MAX_DESCARTADAS = 300

router = APIRouter()
S = {}
_cerrojo = threading.Lock()
_pendientes = queue.Queue()


def iniciar(globales):
    S.clear()
    S["_g"] = globales
    threading.Thread(target=_bucle, name="aprender", daemon=True).start()
    threading.Thread(target=_consolidar_bucle, name="consolidar", daemon=True).start()


def g(nombre):
    return S["_g"][nombre]


def _redactor():
    import redactor
    return redactor


def _log(texto):
    try:
        g("log")(texto)
    except Exception:
        print(texto, flush=True)


def _json_de(salida):
    """El primer objeto JSON de la respuesta del modelo (puede venir con texto o ``` alrededor)."""
    m = re.search(r"\{.*\}", salida or "", re.S)
    if not m:
        raise ValueError("el modelo no ha devuelto JSON")
    return json.loads(m.group(0))


def _criterio_base():
    cfg = g("CFG")
    ruta = BASE_DIR / cfg.get("reglas", "reglas.md")
    if not ruta.exists():
        ruta = BASE_DIR / "reglas.md"
    return ruta.name, ruta.read_text(encoding="utf-8")


# ====================================================================== colocar una regla en el criterio
INSTRUCCIONES_COLOCAR = """Eres el responsable del criterio de catalogacion de un archivo de television (fichas de material de agencia).
Te paso el criterio linea a linea, cada una con su identificador entre corchetes, y una regla nueva del catalogador.

Tu tarea: integrar la regla nueva en el criterio, en su sitio.
1. Elige la linea del criterio DESPUES de la cual tiene que ir (la seccion y el parrafo del tema que trata).
2. Redactala con el estilo del criterio: breve, directa, en mayusculas los terminos de ficha (NAME, COMMENT, RESTRICCIONES)
   y con un ejemplo corto si el original lo trae o si ayuda. No cambies lo que pide la regla: ni la suavices ni la amplies.
3. Señala las lineas del criterio que la regla nueva contradice o deja sobrando (para quitarlas). Solo las que de verdad
   chocan o repiten lo mismo; si no hay ninguna, lista vacia.

Responde SOLO con un objeto JSON, sin nada mas:
{"despues_de": "<identificador>", "texto": "<la regla redactada>", "sustituye": ["<identificador>", ...], "motivo": "<una frase: por que va ahi>"}"""


def _lineas_vivas(nombre, texto):
    """Las lineas del criterio tal como se redacta ahora (sin las quitadas), con su identificador."""
    return [l for l in criterio.vista(nombre, texto)["lineas"] if l["estado"] != "borrado"]


def proponer_sitio(regla):
    """Pide al modelo donde y como va la regla en el criterio base. Devuelve la propuesta para la pagina."""
    regla = " ".join(str(regla or "").split())
    regla = re.sub(r"\s+\(\d{4}-\d{2}-\d{2}\)$", "", regla)
    if not regla:
        raise ValueError("Regla vacia")
    nombre, texto = _criterio_base()
    lineas = _lineas_vivas(nombre, texto)
    por_id = {l["id"]: l for l in lineas}
    listado = "\n".join(("\n" if l["es_seccion"] else "") + f"[{l['id']}] {l['texto']}" for l in lineas)
    user = f"=== CRITERIO ===\n{listado}\n\n=== REGLA NUEVA DEL CATALOGADOR ===\n{regla}\n\nDevuelve ahora el JSON."
    salida = _redactor().llamar_modelo(INSTRUCCIONES_COLOCAR, user, timeout=300)
    datos = _json_de(salida)
    despues = str(datos.get("despues_de") or "").strip("[] ")
    if despues not in por_id:
        raise ValueError("El modelo no ha sabido situar la regla en el criterio; vuelve a probar o añádela a mano en Criterio base")
    sustituye = [i for i in (str(x).strip("[] ") for x in datos.get("sustituye") or []) if i in por_id and i != despues]
    ref = por_id[despues]
    return {
        "regla": regla,
        "despues_de": despues,
        "despues_texto": ref["texto"],
        "seccion": ref.get("seccion") or "",
        "texto": " ".join(str(datos.get("texto") or regla).split()),
        "sustituye": [{"id": i, "texto": por_id[i]["texto"]} for i in sustituye],
        "motivo": str(datos.get("motivo") or "").strip(),
    }


def aplicar_sitio(despues_de, texto, quitar=(), indice=None):
    """Añade la regla al criterio base tras despues_de, quita las lineas que sobran y, si venia de
    Mis reglas, la quita de alli (ya no hace falta repetirla al final)."""
    nombre, base = _criterio_base()
    aplicar_sitio.ultimo_id = criterio.anadir(nombre, base, str(despues_de or ""), texto)
    for ident in quitar or ():
        try:
            criterio.quitar(nombre, base, str(ident))
        except KeyError:
            pass
    r = _redactor()
    if indice:
        lista = r.listar_reglas_extra()
        if 1 <= int(indice) <= len(lista):
            ruta = r.REGLAS_EXTRA_PATH
            cabecera = [l for l in ruta.read_text(encoding="utf-8").splitlines() if l.startswith("#")]
            quedan = [x for i, x in enumerate(lista, 1) if i != int(indice)]
            ruta.write_text("\n".join(cabecera + quedan) + "\n", encoding="utf-8")
    return r.listar_reglas_extra()


@router.post("/api/reglas/colocar")
async def colocar(request: Request):
    """Propuesta de sitio para una regla: {"indice": n} (de Mis reglas) o {"texto": "..."}."""
    datos = await g("_json")(request)
    texto = datos.get("texto")
    indice = datos.get("indice")
    if indice:
        lista = _redactor().listar_reglas_extra()
        if not 1 <= int(indice) <= len(lista):
            raise HTTPException(404, f"No hay regla {indice}")
        texto = lista[int(indice) - 1]
    import asyncio
    try:
        propuesta = await asyncio.to_thread(proponer_sitio, texto)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except _redactor().RedactorError as e:
        raise HTTPException(502, "El modelo no ha respondido: " + str(e).splitlines()[0][:200])
    propuesta["indice"] = indice
    return propuesta


@router.post("/api/reglas/colocar/aplicar")
async def colocar_aplicar(request: Request):
    datos = await g("_json")(request)
    try:
        reglas = aplicar_sitio(datos.get("despues_de"), datos.get("texto"), datos.get("quitar") or [], datos.get("indice"))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e).strip("'\""))
    _log("Regla pasada al criterio base" + (f" (quitadas {len(datos.get('quitar') or [])} linea(s) que sobraban)" if datos.get("quitar") else ""))
    return {"reglas": reglas}


# ====================================================================== consolidacion automatica
# Una regla de Mis reglas que lleva reglas_auto_dias sin tocarse ya esta asentada: pasa sola a su sitio del
# criterio y sale de Mis reglas. Solo añade (no quita lineas del criterio por su cuenta) y se puede deshacer.
_cerrojo_consolidar = threading.Lock()


def _leer_consolidadas():
    try:
        datos = json.loads(CONSOLIDADAS_PATH.read_text(encoding="utf-8"))
        return datos if isinstance(datos, list) else []
    except (OSError, ValueError):
        return []


def _guardar_consolidadas(lista):
    CONSOLIDADAS_PATH.parent.mkdir(exist_ok=True)
    tmp = CONSOLIDADAS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(lista[-100:], ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(CONSOLIDADAS_PATH)


def fecha_regla(linea):
    m = re.search(r"\((\d{4}-\d{2}-\d{2})\)\s*$", linea or "")
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d") if m else None
    except ValueError:
        return None


def consolidar_una():
    """Pasa al criterio la regla mas antigua que ya cumple el plazo. Devuelve el registro o None."""
    cfg = g("CFG")
    if not cfg.get("reglas_auto_criterio", True):
        return None
    dias = int(cfg.get("reglas_auto_dias", 3) or 3)
    r = _redactor()
    with _cerrojo_consolidar:
        lista = r.listar_reglas_extra()
        maduras = [(fecha_regla(l), i, l) for i, l in enumerate(lista, 1)
                   if fecha_regla(l) and (datetime.now() - fecha_regla(l)).days >= dias]
        if not maduras:
            return None
        _, indice, linea = min(maduras)
        propuesta = proponer_sitio(linea)
        # la lista puede haber cambiado mientras pensaba el modelo: se busca otra vez la misma linea
        lista = r.listar_reglas_extra()
        if linea not in lista:
            return None
        aplicar_sitio(propuesta["despues_de"], propuesta["texto"], quitar=(), indice=lista.index(linea) + 1)
        registro = {"fecha": datetime.now().strftime("%d/%m/%Y %H:%M"), "regla": linea, "texto": propuesta["texto"],
                    "seccion": propuesta["seccion"], "id": aplicar_sitio.ultimo_id, "deshecha": False}
        todas = _leer_consolidadas()
        todas.append(registro)
        _guardar_consolidadas(todas)
    _log(f"Regla pasada sola al criterio ({propuesta['seccion'] or 'instrucciones generales'}): {propuesta['texto'][:90]}")
    return registro


def _consolidar_bucle():
    import time
    time.sleep(300)                              # que arranque antes lo demas
    while True:
        try:
            while consolidar_una():              # todas las que cumplan, de una en una
                pass
        except r_error() as e:
            _log(f"Pasar reglas al criterio: el modelo no ha podido ahora ({str(e).splitlines()[0][:120]}); se reintenta luego")
        except Exception as e:
            _log(f"Pasar reglas al criterio: no ha salido ({type(e).__name__}: {str(e)[:120]})")
        time.sleep(3600)


def r_error():
    return _redactor().RedactorError


@router.get("/api/consolidadas")
def ver_consolidadas():
    cfg = g("CFG")
    return {"consolidadas": list(reversed(_leer_consolidadas()))[:30],
            "auto": bool(cfg.get("reglas_auto_criterio", True)), "dias": int(cfg.get("reglas_auto_dias", 3) or 3)}


@router.post("/api/consolidadas/deshacer")
async def deshacer_consolidada(request: Request):
    """Quita del criterio la linea que se añadio sola y devuelve la regla a Mis reglas, tal como estaba
    pero con la fecha de hoy (para que no vuelva a pasar enseguida)."""
    datos = await g("_json")(request)
    ident = str(datos.get("id") or "")
    with _cerrojo_consolidar:
        todas = _leer_consolidadas()
        reg = next((x for x in todas if x.get("id") == ident and not x.get("deshecha")), None)
        if reg is None:
            raise HTTPException(404, "Esa ya no está o ya se deshizo")
        nombre, _ = _criterio_base()
        criterio.restaurar(nombre, ident)
        r = _redactor()
        r.anadir_regla(re.sub(r"\s+\(\d{4}-\d{2}-\d{2}\)\s*$", "", reg["regla"]))
        reg["deshecha"] = True
        _guardar_consolidadas(todas)
    _log("Deshecho el paso de una regla al criterio: vuelve a Mis reglas")
    return {"consolidadas": list(reversed(todas))[:30], "reglas": r.listar_reglas_extra()}


# ====================================================================== aprender de las correcciones
INSTRUCCIONES_APRENDER = """Eres el responsable del criterio de catalogacion de un archivo de television. El modelo redacto una ficha
siguiendo el criterio que va abajo y el catalogador la corrigio antes de aprobarla.

Decide si la correccion enseña una regla GENERAL, que sirva para otros envios:
- NO lo es un dato de este envio: un nombre o un lugar mal leido, una errata, una cifra, una fecha, quitar o
  añadir un plano concreto. Entonces "regla" va vacia.
- Si el criterio ya dice eso y el modelo no lo siguio, "regla" va vacia y en "ya_estaba" citas en pocas palabras
  la regla del criterio que no se siguio.
- Si es general y el criterio no lo cubre, o lo contradice, escribe la regla en una o dos frases, con el estilo del
  criterio (directa, terminos de ficha en mayusculas) y un ejemplo corto sacado de esta ficha si ayuda.

Responde SOLO con un objeto JSON, sin nada mas:
{"regla": "", "por_que": "<una frase: que ha corregido el catalogador>", "ya_estaba": ""}

=== CRITERIO VIGENTE ===
"""


def _leer():
    try:
        datos = json.loads(SUGERENCIAS_PATH.read_text(encoding="utf-8"))
        if isinstance(datos, dict):
            return {"sugerencias": list(datos.get("sugerencias") or []), "descartadas": list(datos.get("descartadas") or [])}
    except (OSError, ValueError):
        pass
    return {"sugerencias": [], "descartadas": []}


def _guardar(datos):
    SUGERENCIAS_PATH.parent.mkdir(exist_ok=True)
    tmp = SUGERENCIAS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(SUGERENCIAS_PATH)


def _parecida(texto, lista, corte=0.8):
    t = texto.upper()
    return next((x for x in lista if difflib.SequenceMatcher(None, t, x.upper()).ratio() >= corte), None)


def cambio_de_verdad(borrador, aprobada):
    """True si la aprobada cambia palabras respecto al borrador (no solo tildes, mayusculas o signos)."""
    if not borrador:
        return False
    huella = _redactor()._huella
    return any(huella(borrador.get(c) or "") != huella(aprobada.get(c) or "") for c in CAMPOS_FICHA)


def encolar(borrador, aprobada, envio):
    """Desde la aprobacion: si la ficha se corrigio, se estudia la correccion en segundo plano."""
    if not g("CFG").get("aprender_correcciones", True) or not cambio_de_verdad(borrador, aprobada):
        return False
    _pendientes.put(({c: borrador.get(c, "") for c in CAMPOS_FICHA}, {c: aprobada.get(c, "") for c in CAMPOS_FICHA}, envio))
    return True


def _bucle():
    while True:
        borrador, aprobada, envio = _pendientes.get()
        try:
            _estudiar(borrador, aprobada, envio)
        except Exception as e:                   # aprender es un extra: nunca para nada
            _log(f"Aprender de la correccion de {envio[:40]}: no ha salido ({type(e).__name__}: {str(e)[:120]})")


def _estudiar(borrador, aprobada, envio):
    r = _redactor()
    user = (f"ENVIO: {envio}\n\n=== LO QUE REDACTO EL MODELO ===\n"
            + "\n".join(f"{c}: {borrador[c]}" for c in CAMPOS_FICHA)
            + "\n\n=== LO QUE APROBO EL CATALOGADOR ===\n"
            + "\n".join(f"{c}: {aprobada[c]}" for c in CAMPOS_FICHA)
            + "\n\nDevuelve ahora el JSON.")
    try:
        salida = r.llamar_modelo(INSTRUCCIONES_APRENDER + r.criterio_vigente(), user, timeout=240)
    except r.ModeloNoDisponible:
        return                                  # sin modelo ahora: esta correccion no se estudia
    datos = _json_de(salida)
    regla = " ".join(str(datos.get("regla") or "").split())
    if not regla:
        if datos.get("ya_estaba"):
            _log(f"Correccion de {envio[:40]}: el criterio ya lo decia ({str(datos['ya_estaba'])[:100]})")
        return
    with _cerrojo:
        todo = _leer()
        if _parecida(regla, todo["descartadas"]) or _parecida(regla, r.listar_reglas_extra()):
            return
        igual = next((s for s in todo["sugerencias"] if _parecida(regla, [s["regla"]])), None)
        if igual:                               # la misma leccion otra vez: cuenta, y se queda la ultima ficha
            igual["veces"] = int(igual.get("veces") or 1) + 1
            igual.update(envio=envio, antes=borrador, despues=aprobada, fecha=datetime.now().strftime("%d/%m/%Y %H:%M"))
        else:
            todo["sugerencias"].append({"id": "s-" + secrets.token_hex(4), "regla": regla,
                                        "por_que": str(datos.get("por_que") or "").strip(), "envio": envio,
                                        "antes": borrador, "despues": aprobada, "veces": 1,
                                        "fecha": datetime.now().strftime("%d/%m/%Y %H:%M")})
        _guardar(todo)
    _log(f"Regla sugerida a partir de tu correccion de {envio[:40]}: mira Reglas → Sugerencias")


def pendientes():
    return len(_leer()["sugerencias"])


@router.get("/api/sugerencias")
def ver_sugerencias():
    return {"sugerencias": list(reversed(_leer()["sugerencias"])), "estudiando": _pendientes.qsize()}


@router.post("/api/sugerencias/{ident}/aceptar")
async def aceptar(ident: str, request: Request):
    """Pasa la sugerencia (con el texto que venga, si el catalogador lo ha retocado) a Mis reglas."""
    datos = await g("_json")(request)
    r = _redactor()
    with _cerrojo:
        todo = _leer()
        s = next((x for x in todo["sugerencias"] if x["id"] == ident), None)
        if s is None:
            raise HTTPException(404, "Esa sugerencia ya no está")
        try:
            r.anadir_regla(datos.get("texto") or s["regla"])
        except ValueError as e:
            raise HTTPException(400, str(e))
        todo["sugerencias"] = [x for x in todo["sugerencias"] if x["id"] != ident]
        _guardar(todo)
    _log("Regla sugerida aceptada: pasa a Mis reglas")
    return {"sugerencias": list(reversed(todo["sugerencias"])), "reglas": r.listar_reglas_extra()}


@router.delete("/api/sugerencias/{ident}")
def descartar(ident: str):
    with _cerrojo:
        todo = _leer()
        s = next((x for x in todo["sugerencias"] if x["id"] == ident), None)
        if s is None:
            raise HTTPException(404, "Esa sugerencia ya no está")
        todo["sugerencias"] = [x for x in todo["sugerencias"] if x["id"] != ident]
        todo["descartadas"] = (todo["descartadas"] + [s["regla"]])[-MAX_DESCARTADAS:]   # no se vuelve a proponer
        _guardar(todo)
    return {"sugerencias": list(reversed(todo["sugerencias"]))}
