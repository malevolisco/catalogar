# -*- coding: utf-8 -*-
"""Catalogator en la nube: lee el CSV de MediaCentral, redacta con el mismo codigo y contesta; el cerebro
viaja sin contraseñas; la Raspberry deja en paz los correos a la direccion +csv."""
import email
import email.policy
import io
import json
import zipfile
from email.message import EmailMessage
from unittest import mock

import buzon
import cerebro
import nube

SCRIPT = ("VIDEO SHOWS: PARIS, FRANCE (OCTOBER 16, 2026) (REUTERS - ACCESS ALL)\n"
          "1. WIDE OF PROTESTERS BLOCKING SCHOOL ENTRANCE\n2. POLICE PUSHING PROTESTERS BACK\n"
          "3. VARIOUS OF PROTESTERS SHOUTING\nSTORY: Police pushed back protesters blocking a Paris high school "
          "on Thursday during a day of demonstrations.")
CSV = ('Name,Catalogador,Comments,Notas Internas,Created\n'
       f'"0707 FRANCE-PROTEST/BLOCKADE-MORNING: Police push back protesters blocking Paris high school",,"{SCRIPT}",,'
       '"16.10.2026 10:40:20 +02:00"\n'
       '"4359 USA-MAMDANI LULA-PRESSER: MAMDANI PRAISES LULA",,"TRATAR AGENCIAS",,"16.10.2026 10:50:00 +02:00"\n')
RESPUESTA = ("ENVIO: x\nNAME: FRANCIA ENFRENTAMIENTOS POLICIA CON MANIFESTANTES EN UN INSTITUTO DE PARIS\n"
             "COMMENT: PARIS. RECURSOS DE LA POLICIA DESALOJANDO A MANIFESTANTES QUE BLOQUEAN UN INSTITUTO.\n"
             "RESTRICCIONES: SIN AVISO")


def test_filas_del_csv_de_mediacentral():
    from listas import _filas_csv
    fichas, descartes = nube.fichas_de_tabla(_filas_csv(CSV.encode("utf-8")))
    assert descartes == ["4359"]                      # "TRATAR AGENCIAS" no es el texto de la agencia
    f = fichas[0]
    assert (f["numero"], f["agencia"], f["fecha"]) == ("0707", "REUTERS", "16/10/2026")
    assert f["slug"] == "FRANCE-PROTEST/BLOCKADE-MORNING"
    assert f["headline"].startswith("Police push back")
    assert f["texto"].startswith("VIDEO SHOWS")


def test_cerebro_sin_secretos_y_sin_rutas_raras(tmp_path):
    cfg = {"correo_usuario": "c@gmail.com", "correo_clave": "x", "api_key": "y", "openai_key": "z",
           "reuters_clave": "r", "claude_token": "t", "ejemplos_por_ficha": 8, "correo_dominios": ["rtve.es"]}
    (tmp_path / "ejemplos.md").write_text("# Fichas aprobadas\n", encoding="utf-8")
    datos = cerebro.empaquetar(cfg, base=tmp_path)
    texto = json.loads(zipfile.ZipFile(io.BytesIO(datos)).read("config.json"))
    assert set(texto) == {"correo_usuario", "ejemplos_por_ficha", "correo_dominios"}
    # un zip con nombres que no son los esperados no escribe nada fuera de su sitio
    malo = io.BytesIO()
    with zipfile.ZipFile(malo, "w") as z:
        z.writestr("../fuera.txt", "x")
        z.writestr("ejemplos.md", "# nuevas\n")
    destino = tmp_path / "nube"
    destino.mkdir()
    cerebro.desempaquetar(malo.getvalue(), base=destino)
    assert (destino / "ejemplos.md").read_text(encoding="utf-8") == "# nuevas\n"
    assert not (tmp_path / "fuera.txt").exists()


def _correo(para, adjunto=True):
    msg = EmailMessage()
    msg["From"] = "Ana Pérez <ana.perez@rtve.es>"
    msg["To"] = para
    msg["Subject"] = "Lista de hoy"
    msg["Message-ID"] = "<abc@rtve.es>"
    msg.set_content("Os mando la lista.")
    if adjunto:
        msg.add_attachment(CSV.encode("utf-8"), maintype="text", subtype="csv", filename="Search Results.csv")
    return email.message_from_bytes(msg.as_bytes(), policy=email.policy.default)


