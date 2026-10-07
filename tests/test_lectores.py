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
