# -*- coding: utf-8 -*-
"""Catalogator en la nube: lee el CSV de MediaCentral, redacta con el mismo codigo y contesta; el cerebro
viaja sin contraseñas; la Raspberry deja en paz los correos a la direccion +csv."""
import email
import email.policy
import imaplib
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


def test_en_la_nube_cont_manda_y_basta_con_cont():
    from listas import _filas_csv
    cont = SCRIPT.replace("PARIS", "LYON")
    csv = ('Name,Comments,Cont.\n'
           f'"0707 FRANCE-PROTEST: Police push back","{SCRIPT}","{cont}"\n'
           f'"0708 FRANCE-X: Y","{SCRIPT}",""\n')
    fichas, descartes = nube.fichas_de_tabla(_filas_csv(csv.encode("utf-8")))
    assert [f["texto"] for f in fichas] == [cont, SCRIPT] and descartes == []
    fichas, _ = nube.fichas_de_tabla(_filas_csv(f'Name,Cont.\n"0707 FRANCE-PROTEST: P","{cont}"\n'.encode("utf-8")))
    assert fichas[0]["texto"] == cont


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
            criterios, ok = list(args[1:]), True
            for i, c in enumerate(criterios):
                if c == "UNSEEN":
                    ok &= "\\Seen" not in self.marcas
                elif c == "KEYWORD":
                    ok &= criterios[i + 1] in self.marcas
                elif c == "UNKEYWORD":
                    ok &= criterios[i + 1] not in self.marcas
            return "OK", [b"7" if ok else b""]
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



def test_respaldo_coge_el_correo_de_la_raspberry_y_lo_marca_leido_antes_de_redactar():
    """Un correo a la direccion normal que la Raspberry no ha leido: se contesta igual que un +csv, y se marca
    leido antes de redactar para que la Raspberry, si vuelve, no lo conteste otra vez."""
    m = ImapFalso(_correo("catalogator@gmail.com"))
    cfg = {"correo_usuario": "catalogator@gmail.com", "correo_dominios": ["rtve.es"]}
    marcas_al_redactar = []

    def redactar_todas(fichas):
        marcas_al_redactar.append(set(m.marcas))
        return [], []

    with mock.patch("nube.redactar_todas", side_effect=redactar_todas), mock.patch("correo.enviar") as enviar:
        nube.atender(m, b"7", cfg, respaldo=True)
    assert {"\\Seen", nube.RESPALDO} <= marcas_al_redactar[0]
    assert {nube.HECHO, "\\Seen", nube.RESPALDO} <= m.marcas
    assert enviar.call_args.args[1] == "ana.perez@rtve.es"


def test_respaldo_sin_nada_que_hacer_se_deja_sin_leer_para_la_raspberry():
    cfg = {"correo_usuario": "catalogator@gmail.com", "correo_dominios": ["rtve.es"]}
    solo_numeros = ImapFalso(_correo("catalogator@gmail.com", adjunto=False))
    with mock.patch("nube.redactar_todas") as redactar, mock.patch("correo.enviar") as enviar:
        nube.atender(solo_numeros, b"7", cfg, respaldo=True)
        de_fuera = _correo("catalogator@gmail.com")
        de_fuera.replace_header("From", "alguien@otro.com")
        nube.atender(ImapFalso(de_fuera), b"7", cfg, respaldo=True)
    assert solo_numeros.marcas == {nube.NO_RESPALDO}          # sin leer, para la Raspberry
    assert not redactar.called and not enviar.called



def test_respaldo_si_la_raspberry_lo_lee_entretanto_no_se_contesta_dos_veces():
    m = ImapFalso(_correo("catalogator@gmail.com"))
    m.marcas.add("\\Seen")                       # la Raspberry ha vuelto y lo ha leido despues de pendientes()
    cfg = {"correo_usuario": "catalogator@gmail.com", "correo_dominios": ["rtve.es"]}
    with mock.patch("nube.redactar_todas") as redactar, mock.patch("correo.enviar") as enviar:
        nube.atender(m, b"7", cfg, respaldo=True)
    assert m.marcas == {"\\Seen"} and not redactar.called and not enviar.called


def test_respaldo_no_abre_el_adjunto_de_quien_no_puede_pedir():
    de_fuera = _correo("catalogator@gmail.com")
    de_fuera.replace_header("From", "alguien@otro.com")
    m = ImapFalso(de_fuera)
    cfg = {"correo_usuario": "catalogator@gmail.com", "correo_dominios": ["rtve.es"]}
    with mock.patch("nube.fichas_del_correo") as leer:
        nube.atender(m, b"7", cfg, respaldo=True)
    assert not leer.called and m.marcas == {nube.NO_RESPALDO}

