# -*- coding: utf-8 -*-
"""El buzon solo saca los numeros de la peticion: nada de la firma, del logo, de enlaces ni de telefonos."""
import buzon

FIRMA_RTVE = """Silvia Domínguez Mancha.
Unidad Gestión de Archivo de Informativos TVE
Archivo RTVE.
[cid:image001.png@01DC3F5A.9631E0B0]
Radiotelevisión Española
Alcalde Sainz de Baranda, 92
28007 Madrid - Madrid
913469736 - 53590
Mi jornada laboral puede no coincidir con la tuya.
www.rtve.es/noticias/20261016/1234567
Corporación RTVE - www.rtve.es
AVISO LEGAL. Este mensaje está dirigido solo a su destinatario y es confidencial."""


def numeros(cuerpo, nombre="Silvia Domínguez Mancha"):
    return [n for n, _ in buzon._trabajos(buzon._sin_firma(cuerpo, nombre))]


def test_firma_de_rtve_no_da_numeros():
    assert numeros("Hola, ¿me pasas el 7660?\n\n" + FIRMA_RTVE) == ["7660"]


def test_firma_sin_nombre_ni_despedida():
    # aunque no se reconozca el nombre, el logo, el telefono y los enlaces no cuentan
    cuerpo = "7660 y 4681323\n[cid:image001.png@01DC3F5A.9631E0B0]\nTel: 91 346 9736\nhttps://x.rtve.es/a/5555"
    assert numeros(cuerpo, nombre="") == ["7660", "4681323"]


def test_despedidas_cortan():
    for despedida in ("Un saludo", "Saludos,", "Gracias!", "Muchas gracias.", "Atentamente"):
        assert numeros(f"Me haces el 0624 y el 0611\n{despedida}\nJuan\n9876", nombre="") == ["0624", "0611"]


def test_numeros_en_la_misma_linea_que_gracias_se_quedan():
    assert numeros("Gracias, ¿me pasas el 0624?", nombre="") == ["0624"]


def test_lista_varias_lineas():
    assert numeros("Buenos días:\n0624\n0611 del 25/09/2026\nAP 4681323\n\nUn saludo\n" + FIRMA_RTVE) == \
        ["0624", "0611", "4681323"]


def test_saludo_al_principio_no_corta():
    assert numeros("Saludos,\nme pasas el 0624 y el 0611\nGracias\n9999", nombre="") == ["0624", "0611"]
