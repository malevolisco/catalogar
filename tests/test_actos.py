# -*- coding: utf-8 -*-
"""Que clase de material hablado es, leido del texto de la agencia: rueda de prensa, entrevista, encuesta y, si el
texto no lo dice, por quien habla en cada SOUNDBITE (testimonios, encuesta o declaraciones)."""
import actos
import situaciones


def _ficha(lineas, headline="", slug=""):
    texto = "VIDEO SHOWS: KHAN YOUNIS, GAZA STRIP (NOVEMBER 2, 2026) (REUTERS - ACCESS ALL)\n" + "\n".join(
        f"{i}. {linea}" for i, linea in enumerate(lineas, 1)) + "\nSTORY: Something happened."
    return {"texto": texto, "headline": headline, "slug": slug}


def test_ciudadanos_que_cuentan_lo_vivido_son_testimonios():
    f = _ficha(["VARIOUS OF DESTROYED HOUSES",
                "(SOUNDBITE) (Arabic) RESIDENT, MOHAMMED ALI, SAYING:",
                "SOUNDBITE (Arabic) MOTHER OF VICTIM, UM AHMED, SAYING:"])
    assert actos.acto_por_texto(f)[0] == "testimonios"


def test_formato_de_ap_y_de_ebu():
    ap = _ficha(["SOUNDBITE (Spanish) Maria Lopez, flood survivor: \"We lost everything.\""])
    ebu = _ficha(["SOT Ali Hassan, displaced: \"We have nowhere to go.\""])
    assert actos.acto_por_texto(ap)[0] == "testimonios"
    assert actos.acto_por_texto(ebu)[0] == "testimonios"


def test_alguien_con_cargo_son_declaraciones_aunque_hablen_vecinos():
    f = _ficha(["SOUNDBITE (English) U.S. SECRETARY OF STATE MARCO RUBIO, SAYING:",
                "SOUNDBITE (English) RESIDENT JOHN SMITH, SAYING:"])
    clase, evidencia = actos.acto_por_texto(f)
    assert clase == "declaraciones" and "RUBIO" in evidencia


def test_cargo_con_palabra_de_ciudadano_no_es_testimonio():
    f = _ficha(["SOUNDBITE (English) UN RESIDENT COORDINATOR, JANE DOE, SAYING:",
                "SOUNDBITE (English) LAWYER FOR THE VICTIMS, PAUL LIM, SAYING:"])
    assert actos.acto_por_texto(f)[0] == "declaraciones"


def test_cuatro_ciudadanos_opinando_son_encuesta_y_vox_pop_tambien():
    f = _ficha([f"SOUNDBITE (English) VOTER {n}, SAYING:" for n in ("ANNA", "BEN", "CARL", "DORA")])
    assert actos.acto_por_texto(f)[0] == "encuesta"
    assert actos.acto_por_texto(_ficha(["VOX POP (English) VARIOUS PEOPLE IN THE STREET"]))[0] == "encuesta"
    dos = _ficha(["SOUNDBITE (English) VOTER ANNA, SAYING:", "SOUNDBITE (English) VOTER BEN, SAYING:"])
    assert actos.acto_por_texto(dos) == (None, "")          # dos opiniones sueltas: no es encuesta


def test_lo_que_ya_se_detectaba_sigue_mandando():
    rdp = _ficha(["NEWS CONFERENCE", "SOUNDBITE (English) RESIDENT JOHN SMITH, SAYING:"])
    assert actos.acto_por_texto(rdp)[0] == "rueda_de_prensa"
    entrevista = _ficha(["SOUNDBITE (English) MINISTER JOHN DOE TOLD AP:"])
    assert actos.acto_por_texto(entrevista)[0] == "entrevista"
    titular = _ficha(["SOUNDBITE (English) JOHN DOE, CEO, SAYING:"], headline="REUTERS INTERVIEW-CEO says")
    assert actos.acto_por_texto(titular)[0] == "entrevista"
    assert actos.acto_por_texto(_ficha(["WIDE OF CITY", "TRAFFIC"]))[0] == "recursos"


def test_sin_saber_quien_habla_no_se_decide():
    assert actos.acto_por_texto(_ficha(["SOUNDBITE: \"We will win.\""])) == (None, "")


def test_aviso_si_el_comment_no_dice_que_es_lo_hablado():
    ficha = {"acto": {"clase": "testimonios", "evidencia": "RESIDENT"}}
    solo_nombres = {"NAME": "GAZA BOMBARDEOS KHAN YUNIS", "COMMENT": "JAN YUNIS. DAÑOS. MOHAMMED ALI, VECINO."}
    assert any("TESTIMONIOS delante de quien habla" in a for a in actos.comparar(ficha, solo_nombres))
    bien = {"NAME": "GAZA BOMBARDEOS KHAN YUNIS", "COMMENT": "JAN YUNIS. DAÑOS. INCLUYE TESTIMONIOS DE VECINOS."}
    assert actos.comparar(ficha, bien) == []
    recursos = {"acto": {"clase": "recursos", "evidencia": "ningun SOUNDBITE"}}
    assert actos.comparar(recursos, {"NAME": "X", "COMMENT": "LUGAR. RECURSOS DE X."}) == []


def test_la_pista_pide_ponerlo_en_el_comment():
    f = _ficha(["SOUNDBITE (Arabic) RESIDENT, MOHAMMED ALI, SAYING:"])
    situaciones.detectar(f)
    assert "TESTIMONIOS" in f["pistas"] and "delante de quien habla" in f["pistas"]


def test_las_clases_de_imagen_no_cambian():
    assert "testimonios" not in actos.DESCRIPTOR and "encuesta" not in actos.DESCRIPTOR
