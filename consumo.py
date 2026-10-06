# -*- coding: utf-8 -*-
"""
consumo.py - Cuanto gasta Catalogator: trafico de red y electricidad, por horas y por dias.

Cada minuto mira:
    trafico     los bytes que han pasado por la red del equipo (todas las conexiones menos la interna)
    vatios      en una Raspberry Pi 5, lo que mide su propio chip de alimentacion (vcgencmd pmic_read_adc);
                en otro equipo, una estimacion segun lo ocupado que este el procesador, entre los vatios en
                reposo y a plena carga de config.json (consumo_vatios_reposo / consumo_vatios_carga)
y lo suma por horas en cola/consumo.json (se guardan 90 dias). servidor.py llama a contar_item() cada
vez que termina una ficha, para sacar la media por item.

    GET /api/admin/consumo      hoy por horas, ultimos 30 dias, medias por item y coste (precio_kwh)
"""
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter

BASE_DIR = Path(__file__).resolve().parent
CONSUMO_PATH = BASE_DIR / "cola" / "consumo.json"
DIAS_GUARDADOS = 90
CADA = 60                     # segundos entre medidas

router = APIRouter()
S = {}
DATOS = {"dias": {}}
_cerrojo = threading.Lock()
ESTADO = {"fuente": "", "placa": "", "vatios": None}


def iniciar(globales):
    S.clear()
    S["_g"] = globales
    _cargar()
    threading.Thread(target=_bucle, name="consumo", daemon=True).start()


def _cfg():
    try:
        return S["_g"]["CFG"]
    except KeyError:
        return {}


