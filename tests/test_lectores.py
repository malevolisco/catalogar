# -*- coding: utf-8 -*-
"""Los lectores del XML de Reuters y de los datos de AP, con fichas de muestra."""
import pytest

import ap_json
import reuters_xml

XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<newsMessage xmlns="http://iptc.org/std/nar/2006-10-01/" xmlns:x="http://www.w3.org/1999/xhtml">
 <itemSet>
  <packageItem version="3">
   <itemMeta><firstCreated>2026-10-06T10:00:00Z</firstCreated></itemMeta>
   <rightsInfo><usageTerms>No use Spain</usageTerms></rightsInfo>
   <contentMeta><altId type="idType:editNumber">7612</altId><slugline>USA-HASTERT/</slugline>
    <headline>Former speaker dies</headline></contentMeta>
  </packageItem>
  <newsItem><contentSet><inlineXML><x:html><x:body><x:p>SHOWS: WASHINGTON</x:p><x:p>1. VARIOUS</x:p>
  </x:body></x:html></inlineXML>
  <remoteContent contenttype="video/mpeg" duration="125"/></contentSet></newsItem>
 </itemSet>
</newsMessage>"""


def test_reuters_xml():
    d = reuters_xml.leer(XML)
    assert (d["numero"], d["rev"], d["fecha"], d["duracion"]) == ("7612", "3", "06/10/2026", "00:02:05")
    assert d["guion"] == ["SHOWS: WASHINGTON", "1. VARIOUS"] and d["restricciones"] == "No use Spain"


def test_reuters_xml_malo():
    with pytest.raises(ValueError):
        reuters_xml.leer(b"<no-es-reuters/>")


def test_ap_json():
    fuente = {"editorialid": "4681323", "title": "US Hastert", "headline": "Hastert dies",
              "arrivaldatetime": "2026-10-06T10:00:00Z", "rightsline": "No access Spain",
              "renditions": [{"totalduration": 90000}], "sources": [{"name": "AP"}],
              "script": {"nitf": "<p>SHOTLIST</p><p>1. Wide</p>"}}
    respuesta = {"Items": [{"_source": fuente}]}
    d = ap_json.leer(ap_json.item_de(respuesta, "4681323"))
    assert (d["numero"], d["fecha"], d["duracion"]) == ("4681323", "06/10/2026", "00:01:30")
    assert "SHOTLIST" in d["texto"] and "No access Spain" in d["texto"]


def test_identificador_de_reuters():
    from datetime import datetime
    import extractor
    dia = datetime(2026, 10, 13)
    assert extractor.reuters_ident("9631", dia, "4") == "tag:reuters.com,2026:newsml_RW963113102026RP1:4"
    assert extractor.reuters_ident("9631", dia) == "tag:reuters.com,2026:newsml_RW963113102026RP1"


def test_plan_b_de_reuters_busca_por_identificador():
    """Sin resultados por numero: escribe el identificador en la caja, primero sin revision y luego 1, 2, 3..."""
    from datetime import datetime
    import extractor
    ex = object.__new__(extractor.Extractor)
    escritos = []
    ex._buscar_a_mano = lambda numero, texto=None: escritos.append(texto) or True
    ex._sondear_lista = lambda numero, ms: ([{"fecha": "13/10/2026", "rev": "4"}]
                                           if escritos[-1].endswith("RP1:4") else [])
    ex._is_login = lambda: False
    ex.captura = lambda nombre: None
    cands = ex._buscar_por_id("9631", "13/10/2026")
    assert cands and cands[0]["rev"] == "4"
    assert escritos == ["tag:reuters.com,2026:newsml_RW963113102026RP1"] + [
        f"tag:reuters.com,2026:newsml_RW963113102026RP1:{r}" for r in (1, 2, 3, 4)]
