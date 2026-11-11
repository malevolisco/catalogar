# -*- coding: utf-8 -*-
"""Localizadores: los envios que en la lista piden "TRATAR AGENCIA CON LOCALIZADORES" llevan, ademas de la ficha, los
localizadores sacados del shotlist. Solo se ven en el panel: ni correo, ni MediaCentral."""
import threading
import time
from unittest import mock

import correo
import redactor
import servidor
from listas import leer_lista
from test_colas import lote, worker  # noqa: F401  (fixture)

SHOTLIST = ("VIDEO SHOWS: KIEV, UKRAINE (OCTOBER 12, 2026) (REUTERS - ACCESS ALL)\n"
            "1. AERIAL OF DAMAGED BUILDING\n2. JOURNALISTS FILMING\n"
            "3. (SOUNDBITE) (Ukrainian) MAYOR VITALI KLITSCHKO, SAYING:\nSTORY: Russia attacked Kyiv overnight.")


def test_la_lista_marca_los_envios_con_localizadores():
    csv = ('Name,Catalogador,Comments,Created\n'
           '"0707 FRANCE-PROTEST: Police",,"TRATAR AGENCIA CON LOCALIZADORES",""\n'
           '"0708 FRANCE-X: Y",,"Tratar agencias con localizadores",""\n'
           '"0709 FRANCE-Z: W",,"TRATAR AGENCIAS",""\n')
    envios, _, _ = leer_lista("Search Results.csv", csv.encode("utf-8"))
    assert {e["numero"]: e["localizadores"] for e in envios} == {"0707": True, "0708": True, "0709": False}
    assert all(e["texto"] == "" for e in envios)              # la nota no es texto de agencia: se va a la web


# un trozo del 5455 de Reuters (WRAP de las elecciones de medio mandato), como sale del XML
WRAP = """WRAP: Last-minute appeals and steady turnout as US voters cast ballots

VIDEO SHOWS: WRAP NUMBER 3 OF ELECTION DAY ACROSS US STATES SHOWING MIDDAY VOTING AND SOUNDBITES FROM VOTERS

GRIMES, IOWA, UNITED STATES (NOVEMBER 3, 2026) (REUTERS - Access all)

1. VARIOUS OF PEOPLE VOTING

PORTLAND, MAINE, UNITED STATES (NOVEMBER 3, 2026) (REUTERS - Access all)

7. (SOUNDBITE) (English) DEMOCRAT CANDIDATE FOR US SENATE, TROY JACKSON, SAYING:

"There is the real opportunity across the country for everyday people."

Michigan Senate candidate Abdul El-Sayed said Tuesday (November 3) he is focused on his agenda.

SOUTHFIELD, MICHIGAN, UNITED STATES (NOVEMBER 3, 2026) (REUTERS - Access All)

11. (SOUNDBITE) (English) CHLOE ANDERSON, 19, MICHIGAN VOTER, SAYING:

    "It's a waste of money."

12. (SOUNDBITE) (English) RACHEL BROWN, 24, MICHIGAN VOTER, SAYING:

    "I feel like giving out help is great."

13. JOURNALISTS FILMING

IN SPACE (RELEASED NOVEMBER 3, 2026) (NASA / ANIL MENON - Editorial Use Only / Must on-screen courtesy NASA/Anil Menon.) (ORIGINALLY SHOT IN PORTRAIT)

30. (SOUNDBITE) (English) NASA ASTRONAUT, ANIL MENON, SAYING:

    "Welcome to the space station, where today we have the privilege of being able to vote."

STORY: See shotlist for story details.
"""
DATOS = {"texto": WRAP, "agencia": "REUTERS", "numero": "5455", "fecha": "03/11/2026", "headline": "WRAP"}


