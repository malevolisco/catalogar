# -*- coding: utf-8 -*-
"""
salud.py - Chequeos de salud automaticos, el correo diario de "sigo vivo" y el informe semanal.

Cada salud_cada_minutos (60) mira:
    trabajo     el hilo que redacta sigue vivo; el modelo no esta en pausa
    agencias    la sesion de las agencias no esta caducada
    correo      el buzon sigue mirando (si esta encendido)
    errores     fichas que han fallado desde el ultimo chequeo
    disco       espacio libre (si baja de 1 GB borra miniaturas de mas de 14 dias)
    memoria     memoria libre del equipo
    temperatura la del procesador (Raspberry)
Lo que se puede arreglar solo, se arregla (el hilo de trabajo muerto dos veces seguidas: reinicio del
servidor; el disco lleno: limpieza). Si aparece un problema nuevo, o se resuelve, manda un correo a
correo_copia; un problema que sigue igual no se repite cada hora.

Copias de seguridad: cada noche (a las 03:00) un zip con todos los datos en copias/ (se guardan las 7
ultimas); y los lunes, con el informe, otra al correo de correo_copia sin config.json (contraseñas) ni lo
pesado, para que sobreviva aunque se estropee la tarjeta de la Raspberry.

Cada dia a la hora de salud_hora (08:00) manda una linea de "sigo vivo" con lo de ayer; si un dia no
llega, el equipo esta apagado. Los lunes a esa hora, el informe de la semana (a correo_copia y a
informe_destinatarios): fichas, agencias, aprobadas sin tocar y corregidas, tiempo ahorrado y consumo.

    GET  /api/admin/salud               chequeo actual e historial de 7 dias
    POST /api/admin/salud/comprobar     chequea ahora
    GET  /api/admin/informe             el informe semanal (vista previa)
    POST /api/admin/informe/enviar      lo manda ahora
"""
import json
import os
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException

BASE_DIR = Path(__file__).resolve().parent
SALUD_PATH = BASE_DIR / "cola" / "salud.json"
DIAS_HISTORIAL = 7
DISCO_MIN_GB = 1.0
COPIAS_DIR = BASE_DIR / "copias"
COPIA_HORA = 3
COPIA_CORREO_MAX = 20 * 1024 * 1024       # Gmail admite 25 MB por correo
MINIATURAS_DIAS = 14

router = APIRouter()
S = {}
DATOS = {"actual": {}, "historial": [], "dia_enviado": "", "semana_enviada": ""}
_cerrojo = threading.Lock()
_fallos_worker = [0]
_errores_vistos = [None]


