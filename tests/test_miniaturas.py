# -*- coding: utf-8 -*-
"""Miniaturas junto a la ficha hecha (panel): uno o dos fotogramas de los que ya se guardan al redactar, solo si
siguen siendo de esa ficha y solo imagenes dentro de miniaturas/."""
import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

import servidor
from actos import huella_fotogramas
from test_colas import lote

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


@pytest.fixture
def ficha_con_fotos(tmp_path, monkeypatch):
    carpeta = tmp_path / "miniaturas" / "5455"
    carpeta.mkdir(parents=True)
    rutas = []
    for i in range(1, 7):
        r = carpeta / f"{i:02d}.jpg"
        r.write_bytes(b"JPG" + bytes([i]))
        rutas.append(str(r))
    monkeypatch.setattr(servidor, "MINIATURAS_DIR", tmp_path / "miniaturas")
    l = lote("L", ["5455"])
    f = l["fichas"][0]
    f.update(estado="hecha", NAME="N", COMMENT="C", fotogramas=huella_fotogramas(rutas))
    monkeypatch.setattr(servidor.ESTADO, "lotes", [l])
    return l, f, rutas


def test_da_dos_fotogramas_sin_el_primero_ni_el_ultimo(ficha_con_fotos):
    l, f, rutas = ficha_con_fotos
    r0 = servidor.miniatura(l["id"], f["id"], 0)
    r1 = servidor.miniatura(l["id"], f["id"], 1)
    assert (r0.body, r1.body) == (Path(rutas[1]).read_bytes(), Path(rutas[4]).read_bytes())
    assert r0.media_type == "image/jpeg" and r0.headers["x-content-type-options"] == "nosniff"
    assert "private" in r0.headers["cache-control"]
    for n in (2, -1):
        with pytest.raises(HTTPException) as e:
            servidor.miniatura(l["id"], f["id"], n)
        assert e.value.status_code == 404
    for lote_id, ficha_id in ((l["id"], "no-existe"), ("no-existe", f["id"])):
        with pytest.raises(HTTPException):
            servidor.miniatura(lote_id, ficha_id, 0)


def test_si_el_numero_se_ha_vuelto_a_sacar_no_se_ensenan(ficha_con_fotos):
    l, f, rutas = ficha_con_fotos
    Path(rutas[2]).write_bytes(b"OTRO ENVIO CON EL MISMO NUMERO")       # Reuters reutiliza el numero
    with pytest.raises(HTTPException) as e:
        servidor.miniatura(l["id"], f["id"], 0)
    assert e.value.status_code == 404


def test_solo_imagenes_dentro_de_miniaturas(ficha_con_fotos, tmp_path):
    l, f, _ = ficha_con_fotos
    fuera = tmp_path / "config.json"
    fuera.write_text("{}")
    texto = tmp_path / "miniaturas" / "5455" / "notas.txt"
    texto.write_text("hola")
    for intruso in (fuera, texto):
        f["fotogramas"] = huella_fotogramas([str(intruso)] * 3)         # el del medio es el intruso
        with pytest.raises(HTTPException) as e:
            servidor.miniatura(l["id"], f["id"], 0)
        assert e.value.status_code == 404
    png = tmp_path / "miniaturas" / "5455" / "07.png"
    png.write_bytes(PNG)
    f["fotogramas"] = huella_fotogramas([str(png)])
    assert servidor.miniatura(l["id"], f["id"], 0).media_type == "image/png"


@pytest.mark.skipif(os.name == "nt", reason="enlaces simbolicos")
def test_un_enlace_que_sale_de_miniaturas_no_se_sirve(ficha_con_fotos, tmp_path):
    l, f, _ = ficha_con_fotos
    secreto = tmp_path / "secreto.jpg"
    secreto.write_bytes(b"NO")
    enlace = tmp_path / "miniaturas" / "5455" / "99.jpg"
    enlace.symlink_to(secreto)
    f["fotogramas"] = huella_fotogramas([str(enlace)])
    with pytest.raises(HTTPException):
        servidor.miniatura(l["id"], f["id"], 0)


def test_el_panel_recibe_cuantas_hay_y_no_las_rutas(ficha_con_fotos):
    l, f, _ = ficha_con_fotos
    datos = json.loads(servidor.lotes().body)
    g = datos["lotes"][0]["fichas"][0]
    assert g["miniaturas_n"] == 2 and g["miniaturas_v"] and "fotogramas" not in g
    assert "fotogramas" in f                                              # la ficha guardada no se toca
    f["fotogramas"] = []
    g = json.loads(servidor.lotes().body)["lotes"][0]["fichas"][0]
    assert "miniaturas_n" not in g


def test_el_panel_las_pinta_al_lado_y_las_quita_si_fallan():
    panel = (Path(__file__).resolve().parent.parent / "panel" / "index.html").read_text(encoding="utf-8")
    assert "/api/miniatura/" in panel and 'class="fotos-ficha"' in panel and "f.miniaturas_n" in panel
    assert 'target="_blank" rel="noopener"' in panel and "img[data-foto]" in panel and '"error"' in panel