def test_el_codigo_parte_el_shotlist_en_planos_con_su_dateline():
    planos = redactor.shotlist_planos(WRAP)
    assert [p["n"] for p in planos] == [1, 7, 11, 12, 13, 30]
    por = {p["n"]: p for p in planos}
    assert (por[1]["lugar"], por[1]["fecha"], por[1]["restriccion"]) == ("GRIMES, IOWA, UNITED STATES", "03/11/2026", "")
    assert por[7]["soundbite"] and not por[7]["ciudadano"] and por[7]["habla"].endswith("TROY JACKSON")
    assert por[7]["dice"].startswith('"There is')                    # la cita, no el parrafo del bloque siguiente
    assert por[11]["ciudadano"] and por[12]["ciudadano"]
    assert por[30]["lugar"] == "IN SPACE" and "Must on-screen courtesy NASA/Anil Menon" in por[30]["restriccion"]
    assert "PORTRAIT" not in por[30]["restriccion"] and "ACCESS" not in por[1]["restriccion"].upper()
    # las declaraciones de votantes seguidas van juntas
    assert [i["id"] for i in redactor._items_localizadores(planos)] == ["1", "7", "11-12", "13", "30"]


RESPUESTA_WRAP = """```
1. | EEUU | GRIMES (IOWA) | VOTANTES EN UN COLEGIO ELECTORAL |
7|EEUU|PORTLAND (MAINE)|DECLARACIONES DE TROY JACKSON, CANDIDATO DEMOCRATA AL SENADO, SOBRE LA PARTICIPACION. DICE QUE ES UNA OPORTUNIDAD PARA LA GENTE CORRIENTE||
PLANOS 11-12|EEUU|SOUTHFIELD (MICHIGAN)|DECLARACIONES DE VOTANTES QUE DICEN QUE LAS AYUDAS SON UN DESPILFARRO|
13|NO
30||ESPACIO EXTERIOR|DECLARACIONES DE ANIL MENON, ASTRONAUTA DE LA NASA, SOBRE SU VOTO DESDE LA ESTACION ESPACIAL|ROTULAR CORTESIA DE ''NASA/ANIL MENON''
```"""


def test_reglas_de_adrian_y_el_programa_quita_lo_que_no_cambia():
    with mock.patch("redactor.llamar_modelo", return_value=RESPUESTA_WRAP) as llamada:
        locs, avisos = redactor.localizadores(DATOS)
    assert llamada.call_count == 1 and avisos == []
    system, enviado = llamada.call_args.args[0], llamada.call_args.args[1]
    assert "ID 11-12" in enviado and "GRUPO DE 2 SEGUIDOS" in enviado and "DICEN:" in enviado
    assert "SOUTHFIELD, MICHIGAN, UNITED STATES (NOVEMBER 3, 2026) (REUTERS - Access All)" in enviado   # la dateline
    assert "FORMATO DE CADA LOCALIZADOR" in system and "COMPROBACIÓN FINAL" in system   # reglas_localizadores.md
    assert "AJUSTES DE ADRIAN" in system and "DECLARACIONES DE VOTANTES QUE" in system
    assert "=== CRITERIO DE CATALOGACION" in system and "RESTRICCIONES" in system        # todo el criterio normal
    assert locs == [
        {"plano": "1", "texto": "EEUU. GRIMES (IOWA). 3 DE NOVIEMBRE DE 2026. VOTANTES EN UN COLEGIO ELECTORAL."},
        {"plano": "7", "texto": "PORTLAND (MAINE). DECLARACIONES DE TROY JACKSON, CANDIDATO DEMOCRATA AL SENADO, SOBRE "
                                "LA PARTICIPACION. DICE QUE ES UNA OPORTUNIDAD PARA LA GENTE CORRIENTE."},
        {"plano": "11-12", "texto": "SOUTHFIELD (MICHIGAN). DECLARACIONES DE VOTANTES QUE DICEN QUE LAS AYUDAS SON UN "
                                    "DESPILFARRO."},
        {"plano": "30", "texto": "ESPACIO EXTERIOR. DECLARACIONES DE ANIL MENON, ASTRONAUTA DE LA NASA, SOBRE SU VOTO "
                                 "DESDE LA ESTACION ESPACIAL. ROTULAR CORTESIA DE ''NASA/ANIL MENON''.",
         "restriccion": "ROTULAR CORTESIA DE ''NASA/ANIL MENON''."},
        {"plano": "", "pie": True, "texto": "RESTRICCIONES: ROTULAR CORTESIA DE ''NASA/ANIL MENON''."},
    ]


