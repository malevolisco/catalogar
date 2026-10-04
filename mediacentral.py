# -*- coding: utf-8 -*-
"""
mediacentral.py - Rellena y guarda en MediaCentral las fichas que hace el servidor de catalogar.

Se usa en el equipo desde el que se trabaja en MediaCentral (la web). No hace falta que sea el PC del
servidor: habla con el por su direccion de siempre (la .ts.net) y con la clave de la pagina.

Como trabaja:
  1. Abre Chrome con un perfil propio (la sesion de MediaCentral se queda guardada en el).
  2. Tu entras en MediaCentral y haces la busqueda "tratar agencias". Nada mas.
  3. Si la lista enseña los numeros, se los encarga todos de golpe al servidor; si no (con un envio abierto
     la lista queda estrecha y la columna Name no se ve), cada uno se encarga al abrirlo.
  4. Va abriendo los envios uno a uno con DOBLE CLIC, como se hace a mano. El numero lo lee del Name de
     MediaCentral al abrir el envio ("5619 POPE-FRANCE ..."). En cada envio cuyo Comments siga diciendo "tratar agencias"
     escribe en el Name el NAME de la ficha (solo el NAME, sin numero), lo mismo en el Titulo, el COMMENT
     en Comments y tu CATALOGADOR, y comprueba que se han quedado. Si algo no cuadra, lo deja como estaba,
     lo apunta y sigue.
  5. MediaCentral guarda solo al pasar a otro envio: el programa no pulsa ningun boton. Al terminar
     vuelve a abrir los que ha rellenado y comprueba que ya no dicen "tratar agencias".
  6. Dice cuales tienen RESTRICCIONES o ALERTAS, que son las que rellenas tu a mano (pestaña y
     desplegable; estan tambien en la pagina de catalogar), y lo deja todo en mediacentral_registro.csv.
  Lo ya tratado (lo que no dice "tratar agencias") no lo toca nunca.

La primera vez hay que enseñarle la pantalla, porque MediaCentral no se ha podido ver desde aqui:
    python mediacentral.py --aprender
Pide que abras el primer envio con doble clic y que pongas el cursor en cada campo (Name, Titulo,
Comments y Catalogador). Lo guarda en mediacentral.json. Si un dia cambia la pantalla, se repite.

Cada dia:
    python mediacentral.py              todo seguido
    python mediacentral.py --max 3      solo los tres primeros (para probar)
    python mediacentral.py --revisar    rellena y espera: revisas tu y pasas al siguiente (asi se guarda)

Configuracion: mediacentral.json (copiar de mediacentral.example.json).
"""
import argparse
import csv
import http.cookiejar
import json
import re
import sys
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "mediacentral.json"
PERFIL_DIR = BASE_DIR / "perfil_mediacentral"
REGISTRO_PATH = BASE_DIR / "mediacentral_registro.csv"
DEBUG_DIR = BASE_DIR / "debug"

DEFAULTS = {
    "servidor_url": "",              # https://tu-pc.tu-red.ts.net (la misma direccion de la pagina)
    "servidor_clave": "",            # la clave de la pagina (servidor_clave del config.json del servidor)
    "mediacentral_url": "",          # la direccion de MediaCentral en el navegador
    "navegador": "chrome",           # chrome | msedge | chromium
    "ignorar_certificado": True,     # MediaCentral usa un certificado interno de RTVE que Chrome no reconoce
    "catalogador": "",               # tu usuario de MediaCentral: I23785
    "texto_pendiente": "TRATAR AGENCIAS",
    "presentacion": "mayusculas",    # mayusculas | normalizado (si el servidor la genero)
    "espera_ficha_min": 8,           # tope de espera por una ficha que se esta haciendo
    "abrir_con_doble_clic": True,    # asi se abre un envio en MediaCentral
    "comprobar_al_final": True,      # reabrir los rellenados para ver que se han guardado
    "campos": {},                    # lo rellena --aprender
}

# Lo que se le enseña, en este orden: (clave, tipo, que es)
#   fila: doble clic, como siempre;  texto: seleccionar con el raton;  campo: poner el cursor dentro
# Las RESTRICCIONES no estan: van en otra pestaña con un desplegable y las rellenas tu.
PASOS = [
    ("fila", "fila", "ABRE el PRIMER envio de la lista con DOBLE CLIC, como siempre"),
    ("name", "campo", "el campo NAME de la zona de metadatos (el que pone \"5619 POPE-FRANCE...\"; de ahi se saca el numero y ahi va el NAME de la ficha): pon el cursor dentro"),
    ("titulo", "campo", "el campo TITULO (mas abajo; lleva el mismo NAME de la ficha): pon el cursor dentro"),
    ("comment", "campo", "el campo COMMENTS, el que dice TRATAR AGENCIAS (ahi va el COMMENT de la ficha): pon el cursor dentro"),
    ("catalogador", "campo", "el campo CATALOGADOR: pon el cursor dentro"),
]

