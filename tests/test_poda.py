# -*- coding: utf-8 -*-
"""Poda de las aprobadas: se van las repetidas y las que chocan con el criterio; las protegidas y las valiosas
se quedan; nada se pierde (se recuperan)."""
import pytest

import redactor as r


def bloque(num, name, comment):
    return f"ENVIO: {num} · 01/10/2026 · SLUG · Titular\nNAME: {name}\nCOMMENT: {comment}\nRESTRICCIONES: SIN AVISO\n"


BUENO = ("MADRID. CONCENTRACION DE VECINOS {n} ANTE EL AYUNTAMIENTO PARA PROTESTAR POR EL CIERRE DEL CENTRO DE SALUD "
         "DEL BARRIO NUMERO {n}. INCLUYE DECLARACIONES DE ANA LOPEZ, PORTAVOZ VECINAL, SOBRE LA PROTESTA.")


@pytest.fixture
def aprobadas(tmp_path, monkeypatch):
    monkeypatch.setattr(r, "EJEMPLOS_PATH", tmp_path / "ejemplos.md")
    monkeypatch.setattr(r, "RETIRADAS_PATH", tmp_path / "retiradas.json")
    monkeypatch.setattr(r, "USO_PATH", tmp_path / "uso.json")
    monkeypatch.setattr(r, "MIN_APROBADAS", 3)
    r._SALUD.update(marca=None, datos={})
    distintas = [
        ("CHILE INCENDIO FORESTAL VALPARAISO", "VALPARAISO. TRABAJOS DE EXTINCION DEL INCENDIO FORESTAL QUE AFECTA A LOS CERROS DE LA CIUDAD. INCLUYE VISTAS AEREAS DE LAS LLAMAS Y EVACUACION DE VECINOS."),
        ("JAPON TERREMOTO NOTO DAÑOS", "WAJIMA. DAÑOS TRAS EL TERREMOTO DE MAGNITUD 7 EN LA PENINSULA DE NOTO. INCLUYE RECURSOS DE EDIFICIOS DERRUMBADOS Y EQUIPOS DE RESCATE."),
        ("ITALIA FUTBOL SERIE A RESUMEN", "MILAN. RESUMEN DEL PARTIDO ENTRE EL INTER Y LA JUVENTUS DE LA SERIE A, QUE TERMINA CON EMPATE A DOS. INCLUYE GOLES Y CELEBRACIONES."),
        ("BELGICA CUMBRE OTAN LLEGADA JEFES DE ESTADO", "BRUSELAS. LLEGADA DE JEFES DE ESTADO Y DE GOBIERNO A LA CUMBRE DE LA OTAN SOBRE EL GASTO EN DEFENSA. INCLUYE PLANOS DEL EXTERIOR DE LA SEDE."),
        ("INDIA MONZON INUNDACIONES KERALA", "KOCHI. INUNDACIONES PROVOCADAS POR LAS LLUVIAS DEL MONZON EN EL ESTADO DE KERALA. INCLUYE PLANOS DE CALLES ANEGADAS Y BARCAS DE SALVAMENTO."),
        ("EGIPTO ARQUEOLOGIA HALLAZGO LUXOR", "LUXOR. PRESENTACION DEL HALLAZGO DE UNA TUMBA DE LA DINASTIA XVIII EN LA NECROPOLIS DE TEBAS. INCLUYE PLANOS DEL INTERIOR Y DE LOS SARCOFAGOS."),
    ]
    fichas = [bloque(f"10{i}", n, c) for i, (n, c) in enumerate(distintas)]
    mala = ("WASHINGTON. DECLARACIONES DE BILL CLINTON, PTE. DE EEUU, SOBRE LA AYUDA A LAS VICTIMAS DEL HURACAN "
            "EN FLORIDA Y EL PLAN DE RECONSTRUCCION. INCLUYE PLANOS DE LA RUEDA DE PRENSA.")
    fichas += [bloque("2001", "EEUU DECLARACIONES CLINTON AYUDA", mala),
               bloque("2002", "PERU VISITA TOLEDO CUZCO", "CUZCO. VISITA DE ALEJANDRO TOLEDO, PTE. DE PERU, A LAS "
                      "OBRAS DE RESTAURACION DE LA CATEDRAL TRAS EL SEISMO. INCLUYE SALUDOS A LOS OBREROS."),  # protegida
               bloque("3001", "FRANCIA MANIFESTACION PARIS PENSIONES", BUENO.format(n=99).replace("MADRID", "PARIS")),
               bloque("3002", "FRANCIA MANIFESTACION PARIS PENSIONES", BUENO.format(n=99).replace("MADRID", "PARIS"))]
    (tmp_path / "ejemplos.md").write_text("# Fichas aprobadas\n# Aprobada aposta: 2002\n\n" + "\n".join(fichas), encoding="utf-8")
    return tmp_path


def test_poda(aprobadas):
    numeros = lambda: [e["numero"] for e in r.listar_ejemplos()]
    vista = r.podar_ejemplos(simular=True)
    assert {x["numero"] for x in vista} == {"2001", "3001"} and "3001" in numeros()     # simular no toca nada
    retiradas = r.podar_ejemplos()
    assert {x["numero"] for x in retiradas} == {"2001", "3001"}
    quedan = numeros()
    assert "2002" in quedan and "3002" in quedan and "2001" not in quedan              # protegida y la nueva, dentro
    assert any("PTE." in x["motivo"] for x in retiradas) and any("Casi igual" in x["motivo"] for x in retiradas)
    assert r.recuperar_ejemplo("2001") and "2001" in numeros()
    assert [x["numero"] for x in r.leer_retiradas()] == ["3001"]


def test_nunca_baja_del_minimo(aprobadas, monkeypatch):
    monkeypatch.setattr(r, "MIN_APROBADAS", 50)
    retiradas = r.podar_ejemplos(simular=True)
    assert [x["estado"] for x in retiradas] == ["repetida"]                            # solo la repetida
