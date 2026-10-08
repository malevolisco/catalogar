# -*- coding: utf-8 -*-
"""
visor.py - El navegador de las agencias dentro de la pagina (pestaña Navegador).

El navegador de verdad sigue fuera de la pantalla (navegador_oculto). Lo que se ve en la pagina es su
imagen en directo (Page.startScreencast de Chrome) y lo que se hace en ella (clic, rueda, teclas,
pegar) se le manda al navegador. Asi se inicia sesion, se pasa una verificacion o se mira que esta
haciendo, sin ventanas aparte, desde la ventana de escritorio o desde fuera por Tailscale.

Playwright no se puede tocar desde dos hilos: todo lo que toca el navegador lo hace el hilo del Worker,
en bombear(). Los demas hilos (las peticiones de la pagina) solo leen la ultima imagen y dejan
ordenes en una cola, siempre bajo el cerrojo.
"""
import base64
import sys
import collections
import re
import threading
import time

# en una Raspberry (Linux) la imagen en directo va mas ligera: la mitad de cuadros y algo menos de calidad.
# Pesa menos para el equipo y para la conexion desde fuera (Tailscale); para leer y pulsar sobra
LIGERO = sys.platform.startswith("linux")
CALIDAD, CADA_CUADRO = (45, 2) if LIGERO else (65, 1)
VIEWPORT = (1400, 1000)        # el de abrir_contexto: las coordenadas que llegan van de 0 a 1 sobre esto


