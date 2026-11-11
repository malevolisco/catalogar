# -*- coding: utf-8 -*-
"""Fotogramas de la pagina de la agencia (miniaturas del panel y clasificador de escenas): se espera a que la
pagina los pinte, en AP sin cerrar la ventana de la ficha, y si no salen se guarda una copia para verlo."""
import pytest

import extractor


class Respuesta:
    ok = True
    headers = {"content-type": "image/jpeg"}

    def body(self):
        return b"JPG"


class Pagina:
    """Lo justo de una pagina de Playwright: imagenes que aparecen al bajar o al volver a pedirlas."""

    def __init__(self, al_principio=1, tras_bajar=None, tras_repedir=None, modal=False, urls=None, imagen=None):
        self.n = al_principio
        self.urls, self.imagen, self.selectores, self.pedidas = urls, imagen, [], []
        self.tras_bajar, self.tras_repedir = tras_bajar, tras_repedir
        self.modal, self.bajadas, self.recargas, self.repedidas = modal, 0, 0, 0
        self.request = self

    def get(self, url, timeout=None):
        self.pedidas.append(url)
        if self.imagen is not None:
            r = Respuesta()
            r.body = lambda: self.imagen
            return r
        return Respuesta()

    def eval_on_selector_all(self, sel, js):
        self.selectores.append(sel)
        if self.urls is not None:
            return [{"s": u, "w": 320, "h": 180} for u in self.urls]
        return [{"s": f"https://cdn.agencia/frame{i}.jpg", "w": 320, "h": 180} for i in range(self.n)] + \
               [{"s": "https://cdn.agencia/logo.png", "w": 300, "h": 100}]

    def evaluate(self, js):
        if "removeAttribute" in js:
            self.repedidas += 1
            if self.tras_repedir:
                self.n = self.tras_repedir
        elif "scrollBy" in js:
            self.bajadas += 1
            if self.tras_bajar and self.bajadas >= self.tras_bajar[0]:
                self.n = self.tras_bajar[1]

    def wait_for_timeout(self, ms):
        pass

    def wait_for_load_state(self, *a, **k):
        pass

    def reload(self, **k):
        self.recargas += 1

    def locator(self, sel):
        pagina = self

        class L:
            def count(self):
                return 1 if (sel == "lib-video-detail" and pagina.modal) else 0

            def inner_text(self):
                return "texto de la ficha"
        return L()

    def content(self):
        return "<html></html>"

    def screenshot(self, path=None, **k):
        open(path, "wb").write(b"PNG")


@pytest.fixture
def ex(tmp_path, monkeypatch):
    monkeypatch.setattr(extractor, "BASE_DIR", tmp_path)
    monkeypatch.setattr(extractor, "DEBUG_DIR", tmp_path / "debug")
    e = extractor.Extractor()
    e._bloqueando = False
    monkeypatch.setattr(e, "_bloquear", lambda si: setattr(e, "_bloqueando", si))
    return e


def test_se_espera_a_que_la_pagina_pinte_los_fotogramas(ex, tmp_path):
    ex._page = Pagina(al_principio=1, tras_bajar=(3, 5))       # aparecen al bajar tres veces
    rutas = ex.miniaturas("6597", 4)
    assert len(rutas) == 4 and ex._page.bajadas == 3
    assert all(r.startswith(str(tmp_path / "miniaturas" / "6597")) for r in rutas)
    assert not (tmp_path / "debug").exists()                   # salieron: no se guarda copia


def test_ap_sin_cerrar_la_ficha_se_vuelven_a_pedir_las_imagenes(ex):
    ex._page = Pagina(al_principio=0, tras_repedir=6, modal=True)
    ex._bloqueando = True                                       # modo solo texto: imagenes bloqueadas
    rutas = ex.miniaturas("4696362", 6)
    assert len(rutas) == 6 and ex._page.recargas == 0 and ex._page.repedidas == 1 and not ex._bloqueando
    # solo las de la ventana de la ficha: detras quedan los resultados de la busqueda, que son otras noticias
    assert set(ex._page.selectores) == {"lib-video-detail img"}


def test_reuters_solo_las_imagenes_de_este_envio(ex):
    propias = [f"https://cdn.reuters/binary_LOP651207112026RP1-FRAME{i}.jpg" for i in range(2)]
    otras = [f"https://cdn.reuters/binary_LOP449905112026RP1-FRAME{i}.jpg" for i in range(5)]
    ex._page = Pagina(urls=otras[:2] + propias + otras[2:])
    rutas = ex.miniaturas("6512", 6, propias="651207112026")
    assert len(rutas) == 2 and ex._page.pedidas == propias          # dos propias valen; las de otra noticia no


def test_si_ninguna_lleva_el_identificador_se_avisa_y_se_guarda_copia(ex, tmp_path, capsys):
    ex._page = Pagina(urls=[f"https://cdn.reuters/x/{i}.jpg" for i in range(4)])
    assert len(ex.miniaturas("6512", 6, propias="651207112026")) == 4   # como antes, pero queda constancia
    assert "no llevan el identificador del envio" in capsys.readouterr().out
    assert (tmp_path / "debug" / "6512_fotospagina.html").exists()