def _cargar():
    try:
        DATOS.update(json.loads(CONSUMO_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    DATOS.setdefault("dias", {})


def _guardar():
    with _cerrojo:
        limite = (datetime.now() - timedelta(days=DIAS_GUARDADOS)).strftime("%Y-%m-%d")
        for d in [d for d in DATOS["dias"] if d < limite]:
            del DATOS["dias"][d]
        texto = json.dumps(DATOS, ensure_ascii=False)
    try:
        CONSUMO_PATH.parent.mkdir(exist_ok=True)
        tmp = CONSUMO_PATH.with_suffix(".tmp")
        tmp.write_text(texto, encoding="utf-8")
        os.replace(tmp, CONSUMO_PATH)
    except OSError:
        pass


def _hueco(momento=None):
    """El cajon de la hora actual: {mb, wh, items}."""
    m = momento or datetime.now()
    dia = DATOS["dias"].setdefault(m.strftime("%Y-%m-%d"), {})
    return dia.setdefault(f"{m.hour:02d}", {"mb": 0.0, "wh": 0.0, "items": 0})


def contar_item():
    """Una ficha redactada mas en esta hora."""
    with _cerrojo:
        _hueco()["items"] += 1


# ---------------------------------------------------------------------- medidas
def placa():
    """El modelo de la placa ("Raspberry Pi 5 Model B Rev 1.0") o "" si no es una Raspberry."""
    try:
        return Path("/proc/device-tree/model").read_bytes().decode("ascii", "ignore").strip("\x00 \n")
    except OSError:
        return ""


def bytes_red():
    """Bytes recibidos + enviados por todas las conexiones de red menos la interna (lo). None si no se sabe."""
    try:
        import psutil
        return sum(c.bytes_recv + c.bytes_sent for n, c in psutil.net_io_counters(pernic=True).items()
                   if not n.lower().startswith(("lo", "loopback")))
    except Exception:
        pass
    try:
        total = 0
        for linea in Path("/proc/net/dev").read_text().splitlines()[2:]:
            nombre, _, resto = linea.partition(":")
            if nombre.strip() == "lo":
                continue
            campos = resto.split()
            total += int(campos[0]) + int(campos[8])
        return total
    except (OSError, ValueError, IndexError):
        return None


def vatios_pi5():
    """Lo que mide el chip de alimentacion de la Pi 5: suma de tension x corriente de cada linea. None si no hay."""
    try:
        salida = subprocess.run(["vcgencmd", "pmic_read_adc"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    amperios, voltios = {}, {}
    for nombre, tipo, valor in re.findall(r"(\S+)_([AV])\s+\w+\(\d+\)=([\d.]+)", salida):
        (amperios if tipo == "A" else voltios)[nombre] = float(valor)
    comunes = set(amperios) & set(voltios)
    if not comunes:
        return None
    return sum(amperios[n] * voltios[n] for n in comunes)


_cpu_antes = [None]


def uso_cpu():
    """Parte del procesador ocupada desde la ultima medida (0..1), o None si no se sabe."""
    try:
        import psutil
        return psutil.cpu_percent(interval=None) / 100
    except Exception:
        pass
    try:
        n = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
    except (OSError, ValueError):
        return None
    reposo, total = n[3] + (n[4] if len(n) > 4 else 0), sum(n)
    antes, _cpu_antes[0] = _cpu_antes[0], (reposo, total)
    if not antes or total <= antes[1]:
        return None
    return max(0.0, min(1.0, 1 - (reposo - antes[0]) / (total - antes[1])))


def medir_vatios():
    """(vatios, fuente): "medido" en una Pi 5, "estimado" en el resto."""
    v = vatios_pi5()
    if v:
        return v, "medido"
    cfg = _cfg()
    es_pi = "raspberry" in ESTADO["placa"].lower()
    reposo = float(cfg.get("consumo_vatios_reposo") or (3 if es_pi else 40))
    carga = float(cfg.get("consumo_vatios_carga") or (9 if es_pi else 90))
    cpu = uso_cpu()
    return reposo + (carga - reposo) * (cpu or 0), "estimado"


def _bucle():
    ESTADO["placa"] = placa()
    uso_cpu()                                   # primera lectura: la base para la siguiente
    red_antes, t_antes = bytes_red(), time.time()
    vueltas = 0
    while True:
        time.sleep(CADA)
        try:
            ahora = time.time()
            horas = (ahora - t_antes) / 3600
            red = bytes_red()
            vatios, fuente = medir_vatios()
            ESTADO.update(vatios=round(vatios, 2), fuente=fuente)
            with _cerrojo:
                h = _hueco()
                if red is not None and red_antes is not None and red >= red_antes:   # al reiniciar el equipo el contador vuelve a 0
                    h["mb"] = round(h["mb"] + (red - red_antes) / 1e6, 3)
                h["wh"] = round(h["wh"] + vatios * min(horas, CADA * 3 / 3600), 4)
            red_antes, t_antes = red, ahora
            vueltas += 1
            if vueltas % 5 == 0:
                _guardar()
        except Exception as e:                   # una medida mala no para el contador
            print(f"consumo: {type(e).__name__}: {e}", flush=True)


# ---------------------------------------------------------------------- informe
def _sumar(horas):
    return {"mb": round(sum(h["mb"] for h in horas), 1), "wh": round(sum(h["wh"] for h in horas), 1),
            "items": sum(h["items"] for h in horas)}


@router.get("/api/admin/consumo")
def informe():
    precio = float(_cfg().get("precio_kwh") or 0.18)
    with _cerrojo:
        dias = {d: dict(v) for d, v in DATOS["dias"].items()}
    hoy = datetime.now().strftime("%Y-%m-%d")
    por_horas = [{"hora": f"{h:02d}", **dias.get(hoy, {}).get(f"{h:02d}", {"mb": 0, "wh": 0, "items": 0})}
                 for h in range(24)]
    ultimos = sorted(dias)[-30:]
    por_dias = [{"dia": d, **_sumar(dias[d].values())} for d in ultimos]
    # medias con los dias completos (hoy va a medias); si solo hay hoy, con hoy
    completos = [d for d in por_dias if d["dia"] != hoy] or por_dias
    n = len(completos) or 1
    mb_dia = sum(d["mb"] for d in completos) / n
    wh_dia = sum(d["wh"] for d in completos) / n
    items = sum(d["items"] for d in por_dias)
    return {
        "placa": ESTADO["placa"] or "Este equipo",
        "vatios": ESTADO["vatios"], "fuente": ESTADO["fuente"],
        "precio_kwh": precio,
        "hoy": por_horas,
        "dias": por_dias,
        "dias_medidos": len(completos),
        "media": {
            "mb_dia": round(mb_dia, 1), "kwh_dia": round(wh_dia / 1000, 3),
            "mb_mes": round(mb_dia * 30), "kwh_anio": round(wh_dia * 365 / 1000, 1),
            "euros_mes": round(wh_dia * 30 / 1000 * precio, 2), "euros_anio": round(wh_dia * 365 / 1000 * precio, 2),
            "mb_item": round(sum(d["mb"] for d in por_dias) / items, 2) if items else None,
            "wh_item": round(sum(d["wh"] for d in por_dias) / items, 2) if items else None,
        },
    }