def test_el_modelo_puede_juntar_planos_seguidos():
    respuesta = ("1-7|EEUU|GRIMES (IOWA)|VOTANTES Y DECLARACIONES DE TROY JACKSON, CANDIDATO, SOBRE EL VOTO|\n"
                 "11-12|EEUU|SOUTHFIELD (MICHIGAN)|DECLARACIONES DE VOTANTES QUE DICEN QUE VOTAN TEMPRANO|\n"
                 "13-30|EEUU|SOUTHFIELD (MICHIGAN)|PERIODISTAS Y DECLARACIONES DE ANIL MENON, ASTRONAUTA|\n"
                 "7-12|EEUU|X|SOLAPADO: SE DESCARTA|\n")
    with mock.patch("redactor.llamar_modelo", return_value=respuesta) as llamada:
        locs, avisos = redactor.localizadores(DATOS)
    assert llamada.call_count == 1 and avisos == []
    assert [x["plano"] for x in locs] == ["1-7", "11-12", "13-30", ""]
    assert locs[2]["texto"] == "PERIODISTAS Y DECLARACIONES DE ANIL MENON, ASTRONAUTA."    # nada cambia: solo el texto
    assert locs[-1]["texto"] == "RESTRICCIONES: SIN AVISO."


def test_pais_ciudad_y_fecha_solo_cuando_cambian():
    lineas = [
        {"plano": "1", "pais": "UCRANIA", "ciudad": "KIEV", "fecha": "12/10/2026", "texto": "DAÑOS DE UN ATAQUE",
         "restriccion": ""},
        {"plano": "2", "pais": "UCRANIA", "ciudad": "KIEV", "fecha": "13/10/2026", "texto": "FUNERAL", "restriccion": ""},
        {"plano": "3", "pais": "UCRANIA", "ciudad": "JARKOV", "fecha": "13/10/2026", "texto": "RESCATE",
         "restriccion": "NO ARCHIVAR"},
        {"plano": "4", "pais": "", "ciudad": "INTERNET", "fecha": "RECIENTE", "texto": "PUBLICACION EN REDES SOCIALES",
         "restriccion": "SIN AVISO"},
        {"plano": "5", "pais": "UCRANIA", "ciudad": "JARKOV", "fecha": "13/10/2026", "texto": "BOMBEROS", "restriccion": ""},
    ]
    assert [x["texto"] for x in redactor._componer_lineas(lineas)] == [
        "UCRANIA. KIEV. 12 DE OCTUBRE DE 2026. DAÑOS DE UN ATAQUE.",
        "13 DE OCTUBRE DE 2026. FUNERAL.",
        "JARKOV. RESCATE. NO ARCHIVAR.",
        "INTERNET. FECHA RECIENTE SIN ESPECIFICAR. PUBLICACION EN REDES SOCIALES.",   # sin pais, y nunca SIN AVISO
        "UCRANIA. JARKOV. 13 DE OCTUBRE DE 2026. BOMBEROS.",                           # tras INTERNET, el pais otra vez
        "RESTRICCIONES: NO ARCHIVAR.",
    ]
    assert redactor._componer_lineas([]) == []                                    # sin localizadores, tampoco pie


def test_una_dateline_recent_es_fecha_reciente():
    texto = ("UNKNOWN LOCATION (RECENT) (SOCIAL MEDIA - No resale)\n1. VARIOUS OF DRONE STRIKE\n"
             "KYIV, UKRAINE (OCTOBER 12, 2026) (REUTERS - Access all)\n2. DAMAGED BUILDING")
    planos = redactor.shotlist_planos(texto)
    assert [(p["n"], p["fecha"]) for p in planos] == [(1, "RECIENTE"), (2, "12/10/2026")]
    assert redactor.fecha_en_letra("RECIENTE") == "FECHA RECIENTE SIN ESPECIFICAR"


