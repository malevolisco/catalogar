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

    def __init__(self, al_principio=1, tras_bajar=None, tras_repedir=None, modal=False):
        self.n = al_principio
        self.tras_bajar, self.tras_repedir = tras_bajar, tras_repedir
        self.modal, self.bajadas, self.recargas, self.repedidas = modal, 0, 0, 0
        self.request = self

    def get(self, url, timeout=None):
        return Respuesta()

    def eval_on_selector_all(self, sel, js):
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


def test_el_diagnostico_las_incluye():
    import inspect
    import admin
    assert "_sinfotos.*" in inspect.getsource(admin.diagnostico)