class Visor:
    def __init__(self):
        self.lock = threading.Lock()
        self.nueva = threading.Condition(self.lock)     # avisa a la emision en directo de cada imagen nueva
        self.ordenes = collections.deque(maxlen=500)
        self._reset()

    def _reset(self):
        self.ctx = None
        self.page = None
        self.cdp = None
        self.origen = ""             # "inicio de sesion", "trabajo", "comprobando"
        self.imagen = b""
        self.seq = 0
        self.t_imagen = 0.0
        self.paginas = []            # [{"i", "titulo", "url"}]
        self.activa = 0
        self.url = ""
        self.titulo = ""
        self.t_lista = 0.0
        self.mirando = 0.0           # la pagina ha pedido imagen hace poco: solo entonces se fuerza captura

    # ------------------------------------------------------------------ desde el hilo del Worker
    def conectar(self, ctx, origen):
        """Empieza a enseñar ese navegador. Si ya habia otro, lo sustituye."""
        mismo = ctx is self.ctx
        self.desconectar()
        with self.lock:
            self.ctx, self.origen = ctx, origen
            if not mismo:                    # lo pedido para el mismo navegador (al reconectar) no se pierde
                self.ordenes.clear()
        try:
            paginas = [p for p in ctx.pages if not p.is_closed()]
            self._elegir(paginas[0] if paginas else ctx.new_page())
        except Exception:
            pass

    def desconectar(self):
        try:
            if self.cdp is not None:
                self.cdp.detach()
        except Exception:
            pass
        with self.lock:
            seq = self.seq
            self._reset()
            self.seq = seq + 1             # la pagina ve el cambio y deja de pedir imagen
            self.nueva.notify_all()

    def _elegir(self, page):
        """Cambia la pestaña que se emite."""
        try:
            if self.cdp is not None:
                self.cdp.detach()
        except Exception:
            pass
        self.page, self.cdp = page, None
        try:
            cdp = page.context.new_cdp_session(page)

            def cuadro(params):
                datos = base64.b64decode(params.get("data") or "")
                with self.lock:
                    if self.cdp is cdp:
                        self.imagen, self.seq, self.t_imagen = datos, self.seq + 1, time.time()
                        self.nueva.notify_all()
                try:
                    cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
                except Exception:
                    pass

            cdp.on("Page.screencastFrame", cuadro)
            cdp.send("Page.startScreencast", {"format": "jpeg", "quality": CALIDAD, "everyNthFrame": CADA_CUADRO,
                                              "maxWidth": VIEWPORT[0], "maxHeight": VIEWPORT[1]})
            self.cdp = cdp
        except Exception:
            self.cdp = None
        self._captura()                  # una imagen ya, sin esperar a que cambie algo
        self._lista(forzar=True)

    def _captura(self):
        try:
            datos = self.page.screenshot(type="jpeg", quality=CALIDAD + 5, timeout=5000)
            with self.lock:
                self.imagen, self.seq, self.t_imagen = datos, self.seq + 1, time.time()
                self.nueva.notify_all()
        except Exception:
            pass

    def _lista(self, forzar=False):
        if not forzar and time.time() - self.t_lista < 1.5:
            return
        self.t_lista = time.time()
        try:
            paginas = [p for p in self.ctx.pages if not p.is_closed()]
        except Exception:
            paginas = []
        if self.page is not None and self.page not in paginas and paginas:
            self._elegir(paginas[-1])        # se ha cerrado la que se veia
            return
        lista = []
        for i, p in enumerate(paginas):
            try:
                lista.append({"i": i, "titulo": (p.title() or p.url)[:60], "url": p.url})
            except Exception:
                lista.append({"i": i, "titulo": "…", "url": ""})
        with self.lock:
            self.paginas = lista
            self.activa = paginas.index(self.page) if self.page in paginas else 0
            actual = lista[self.activa] if lista else {}
            self.url, self.titulo = actual.get("url", ""), actual.get("titulo", "")

    def bombear(self, espera_ms=60):
        """Atiende lo que ha pedido la pagina y deja que lleguen imagenes durante espera_ms. Las ordenes que
        llegan mientras tanto se atienden al momento (esperas de 20 ms), no al final. SOLO desde el hilo del Worker."""
        if self.ctx is None or self.page is None:
            return
        fin = time.time() + espera_ms / 1000
        while True:
            with self.lock:
                ordenes = list(self.ordenes)
                self.ordenes.clear()
            for o in ordenes:
                try:
                    self._ejecutar(o)
                except Exception:
                    pass
            if self.page is None:
                return
            self._lista()
            # si la emision no da imagenes (ventana tapada, nada cambia) y alguien esta mirando, una captura
            if time.time() - self.mirando < 3 and time.time() - self.t_imagen > 2:
                self._captura()
            if time.time() >= fin:
                return
            try:
                self.page.wait_for_timeout(20)          # aqui llegan los cuadros de la emision
            except Exception:
                return

    def _ejecutar(self, o):
        p = self.page
        ancho, alto = (p.viewport_size or {}).get("width", VIEWPORT[0]), (p.viewport_size or {}).get("height", VIEWPORT[1])
        t = o.get("t")
        x, y = float(o.get("x", 0)) * ancho, float(o.get("y", 0)) * alto
        if t == "clic":
            p.mouse.click(x, y, button=o.get("boton") or "left", click_count=int(o.get("veces") or 1))
        elif t == "rueda":
            p.mouse.move(x, y)
            p.mouse.wheel(float(o.get("dx") or 0), float(o.get("dy") or 0))
        elif t == "texto":
            p.keyboard.insert_text(str(o.get("s") or ""))
        elif t == "tecla":
            p.keyboard.press(str(o.get("k") or ""))
        elif t == "pestana":
            paginas = [q for q in self.ctx.pages if not q.is_closed()]
            i = int(o.get("i") or 0)
            if 0 <= i < len(paginas):
                self._elegir(paginas[i])
        elif t == "atras":
            p.go_back(timeout=15000)
        elif t == "recargar":
            p.reload(timeout=30000)
        elif t == "ir":
            url = str(o.get("url") or "").strip()
            if url:
                p.goto(url if re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I) else "https://" + url, timeout=30000)
        elif t == "ventana":
            from extractor import mostrar_ventana
            mostrar_ventana(self.ctx, p, bool(o.get("visible", True)))
        if t in ("clic", "tecla", "texto", "atras", "recargar", "ir", "rueda"):
            self.t_lista = 0                 # el titulo y la direccion pueden haber cambiado

    # ------------------------------------------------------------------ desde la pagina (otros hilos)
    def estado(self):
        with self.lock:
            self.mirando = time.time()
            return {"activo": self.ctx is not None, "origen": self.origen, "seq": self.seq,
                    "paginas": list(self.paginas), "activa": self.activa, "url": self.url, "titulo": self.titulo}

    def esperar_imagen(self, vista, espera=10.0):
        """Espera a que haya una imagen distinta de la vista (o a que se cierre el navegador): (seq, imagen)."""
        with self.nueva:
            self.mirando = time.time()
            self.nueva.wait_for(lambda: self.seq != vista or self.ctx is None, timeout=espera)
            return self.seq, self.imagen

    def ultima_imagen(self):
        with self.lock:
            self.mirando = time.time()
            return self.imagen

    def ordenar(self, orden):
        with self.lock:
            if self.ctx is None:
                return False
            self.ordenes.append(orden)
            return True

    @property
    def activo(self):
        return self.ctx is not None


VISOR = Visor()
