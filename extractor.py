
# -*- coding: utf-8 -*-
            self._dump(f"AP{numero}_fallo")
            self.debug = debug_previo
            raise NotFound("NO ENCONTRADO" if tarjeta is None else "La ficha de AP no llego a mostrar sus datos")

        if self.debug:
            print(f"[debug] AP ficha via {modo}, {_time.time() - t0:.1f} s en total", file=sys.stderr)
        self._dump(f"AP{numero}_ficha")
        datos = self._ap_leer(titular)
        if numero not in (datos["id"] or "") and numero not in datos["texto"]:
            raise NotFound(f"La ficha abierta no contiene el ID {numero}")
        fecha_ficha = datos["fecha"] or fecha or ""
        if fecha and fecha_ficha and fecha != fecha_ficha:
            raise NotFound(f"NO ENCONTRADO EN ESA FECHA. El envio {numero} es del {fecha_ficha}")

        minis = self.miniaturas(numero, self.n_miniaturas)

        # dejar la pagina limpia para el siguiente envio
        if modo == "modal":
            try:
                page.locator("#btn_close").first.click(timeout=2000)
            except Exception:
                pass

        return {
            "agencia": "AP",
            "miniaturas": minis,
            "numero": numero,
            "fecha": fecha_ficha,
            "rev": "",
            "slug": datos["slug"],
            "headline": datos["headline"],
            "url": page.url,
            "texto": datos["texto"],
            "avisos": avisos,
        }

    # ------------------------------------------------------------------ API publica
    def fetch(self, numero, fecha=None):
        """Devuelve dict con agencia, numero, fecha, rev, slug, headline, url, texto.
        4 cifras -> Reuters Connect (Edit No); 7 cifras -> AP Newsroom (Story No)."""
        numero = numero.strip().upper()
        if numero.startswith("AP"):
            numero = numero[2:].strip()
        if fecha:
            fecha = fecha.strip()
            if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", fecha):
                raise ValueError("La fecha debe ser DD/MM/AAAA")
        if not numero.isdigit():
            raise ValueError(f"Numero no valido: {numero}")
        if len(numero) >= 6:
            return self._fetch_ap(numero, fecha)
        return self._fetch_reuters(numero.zfill(4), fecha)


def main():
    ap = argparse.ArgumentParser(description="Extrae la ficha de un envio de Reuters Connect o AP Newsroom")
    ap.add_argument("numero", help="Edit No de Reuters (4 cifras) o Story No de AP (7 cifras)")
    ap.add_argument("fecha", nargs="?", default=None, help="DD/MM/AAAA (opcional)")
    ap.add_argument("--debug", action="store_true", help="guarda capturas, HTML y texto en ./debug")
    ap.add_argument("--headed", action="store_true", help="muestra el navegador")
    args = ap.parse_args()
    try:
        from config import cargar_config
        cfg = cargar_config()
    except Exception:
        cfg = {"headless": False, "navegador": "auto"}
    try:
        with Extractor(headless=(cfg.get("headless", False) and not args.headed), debug=args.debug,
                       canal=cfg.get("navegador", "auto"), ruta=cfg.get("navegador_ruta")) as ex:
            ficha = ex.fetch(args.numero, args.fecha)
    except (NeedsLogin, NotFound, AntiBot) as e:
        print(f"ERROR: {e}")
        sys.exit(2)
    print(json.dumps({k: v for k, v in ficha.items() if k != "texto"}, ensure_ascii=False, indent=2))
    print("\n----- TEXTO -----\n")
    print(ficha["texto"])


if __name__ == "__main__":
    main()
