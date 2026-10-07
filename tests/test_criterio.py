# -*- coding: utf-8 -*-
"""Reglas que no pueden desaparecer del criterio ni del atajo del navegador sin que nadie se entere."""
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PATRONES = (RAIZ / "reglas_patrones.md").read_text(encoding="utf-8")
LIGERAS = (RAIZ / "reglas_ligeras.md").read_text(encoding="utf-8")
ATAJOS = sorted(RAIZ.glob("atajo_catalogar_v*.md"))


def test_nunca_abreviar_ni_dos_puntos():
    for texto in (PATRONES, LIGERAS):
        assert "Sin dos puntos" in texto
        assert "Nunca" in texto and "PTE." in texto


def test_reglas_de_la_jefa():
    assert "CARGO APELLIDO" in PATRONES                   # jefes de Estado en el NAME
    assert "PHOTOCALL" in PATRONES


def test_un_solo_atajo_y_al_dia():
    assert len(ATAJOS) == 1, "tiene que haber un solo atajo_catalogar_vNN.md"
    atajo = ATAJOS[0].read_text(encoding="utf-8")
    version = ATAJOS[0].stem.rsplit("_v", 1)[1]
    assert f"ATAJO CATALOGAR v{version}" in atajo and f"atajo catalogar v{version}" in atajo
    assert "PTE." in atajo and "CARGO APELLIDO" in atajo


def test_criterio_compone_sin_cambios():
    import criterio
    assert criterio.componer("reglas_patrones.md", PATRONES).strip()