def test_los_que_faltan_se_piden_otra_vez_y_si_siguen_faltando_se_avisa(monkeypatch, tmp_path):
    monkeypatch.setattr(redactor, "BASE_DIR", tmp_path)
    primera = "1|EEUU|GRIMES (IOWA)|VOTANTES|\n7|NO\n13|NO\n"            # 7 es una declaracion: NO no vale
    segunda = "7|EEUU|PORTLAND (MAINE)|DECLARACIONES DE TROY JACKSON, CANDIDATO, SOBRE EL VOTO|\n"
    with mock.patch("redactor.llamar_modelo", side_effect=[primera, segunda]) as llamada:
        locs, avisos = redactor.localizadores(DATOS)
    assert llamada.call_count == 2
    reenvio = llamada.call_args_list[1].args[1]
    assert "ID 7 " in reenvio and "ID 11-12" in reenvio and "ID 30" in reenvio and "ID 1 " not in reenvio
    assert [x["plano"] for x in locs] == ["1", "7", ""]
    assert avisos == ["Faltan los localizadores de los planos 11-12, 30: el servicio de redacción no los ha devuelto. "
                      "Ponlos a mano o vuelve a subir la lista."]
    guardada = (tmp_path / "debug" / "localizadores_5455.txt").read_text(encoding="utf-8")
    assert "7|NO" in guardada and "TROY JACKSON" in guardada       # la respuesta, para poder ver que paso


def test_sin_planos_numerados_se_usa_el_metodo_libre():
    texto = ("U.S. MIDTERMS: SCENES ACROSS AMERICA AS THE NATION GOES TO THE POLLS\nDetails\nDuration 00:03:12\n"
             "ANCHORAGE, ALASKA (NOVEMBER 3, 2026) (REUTERS - ACCESS ALL)\nVOTERS QUEUING OUTSIDE POLLING STATION\n"
             "(SOUNDBITE) (English) VOTER, JANE DOE, SAYING: We came early.\nSTORY: Americans voted on Tuesday.")
    respuesta = ("|EEUU|ANCHORAGE (ALASKA)|03/11/2026|VOTANTES HACIENDO COLA ANTE UN COLEGIO ELECTORAL|\n"
                 "-|EEUU|ANCHORAGE (ALASKA)|03/11/2026|DECLARACIONES DE UNA VOTANTE QUE DICE QUE HA VENIDO PRONTO|SIN AVISO\n")
    with mock.patch("redactor.llamar_modelo", return_value=respuesta) as llamada:
        locs, avisos = redactor.localizadores({"texto": texto, "fecha": "03/11/2026"})
    assert "VOTERS QUEUING" in llamada.call_args.args[1] and avisos == []
    assert "FORMATO DE CADA LOCALIZADOR" in llamada.call_args.args[0]
    assert [x["texto"] for x in locs] == [
        "EEUU. ANCHORAGE (ALASKA). 3 DE NOVIEMBRE DE 2026. VOTANTES HACIENDO COLA ANTE UN COLEGIO ELECTORAL.",
        "DECLARACIONES DE UNA VOTANTE QUE DICE QUE HA VENIDO PRONTO.",
        "RESTRICCIONES: SIN AVISO."]
    with mock.patch("redactor.llamar_modelo") as llamada:
        assert redactor.localizadores({"texto": "Un titular suelto."}) == ([], [])
    assert not llamada.called                                        # sin texto de agencia no se gasta una llamada


def test_la_fecha_va_con_el_mes_en_letra():
    assert redactor.fecha_en_letra("12/10/2026") == "12 DE OCTUBRE DE 2026"
    assert redactor.fecha_en_letra("03/11/2026") == "3 DE NOVIEMBRE DE 2026"
    assert redactor.fecha_en_letra("") == "" and redactor.fecha_en_letra("32/1/2026") == ""


def _arrancar(w, ficha_extra, monkeypatch, sacar):
    monkeypatch.setattr(servidor.ESTADO, "lotes", [lote("L", ["0707"])])
    ficha = servidor.ESTADO.lotes[0]["fichas"][0]
    ficha.update(ficha_extra)
    monkeypatch.setattr(servidor, "sacar_localizadores", sacar)
    threading.Thread(target=w.run, daemon=True).start()
    t0 = time.time()
    while ficha["estado"] != "hecha" and time.time() - t0 < 10:
        time.sleep(0.05)
    w.parar = True
    return ficha