def iniciar(globales):
    S.clear()
    S["_g"] = globales
    try:
        DATOS.update(json.loads(SALUD_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    threading.Thread(target=_bucle, name="salud", daemon=True).start()


def g(nombre):
    return S["_g"][nombre]


def _cfg():
    return g("CFG")


def _log(texto):
    try:
        g("log")(texto)
    except Exception:
        print(texto, flush=True)


def _guardar():
    with _cerrojo:
        limite = (datetime.now() - timedelta(days=DIAS_HISTORIAL)).strftime("%Y-%m-%d %H:%M")
        DATOS["historial"] = [h for h in DATOS["historial"] if h["cuando"] >= limite][-500:]
        texto = json.dumps(DATOS, ensure_ascii=False)
    try:
        SALUD_PATH.parent.mkdir(exist_ok=True)
        tmp = SALUD_PATH.with_suffix(".tmp")
        tmp.write_text(texto, encoding="utf-8")
        os.replace(tmp, SALUD_PATH)
    except OSError:
        pass


# ---------------------------------------------------------------------- medidas del equipo
def memoria_libre():
    """Parte de la memoria disponible (0..1) o None."""
    try:
        import psutil
        return psutil.virtual_memory().available / psutil.virtual_memory().total
    except Exception:
        pass
    try:
        datos = {}
        for linea in Path("/proc/meminfo").read_text().splitlines():
            clave, _, valor = linea.partition(":")
            datos[clave] = int(valor.split()[0])
        return datos["MemAvailable"] / datos["MemTotal"]
    except (OSError, KeyError, ValueError, ZeroDivisionError):
        return None


def temperatura():
    """Grados del procesador (Raspberry y la mayoria de Linux) o None."""
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()) / 1000
    except (OSError, ValueError):
        return None


def limpiar_miniaturas(dias=MINIATURAS_DIAS):
    """Borra las carpetas de miniaturas/ de mas de 'dias'. Devuelve los MB liberados."""
    raiz = BASE_DIR / "miniaturas"
    if not raiz.is_dir():
        return 0
    limite = time.time() - dias * 86400
    liberado = 0
    for carpeta in raiz.iterdir():
        try:
            if carpeta.is_dir() and carpeta.stat().st_mtime < limite:
                liberado += sum(f.stat().st_size for f in carpeta.rglob("*") if f.is_file())
                shutil.rmtree(carpeta, ignore_errors=True)
        except OSError:
            continue
    return round(liberado / 1e6)


# ---------------------------------------------------------------------- el chequeo
def chequear(arreglar=True):
    """Una vuelta de chequeos. Devuelve {"cuando", "piezas": [{clave, nombre, ok, texto, arreglo}], "ok"}."""
    piezas = []

    def pieza(clave, nombre, ok, texto, arreglo=""):
        piezas.append({"clave": clave, "nombre": nombre, "ok": bool(ok), "texto": texto, "arreglo": arreglo})

    cfg = _cfg()
    estado = g("ESTADO")
    resumen = estado.resumen()

    # trabajo: el hilo y el modelo
    vivo = resumen.get("worker_vivo")
    _fallos_worker[0] = 0 if vivo else _fallos_worker[0] + 1
    arreglo = ""
    if not vivo and _fallos_worker[0] >= 2 and arreglar:
        arreglo = "Se reinicia el servidor"
    pieza("trabajo", "Trabajo", vivo, "Funcionando" if vivo else "El hilo de trabajo se ha parado", arreglo)
    parado = resumen.get("modelo_parado")
    pieza("modelo", "Redacción", not parado, "Responde" if not parado else str(parado)[:200])

    # agencias
    sesion = str(resumen.get("sesion_agencia") or "")
    mala = any(p in sesion for p in ("caducada", "error", "antibot"))
    pieza("agencias", "Agencias", not mala, sesion[:1].upper() + sesion[1:] if sesion else "Sin abrir todavía")

    # correo
    if float(cfg.get("correo_buzon_minutos") or 0):
        buzon_vivo = g("BUZON").is_alive()
        pieza("correo", "Buzón", buzon_vivo, "Mirando el buzón" if buzon_vivo else "El buzón se ha parado")

    # errores nuevos desde el ultimo chequeo
    with estado.lock:
        con_error = {f.get("id") or f.get("numero") for l in estado.lotes for f in l["fichas"]
                     if f["estado"] == "error"}
    nuevos = con_error - (_errores_vistos[0] if _errores_vistos[0] is not None else con_error)
    _errores_vistos[0] = con_error
    pieza("errores", "Fichas con error", not nuevos,
          f"{len(nuevos)} nueva(s) desde el último chequeo" if nuevos else f"Ninguna nueva ({len(con_error)} en total)")

    # disco
    try:
        libre = shutil.disk_usage(BASE_DIR).free / 1e9
    except OSError:
        libre = None
    if libre is not None:
        arreglo = ""
        if libre < DISCO_MIN_GB and arreglar:
            mb = limpiar_miniaturas()
            arreglo = f"Borradas miniaturas viejas ({mb} MB)"
            libre = shutil.disk_usage(BASE_DIR).free / 1e9
        pieza("disco", "Disco", libre >= DISCO_MIN_GB, f"{libre:.1f} GB libres", arreglo)

    # memoria y temperatura
    mem = memoria_libre()
    if mem is not None:
        pieza("memoria", "Memoria", mem >= 0.1, f"{mem * 100:.0f} % libre")
    temp = temperatura()
    if temp is not None:
        pieza("temperatura", "Temperatura", temp < 80, f"{temp:.0f} °C" + (" (demasiado caliente: necesita aire)" if temp >= 80 else ""))

    # copia de seguridad: la ultima de la noche no puede tener mas de dos dias
    if cfg.get("copias_locales", True):
        ultima = ultima_copia()
        if ultima is None:
            pieza("copia", "Copia de seguridad", True, "La primera se hace esta noche a las 03:00")
        else:
            horas = (time.time() - ultima.stat().st_mtime) / 3600
            pieza("copia", "Copia de seguridad", horas < 48,
                  f"Última hace {horas:.0f} h" if horas < 48 else f"La última es de hace {horas / 24:.0f} días")

    resultado = {"cuando": datetime.now().strftime("%Y-%m-%d %H:%M"), "piezas": piezas,
                 "ok": all(p["ok"] for p in piezas)}
    _comparar_y_avisar(resultado)
    with _cerrojo:
        DATOS["actual"] = resultado
        DATOS["historial"].append({"cuando": resultado["cuando"], "ok": resultado["ok"],
                                   "mal": [p["nombre"] + ": " + p["texto"] for p in piezas if not p["ok"]],
                                   "arreglos": [p["arreglo"] for p in piezas if p["arreglo"]]})
    _guardar()
    for p in piezas:
        if p["arreglo"]:
            _log(f"Salud: {p['nombre']}: {p['texto']} → {p['arreglo']}")
    if any(p["clave"] == "trabajo" and p["arreglo"] for p in piezas):
        threading.Timer(5, lambda: os._exit(75)).start()      # el lanzador o systemd lo vuelve a arrancar
    return resultado


def _comparar_y_avisar(nuevo):
    """Correo solo cuando algo pasa a ir mal o vuelve a ir bien (no cada hora con lo mismo)."""
    antes = {p["clave"]: p["ok"] for p in (DATOS.get("actual") or {}).get("piezas", [])}
    rotos = [p for p in nuevo["piezas"] if not p["ok"] and antes.get(p["clave"], True)]
    try:                                  # la sesion caducada ya la avisa el Worker con su propio correo
        if getattr(g("WORKER"), "aviso_agencia_mandado", False):
            rotos = [p for p in rotos if p["clave"] != "agencias"]
    except KeyError:
        pass
    # las fichas con error son un hecho, no un estado: avisan al aparecer, pero no "vuelven a ir bien"
    arreglados = [p for p in nuevo["piezas"] if p["ok"] and antes.get(p["clave"]) is False and p["clave"] != "errores"]
    if not (rotos or arreglados) or not _cfg().get("salud_avisos", True):
        return
    lineas = []
    if rotos:
        lineas.append("Algo no va bien en Catalogator:\n")
        lineas += [f"  · {p['nombre']}: {p['texto']}" + (f" ({p['arreglo']})" if p["arreglo"] else "") for p in rotos]
        lineas.append("\nMás detalles en la página → Admin → Salud.")
    if arreglados:
        lineas.append(("\n" if rotos else "") + "Vuelve a ir bien:\n")
        lineas += [f"  · {p['nombre']}: {p['texto']}" for p in arreglados]
    asunto = ("Catalogator necesita atención: " + ", ".join(p["nombre"] for p in rotos)) if rotos else "Catalogator: todo vuelve a ir bien"
    _mandar([_cfg().get("correo_copia")], asunto, "\n".join(lineas))


def _mandar(destinos, asunto, texto, html=None, adjuntos=()):
    cfg = _cfg()
    destinos = [d for d in dict.fromkeys(x.strip() for x in destinos if x and x.strip())]
    if not destinos or not (cfg.get("correo_usuario") and cfg.get("correo_clave")):
        return False
    try:
        g("enviar")(cfg, ", ".join(destinos), asunto, texto, html=html, adjuntos=adjuntos)
        _log(f"Salud: correo '{asunto}' mandado a {', '.join(destinos)}")
        return True
    except Exception as e:
        _log(f"Salud: no se ha podido mandar '{asunto}' ({type(e).__name__})")
        return False


# ---------------------------------------------------------------------- copias de seguridad
def ultima_copia():
    copias = sorted(COPIAS_DIR.glob("catalogator-datos-*.zip")) if COPIAS_DIR.is_dir() else []
    return copias[-1] if copias else None


def hacer_copia():
    """Zip con todos los datos en copias/; se quedan las copias_guardar (7) ultimas. Devuelve la ruta."""
    datos, nombre = sys.modules["admin"]._zip_copia()
    COPIAS_DIR.mkdir(exist_ok=True)
    ruta = COPIAS_DIR / nombre
    ruta.write_bytes(datos)
    guardar = max(1, int(_cfg().get("copias_guardar") or 7))
    for vieja in sorted(COPIAS_DIR.glob("catalogator-datos-*.zip"))[:-guardar]:
        vieja.unlink(missing_ok=True)
    _log(f"Copia de seguridad hecha: {ruta.name} ({len(datos) / 1e6:.1f} MB)")
    return ruta


def copia_por_correo():
    """La copia sin contraseñas al correo de correo_copia. Si pesa demasiado, sin imagenes."""
    zip_copia = sys.modules["admin"]._zip_copia
    datos, nombre = zip_copia(excluir=("config.json", "broma"))
    nota = ""
    if len(datos) > COPIA_CORREO_MAX:
        datos, nombre = zip_copia(excluir=("config.json", "broma", "escenas", "escenas_auto"))
        nota = "\nVa sin las imágenes de entrenar (pesaban demasiado para un correo); esas están en las copias del equipo.\n"
    if len(datos) > COPIA_CORREO_MAX:
        _log(f"Copia por correo: {len(datos) / 1e6:.0f} MB, demasiado para un correo; se queda solo la del equipo")
        return False
    texto = ("Copia semanal de tus datos de Catalogator: fichas aprobadas, reglas, cambios del criterio, historial "
             "y consumo.\n" + nota +
             "\nNo lleva config.json (contraseñas y ajustes): si un día hay que montar el equipo de nuevo, se carga "
             "en Admin → Copia de tus datos → Cargar una copia, y los ajustes se vuelven a poner a mano.\n"
             "Guarda este correo: es lo que salva tu trabajo si se estropea la tarjeta de la Raspberry.")
    return _mandar([_cfg().get("correo_copia")], f"Catalogator: copia de tus datos ({datetime.now():%d/%m/%Y})",
                   texto, adjuntos=[(nombre, datos)])


# ---------------------------------------------------------------------- numeros para los correos
def _fichas_entre(desde, hasta):
    """Fichas hechas en lotes creados entre dos fechas: (hechas, errores, aprobadas, sin_tocar, agencias, segundos)."""
    estado, agencia_de = g("ESTADO"), g("agencia_de")
    hechas = errores = aprobadas = sin_tocar = 0
    agencias, segundos = {}, []
    with estado.lock:
        for lote in estado.lotes:
            try:
                creado = datetime.strptime(str(lote.get("creado", "")), "%d/%m/%Y %H:%M")
            except ValueError:
                continue
            if not desde <= creado < hasta:
                continue
            for f in lote["fichas"]:
                if f["estado"] == "error":
                    errores += 1
                if f["estado"] != "hecha" or f.get("copiada"):
                    continue
                hechas += 1
                if f.get("segundos"):
                    segundos.append(f["segundos"])
                try:
                    ag = agencia_de(f.get("numero") or "") or "Otra"
                except Exception:
                    ag = "Otra"
                agencias[ag] = agencias.get(ag, 0) + 1
                if f.get("aprobada"):
                    aprobadas += 1
                    borrador = f.get("borrador") or {}
                    if borrador and all((borrador.get(c) or "") == (f.get(c) or "") for c in ("NAME", "COMMENT", "RESTRICCIONES")):
                        sin_tocar += 1
    return {"hechas": hechas, "errores": errores, "aprobadas": aprobadas, "sin_tocar": sin_tocar,
            "corregidas": aprobadas - sin_tocar, "agencias": agencias,
            "segundos_medio": round(sum(segundos) / len(segundos)) if segundos else 0}


def _consumo_entre(desde, hasta):
    try:
        import consumo
        with consumo._cerrojo:
            dias = {d: dict(v) for d, v in consumo.DATOS["dias"].items()}
    except Exception:
        return None
    mb = wh = 0.0
    for d, horas in dias.items():
        if desde.strftime("%Y-%m-%d") <= d < hasta.strftime("%Y-%m-%d"):
            mb += sum(h["mb"] for h in horas.values())
            wh += sum(h["wh"] for h in horas.values())
    precio = float(_cfg().get("precio_kwh") or 0.18)
    return {"mb": round(mb), "kwh": round(wh / 1000, 2), "euros": round(wh / 1000 * precio, 2)}


def linea_diaria():
    hoy = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    f = _fichas_entre(hoy - timedelta(days=1), hoy)
    actual = DATOS.get("actual") or {}
    mal = [p["nombre"] for p in actual.get("piezas", []) if not p["ok"]]
    estado = "Todo bien" if not mal else "Ojo: " + ", ".join(mal)
    return f"{estado} · {f['hechas']} fichas ayer · {f['errores']} con error · {f['aprobadas']} aprobadas"


def _tiempo(minutos):
    """112 -> 'unas 1,9 horas'; 14 -> 'unos 14 minutos'."""
    if minutos < 60:
        return f"unos {round(minutos)} minutos"
    return f"unas {minutos / 60:.1f} horas".replace(".", ",")


def informe_semanal():
    """(asunto, texto) con los numeros de los ultimos 7 dias."""
    cfg = _cfg()
    hoy = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    desde = hoy - timedelta(days=7)
    f = _fichas_entre(desde, hoy)
    c = _consumo_entre(desde, hoy)
    a_mano = float(cfg.get("informe_minutos_a_mano") or 8)
    revision = float(cfg.get("informe_minutos_revision") or 1)
    ahorro = max(0.0, f["hechas"] * (a_mano - revision))
    caidas = sum(1 for h in DATOS.get("historial", []) if not h["ok"] and h["cuando"] >= desde.strftime("%Y-%m-%d"))
    periodo = f"{desde:%d/%m} – {hoy - timedelta(days=1):%d/%m/%Y}"
    lineas = [f"Catalogator · semana del {periodo}", ""]
    lineas.append(f"Fichas redactadas: {f['hechas']}" + (f" (unos {f['segundos_medio']} s cada una)" if f["segundos_medio"] else ""))
    if f["agencias"]:
        lineas.append("Por agencia: " + ", ".join(f"{a} {n}" for a, n in sorted(f["agencias"].items(), key=lambda x: -x[1])))
    if f["aprobadas"]:
        pct = round(f["sin_tocar"] * 100 / f["aprobadas"])
        lineas.append(f"Aprobadas: {f['aprobadas']} · sin tocar {f['sin_tocar']} ({pct} %) · corregidas {f['corregidas']}")
    lineas.append(f"Con error: {f['errores']}")
    lineas.append(f"Tiempo ahorrado: {_tiempo(ahorro)} "
                  f"(calculado con {a_mano:g} min por ficha a mano y {revision:g} min de revisión)")
    if c and (c["kwh"] or c["mb"]):
        lineas.append(f"Consumo: {c['kwh']:.2f} kWh ({c['euros']:.2f} €) · {c['mb'] / 1000:.2f} GB de red".replace(".", ","))
    lineas.append(f"Salud: {'sin incidencias' if not caidas else f'{caidas} chequeo(s) con algún problema'}")
    lineas += ["", "Lo cuenta Catalogator solo, con lo que hay en su cola."]
    asunto = f"Catalogator: resumen de la semana ({f['hechas']} fichas, ahorro de {_tiempo(ahorro)})"
    return asunto, "\n".join(lineas)


def _destinos_informe():
    cfg = _cfg()
    otros = cfg.get("informe_destinatarios") or []
    if isinstance(otros, str):
        otros = [x for x in otros.split(",")]
    return [cfg.get("correo_copia")] + list(otros)


# ---------------------------------------------------------------------- el bucle
def _bucle():
    time.sleep(120)                       # que arranque todo antes del primer chequeo
    ultimo = 0.0
    while True:
        try:
            cfg = _cfg()
            cada = max(10, int(cfg.get("salud_cada_minutos") or 60)) * 60
            if time.time() - ultimo >= cada:
                ultimo = time.time()
                chequear()
            _correos_programados(cfg)
        except Exception as e:
            _log(f"Salud: {type(e).__name__}: {e}")
        time.sleep(60)


def _correos_programados(cfg):
    ahora = datetime.now()
    hoy = ahora.strftime("%Y-%m-%d")
    # la copia de la noche (o la primera vez que se enciende despues, si estaba apagado)
    if cfg.get("copias_locales", True) and ahora.hour >= COPIA_HORA and DATOS.get("copia_dia") != hoy:
        DATOS["copia_dia"] = hoy
        _guardar()
        try:
            hacer_copia()
        except Exception as e:
            _log(f"No se ha podido hacer la copia de seguridad ({type(e).__name__}: {e})")
    try:
        h, m = (int(x) for x in str(cfg.get("salud_hora") or "08:00").split(":")[:2])
    except ValueError:
        h, m = 8, 0
    if (ahora.hour, ahora.minute) < (h, m):
        return
    if cfg.get("salud_diario", True) and DATOS.get("dia_enviado") != hoy:
        DATOS["dia_enviado"] = hoy
        _guardar()
        _mandar([cfg.get("correo_copia")], "Catalogator: " + linea_diaria(), linea_diaria() +
                "\n\nSi un día no te llega este correo, el equipo de Catalogator está apagado o sin internet.")
    semana = ahora.strftime("%G-%V")
    if cfg.get("informe_semanal", True) and ahora.weekday() == 0 and DATOS.get("semana_enviada") != semana:
        DATOS["semana_enviada"] = semana
        _guardar()
        asunto, texto = informe_semanal()
        _mandar(_destinos_informe(), asunto, texto)
        if cfg.get("copia_correo_semanal", True):
            try:
                copia_por_correo()
            except Exception as e:
                _log(f"No se ha podido mandar la copia por correo ({type(e).__name__}: {e})")


# ---------------------------------------------------------------------- pagina
@router.get("/api/admin/salud")
def ver_salud():
    with _cerrojo:
        return {"actual": DATOS.get("actual") or {}, "historial": list(reversed(DATOS["historial"][-200:])),
                "diaria": linea_diaria()}


@router.post("/api/admin/salud/comprobar")
def comprobar_ahora():
    return chequear()


@router.post("/api/admin/copia/ahora")
def copia_ahora():
    ruta = hacer_copia()
    return {"ok": True, "nombre": ruta.name}


@router.get("/api/admin/informe")
def ver_informe():
    asunto, texto = informe_semanal()
    return {"asunto": asunto, "texto": texto, "destinos": [d for d in _destinos_informe() if d]}


@router.post("/api/admin/informe/enviar")
def enviar_informe():
    asunto, texto = informe_semanal()
    if not _mandar(_destinos_informe(), asunto, texto):
        raise HTTPException(400, "No se ha podido mandar: revisa el correo en Ajustes (y que haya tu dirección en «correo_copia»)")
    return {"ok": True}
