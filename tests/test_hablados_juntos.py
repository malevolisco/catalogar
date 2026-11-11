# -*- coding: utf-8 -*-
"""Varios que hablan en el mismo acto: una sola frase con el acto, los que hablan y los temas, y sin el protocolo
(Adrián, 11/11/2026: «necesito que sea cortito y al pie», «se aplica a todo»). En el COMMENT y en los localizadores."""
from pathlib import Path
from unittest import mock

import actos
import redactor

RAIZ = Path(__file__).resolve().parent.parent
EJEMPLO = ("TOKIO. COMPARECENCIA CONJUNTA DE TO LAM, PRESIDENTE DE VIETNAM, CON SANAE TAKAICHI, PRIMERA MINISTRA DE "
           "JAPON, SOBRE ACUERDOS DE COOPERACION EN CIENCIA, TECNOLOGIA E INTELIGENCIA ARTIFICIAL Y SOBRE EL MAR DE "
           "CHINA MERIDIONAL.")


def test_el_criterio_lo_dice_en_los_dos_ficheros():
    completo = (RAIZ / "reglas_patrones.md").read_text(encoding="utf-8")
    ligero = (RAIZ / "reglas_ligeras.md").read_text(encoding="utf-8")
    assert "VARIOS HABLAN EN EL MISMO ACTO" in completo and EJEMPLO in completo and EJEMPLO in ligero
    assert "nunca una frase por persona" in completo and "protocolo" in completo
    assert "DECLARACIONES DE [quien habla, cargo], EN COMPARECENCIA CONJUNTA CON" not in completo   # la formula vieja


def test_los_localizadores_tambien_los_juntan():
    datos = {"texto": "KYIV, UKRAINE (OCTOBER 12, 2026) (REUTERS - Access all)\n1. (SOUNDBITE) (Ukrainian) PRESIDENT, "
                      "VOLODYMYR ZELENSKIY, SAYING:\n\"We need air defence.\"\n2. (SOUNDBITE) (French) FRENCH PRESIDENT, "
                      "EMMANUEL MACRON, SAYING:\n\"We will help.\"\nSTORY: Leaders met in Kyiv.",
             "agencia": "REUTERS", "numero": "1234", "fecha": "12/10/2026", "headline": "JOINT"}
    respuesta = ("1-2|UCRANIA|KIEV|COMPARECENCIA CONJUNTA DE VOLODIMIR ZELENSKI, PRESIDENTE DE UCRANIA, CON EMMANUEL "
                 "MACRON, PRESIDENTE DE FRANCIA, SOBRE LA DEFENSA AEREA Y SOBRE LA AYUDA DE FRANCIA|\n")
    with mock.patch("redactor.llamar_modelo", return_value=respuesta) as llamada:
        locs, avisos = redactor.localizadores(datos)
    assert "COMPARECENCIA CONJUNTA DE TO LAM" in llamada.call_args.args[0]      # el ajuste va en las instrucciones
    assert avisos == [] and [x["plano"] for x in locs] == ["1-2", ""]           # dos totales en un solo localizador


def test_la_ficha_se_reconoce_como_comparecencia_conjunta():
    assert actos.clase_de_ficha("JAPON COMPARECENCIA CONJUNTA PRESIDENTE VIETNAM", EJEMPLO) == "comparecencia_conjunta"
    assert actos.clase_de_ficha("X", "LUGAR. DECLARACIONES DE A, EN COMPARECENCIA CONJUNTA CON B, SOBRE C.") == \
        "comparecencia_conjunta"                                               # las aprobadas de antes, igual
    assert actos.DESCRIPTOR["comparecencia_conjunta"] == "COMPARECENCIA CONJUNTA"