# ---------------------------------------------------------------- JavaScript que se mete en la pagina
# Describe un elemento (al aprender) y lo vuelve a encontrar (al trabajar), tambien dentro de iframes
# y de shadow DOM. Se busca por varias vias, de la mas estable a la menos: data-testid, name,
# aria-label, placeholder, la etiqueta que tiene delante, el id si no
# parece generado y, por ultimo, la ruta CSS.
LIB_JS = r"""
() => {
  if (window.__mc) return;
  const limpio = s => (s || "").replace(/\s+/g, " ").replace(/[:*]\s*$/, "").trim();
  const norm = s => limpio(s).replace(/^[*\s]+/, "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toUpperCase();  // "* Título:" == "titulo"
  const esCampo = el => el && (el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable ||
      (el.tagName === "INPUT" && !/^(button|submit|checkbox|radio|hidden|file|image|reset)$/i.test(el.type || "")));
  const visible = el => !!(el && el.getClientRects().length && getComputedStyle(el).visibility !== "hidden");
  const padre = e => e.parentElement || (e.getRootNode && e.getRootNode().host) || null;
  function* todos(raiz) {
    const pila = [raiz];
    while (pila.length) {
      const r = pila.pop();
      for (const el of r.querySelectorAll("*")) { yield el; if (el.shadowRoot) pila.push(el.shadowRoot); }
    }
  }
  // la etiqueta de un campo: su texto y, si es un texto de la pagina, el elemento que lo lleva
  function etiquetaInfo(el) {
    if (el.getAttribute("aria-label")) return { texto: limpio(el.getAttribute("aria-label")), nodo: null };
    const raiz = el.getRootNode();
    const lb = el.getAttribute("aria-labelledby");
    if (lb && raiz.getElementById) {
      const nodos = lb.split(/\s+/).map(i => raiz.getElementById(i)).filter(Boolean);
      const t = limpio(nodos.map(x => x.textContent).join(" "));
      if (t) return { texto: t, nodo: nodos[0] };
    }
    if (el.id && raiz.querySelector) {
      const l = raiz.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l && limpio(l.textContent)) return { texto: limpio(l.textContent), nodo: l };
    }
    // el texto corto mas cercano que va delante del campo, subiendo hasta cuatro niveles
    let a = el.parentElement;
    for (let nivel = 0; a && nivel < 4; nivel++, a = a.parentElement) {
      const tw = document.createTreeWalker(a, NodeFilter.SHOW_TEXT);
      let ultimo = "", nodo = null;
      for (let n = tw.nextNode(); n; n = tw.nextNode()) {
        if (el.contains(n)) break;
        if (el.compareDocumentPosition(n) & Node.DOCUMENT_POSITION_FOLLOWING) break;
        const t = limpio(n.textContent);
        if (t && t.length <= 40) { ultimo = t; nodo = n.parentElement; }
      }
      if (ultimo) return { texto: ultimo, nodo };
    }
    return { texto: "", nodo: null };
  }
  const etiqueta = el => etiquetaInfo(el).texto;
  // ruta nth-of-type de un descendiente respecto a un antepasado ("" si es el mismo)
  function rutaDesde(raiz, el) {
    const partes = [];
    for (let e = el; e && e !== raiz; e = e.parentElement) {
      let i = 1;
      for (let h = e.previousElementSibling; h; h = h.previousElementSibling) if (h.tagName === e.tagName) i++;
      partes.unshift(`${e.tagName.toLowerCase()}:nth-of-type(${i})`);
    }
    return partes.join(" > ");
  }
  // la fila del campo: el antepasado comun del campo y de su etiqueta. En MediaCentral el editor
  // (textarea) solo existe mientras el campo tiene el cursor: lo que permanece es la fila, con la
  // etiqueta a un lado y la celda del valor al otro. Se guarda cuantos niveles hay de la etiqueta a
  // la fila y la ruta de la fila a la celda del valor (el padre del editor).
  function filaDe(el) {
    const info = etiquetaInfo(el);
    if (!info.nodo) return null;
    const arriba = new Set();
    for (let e = el; e; e = padre(e)) arriba.add(e);
    let n = info.nodo, subida = 0;
    while (n && !arriba.has(n)) { n = padre(n); subida++; }
    if (!n || n === document.body || n === el) return null;
    return { subida, ruta_valor: rutaDesde(n, el.parentElement || el), ruta_etiqueta: rutaDesde(n, info.nodo) };
  }
  function porRuta(raiz, ruta) {           // baja por la ruta hasta donde exista
    let e = raiz;
    for (const seg of (ruta ? ruta.split(" > ") : [])) {
      let m = null;
      try { m = e.querySelector(":scope > " + seg); } catch (x) {}
      if (!m) break;
      e = m;
    }
    return e;
  }
  function campoEn(celda) {
    if (esCampo(celda)) return celda;
    for (const e of todos(celda)) if (esCampo(e) && visible(e)) return e;
    return null;
  }
  // la celda del valor de un campo aprendido, por su etiqueta y la forma de su fila
  function celdaDe(d) {
    if (!d.etiqueta || d.subida === undefined || d.subida === null || d.subida < 0) return null;
    const objetivo = norm(d.etiqueta);
    for (const el of todos(document)) {
      if (el.children.length > 3 || !visible(el) || norm(el.textContent) !== objetivo) continue;
      let fila = el;
      for (let i = 0; i < d.subida && fila; i++) fila = padre(fila);
      if (!fila || fila === document.body) continue;
      if (d.ruta_etiqueta !== undefined && rutaDesde(fila, el) !== d.ruta_etiqueta) continue;   // misma posicion en la fila
      const celda = porRuta(fila, d.ruta_valor);
      if (celda && celda !== el && !el.contains(celda) && !celda.contains(el)) return celda;
    }
    return null;
  }
  function leerCelda(d) {
    const c = celdaDe(d);
    if (!c) return null;
    const f = campoEn(c);
    return f ? leer(f) : limpio(c.innerText || c.textContent);
  }
  function rutaCss(el) {
    const partes = [];
    for (let e = el; e && e.nodeType === 1 && e !== document.body; e = e.parentElement) {
      let i = 1;
      for (let h = e.previousElementSibling; h; h = h.previousElementSibling) if (h.tagName === e.tagName) i++;
      partes.unshift(`${e.tagName.toLowerCase()}:nth-of-type(${i})`);
    }
    return partes.join(" > ");
  }
  const idEstable = id => id && !/\d{3,}|[-_][0-9a-f]{6,}/i.test(id);
  const claseComun = e => {                     // la clase que comparte una fila con sus hermanas
    const h = e.nextElementSibling || e.previousElementSibling;
    if (!h) return "";
    return [...e.classList].find(c => h.classList.contains(c) && !/\d{3,}/.test(c)) || "";
  };
  function candidatos(tipo) {
    const ok = tipo === "campo" ? esCampo : () => true;
    return [...todos(document)].filter(e => visible(e) && ok(e));
  }
  function describir(el, tipo) {
    const et = etiqueta(el);
    const mismos = tipo === "campo" || tipo === "texto" ? candidatos(tipo).filter(e => etiqueta(e) === et) : [];
    return {
      tipo, tag: el.tagName.toLowerCase(), testid: el.getAttribute("data-testid") || "",
      name: el.getAttribute("name") || "", aria: el.getAttribute("aria-label") || "",
      placeholder: el.getAttribute("placeholder") || "", title: el.getAttribute("title") || "",
      id: idEstable(el.id) ? el.id : "", idBruto: el.id || "", etiqueta: et,
      clase: tipo === "fila" ? claseComun(el) : "",
      orden: Math.max(0, mismos.indexOf(el)), css: rutaCss(el),
      valor: leer(el).slice(0, 120),
      ...(tipo === "campo" ? (filaDe(el) || { subida: -1, ruta_valor: "" }) : {}),
    };
  }
  function activo() {
    let a = document.activeElement;
    while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
    if (!a || a === document.body || a.tagName === "IFRAME" || a.tagName === "FRAME") return null;
    return a;
  }
  function seleccionado() {
    const s = getSelection();
    if (!s || !s.toString().trim()) return null;
    const n = s.anchorNode;
    return n.nodeType === 1 ? n : n.parentElement;
  }
  // doble clic en la fila: se describe en el momento (al abrir el envio la lista puede volver a pintarse)
  // y se deja que MediaCentral lo abra como siempre
  function escucharDobleClic() {
    window.__mc_clic = null;
    window.__mc_pulsado = null;
    if (window.__mc_oyentes) return;
    window.__mc_oyentes = true;
    const fila = ev => { try { return describir(hastaFila(ev.composedPath()[0]), "fila"); } catch (x) { return null; } };
    document.addEventListener("mousedown", ev => { window.__mc_pulsado = fila(ev); }, true);
    document.addEventListener("dblclick", ev => { window.__mc_clic = fila(ev); }, true);
  }
  function hastaFila(el) {
    // primero una fila de verdad (tr o role=row) por encima de la celda pulsada
    for (let e = el; e && e !== document.body; e = padre(e))
      if (e.tagName === "TR" || (e.getAttribute && e.getAttribute("role") === "row")) return e;
    // si no, el primer elemento que tenga hermanas iguales apiladas debajo o encima (las celdas van de lado)
    for (let e = el; e && padre(e) && padre(e) !== document.body; e = padre(e)) {
      const p = padre(e);
      const iguales = [...(p.children || [])].filter(h => h.tagName === e.tagName && h !== e);
      const r = e.getBoundingClientRect();
      const apiladas = iguales.filter(h => Math.abs(h.getBoundingClientRect().left - r.left) < 2 &&
                                           Math.abs(h.getBoundingClientRect().top - r.top) >= r.height - 1);
      if (apiladas.length >= 2 && r.height < 200) return e;
    }
    return el;
  }
  function clicCapturado() {
    return window.__mc_clic || window.__mc_pulsado || null;   // el mousedown, por si la lista no lanza dblclick
  }
  // la etiqueta por su texto y, detras de ella en el documento, el primer campo: aguanta ids y rutas cambiantes
  function porEtiqueta(et, tag) {
    const objetivo = norm(et);
    if (!objetivo) return null;
    const cands = candidatos("campo");
    for (const el of todos(document)) {
      if (el.children.length > 3 || !visible(el)) continue;
      if (norm(el.textContent) !== objetivo) continue;
      const f = cands.find(c => (el.compareDocumentPosition(c) & Node.DOCUMENT_POSITION_FOLLOWING) && !el.contains(c) &&
                                (!tag || c.tagName.toLowerCase() === tag || c.isContentEditable));
      if (f && norm(etiqueta(f)) === objetivo) return f;
    }
    return null;
  }
  function inventario() {          // todos los campos visibles, para el diagnostico
    return candidatos("campo").map(e => ({ tag: e.tagName.toLowerCase(), id: e.id || "", name: e.getAttribute("name") || "",
      etiqueta: etiqueta(e), valor: leer(e).slice(0, 50) }));
  }
  function buscarCon(d) {          // [elemento, por que via] o [null, ""]
    if (d.tipo === "fila") { const e = buscar(d); return [e, e ? "fila" : ""]; }
    const cands = candidatos(d.tipo);
    const mismoTag = e => d.tipo !== "campo" || e.tagName.toLowerCase() === d.tag || e.isContentEditable;
    const vias = [
      ["testid", d.testid && (e => e.getAttribute("data-testid") === d.testid)],
      ["name", d.name && (e => e.getAttribute("name") === d.name)],
      ["aria", d.aria && (e => e.getAttribute("aria-label") === d.aria)],
      ["placeholder", d.placeholder && (e => e.getAttribute("placeholder") === d.placeholder)],
      ["etiqueta", d.etiqueta && (e => norm(etiqueta(e)) === norm(d.etiqueta))],
      ["title", d.title && (e => e.getAttribute("title") === d.title)],
      ["id", d.id && (e => e.id === d.id)],
    ].filter(v => v[1]);
    for (const [nombre, via] of vias) {
      const hay = cands.filter(via).filter(mismoTag);
      if (hay.length) return [hay[Math.min(d.orden || 0, hay.length - 1)], nombre];
    }
    const c = celdaDe(d);
    if (c) { const f = campoEn(c); if (f) return [f, "fila de la etiqueta"]; }
    try { const e = document.querySelector(d.css); if (e && visible(e)) return [e, "css"]; } catch (x) {}
    if (d.idBruto) { const e = document.getElementById(d.idBruto); if (e && visible(e)) return [e, "id cambiante"]; }
    const e = porEtiqueta(d.etiqueta, d.tag);
    if (e) return [e, "etiqueta + campo siguiente"];
    return [null, ""];
  }
  function buscar(d) {
    if (d.tipo === "fila") {
      try { const e = document.querySelector(d.css); if (e && visible(e)) return e; } catch (x) {}
      if (d.clase) {
        const e = [...todos(document)].find(x => x.tagName.toLowerCase() === d.tag && x.classList.contains(d.clase) && visible(x));
        if (e) return e;
      }
      return null;
    }
    return buscarCon(d)[0];
  }
  // todas las filas de la lista: las hermanas de la primera (mismo tipo y, si la tiene, misma clase)
  function filas(d) {
    const f = buscar(d);
    if (!f) return [];
    const p = f.parentElement;
    return [...p.children].filter(h => h.tagName === f.tagName && visible(h) && (!d.clase || h.classList.contains(d.clase)));
  }
  function claveFila(f) {              // con que reconocer una fila aunque la lista se vuelva a pintar
    for (const a of ["row-id", "data-id", "data-row-id", "aria-rowindex", "row-index", "data-index"])
      if (f.getAttribute(a)) return a + "=" + f.getAttribute(a);
    const m = /-row-(\d+)$/.exec(f.id || "");       // lazy-grid-<uuid>-row-113
    return m ? "row-" + m[1] : "";
  }
  function bajarLista(d) {             // listas que solo pintan lo que se ve: se baja para que pinten mas
    const f = buscar(d);
    if (!f) return false;
    let c = f.parentElement;
    while (c && c !== document.body && c.scrollHeight <= c.clientHeight + 2) c = c.parentElement;
    if (!c || c === document.body) return false;
    const antes = c.scrollTop;
    c.scrollTop = c.scrollTop + c.clientHeight * 0.8;
    return c.scrollTop !== antes;
  }
  function leer(el) {
    if (el.tagName === "SELECT") return el.selectedOptions.length ? el.selectedOptions[0].text : "";
    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") return el.value;
    return el.innerText || el.textContent || "";
  }
  // las celdas de una fila, para leer solo la del Name (en la fila tambien van fechas y otros numeros)
  function celdas(f) {
    const c = f.querySelectorAll('td, [role="gridcell"], [role="cell"]');
    return c.length ? [...c] : [...f.children];
  }
  function textoCelda(f, i) {
    const c = i >= 0 ? celdas(f)[i] : null;
    return limpio(c ? (c.innerText || c.textContent) : (f.innerText || f.textContent));
  }
  function indiceCelda(d, numero) {             // la celda que empieza por el numero del envio
    const f = buscar(d);
    if (!f) return -1;
    const rx = new RegExp("^\\W*" + numero + "(?!\\d)");
    return celdas(f).findIndex(c => rx.test(limpio(c.innerText || c.textContent)));
  }
  window.__mc = { describir, activo, seleccionado, buscar, buscarCon, filas, leer, escucharDobleClic, clicCapturado,
                  textoCelda, indiceCelda, inventario, claveFila, bajarLista, celdaDe, leerCelda, campoEn };
}
"""

