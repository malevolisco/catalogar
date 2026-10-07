# -*- coding: utf-8 -*-
"""Lo que hace el redactor sin llamar al modelo: limpiar, validar, minusculas y las salvaguardas."""
from unittest import mock

import redactor as r


def campos(name="EEUU ARCHIVO DENNIS HASTERT", comment=None, restr="SIN AVISO"):
    comment = comment or ("WASHINGTON. IMAGENES DE ARCHIVO DE DENNIS HASTERT, EXPRESIDENTE DE LA CAMARA DE "
                          "REPRESENTANTES DE EEUU, DURANTE SU MANDATO. INCLUYE SALUDO A BILL CLINTON.")
    return {"ENVIO": "", "NAME": name, "COMMENT": comment, "RESTRICCIONES": restr}


def test_normalizar_quita_tildes_y_separadores_de_miles():
    assert r.normalizar("Camión con 360.000 pesos") == "CAMION CON 360000 PESOS"
    assert "Ñ" in r.normalizar("españa")


def test_validar_ficha_buena_sin_avisos_de_forma():
    avisos = r.validar(campos())
    assert not [a for a in avisos if "dos puntos" in a or "abreviada" in a]


def test_validar_avisa_de_dos_puntos_y_abreviaturas():
    c = campos(comment="VARIAS LOCALIZACIONES. DECLARACIONES: AYUDA. INCLUYE SALUDO A BILL CLINTON, PTE. EEUU, "
                       "Y A ALEJANDRO TOLEDO, PRESIDENTE DE PERU, EN UN ACTO EN LIMA.")
    avisos = r.validar(c)
    assert any("dos puntos" in a for a in avisos)
    assert any("abreviada (PTE.)" in a for a in avisos)


def test_abreviatura_no_salta_con_palabras_normales():
    assert not r.ABREVIATURA_RE.search("DIRECTOR DE LA CIA. INCLUYE PLANOS DEL MIN DE JUEGO.")


def test_acortar_descarta_versiones_con_abreviaturas_o_dos_puntos():
    largo = "WASHINGTON. " + "IMAGENES DE ARCHIVO DE DENNIS HASTERT, EXPRESIDENTE DE LA CAMARA. " * 8
    with mock.patch.object(r, "llamar_modelo", return_value="WASHINGTON. DECLARACIONES: AYUDA, PTE. EEUU."):
        assert r.acortar_comment(largo) == largo


def test_parsear_lee_las_lineas_y_las_normales():
    salida = ("NAME: EEUU ARCHIVO\nCOMMENT: WASHINGTON. IMAGENES.\nRESTRICCIONES: SIN AVISO\n"
              "NAME_NORMAL: EEUU archivo\nCOMMENT_NORMAL: Washington. Imágenes.\nRESTRICCIONES_NORMAL: Sin aviso")
    c = r.parsear(salida)
    assert c["NAME"] == "EEUU ARCHIVO" and c["COMMENT_NORMAL"] == "Washington. Imágenes."
    assert {"NAME", "COMMENT", "RESTRICCIONES"} <= c["_presentes"]


def test_fusionar_normal_no_cambia_palabras_ni_signos():
    original = "WASHINGTON. IMAGENES DE BILL CLINTON, PRESIDENTE DE EEUU."
    texto, _ = r.fusionar_normal(original, "Washington. Imágenes de Bill Clinton, presidente de EEUU.")
    assert texto == "Washington. Imágenes de Bill Clinton, presidente de EEUU."
    # el modelo se inventa una palabra: no entra, se pasa a minuscula la original
    texto, sueltas = r.fusionar_normal(original, "Washington. Fotos de Bill Clinton, pte. de EEUU.")
    assert "pte." not in texto and "Fotos" not in texto and sueltas >= 1


def test_openai_convierte_imagenes():
    bloques = r._bloques_openai([{"type": "text", "text": "hola"},
                                 {"type": "image", "source": {"media_type": "image/jpeg", "data": "AAA"}}])
    assert bloques[1] == {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}}


def test_claude_no_instalado_se_reconoce():
    assert r.NO_INSTALADO_RE.search('"claude" no se reconoce como un comando interno o externo,')
    assert r.NO_INSTALADO_RE.search("/bin/sh: 1: claude: not found")
    assert not r.NO_INSTALADO_RE.search("Error: File not found: foto.jpg")