def test_respaldo_si_falla_dos_veces_no_habla_del_csv():
    m = ImapFalso(_correo("catalogator@gmail.com"))
    cfg = {"correo_usuario": "catalogator@gmail.com", "correo_dominios": ["rtve.es"]}
    with mock.patch("nube.redactar_todas", side_effect=RuntimeError("roto")), mock.patch("correo.enviar") as enviar:
        nube.atender(m, b"7", cfg, respaldo=True)
        assert {"\\Seen", nube.RESPALDO, nube.FALLO} <= m.marcas and not enviar.called
        nube.atender(m, b"7", cfg, respaldo=True)            # segunda vuelta: ya es suyo, no se vuelve a mirar
    cuerpo = enviar.call_args.args[3]
    assert "+csv" not in cuerpo and "más tarde" in cuerpo and nube.HECHO in m.marcas


class BuzonFalso:
    """Lo justo de imaplib para nube.pendientes: correos con destino, hora de llegada y marcas."""

    def __init__(self, correos):
        self.correos = correos          # {uid: (para, llegada, marcas)}

    def uid(self, orden, *args):
        if orden == "SEARCH":
            criterios, salida = list(args[1:]), []
            for uid, (_, _, marcas) in self.correos.items():
                ok = True
                for i, c in enumerate(criterios):
                    if c == "UNSEEN":
                        ok &= "\\Seen" not in marcas
                    elif c == "KEYWORD":
                        ok &= criterios[i + 1] in marcas
                    elif c == "UNKEYWORD":
                        ok &= criterios[i + 1] not in marcas
                if ok:
                    salida.append(uid)
            return "OK", [b" ".join(salida)]
        para, llegada, _ = self.correos[args[0]]
        if args[1] == "(INTERNALDATE)":
            fecha = imaplib.Time2Internaldate(llegada).encode()
            return "OK", [args[0] + b" (UID " + args[0] + b" INTERNALDATE " + fecha + b")"]
        return "OK", [(b"1 (BODY[] {1}", f"To: {para}\r\n\r\n".encode())]


def test_pendientes_coge_los_csv_y_de_respaldo_solo_lo_que_la_raspberry_no_ha_leido_a_tiempo():
    ahora = 1_800_000_000
    normal, csv = "catalogator@gmail.com", "catalogator+csv@gmail.com"
    m = BuzonFalso({
        b"1": (csv, ahora - 60, set()),                                   # +csv: siempre
        b"2": (normal, ahora - 20 * 60, set()),                           # sin leer a los 20 min: respaldo
        b"3": (normal, ahora - 5 * 60, set()),                            # 5 min: aun le toca a la Raspberry
        b"4": (normal, ahora - 20 * 60, {"\\Seen"}),                     # leido: la Raspberry esta
        b"5": (normal, ahora - 30 * 3600, set()),                         # mas de un dia: olvidado
        b"6": (normal, ahora - 20 * 60, {buzon.MARCA_RECHAZADO}),         # remitente rechazado
        b"7": (normal, ahora - 20 * 60, {nube.NO_RESPALDO}),              # ya mirado: nada que hacer
        b"8": (normal, ahora - 3 * 3600, {"\\Seen", nube.RESPALDO}),     # cogido y fallo una vez: se reintenta
        b"9": (normal, ahora - 20 * 60, {"\\Seen", nube.HECHO}),         # ya contestado
    })
    assert sorted(nube.pendientes(m, ahora)) == [(b"1", False), (b"2", True), (b"8", True)]


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


def test_el_timbre_apunta_a_la_nube():
    """El timbre de Gmail (nube_timbre.gs) cambia el issue que arranca nube.yml: si se renombra algo, avisa."""
    import re
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent
    timbre = (raiz / "nube_timbre.gs").read_text(encoding="utf-8")
    flujo = (raiz / ".github" / "workflows" / "nube.yml").read_text(encoding="utf-8")
    assert "const REPOSITORIO = 'malevolisco/catalogar'" in timbre
    issue = re.search(r"const ISSUE = (\d+);", timbre).group(1)
    assert "issues:\n    types: [edited]" in flujo and "workflow_dispatch" in flujo
    assert (f"github.event.issue.number == {issue} && github.event.sender.login == github.repository_owner"
            in flujo)
    assert "/dispatches" not in timbre and "/actions/" not in timbre      # la llave nunca lanza tareas
    assert "GITHUB_TOKEN" in timbre and "github_pat_" not in timbre        # la llave nunca en el codigo
    assert f"'{buzon.SUFIJO_NUBE}'" in timbre                              # la misma direccion que la Raspberry deja


def test_publicar_a_mano_solo_desde_main_y_sin_ordenes_en_la_version():
    """Publicar a mano: solo desde main y con la version como variable, nunca dentro de una orden."""
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent
    flujo = (raiz / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "if: github.event_name == 'push' || github.ref == 'refs/heads/main'" in flujo
    for linea in flujo.splitlines():
        if "${{" in linea and ("inputs." in linea or "outputs.version" in linea):
            # dentro de una orden, una version con comillas o ; ejecutaria lo que trajera
            assert linea.strip().startswith(("VERSION", "tag_name:", "name:")), linea
    assert '=~ ^v[0-9]{4}' in flujo