def test_la_raspberry_reconoce_los_correos_de_la_nube():
    assert buzon.direccion_nube("catalogator@gmail.com") == "catalogator+csv@gmail.com"
    assert buzon.es_para_nube(_correo("catalogator+csv@gmail.com"))
    assert buzon.es_para_nube(_correo("Catalogator <Catalogator+CSV@gmail.com>"))
    assert not buzon.es_para_nube(_correo("catalogator@gmail.com"))


class ImapFalso:
    """Lo justo de imaplib para nube.atender: un correo y sus marcas."""

    def __init__(self, msg):
        self.bruto = msg.as_bytes()
        self.marcas = set()

    def uid(self, orden, *args):
        if orden == "FETCH":
            return "OK", [(b"1 (BODY[] {1}", self.bruto)]
        if orden == "SEARCH":
            return "OK", [b"7" if args[-1] in self.marcas else b""]
        if orden == "STORE":
            self.marcas.update(args[-1].strip("()").split())
            return "OK", [b""]
        raise AssertionError(orden)


def test_atiende_un_correo_y_contesta_con_las_fichas():
    m = ImapFalso(_correo("catalogator+csv@gmail.com"))
    cfg = {"correo_usuario": "catalogator@gmail.com", "correo_dominios": ["rtve.es"]}
    with mock.patch("redactor.llamar_modelo", return_value=RESPUESTA), \
            mock.patch("correo.enviar") as enviar, \
            mock.patch("redactor.registrar_uso"):
        nube.atender(m, b"7", cfg)
    assert {nube.HECHO, "\\Seen"} <= m.marcas
    destino, asunto, cuerpo = enviar.call_args.args[1:4]
    assert destino == "ana.perez@rtve.es"
    # quien no esta en la agenda recibe el tono formal, igual que de la Raspberry
    assert asunto == "[Catalogación] Lote completado: 1 ficha · Lista de hoy"
    assert "NAME: FRANCIA ENFRENTAMIENTOS" in cuerpo
    assert "0707 · 16/10/2026 · FRANCE-PROTEST/BLOCKADE-MORNING" in cuerpo   # la linea ENVIO la pone el programa
    assert "4359" in cuerpo                            # dice cual no traia texto


def test_si_falla_se_reintenta_una_vez_y_luego_se_avisa():
    m = ImapFalso(_correo("catalogator+csv@gmail.com"))
    cfg = {"correo_usuario": "catalogator@gmail.com", "correo_dominios": ["rtve.es"]}
    with mock.patch("nube.redactar_todas", side_effect=RuntimeError("roto")), mock.patch("correo.enviar") as enviar:
        nube.atender(m, b"7", cfg)
        assert nube.FALLO in m.marcas and nube.HECHO not in m.marcas and not enviar.called
        nube.atender(m, b"7", cfg)
    assert nube.HECHO in m.marcas
    assert "No se ha podido" in enviar.call_args.args[2]


def test_sin_secretos_no_hace_nada(monkeypatch):
    for k in ("CATALOGATOR_CORREO", "CATALOGATOR_CLAVE", "CATALOGATOR_CLAUDE_TOKEN", "CATALOGATOR_API_KEY",
              "CATALOGATOR_OPENAI_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert nube.main() == 0


def test_con_el_token_redacta_con_la_suscripcion(monkeypatch):
    """Sin clave de pago: el token de un año de Claude Code basta, y manda sobre las claves de pago."""
    monkeypatch.setenv("CATALOGATOR_CORREO", "c@gmail.com")
    monkeypatch.setenv("CATALOGATOR_CLAVE", "x")
    monkeypatch.setenv("CATALOGATOR_CLAUDE_TOKEN", "sk-ant-oat01-prueba")
    monkeypatch.delenv("CATALOGATOR_API_KEY", raising=False)
    monkeypatch.delenv("CATALOGATOR_OPENAI_KEY", raising=False)
    secretos = nube.configuracion()
    assert secretos and nube.motor(secretos) == "claude_code"
    assert nube.motor({"api_key": "y"}) == "api" and nube.motor({"openai_key": "z"}) == "openai"
    assert nube.motor({"claude_token": "t", "api_key": "y"}) == "claude_code"
