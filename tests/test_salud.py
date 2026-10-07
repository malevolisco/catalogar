# -*- coding: utf-8 -*-
"""Chequeos de salud, informe semanal, copias y consumo con un servidor de mentira."""
import threading
from datetime import datetime, timedelta

import consumo
import salud


class Estado:
    lock = threading.Lock()

    def __init__(self):
        creado = (datetime.now() - timedelta(days=2)).strftime("%d/%m/%Y %H:%M")
        igual = {"NAME": "N", "COMMENT": "C", "RESTRICCIONES": "SIN AVISO"}
        self.lotes = [{"id": "l", "creado": creado, "fichas": [
            {"id": "a", "numero": "7612", "estado": "hecha", "aprobada": True, "segundos": 40, **igual, "borrador": igual},
            {"id": "b", "numero": "4681323", "estado": "hecha", "aprobada": True, "segundos": 50, **igual,
             "borrador": {**igual, "NAME": "OTRO"}},
            {"id": "c", "numero": "7613", "estado": "error"}]}]

    def resumen(self):
        return {"worker_vivo": True, "modelo_parado": "", "sesion_agencia": "abierta"}


def preparar(tmp_path, correos):
    salud.SALUD_PATH = tmp_path / "salud.json"
    salud.COPIAS_DIR = tmp_path / "copias"
    salud.DATOS.update({"actual": {}, "historial": []})
    salud._errores_vistos[0] = None
    salud.S["_g"] = {"CFG": {"correo_copia": "yo@x", "correo_usuario": "u", "correo_clave": "k",
                             "informe_destinatarios": ["jefa@x"]},
                     "ESTADO": Estado(), "log": lambda t: None,
                     "agencia_de": lambda n: "Reuters" if len(n) == 4 else "AP",
                     "enviar": lambda cfg, a, asunto, t, html=None, adjuntos=(): correos.append((a, asunto))}


def test_chequeo_y_aviso_solo_al_cambiar(tmp_path):
    correos = []
    preparar(tmp_path, correos)
    assert salud.chequear(arreglar=False)["ok"]
    salud.S["_g"]["ESTADO"].lotes[0]["fichas"].append({"id": "d", "estado": "error"})
    assert not salud.chequear(arreglar=False)["ok"]
    salud.chequear(arreglar=False)                   # mismo estado: sin correo nuevo
    assert len(correos) == 1 and "necesita atención" in correos[0][1]


def test_informe_semanal(tmp_path):
    preparar(tmp_path, [])
    asunto, texto = salud.informe_semanal()
    assert "2 fichas" in asunto
    assert "sin tocar 1 (50 %)" in texto and "corregidas 1" in texto
    assert salud._destinos_informe() == ["yo@x", "jefa@x"]


def test_tiempo_en_espanol():
    assert salud._tiempo(14) == "unos 14 minutos" and salud._tiempo(112) == "unas 1,9 horas"


def test_consumo_informe_sin_datos():
    consumo.S["_g"] = {"CFG": {"precio_kwh": 0.2}}
    consumo.DATOS["dias"] = {}
    m = consumo.informe()["media"]
    assert m["mb_item"] is None and m["euros_anio"] == 0