def test_el_servidor_los_guarda_solo_si_la_lista_los_pide(worker, monkeypatch):  # noqa: F811
    w, _, _ = worker
    locs = [{"plano": "1", "texto": "KIEV. DOCE DE OCTUBRE DE 2026. VISTA AEREA."}]
    ficha = _arrancar(w, {"con_localizadores": True}, monkeypatch, lambda datos, timeout=None: (locs, ["AVISO X"]))
    assert ficha["estado"] == "hecha" and ficha["localizadores"] == locs
    assert ficha["localizadores_v"] == redactor.LOCALIZADORES_VERSION and "AVISO X" in ficha["avisos"]


def test_sin_peticion_no_se_piden(worker, monkeypatch):  # noqa: F811
    w, _, _ = worker
    pedidos = []
    ficha = _arrancar(w, {}, monkeypatch, lambda datos, timeout=None: pedidos.append(1) or ([], []))
    assert ficha["estado"] == "hecha" and "localizadores" not in ficha and not pedidos


def test_si_fallan_la_ficha_queda_hecha_con_un_aviso(worker, monkeypatch):  # noqa: F811
    w, _, _ = worker

    def falla(datos, timeout=None):
        raise RuntimeError("sin respuesta")

    ficha = _arrancar(w, {"con_localizadores": True}, monkeypatch, falla)
    assert ficha["estado"] == "hecha" and ficha["NAME"] == "N" and ficha["localizadores_fallo"]
    aviso = next(a for a in ficha["avisos"] if "localizadores" in a)
    assert "RuntimeError" not in aviso and "vuelve a subir la lista" in aviso      # sin nombres internos, y que hacer


def test_no_van_ni_al_correo_ni_a_mediacentral():
    ficha = {"numero": "0707", "estado": "hecha", "ENVIO": "0707", "NAME": "UCRANIA ATAQUE KIEV", "COMMENT": "KIEV. DAÑOS.",
             "RESTRICCIONES": "SIN AVISO", "etiqueta": "0707",
             "localizadores": [{"plano": "1", "texto": "LOCALIZADOR SECRETO."}]}
    assert "localizadores" not in servidor._ficha_para_fuera(ficha, {"id": "L"})
    assert "LOCALIZADOR SECRETO" not in correo.cuerpo_fichas([ficha])
    assert "LOCALIZADOR SECRETO" not in correo.cuerpo_fichas_html([ficha])


def test_el_panel_tiene_la_seccion_y_el_enlace():
    from pathlib import Path
    panel = (Path(__file__).resolve().parent.parent / "panel" / "index.html").read_text(encoding="utf-8")
    assert 'data-vista="localizadores"' in panel and 'id="vista-localizadores"' in panel
    assert "#localizadores/" in panel and "data-locs" in panel


def test_una_ficha_ya_hecha_sin_nada_que_marcar_se_copia_sin_volver_a_pedirlos(monkeypatch):
    w = servidor.Worker()
    hecha = {"numero": "0707", "estado": "hecha", "NAME": "N", "COMMENT": "C", "localizadores": [],
             "localizadores_v": redactor.LOCALIZADORES_VERSION}
    monkeypatch.setattr(servidor.ESTADO, "ya_hecha", lambda *a, **k: ({"creado": "06/11/2026"}, hecha))
    monkeypatch.setattr(servidor.ESTADO, "guardar", lambda: None)
    ficha = {"numero": "0707", "etiqueta": "0707", "con_localizadores": True}
    assert w.volcar_hecha({"id": "L"}, ficha) and ficha["localizadores"] == []
    # los sacados con la version de antes (sin version) no se copian: al subir otra vez la lista se rehacen
    vieja = {"numero": "0707", "estado": "hecha", "NAME": "N", "COMMENT": "C", "localizadores": []}
    monkeypatch.setattr(servidor.ESTADO, "ya_hecha", lambda *a, **k: ({"creado": "06/11/2026"}, vieja))
    assert not w.volcar_hecha({"id": "L"}, {"numero": "0707", "etiqueta": "0707", "con_localizadores": True})