def _tira(n, ancho=427, alto=240):
    from PIL import Image
    import io
    img = Image.new("RGB", (n * ancho, alto))
    for k in range(n):                                          # cada fotograma de un color
        img.paste((k * 10 % 256, 100, 200), (k * ancho, 0, (k + 1) * ancho, alto))
    salida = io.BytesIO()
    img.save(salida, "JPEG")
    return salida.getvalue()


def test_reuters_coge_los_fotogramas_de_la_tira_del_xml(ex, tmp_path, capsys):
    pytest.importorskip("PIL")
    from PIL import Image
    ex._page = Pagina(imagen=_tira(24))
    xml = {"aspecto": "16:9", "imagenes": [
        {"rendition": "rend:viewImage:768x432", "href": "http://content.reuters.com/a/VIEWIMAGE", "ancho": 768, "alto": 432},
        {"rendition": "rend:thumbnailgrid", "href": "http://content.reuters.com/a/THUMBNAILGRID", "ancho": 10248, "alto": 240}]}
    rutas = ex._fotogramas_xml("5455", xml, 6)
    assert len(rutas) == 6 and ex._page.pedidas[0] == "https://content.reuters.com/a/THUMBNAILGRID"
    assert all(Image.open(r).size == (427, 240) for r in rutas)
    assert "6 de la tira del XML de Reuters" in capsys.readouterr().out
    vertical = {"aspecto": "9:16", "imagenes": [{"rendition": "rend:thumbnailgrid", "href": "https://x/G", "ancho": 0, "alto": 0}]}
    ex._page = Pagina(imagen=_tira(10, ancho=135))
    assert [Image.open(r).size for r in ex._fotogramas_xml("6597", vertical, 4)] == [(135, 240)] * 4
    assert ex._fotogramas_xml("1", {"imagenes": []}, 6) == []          # sin tira: se miran las de la pagina


def test_reuters_bloqueado_se_ve_entera_y_se_espera(ex):
    ex._page = Pagina(al_principio=0, tras_bajar=(1, 4))
    ex._bloqueando = True
    assert len(ex.miniaturas("6624", 4)) == 4 and ex._page.recargas == 1


def test_si_no_salen_se_guarda_la_pagina_y_solo_las_ultimas(ex, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(extractor, "ESPERA_FOTOGRAMAS_MS", 1500)
    for i in range(extractor.VOLCADOS_SIN_FOTOS + 3):
        ex._page = Pagina(al_principio=1)
        assert ex.miniaturas(f"{1000 + i}", 6) == []
    assert ex._page.bajadas == 3                                 # 1,5 s bajando de medio en medio segundo
    salida = capsys.readouterr().out
    assert "[fotogramas] 1000: la pagina de la agencia ensena 2 imagen(es)" in salida
    guardadas = {f.name.split("_sinfotos.")[0] for f in (tmp_path / "debug").glob("*_sinfotos.*")}
    assert len(guardadas) == extractor.VOLCADOS_SIN_FOTOS and "1012" in guardadas and "1000" not in guardadas
    assert (tmp_path / "debug" / "1012_sinfotos.html").exists()


def test_el_diagnostico_las_incluye(tmp_path, monkeypatch):
    import io
    import zipfile
    import admin
    debug = tmp_path / "debug"
    debug.mkdir()
    for nombre in ("6512_fotospagina.html", "6597_sinfotos.txt", "otra_cosa.html"):
        (debug / nombre).write_text("x")
    monkeypatch.setattr(admin, "BASE_DIR", tmp_path)
    monkeypatch.setitem(admin.S, "_g", {"log": lambda m: None})
    respuesta = admin.diagnostico()
    nombres = zipfile.ZipFile(io.BytesIO(respuesta.body)).namelist()
    assert {"fotogramas/6512_fotospagina.html", "fotogramas/6597_sinfotos.txt"} <= set(nombres)
    assert not any("otra_cosa" in n for n in nombres)


def test_el_xml_trae_las_imagenes_y_el_aspecto():
    import reuters_xml
    from test_lectores import XML
    xml = XML.replace(b'<remoteContent contenttype="video/mpeg" duration="125"/>',
                      b'<remoteContent contenttype="video/mpeg" duration="125" videoaspectratio="9:16"/>'
                      b'<remoteContent contenttype="image/jpeg" rendition="rend:thumbnailgrid" width="1350" height="240"'
                      b' href="http://content.reuters.com/auth-server/content/x-THUMBNAILGRID"/>')
    d = reuters_xml.leer(xml)
    assert d["aspecto"] == "9:16" and d["imagenes"] == [{"rendition": "rend:thumbnailgrid", "ancho": 1350, "alto": 240,
                                                         "href": "http://content.reuters.com/auth-server/content/x-THUMBNAILGRID"}]
