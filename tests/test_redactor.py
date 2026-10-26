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


def test_mayuscula_tras_el_pais():
    casos = [
        ("FRANCIA ENFRENTAMIENTOS POLICIA CON MANIFESTANTES EN PLAZA DE LA BASTILLA",
         "Francia enfrentamientos policía con manifestantes en plaza de la Bastilla",
         "Francia Enfrentamientos policía con manifestantes en plaza de la Bastilla"),
        ("REINO UNIDO PROTESTA ANTE PARLAMENTO", "Reino Unido protesta ante Parlamento",
         "Reino Unido Protesta ante Parlamento"),
        ("COREA DEL SUR ELECCIONES PRESIDENCIALES", "Corea del Sur elecciones presidenciales",
         "Corea del Sur Elecciones presidenciales"),
        ("INTERNET ISRAEL PUBLICACION GIDEON SAAR", "Internet Israel publicación Gideon Saar",
         "Internet Israel Publicación Gideon Saar"),
        ("ESPAÑA GOBIERNO PEDRO SANCHEZ", "España Gobierno Pedro Sánchez", "España Gobierno Pedro Sánchez"),
    ]
    for original, normal, esperado in casos:
        assert r.mayuscula_tras_pais(normal, original) == esperado


def test_version_normal_aplica_la_mayuscula():
    campos = {"NAME": "FRANCIA ENFRENTAMIENTOS POLICIA CON MANIFESTANTES", "COMMENT": "PARIS. ENFRENTAMIENTOS.",
              "RESTRICCIONES": "SIN AVISO"}
    propuesta = {"NAME": "Francia enfrentamientos policía con manifestantes", "COMMENT": "París. Enfrentamientos.",
                 "RESTRICCIONES": "Sin aviso"}
    normal, _ = r.version_normal(campos, propuesta)
    assert normal["NAME"] == "Francia Enfrentamientos policía con manifestantes"


def test_name_sin_signos_ni_simbolos():
    """En el titulo nunca van signos: V&A, 50%, A/B, comillas, parentesis... (Adrian, 26/10)."""
    name, quitados = r.limpiar_name("REINO UNIDO EXPOSICION VESTUARIO FREDDIE MERCURY V&A Y DECLARACIONES COMISARIA")
    assert "&" not in name and name == "REINO UNIDO EXPOSICION VESTUARIO FREDDIE MERCURY V Y A Y DECLARACIONES COMISARIA"
    assert quitados == ["&"]
    name, quitados = r.limpiar_name('EEUU SUBIDA 50% ARANCELES "TRUMP" (CHINA) A/B #ELECCIONES.')
    assert name == "EEUU SUBIDA 50 POR CIENTO ARANCELES TRUMP CHINA A B ELECCIONES"
    assert set(quitados) >= {"%", "/", '"', "(", ")", "#", "."}


def test_name_limpio_no_cambia_y_conserva_guiones_y_enie():
    for limpio in ("ESPAÑA RESUMEN PARTIDO GAUFF - BADOSA", "NORUEGA PRINCESA METTE-MARIT", "EEUU ARCHIVO 11S"):
        assert r.limpiar_name(limpio) == (limpio, [])


def test_redactar_limpia_el_name_y_avisa():
    salida = ("NAME: REINO UNIDO EXPOSICION VESTUARIO FREDDIE MERCURY V&A\n"
              "COMMENT: LONDRES. IMAGENES DE LA EXPOSICION.\nRESTRICCIONES: SIN AVISO")
    with mock.patch.object(r, "llamar_modelo", return_value=salida), mock.patch.object(r, "registrar_uso"):
        campos_, avisos, _ = r.redactar({"numero": "0001", "texto": "LONDON. SHOWS EXHIBITION."}, acortar=False)
    assert "&" not in campos_["NAME"] and campos_["NAME"].endswith("V Y A")
    assert any("quitados signos" in a for a in avisos)


def test_name_conserva_decimales_y_la_palabra_coma():
    assert r.limpiar_name("JAPON TERREMOTO MAGNITUD 6,5") == ("JAPON TERREMOTO MAGNITUD 6,5", [])
    assert r.limpiar_name("PACIENTE EN ESTADO DE COMA") == ("PACIENTE EN ESTADO DE COMA", [])
    assert r.limpiar_name("EEUU 10.000 MANIFESTANTES")[0] == "EEUU 10000 MANIFESTANTES"     # miles, no decimal
    assert r.limpiar_name("EEUU 10,000 MANIFESTANTES")[0] == "EEUU 10000 MANIFESTANTES"
    assert r.limpiar_name("EEUU DESFILE 26.10.2026")[0] == "EEUU DESFILE 26 10 2026"        # nunca 26,10,2026


def test_quien_distribuye_no_se_dice_salvo_el_ejercito():
    """Nunca DISTRIBUIDO POR AP (ni nadie): videoaficionado o redes sociales; el militar si (Adrian, 26/10)."""
    c = ("LOS ANGELES. VIDEO EN VERTICAL DISTRIBUIDO POR AP. ACTUACION DE CHAPPELL ROAN, CANTANTE, JUNTO A "
         "CHRISTINA AGUILERA, CANTANTE, EN UN CONCIERTO BENEFICO.")
    assert r.quitar_distribuidor(c) == (c.replace(" DISTRIBUIDO POR AP", ""), True)
    assert r.quitar_distribuidor("GAZA. VIDEO DISTRIBUIDO POR REUTERS. DAÑOS EN EL HOSPITAL.") == \
        ("GAZA. DAÑOS EN EL HOSPITAL.", True)
    militar = "KIEV. MATERIAL MILITAR DISTRIBUIDO POR EL EJERCITO DE UCRANIA. ATAQUE CON DRONES."
    assert r.quitar_distribuidor(militar) == (militar, False)



def test_distribuido_por_dentro_de_otra_frase_no_se_toca():
    """Solo la formula de origen: ALIMENTO DISTRIBUIDO POR LA ONU es contenido."""
    for c in ("JUBA. ALIMENTO DISTRIBUIDO POR LA ONU A REFUGIADOS. FIN.",
              "GAZA. VIDEO DISTRIBUIDO POR AP, MUESTRA DAÑOS. FIN."):
        assert r.quitar_distribuidor(c) == (c, False)


def test_redactar_avisa_si_queda_un_distribuidor():
    salida = ("NAME: PALESTINA DAÑOS HOSPITAL GAZA\n"
              "COMMENT: GAZA. VIDEO DISTRIBUIDO POR AP, MUESTRA DAÑOS EN EL HOSPITAL.\nRESTRICCIONES: SIN AVISO")
    with mock.patch.object(r, "llamar_modelo", return_value=salida), mock.patch.object(r, "registrar_uso"):
        _, avisos, _ = r.redactar({"numero": "0002", "texto": "GAZA. SHOWS DAMAGE."}, acortar=False)
    assert any("DISTRIBUIDO POR" in a and "no se dice" in a for a in avisos)