def test_se_guardan_todos_aunque_el_lote_ya_no_salga_en_fichas(monkeypatch):
    import json
    lotes = [lote(f"L{i}", ["0001"]) for i in range(servidor.MAX_LOTES_EN_PANEL + 10)]
    vieja = lotes[-1]["fichas"][0]                          # de un lote que ya no cabe en la vista Fichas
    vieja.update(con_localizadores=True, estado="hecha", NAME="N", COMMENT="C", correo_x="no",
                 localizadores=[{"plano": "1", "texto": "KIEV. DOCE DE OCTUBRE DE 2026. VISTA AEREA."}])
    monkeypatch.setattr(servidor.ESTADO, "lotes", lotes)
    items = json.loads(servidor.localizadores_guardados().body)["items"]
    assert [(i["lote"], i["f"]["id"]) for i in items] == [(lotes[-1]["id"], vieja["id"])]
    assert items[0]["f"]["localizadores"][0]["texto"].startswith("KIEV.") and "COMMENT" not in items[0]["f"]


def test_en_la_ficha_es_un_enlace_en_las_notas_y_en_la_pestana_se_pliegan():
    from pathlib import Path
    panel = (Path(__file__).resolve().parent.parent / "panel" / "index.html").read_text(encoding="utf-8")
    assert "Ver localizadores" in panel and 'class="notas"><p><a href="#localizadores/' in panel
    assert '<details class="fila fila-loc"' in panel and "/api/localizadores" in panel
    assert "No se han podido cargar los localizadores" in panel        # un corte no parece "no hay ninguno"


class _Peticion:
    def __init__(self, datos):
        self.datos = datos

    async def json(self):
        return self.datos


class _Fichero:
    def __init__(self, nombre, contenido):
        self.filename, self.contenido = nombre, contenido

    async def read(self):
        return self.contenido


def test_la_casilla_con_localizadores_los_pide_para_todos(monkeypatch):
    import asyncio
    monkeypatch.setattr(servidor.ESTADO, "lotes", [])
    monkeypatch.setattr(servidor.ESTADO, "guardar", lambda *a, **k: None)
    asyncio.run(servidor.subir_numeros(_Peticion({"texto": "7612 7613", "localizadores": True})))
    asyncio.run(servidor.subir_numeros(_Peticion({"texto": "7614"})))
    marcas = {f["numero"]: bool(f.get("con_localizadores")) for l in servidor.ESTADO.lotes for f in l["fichas"]}
    assert marcas == {"7612": True, "7613": True, "7614": False}
    csv = ('Name,Catalogador,Comments,Created\n'
           '"0624 USA-X",,TRATAR AGENCIA CON LOCALIZADORES,06/10/2026 10:00\n'
           '"0625 USA-Y",,,06/10/2026 10:00\n').encode()
    servidor.ESTADO.lotes = []
    asyncio.run(servidor.subir_lista(_Fichero("lista.csv", csv), reparto="", fecha="", formal="", rehacer=""))
    asyncio.run(servidor.subir_lista(_Fichero("lista.csv", csv), reparto="", fecha="", formal="", rehacer="",
                                     localizadores="1"))
    sin, con = sorted(servidor.ESTADO.lotes, key=lambda l: any(f["con_localizadores"] for f in l["fichas"]
                                                               if f["numero"] == "0625"))
    assert {f["numero"]: f["con_localizadores"] for f in sin["fichas"]} == {"0624": True, "0625": False}
    assert all(f["con_localizadores"] for f in con["fichas"])


def test_la_casilla_esta_encima_de_rehacer():
    from pathlib import Path
    panel = (Path(__file__).resolve().parent.parent / "panel" / "index.html").read_text(encoding="utf-8")
    assert panel.index('id="con-locs"') < panel.index('id="rehacer"')
    assert 'localizadores: $("con-locs").checked' in panel and 'fd.append("localizadores"' in panel
