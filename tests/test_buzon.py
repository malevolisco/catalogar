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


CSV_DESCONOCIDO = ("Name,Catalogador,Comments,Created\n"
                   '"0624 USA-X: ALGO",I99999,,06/10/2026 10:00\n'
                   '"0625 USA-Y: OTRO",I23785,,06/10/2026 10:00\n'
                   '"0626 USA-Z: MAS",,,06/10/2026 10:00\n').encode()


def test_por_correo_un_catalogador_que_no_se_conoce_va_a_quien_lo_manda(monkeypatch):
    import listas
    monkeypatch.setitem(listas.CFG, "catalogadores", {"i23785": "javier"})
    # desde la pagina, como siempre: la fila de un catalogador desconocido se salta
    envios, descartes, _ = listas.leer_lista("lista.csv", CSV_DESCONOCIDO)
    assert [e["numero"] for e in envios] == ["0625", "0626"] and descartes[0]["motivo"] == "ya la tiene I99999"
    # por correo se hace, sin dueño (va en la respuesta a quien lo mando) y se dice en la nota
    envios, descartes, _ = listas.leer_lista("lista.csv", CSV_DESCONOCIDO, desconocidos_al_remitente=True)
    assert [(e["numero"], e["para"]) for e in envios] == [("0624", ""), ("0625", "javier"), ("0626", "")]
    assert envios[0]["catalogador_desconocido"] == "I99999" and "catalogador_desconocido" not in envios[2]
    assert descartes == []
    nota = buzon.nota_adjuntos({"adjuntos": [{"nombre": "lista.csv", "numeros": ["0624", "0625", "0626"],
                                               "envios": envios, "descartes": [], "aviso": "", "error": ""}]})
    assert "1 de catalogador que no esta en la agenda (I99999): se incluyen en esta respuesta" in nota


def test_por_correo_una_lista_toda_de_desconocidos_no_se_queda_sin_contestar(monkeypatch):
    import listas
    monkeypatch.setitem(listas.CFG, "catalogadores", {})
    solo = ("Name,Catalogador\n" '"0624 USA-X: ALGO",I99999\n' '"0625 USA-Y: OTRO",I88888\n').encode()
    try:
        listas.leer_lista("lista.csv", solo)
        assert False, "desde la pagina sigue avisando de que no se sabe quien es"
    except listas.ListaError as e:
        assert "no se quien es" in str(e)
    envios, _, _ = listas.leer_lista("lista.csv", solo, desconocidos_al_remitente=True)
    assert [e["para"] for e in envios] == ["", ""]


def test_el_adjunto_del_correo_usa_los_desconocidos_al_remitente(monkeypatch):
    import email
    import listas
    monkeypatch.setitem(listas.CFG, "catalogadores", {})
    msg = email.message.EmailMessage()
    msg.add_attachment(CSV_DESCONOCIDO, maintype="text", subtype="csv", filename="lista.csv")
    parte = next(p for p in msg.iter_attachments())
    adjunto = buzon._de_adjunto(parte)
    assert adjunto["numeros"] == ["0624", "0625", "0626"] and adjunto["error"] == ""