BANNER_JS = r"""
([titulo, cuerpo, color]) => {
  let b = document.getElementById("__mc_banner");
  if (!b) {
    b = document.createElement("div");
    b.id = "__mc_banner";
    b.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;max-width:520px;max-height:45vh;" +
      "overflow:auto;background:#fff;border:2px solid #333;border-radius:8px;padding:10px 12px;" +
      "font:13px/1.4 'Segoe UI',Arial,sans-serif;color:#111;box-shadow:0 4px 16px rgba(0,0,0,.25)";
    b.addEventListener("dblclick", () => b.remove());
    document.body.appendChild(b);
  }
  b.style.borderColor = color;
  b.innerHTML = "";
  const h = document.createElement("div");
  h.style.cssText = "font-weight:600;margin-bottom:4px;color:" + color;
  h.textContent = titulo;
  b.appendChild(h);
  for (const linea of cuerpo) {
    const p = document.createElement("div");
    p.style.cssText = "margin:3px 0;" + (linea.startsWith("ALERTA") ? "color:#b00020;font-weight:600" : "");
    p.textContent = linea;
    b.appendChild(p);
  }
  const pie = document.createElement("div");
  pie.style.cssText = "margin-top:6px;color:#888;font-size:11px";
  pie.textContent = "catalogar · doble clic para cerrar este recuadro";
  b.appendChild(pie);
}
"""


# ---------------------------------------------------------------- configuracion y utilidades
class ErrorMC(Exception):
    pass


def cargar_config():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError as e:
            raise ErrorMC(f"mediacentral.json no es un JSON valido: {e.msg} en la linea {e.lineno}, columna {e.colno}")
    return cfg


def guardar_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _sin_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).upper()


NUMERO_RE = re.compile(r"(?<![\d_])(\d{4}_\d{6,9}|\d{7}|\d{4})(?![\d_/.:-])")
NUMERO_INICIO_RE = re.compile(r"^\W*(\d{4}_\d{6,9}|\d{7}|\d{4})(?![\d_/.:-])")


def numero_al_inicio(texto):
    """El numero de envio con el que empieza el Name de MediaCentral ("5619 POPE-FRANCE ..."), o "".
    Un numero por el medio ("EN 1500 ACTOS") no vale: eso es un titulo ya catalogado."""
    m = NUMERO_INICIO_RE.match(" ".join((texto or "").split()))
    return m.group(1) if m else ""


def numero_de(texto):
    """Primer numero de envio del texto (Reuters 4 cifras, AP 7, EBU 2026_10420363). Usa el mismo lector
    que el servidor (listas.py, que va en el mismo zip); si faltara, una version corta."""
    try:
        from listas import leer_texto
        trabajos, _ = leer_texto(texto or "")
        return trabajos[0][0] if trabajos else ""
    except ImportError:
        m = NUMERO_RE.search(texto or "")
        return m.group(1) if m else ""


def anotar(numero, resultado, f=None, motivo=""):
    """Una linea por envio en mediacentral_registro.csv: que se hizo, cuando y con que ficha."""
    nuevo = not REGISTRO_PATH.exists()
    f = f or {}
    with REGISTRO_PATH.open("a", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        if nuevo:
            w.writerow(["cuando", "numero", "resultado", "motivo", "NAME", "RESTRICCIONES", "ALERTA", "AVISOS"])
        w.writerow([datetime.now().strftime("%d/%m/%Y %H:%M:%S"), numero, resultado, motivo, f.get("NAME", ""),
                    f.get("RESTRICCIONES", ""), f.get("alerta", ""), " | ".join(f.get("avisos") or [])])


# ---------------------------------------------------------------- servidor de catalogar
class Servidor:
    """Lo minimo para hablar con servidor.py: entrar con la clave, pedir una ficha, encargar numeros."""

    def __init__(self, url, clave):
        if not url or not clave:
            raise ErrorMC("Faltan servidor_url o servidor_clave en mediacentral.json")
        self.url = url.rstrip("/")
        self.clave = clave
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _pedir(self, metodo, ruta, datos=None, reintento=True):
        cuerpo = json.dumps(datos).encode("utf-8") if datos is not None else None
        req = urllib.request.Request(self.url + ruta, data=cuerpo, method=metodo,
                                     headers={"Content-Type": "application/json"})
        try:
            with self.op.open(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            if e.code == 401 and reintento and ruta != "/login":
                self.entrar()
                return self._pedir(metodo, ruta, datos, reintento=False)
            try:
                detalle = json.loads(e.read().decode("utf-8")).get("detail", "")
            except Exception:
                detalle = ""
            raise ErrorMC(f"El servidor ha contestado {e.code} a {ruta}" + (f": {detalle}" if detalle else ""))
        except (urllib.error.URLError, OSError) as e:
            raise ErrorMC(f"No se puede hablar con el servidor ({self.url}): {getattr(e, 'reason', e)}")

    def entrar(self):
        self._pedir("POST", "/login", {"clave": self.clave}, reintento=False)

    def ficha(self, numero, lote=""):
        q = urllib.parse.urlencode({"numero": numero, **({"lote": lote} if lote else {})})
        return self._pedir("GET", "/api/ficha?" + q)

    def encargar(self, numeros):
        """Encarga uno o varios numeros (el servidor los trocea en lotes si son muchos).
        Devuelve {numero: id del lote en que ha quedado}."""
        numeros = list(numeros) if isinstance(numeros, (list, tuple, set)) else [numeros]
        r = self._pedir("POST", "/api/lotes/numeros", {"texto": " ".join(numeros)})
        return r.get("lote_de") or {n: r["id"] for n in numeros}


# ---------------------------------------------------------------- la pagina
def _mismo(a, b):
    return (a or "").lstrip("0") == (b or "").lstrip("0")


def _norm(s):
    """Para comparar textos de MediaCentral: sin tildes, en mayusculas y con los espacios juntos."""
    return " ".join(_sin_tildes(s).split())


class Fila:
    """Una fila de la lista de resultados. El numero solo se sabe si la columna Name esta a la vista;
    si no, se lee del campo Name al abrir el envio (que es lo seguro)."""

    def __init__(self, celda, texto, id_rejilla, handle, posicion):
        self.texto = " ".join((texto or "").split())
        self.numero = numero_de(celda) if celda else ""
        self.handle = handle
        # con que se reconoce la fila entre pasadas: el id que le da la rejilla, o su texto
        self.clave = id_rejilla or (self.texto + f" #{posicion}" if not self.texto else self.texto)

    def dice(self, texto_norm):
        return texto_norm in _norm(self.texto)


class Pagina:
    def __init__(self, page):
        self.page = page

    def _frames(self):
        for fr in self.page.frames:
            try:
                fr.evaluate(LIB_JS)
                yield fr
            except Exception:
                continue          # frames de otro origen o que se estan recargando

    # --- aprender
    def capturar(self, tipo):
        """Campo con el cursor, o texto seleccionado (para el numero): su descripcion."""
        for fr in self._frames():
            d = fr.evaluate("""(tipo) => {
                const el = (tipo === "texto" && window.__mc.seleccionado()) || window.__mc.activo();
                return el ? window.__mc.describir(el, tipo) : null; }""", tipo)
            if d:
                d["frame"] = fr.url.split("?")[0]
                return d
        return None

    def escuchar_doble_clic(self):
        for fr in self._frames():
            fr.evaluate("() => window.__mc.escucharDobleClic()")

    def clic_capturado(self):
        for fr in self._frames():
            d = fr.evaluate("() => window.__mc.clicCapturado()")
            if d:
                d["frame"] = fr.url.split("?")[0]
                return d
        return None

    # --- trabajar
    def _en_frames(self, d):
        frames = list(self._frames())
        frames.sort(key=lambda fr: 0 if fr.url.split("?")[0] == d.get("frame") else 1)   # primero el aprendido
        return frames

    def _elemento(self, d):
        for fr in self._en_frames(d):
            el = fr.evaluate_handle("(d) => window.__mc.buscar(d)", d).as_element()
            if el:
                return fr, el
        return None, None

    def _celda(self, d):
        """La celda del valor de un campo aprendido por su fila (aunque no tenga el editor abierto)."""
        if d.get("subida", -1) is None or d.get("subida", -1) < 0:
            return None, None
        for fr in self._en_frames(d):
            el = fr.evaluate_handle("(d) => window.__mc.celdaDe(d)", d).as_element()
            if el:
                return fr, el
        return None, None

    def leer(self, d):
        """Lo que tiene el campo: el editor si esta abierto, y si no, el texto de su celda."""
        _, el = self._elemento(d)
        if el:
            return el.evaluate("e => window.__mc.leer(e)")
        _, celda = self._celda(d)
        if celda:
            return celda.evaluate("c => { const f = window.__mc.campoEn(c); return f ? window.__mc.leer(f) : (c.innerText || c.textContent || '').trim(); }")
        return None

    def _abrir_editor(self, d):
        """Clic en la celda del valor para que aparezca el editor (MediaCentral solo lo crea al pulsar).
        Devuelve el editor o None."""
        _, celda = self._celda(d)
        if not celda:
            return None
        try:
            celda.scroll_into_view_if_needed(timeout=3000)
            celda.click(timeout=3000)
        except Exception:
            return None
        for _ in range(10):
            self.page.wait_for_timeout(200)
            el = celda.evaluate_handle("c => window.__mc.campoEn(c)").as_element()
            if el:
                return el
        return None

    def escribir(self, d, valor):
        """Escribe el valor y comprueba que se ha quedado. Lanza ErrorMC si no."""
        _, el = self._elemento(d)
        if not el:
            el = self._abrir_editor(d)
        if not el:
            raise ErrorMC(f"no encuentro el campo '{d.get('etiqueta') or d.get('css')}'")
        objetivo = " ".join(valor.split())
        leer = lambda: " ".join((el.evaluate("e => window.__mc.leer(e)") or "").split())
        if d.get("tag") == "select":
            el.select_option(label=valor)
        else:
            el.fill(valor)
            el.evaluate("""e => { e.dispatchEvent(new Event("input", {bubbles: true}));
                                 e.dispatchEvent(new Event("change", {bubbles: true})); }""")
            if leer() != objetivo:
                # campos con autocompletado (el catalogador suele serlo): se teclea y se confirma con Enter
                el.click()
                el.press("Control+A")
                el.type(valor, delay=20)
                el.press("Enter")
                self.page.wait_for_timeout(500)
        if leer() != objetivo:
            raise ErrorMC(f"el campo '{d.get('etiqueta')}' no se ha quedado con el texto (tiene: {leer()[:60]!r})")
        # salir del campo para que el valor se confirme (en MediaCentral el editor se cierra al perder el foco)
        try:
            el.evaluate("e => e.blur()")
        except Exception:
            pass
        self.page.wait_for_timeout(300)
        quedado = " ".join((self.leer(d) or "").split())
        if quedado != objetivo:
            raise ErrorMC(f"el campo '{d.get('etiqueta')}' no ha conservado el texto al salir (tiene: {quedado[:60]!r})")

    def filas(self, d):
        """Las filas de la lista de resultados, como objetos Fila."""
        celda = int(d.get("celda", -1))
        for fr in self._en_frames(d):
            lista = fr.evaluate_handle("(d) => window.__mc.filas(d)", d)
            n = lista.evaluate("l => l.length")
            if n:
                salida = []
                for i in range(n):
                    h = lista.evaluate_handle("(l, i) => l[i]", i).as_element()
                    datos = h.evaluate("""(e, i) => [window.__mc.textoCelda(e, i), e.innerText || "",
                                                      window.__mc.claveFila(e)]""", celda)
                    salida.append(Fila(datos[0], datos[1], datos[2], h, i))
                return salida
        return []

    def bajar_lista(self, d):
        """Baja la lista un poco (para las que solo pintan las filas que se ven). True si se ha movido."""
        for fr in self._en_frames(d):
            try:
                if fr.evaluate("(d) => window.__mc.bajarLista(d)", d):
                    self.page.wait_for_timeout(700)
                    return True
            except Exception:
                continue
        return False

    def indice_celda(self, d, numero):
        for fr in self._en_frames(d):
            i = fr.evaluate("([d, n]) => window.__mc.indiceCelda(d, n)", [d, re.escape(numero)])
            if i is not None and i >= 0:
                return i
        return -1

    def como_se_encuentra(self, d):
        """Por que via se vuelve a encontrar el elemento aprendido, o "" si no se encuentra."""
        for fr in self._en_frames(d):
            via = fr.evaluate("(d) => window.__mc.buscarCon(d)[1]", d)
            if via:
                return via
        return ""

    def diagnostico(self, campos):
        """Comprueba cada campo aprendido y deja en debug/ lo necesario para entender un fallo:
        mediacentral_diagnostico.txt (que se encuentra, por que via, y todos los campos visibles de cada
        marco) y mediacentral_pantalla.png. Devuelve la lista de claves que no se encuentran."""
        DEBUG_DIR.mkdir(exist_ok=True)
        fallan, lineas = [], [f"Diagnostico {datetime.now():%d/%m/%Y %H:%M:%S}  url: {self.page.url}", ""]
        for clave, d in campos.items():
            via = self.como_se_encuentra(d)
            if not via and self._celda(d)[1] is not None:
                via = "celda de su fila (el editor se abre al pulsar)"
            v = self.leer(d) if via else None
            estado = f"OK (por {via})" if via else "NO LO ENCUENTRO"
            print(f"   {clave:<14} {estado:<32} {(v or '')[:50]!r}")
            lineas.append(f"{clave}: {estado}   valor: {(v or '')[:80]!r}")
            lineas.append(f"    aprendido: tag={d.get('tag')} etiqueta={d.get('etiqueta')!r} id={d.get('idBruto') or d.get('id')!r} "
                          f"name={d.get('name')!r} subida={d.get('subida')} ruta_valor={d.get('ruta_valor')!r} frame={d.get('frame')!r}")
            lineas.append(f"    css: {d.get('css')}")
            if not via:
                fallan.append(clave)
        lineas.append("")
        for fr in self._frames():
            try:
                inv = fr.evaluate("() => window.__mc.inventario()")
            except Exception as e:
                inv, lineas = [], lineas + [f"[{fr.url}] no se ha podido leer ({type(e).__name__})"]
            lineas.append(f"[{fr.url.split('?')[0]}] {len(inv)} campo(s) visibles:")
            for c in inv:
                lineas.append(f"    <{c['tag']}> etiqueta={c['etiqueta']!r} id={c['id']!r} name={c['name']!r} valor={c['valor']!r}")
        try:
            (DEBUG_DIR / "mediacentral_diagnostico.txt").write_text("\n".join(lineas), encoding="utf-8")
            self.page.screenshot(path=str(DEBUG_DIR / "mediacentral_pantalla.png"), full_page=False)
        except Exception:
            pass
        return fallan

    def recuadro(self, titulo, lineas, color="#1f6f43"):
        try:
            self.page.evaluate(BANNER_JS, [titulo, lineas, color])
        except Exception:
            pass


def abrir_navegador(pw, cfg):
    canal = (cfg.get("navegador") or "chrome").lower()
    kw = dict(user_data_dir=str(PERFIL_DIR), headless=False, no_viewport=True, args=["--start-maximized"],
              # el servidor de MediaCentral lleva un certificado de la red interna (ERR_CERT_AUTHORITY_INVALID):
              # se acepta solo dentro de este navegador de la herramienta, no en tu Chrome de siempre
              ignore_https_errors=bool(cfg.get("ignorar_certificado", True)))
    ultimo = None
    for c in dict.fromkeys([canal, "chrome", "msedge", "chromium"]):
        try:
            ctx = pw.chromium.launch_persistent_context(**kw, **({} if c == "chromium" else {"channel": c}))
            break
        except Exception as e:
            ultimo = e
    else:
        raise ErrorMC(f"No se ha podido abrir el navegador ({ultimo}). Instala Chrome o Edge, o: python -m playwright install chromium")
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    if cfg.get("mediacentral_url") and page.url in ("about:blank", ""):
        try:
            page.goto(cfg["mediacentral_url"], timeout=60000)
        except Exception as e:                   # sin red, VPN caida, direccion mal escrita: se entra a mano
            print(f"\nNo se ha podido abrir {cfg['mediacentral_url']} ({str(e).splitlines()[0][:120]}).")
            print("Escribe la direccion a mano en la ventana de Chrome que se ha abierto y sigue.\n")
    return ctx, page


# ---------------------------------------------------------------- aprender la pantalla
def aprender(cfg):
    from playwright.sync_api import sync_playwright
    print("\nSe abre Chrome. Entra en MediaCentral y haz la busqueda \"tratar agencias\", con la lista a la vista")
    print("(no abras aun ningun envio). Luego vuelve a esta ventana.\n")
    with sync_playwright() as pw:
        ctx, page = abrir_navegador(pw, cfg)
        input("Cuando tengas la lista, pulsa Enter aqui... ")
        pag = Pagina(page)
        campos = {}
        for clave, tipo, que in PASOS:
            while True:
                if tipo == "fila":
                    pag.escuchar_doble_clic()
                print(f"\n>> {que}")
                input("   Hecho eso, pulsa Enter aqui: ")
                d = pag.clic_capturado() if tipo == "fila" else pag.capturar(tipo)
                if not d:
                    print("   No he visto nada: " + ("haz doble clic en el primer envio de la lista y repite." if tipo == "fila"
                                                     else "haz clic dentro del campo y repite."))
                    continue
                repetido = next((k for k, x in campos.items() if x["css"] == d["css"] and x["frame"] == d["frame"]), None)
                if repetido:
                    print(f"   Es el mismo que ya has dado para '{repetido}'. Repite.")
                    continue
                visto = d["etiqueta"] or d["css"][-60:]
                print(f"   Visto: <{d['tag']}> '{visto}'   contiene: {d['valor'][:70]!r}")
                if clave == "name" and not numero_de(d["valor"]):
                    print("   Ojo: ahi no veo un numero de envio (4 cifras Reuters, 7 AP, 2026_... EBU).")
                if input("   ¿Es ese? (Enter = si, r = repetir): ").strip().lower() != "r":
                    campos[clave] = d
                    break
            if clave == "name":
                # en que columna de la lista esta el Name, si esta a la vista: asi se pueden encargar todos
                # los numeros de golpe; si no se ve, el numero se lee al abrir cada envio, que es lo seguro
                num = numero_de(campos["name"]["valor"])
                campos["fila"]["celda"] = pag.indice_celda(campos["fila"], num) if num else -1
                if campos["fila"]["celda"] < 0:
                    print("   (en la lista no se ve la columna Name: el numero se leera al abrir cada envio)")
        cfg["abrir_con_doble_clic"] = True
        if not cfg.get("catalogador"):
            cfg["catalogador"] = input("\n¿Tu usuario de catalogador en MediaCentral (ej. I23785)?: ").strip().upper()
        # se guarda ANTES de comprobar: lo aprendido no se pierde aunque la comprobacion falle
        cfg["campos"] = campos
        guardar_config(cfg)
        DEBUG_DIR.mkdir(exist_ok=True)
        (DEBUG_DIR / "mediacentral_campos.json").write_text(json.dumps(campos, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nGuardado en {CONFIG_PATH.name}. No he tocado nada del envio abierto.")
        comprobar_campos(pag, campos)
        ctx.close()


def comprobar_campos(pag, campos):
    """Vuelve a buscar cada campo aprendido y la lista. Si algo no se encuentra, dice que mandar."""
    print("\nComprobando que se encuentran otra vez...")
    fallan = pag.diagnostico(campos)
    try:
        filas = pag.filas(campos["fila"])
        print(f"   filas de la lista: {len(filas)}" + (f"   primera: {filas[0].texto[:70]!r}" if filas else ""))
        if filas:
            con_numero = sum(1 for f in filas if f.numero)
            print(f"   con el numero a la vista: {con_numero} de {len(filas)}"
                  + ("" if con_numero else " (se leera del campo Name al abrir cada envio)"))
        else:
            fallan.append("filas")
    except Exception as e:
        print(f"   filas de la lista: error ({type(e).__name__}: {str(e).splitlines()[0][:100]})")
        fallan.append("filas")
    if fallan:
        print(f"\nNo se vuelve(n) a encontrar: {', '.join(fallan)}.")
        print("He dejado en la carpeta debug\\ dos ficheros: mediacentral_diagnostico.txt y mediacentral_pantalla.png.")
        print("Mandalos para ver como es ese campo y ajustar la busqueda. Lo aprendido queda guardado igual.")
    else:
        print("\nTodo se encuentra. Prueba con: python mediacentral.py --max 3")
    return fallan


# ---------------------------------------------------------------- trabajar
def texto_ficha(f, cfg, campo):
    if cfg.get("presentacion") == "normalizado" and isinstance(f.get("normal"), dict) and f["normal"].get(campo):
        return f["normal"][campo]
    return f.get(campo) or ""


class Relleno:
    """El trabajo: recorrer la lista, abrir cada envio con doble clic, leer su numero del Name, rellenar,
    pasar al siguiente (asi guarda MediaCentral) y comprobar al final."""

    ESPERA_ABRIR = 30            # medios segundos de espera a que se abra un envio

    def __init__(self, pag, srv, cfg, revisar=False, maximo=None, pausa=1.0):
        self.pag, self.srv, self.cfg = pag, srv, cfg
        self.campos = cfg["campos"]
        self.revisar = revisar
        self.maximo = maximo
        self.pausa = pausa
        self.pendiente = _norm(cfg.get("texto_pendiente") or "TRATAR AGENCIAS")
        self.espera = float(cfg.get("espera_ficha_min") or 8) * 60
        self.lotes = {}          # numero -> lote en el que se encargo
        self.rellenados = []     # numeros en orden; se guardan al pasar al siguiente y se comprueban al final
        self.escrito = {}        # numero -> (NAME, COMMENT) escritos, para reconocerlos despues
        self.comment_antes = {}  # numero -> COMMENT que tenia, si decia algo mas que tratar agencias
        self.guardados, self.no_guardados, self.saltados, self.tratados = [], [], [], set()
        self.fichas = {}         # numero -> ficha rellenada (para el resumen)
        self.lista_con_comment = False   # las filas enseñan el Comments (se puede saltar lo ya tratado sin abrirlo)
        self.lista_con_numero = False    # las filas enseñan el numero (se puede encargar todo de golpe)
        self.clave_abierta = None        # clave de la fila del envio que esta abierto ahora

    def _saltar(self, numero, motivo, f=None):
        print(f"  {numero}: SALTADO, {motivo}")
        self.pag.recuadro(f"{numero}: saltado", [motivo], "#b00020")
        self.saltados.append((numero, motivo))
        anotar(numero, "saltado", f, motivo)

    def _name(self):
        return self.pag.leer(self.campos["name"]) or ""

    def _pendiente(self):
        return self.pendiente in _norm(self.pag.leer(self.campos["comment"]) or "")

    # --- la lista
    def filas(self):
        return self.pag.filas(self.campos["fila"])

    def por_tratar(self):
        """Filas que pueden estar pendientes: si la lista enseña el Comments, solo las que dicen tratar agencias."""
        return [f for f in self.filas() if not self.lista_con_comment or f.dice(self.pendiente)]

    # --- la ficha del servidor, esperando si hace falta
    def ficha(self, numero):
        desde = time.time()
        while True:
            f = self.srv.ficha(numero, self.lotes.get(numero, ""))
            if f.get("estado") == "no":
                self.lotes.update(self.srv.encargar([numero]))
                print(f"  {numero}: no estaba encargada; encargada ahora")
                continue
            if f.get("estado") in ("hecha", "error"):
                return f
            if time.time() - desde > self.espera:
                return {"estado": "error", "error": f"la ficha no ha salido en {self.espera/60:.0f} min"}
            self.pag.recuadro(f"{numero}: catalogando...", [f"Esperando la ficha ({(time.time()-desde)/60:.0f} min)."], "#b26a00")
            time.sleep(max(self.pausa, 3))

    def _numero_abierto(self):
        """Numero del envio abierto: el del principio del Name; si no lo hay pero el Comments dice tratar
        agencias, uno que este por el medio; si no, "" (ya tratado o no es de agencia)."""
        v = self._name()
        return numero_al_inicio(v) or (numero_de(v) if self._pendiente() else "")

    # --- abrir una fila con doble clic y esperar a que se vea otro envio
    def _refrescar(self, fila):
        """La misma fila tras volver a pintarse la lista (su asiento anterior ya no vale), o None."""
        return next((f for f in self.filas() if f.clave == fila.clave), None)

    def _pulsar(self, fila):
        """Doble clic en la fila, por coordenadas (no depende de que su asiento siga vivo al pulsar). Antes se
        comprueba que en ese punto de la pantalla esta de verdad la fila (no un borde del contenedor ni otra
        cosa que la tape); si no, se desplaza lo minimo y se vuelve a localizar, porque las listas que solo
        pintan lo visible se vuelven a pintar al desplazarse. True si ha pulsado."""
        raton = self.pag.page.mouse
        for intento in range(4):
            if intento:
                fila = self._refrescar(fila) or fila
            try:
                caja = fila.handle.bounding_box()
                a_la_vista = fila.handle.evaluate("""e => { const r = e.getBoundingClientRect();
                    const t = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                    return !!t && (t === e || e.contains(t)); }""")
            except Exception:
                caja, a_la_vista = None, False
            if caja and a_la_vista and caja["width"] > 2 and caja["height"] > 2:
                x, y = caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2
                if self.cfg.get("abrir_con_doble_clic", True):
                    raton.dblclick(x, y)
                else:
                    raton.click(x, y)
                return True
            try:
                fila.handle.evaluate("e => e.scrollIntoView({block: 'center'})")
            except Exception:
                pass
            self.pag.page.wait_for_timeout(400)
        return False

    def _esperar_name(self, condicion, medios_segundos):
        for _ in range(medios_segundos):
            self.pag.page.wait_for_timeout(500)
            v = self._name()
            if condicion(v):
                self.pag.page.wait_for_timeout(500)      # que terminen de cargar los campos
                return v
        return None

    def abrir(self, fila, numero=""):
        """Doble clic en la fila y espera a que el Name enseñe ese numero o, si no se sabe, a que cambie.
        Si no cambia (esa fila ya estaba abierta, o el doble clic no ha entrado), abre otra y vuelve.
        Devuelve el texto del Name del envio abierto, o "" si no se ha abierto."""
        antes = self._name()
        if numero and _mismo(numero_al_inicio(antes), numero):
            self.clave_abierta = fila.clave                # ya esta abierto
            return antes
        if not self._pulsar(fila):
            return ""
        if numero:
            v = self._esperar_name(lambda v: _mismo(numero_al_inicio(v), numero), self.ESPERA_ABRIR)
        else:
            v = self._esperar_name(lambda v: v and v != antes, 8)
            if v is None:
                # puede que ya estuviera abierto: se abre otra fila y se vuelve a esta, y entonces si cambia
                otra = next((f for f in self.filas() if f.clave != fila.clave), None)
                if otra is not None and self._pulsar(otra):
                    self._esperar_name(lambda v: v and v != antes, 8)
                    paso = self._name()
                    if self._pulsar(fila):
                        v = self._esperar_name(lambda v: v and v != paso, self.ESPERA_ABRIR)
        if v is None:
            return ""
        self.clave_abierta = fila.clave
        return v

    # --- un envio abierto: True si se ha rellenado
    def tratar(self, numero):
        comment_antes = self.pag.leer(self.campos["comment"]) or ""
        if self.pendiente not in _norm(comment_antes):
            print(f"  {numero}: ya tratado, no se toca")
            self.tratados.add(numero)
            return False
        f = self.ficha(numero)
        if f.get("estado") != "hecha":
            self._saltar(numero, "la ficha ha fallado: " + (f.get("error") or "sin ficha")[:90], f)
            return False
        name, comment = texto_ficha(f, self.cfg, "NAME"), texto_ficha(f, self.cfg, "COMMENT")
        # en el orden en que se hace a mano: Name (sin numero), Titulo (el mismo NAME), Comments, catalogador
        escribir = [("name", name), ("titulo", name), ("comment", comment), ("catalogador", self.cfg.get("catalogador", ""))]
        antes = {clave: self.pag.leer(self.campos[clave]) or "" for clave, _ in escribir}
        escritos = []
        try:
            for clave, v in escribir:
                if v:
                    escritos.append(clave)
                    self.pag.escribir(self.campos[clave], v)
        except ErrorMC as e:
            # MediaCentral guardaria lo escrito al pasar al siguiente: se deja todo como estaba (el Name con su
            # numero y el Comments en tratar agencias) para que siga saliendo en tu busqueda y lo repases
            try:
                for clave in reversed(escritos):
                    if antes[clave] or self.campos[clave].get("tag") != "select":
                        self.pag.escribir(self.campos[clave], antes[clave])
                nota = "; se ha dejado como estaba"
            except ErrorMC:
                nota = "; OJO: ha quedado a medias, revisalo a mano"
            self._saltar(numero, str(e) + nota, f)
            return False
        if _norm(comment_antes) != self.pendiente:
            self.comment_antes[numero] = " ".join(comment_antes.split())   # decia algo mas: que lo veas
        lineas = ([f["alerta"]] if f.get("alerta") else []) + [f"RESTRICCIONES: {f.get('RESTRICCIONES') or 'SIN AVISO'}"] \
            + [f"AVISO: {a}" for a in (f.get("avisos") or [])] \
            + ([f"El Comments decia: {self.comment_antes[numero]}"] if numero in self.comment_antes else [])
        if self.revisar:
            self.pag.recuadro(f"{numero}: rellenado. Revisalo y pasa tu al siguiente (asi se guarda).", lineas, "#b26a00")
        else:
            self.pag.recuadro(f"{numero}: rellenado (se guarda al pasar al siguiente)", lineas,
                              "#b00020" if f.get("alerta") else "#1f6f43")
        print(f"  {numero}: rellenado" + (" · ALERTA" if f.get("alerta") else "")
              + ("" if (f.get("RESTRICCIONES") or "SIN AVISO") == "SIN AVISO" else " · con RESTRICCIONES")
              + (f" · el Comments decia {self.comment_antes[numero]!r}" if numero in self.comment_antes else ""))
        self.rellenados.append(numero)
        self.escrito[numero] = (name, comment)
        self.fichas[numero] = f
        anotar(numero, "rellenado", f)
        return True

    def _sigue_abierto(self, numero):
        """Modo revisar: True mientras el envio rellenado siga abierto. Su Name ya no lleva numero, asi que se
        sabe que has pasado a otro cuando el Name lleva otro numero o no es ni el NAME escrito ni su numero."""
        v = self._name()
        n = numero_al_inicio(v)
        if n:
            return _mismo(n, numero)
        name, comment = self.escrito[numero]
        return _norm(v) == _norm(name) or _norm(self.pag.leer(self.campos["comment"]) or "") == _norm(comment)

    def encargar_de_golpe(self, filas):
        numeros = [f.numero for f in filas if f.numero]
        if not numeros:
            return
        print(f"En la lista se ven {len(numeros)} numero(s). Encargando al servidor los que falten...")
        pendientes = []
        for n in numeros:
            f = self.srv.ficha(n)
            if f.get("estado") == "no":
                pendientes.append(n)
            elif f.get("lote"):
                self.lotes[n] = f["lote"]
        if pendientes:
            self.lotes.update(self.srv.encargar(pendientes))
            print(f"  encargados {len(pendientes)}: {', '.join(pendientes[:12])}" + (" ..." if len(pendientes) > 12 else ""))

    def todo(self):
        filas = self.filas()
        if not filas:
            raise ErrorMC("No veo la lista de resultados: haz la busqueda \"tratar agencias\" (con la lista a la vista) y vuelve a lanzarlo")
        self.lista_con_comment = any(f.dice(self.pendiente) for f in filas)
        self.lista_con_numero = any(f.numero for f in filas)
        por_tratar = self.por_tratar()
        print(f"En la lista hay {len(filas)} fila(s)" + (f", {len(por_tratar)} con tratar agencias" if self.lista_con_comment else "")
              + ("." if self.lista_con_numero else ". El numero se leera al abrir cada envio."))
        if not por_tratar:
            print("No hay nada por tratar.")
            return
        if self.lista_con_numero:
            self.encargar_de_golpe(por_tratar)
        else:
            print("  (como la lista no enseña el numero, cada envio se encarga al abrirlo; si quieres tenerlos ya\n"
                  "   hechos, sube antes el CSV de la busqueda a la pagina de catalogar)")
        vistos = set()
        sin_nuevas = 0
        while True:
            if self.maximo is not None and len(self.rellenados) >= self.maximo:
                print(f"\nTope de {self.maximo} alcanzado.")
                break
            # la siguiente fila que no se ha tocado aun (la lista puede cambiar al guardarse)
            siguiente = next((f for f in self.por_tratar() if f.clave not in vistos), None)
            if not siguiente:
                # listas que solo pintan lo que se ve: se baja y se vuelve a mirar
                if sin_nuevas < 3 and self.pag.bajar_lista(self.campos["fila"]):
                    sin_nuevas += 1
                    continue
                break
            sin_nuevas = 0
            vistos.add(siguiente.clave)
            name = self.abrir(siguiente, siguiente.numero)
            if not name:
                self._saltar(siguiente.numero or siguiente.texto[:40] or "fila", "no se ha abierto con doble clic en su fila")
                continue
            numero = self._numero_abierto()
            if not numero:
                # el Name no empieza por un numero: es un envio ya tratado (o no es de agencia)
                print(f"  {name[:40]!r}: sin numero en el Name, ya tratado o no es de agencia; no se toca")
                self.tratados.add(name[:40])
                continue
            if numero in self.rellenados or numero in self.tratados:
                continue
            print(f"{numero}:")
            try:
                relleno = self.tratar(numero)
            except ErrorMC as e:
                self._saltar(numero, str(e))
                continue
            if self.revisar and relleno:
                # lo revisas tu; al pasar a otro envio MediaCentral lo guarda y el programa sigue
                while self._sigue_abierto(numero):
                    time.sleep(self.pausa)
        self.comprobar()
        self.resumen()

    # --- al final: que se ha guardado de verdad
    def _guardado(self, numero, como):
        self.guardados.append(numero)
        anotar(numero, "guardado", self.fichas.get(numero), como)

    def _no_guardado(self, numero, motivo):
        print(f"  {numero}: NO GUARDADO, {motivo}")
        self.no_guardados.append((numero, motivo))
        anotar(numero, "NO guardado", self.fichas.get(numero), motivo)

    def _fila_de(self, numero):
        """('hecha' | 'pendiente' | None, Fila) de ese envio en la lista: por el NAME escrito si la lista ya
        se ha actualizado, o por su numero si aun no. None si la lista no lo enseña."""
        name = _norm(self.escrito[numero][0])[:60]
        for f in self.filas():
            if name and name in _norm(f.texto):
                return "hecha", f
            if _mismo(f.numero, numero):
                return "pendiente", f
        return None, None

    def comprobar(self):
        """El ultimo rellenado sigue abierto: hasta pasar a otro envio no se guarda. Luego se mira cada
        rellenado en la lista (con el NAME nuevo y sin tratar agencias = guardado) y, si la lista no lo deja
        claro, se abre y se miran sus campos."""
        if not self.rellenados:
            return
        print("\nComprobando que se han guardado...")
        ultimo = self.rellenados[-1]
        if self._sigue_abierto(ultimo):
            # pasar a otro envio (cualquiera que no sea este) para que MediaCentral guarde el ultimo
            name = _norm(self.escrito[ultimo][0])[:60]
            otro = next((f for f in self.filas() if f.clave != self.clave_abierta
                         and not (name and name in _norm(f.texto)) and not _mismo(f.numero, ultimo)), None)
            if otro is None or not self.abrir(otro):
                self._no_guardado(ultimo, "sigue abierto y no hay otro envio en la lista: pasa tu a otro para que se guarde")
                self.rellenados = self.rellenados[:-1]
        if not self.cfg.get("comprobar_al_final", True):
            for n in self.rellenados:
                self._guardado(n, "sin comprobar")
            return
        for n in self.rellenados:
            estado, fila = self._fila_de(n)
            if estado is None:
                if self.lista_con_numero or self.lista_con_comment:
                    self._guardado(n, "ya no sale en la busqueda")
                else:
                    self._guardado(n, "no comprobable: la lista no enseña ni el Name ni el Comments")
                continue
            if estado == "hecha" and (not self.lista_con_comment or not fila.dice(self.pendiente)):
                self._guardado(n, "comprobado en la lista")
                continue
            # la lista no se ha refrescado: se abre y se miran sus campos
            name, comment = self.escrito[n]
            abierto = self.abrir(fila)
            if not abierto:
                self._no_guardado(n, "no se ha podido volver a abrir para comprobarlo: miralo a mano")
            elif _norm(self._name()) == _norm(name) and not self._pendiente():
                self._guardado(n, "comprobado abriendolo")
            else:
                self._no_guardado(n, "al abrirlo sigue con su numero o diciendo tratar agencias")

    def resumen(self):
        rellenas = [(n, self.fichas[n]) for n in self.fichas]
        con_restr = [(n, f) for n, f in rellenas if (f.get("RESTRICCIONES") or "SIN AVISO") != "SIN AVISO"]
        con_alerta = [(n, f) for n, f in rellenas if f.get("alerta")]
        print("\n" + "=" * 70)
        print(f"Guardados: {len(self.guardados)} · NO guardados: {len(self.no_guardados)} · "
              f"ya tratados: {len(self.tratados)} · saltados: {len(self.saltados)}")
        if con_restr:
            print("\nCON RESTRICCIONES (rellenalas tu):")
            for n, f in con_restr:
                print(f"  {n}  {f['RESTRICCIONES']}")
        if con_alerta:
            print("\nCON ALERTA:")
            for n, f in con_alerta:
                print(f"  {n}  {f['alerta']}")
        if self.comment_antes:
            print("\nEL COMMENTS DECIA ALGO MAS QUE TRATAR AGENCIAS (se ha sustituido, miralo):")
            for n, c in self.comment_antes.items():
                print(f"  {n}  {c}")
        if self.no_guardados:
            print("\nNO GUARDADOS (abrelos y revisalos):")
            for n, m in self.no_guardados:
                print(f"  {n}  {m}")
        if self.saltados:
            print("\nSALTADOS (hazlos a mano o vuelve a lanzar):")
            for n, m in self.saltados:
                print(f"  {n}  {m}")
        print(f"\nTodo queda en {REGISTRO_PATH.name}.")
        lineas = [f"RESTRICCIONES {n}: {f['RESTRICCIONES']}" for n, f in con_restr] \
            + [f"{n}: {f['alerta']}" for n, f in con_alerta] \
            + [f"COMMENTS {n} decia: {c}" for n, c in self.comment_antes.items()] \
            + [f"NO GUARDADO {n}: {m}" for n, m in self.no_guardados] + [f"SALTADO {n}: {m}" for n, m in self.saltados]
        malo = con_restr or con_alerta or self.comment_antes or self.no_guardados or self.saltados
        self.pag.recuadro(f"Terminado: {len(self.guardados)} guardados", lineas or ["Nada pendiente."],
                          "#b00020" if malo else "#1f6f43")


def comprobar(cfg):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx, page = abrir_navegador(pw, cfg)
        input("Haz la busqueda \"tratar agencias\", abre un envio con doble clic y pulsa Enter aqui... ")
        comprobar_campos(Pagina(page), cfg["campos"])
        ctx.close()


def trabajar(cfg, revisar=False, maximo=None):
    from playwright.sync_api import sync_playwright
    if not cfg.get("catalogador"):
        raise ErrorMC("Falta \"catalogador\" en mediacentral.json (tu usuario, ej. I23785)")
    srv = Servidor(cfg["servidor_url"], cfg["servidor_clave"])
    srv.entrar()
    print("Servidor de catalogar: conectado.")
    with sync_playwright() as pw:
        ctx, page = abrir_navegador(pw, cfg)
        # si MediaCentral sacara un aviso del navegador (no deberia), se ve en la consola
        page.on("dialog", lambda d: (print(f"  Aviso de MediaCentral: {d.message[:150]!r} (cerrado)"), d.dismiss()))
        input("Haz en MediaCentral la busqueda \"tratar agencias\" (con la lista a la vista) y pulsa Enter aqui... ")
        Relleno(Pagina(page), srv, cfg, revisar=revisar, maximo=maximo).todo()
        input("\nPulsa Enter para cerrar el navegador... ")
        ctx.close()


def main():
    ap = argparse.ArgumentParser(description="Rellena en MediaCentral las fichas del servidor de catalogar")
    ap.add_argument("--aprender", action="store_true", help="enseñarle la pantalla de MediaCentral (la primera vez)")
    ap.add_argument("--revisar", action="store_true", help="rellena y espera a que revises y pases tu al siguiente")
    ap.add_argument("--max", type=int, default=None, help="tratar solo los N primeros (para probar)")
    ap.add_argument("--comprobar", action="store_true",
                    help="solo comprobar que los campos aprendidos se encuentran (deja un diagnostico en debug\\)")
    args = ap.parse_args()
    try:
        cfg = cargar_config()
        faltan = [c for c, _, _ in PASOS if c not in (cfg.get("campos") or {})]
        if args.aprender or faltan:
            if not args.aprender:
                print("Aun no se como es tu pantalla de MediaCentral: primero me la enseñas (solo esta vez).")
            aprender(cfg)
        elif args.comprobar:
            comprobar(cfg)
        else:
            trabajar(cfg, revisar=args.revisar, maximo=args.max)
        return 0
    except ErrorMC as e:
        print(e)
        return 1
    except KeyboardInterrupt:
        print("\nParado.")
        return 0
    except Exception:
        # nunca cerrarse en silencio: el error entero queda en pantalla y en debug\mediacentral_error.txt
        texto = traceback.format_exc()
        print("\nHa fallado algo inesperado:\n" + texto)
        try:
            DEBUG_DIR.mkdir(exist_ok=True)
            (DEBUG_DIR / "mediacentral_error.txt").write_text(f"{datetime.now():%d/%m/%Y %H:%M:%S}\n{texto}", encoding="utf-8")
            print("(guardado en debug\\mediacentral_error.txt)")
        except OSError:
            pass
        return 1
    finally:
        input("\nPulsa Enter para cerrar... ")


if __name__ == "__main__":
    sys.exit(main())
