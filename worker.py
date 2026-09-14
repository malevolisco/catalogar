# -*- coding: utf-8 -*-
"""
worker.py - Panel remoto sin panel: usa las Issues de un repositorio privado de
GitHub como cola de trabajo.

Desde el trabajo (o el movil): New issue en el repositorio, titulo "0624" o
"0624 06/09/2026". Varios envios: uno por linea en el cuerpo de la issue.
Este script, en el PC de casa, procesa cada issue abierta, responde con las
cuatro lineas como comentario y la cierra. Si la sesion de Reuters caduca,
lo dice en un comentario y deja la issue abierta.

Ademas atiende comandos:
  - Issue con titulo "REGLA: texto"        -> añade la regla a reglas_extra.md
  - Issue con titulo "LISTAR REGLAS" o "LISTAR EJEMPLOS"  -> responde con la lista
  - Issue con titulo "QUITAR REGLA 3" o "QUITAR EJEMPLO 4682902" -> borra
  - Comentario "BUENA" (o "BUENA 4682902") en una issue con fichas -> aprueba esa ficha
    y la guarda en ejemplos.md como referencia para las siguientes.

Uso:
    python worker.py
"""
import re
import sys
import time
import traceback
from datetime import datetime

import requests

from config import cargar_config
from extractor import Extractor, NeedsLogin, NotFound, AntiBot
from redactor import (redactar, formatear, RedactorError, configurar, anadir_regla,
                      anadir_ejemplo, listar_ejemplos, quitar_ejemplo, listar_reglas_extra,
                      REGLAS_EXTRA_PATH)

JOB_RE = re.compile(r"\b(\d{7}|\d{4})(?:\s+(\d{2}/\d{2}/\d{4}))?\b")
REGLA_RE = re.compile(r"^\s*regla\s*[:\-]\s*(.+)$", re.I | re.S)
BUENA_RE = re.compile(r"^\s*buena\b\s*(\d{4,7})?", re.I)
QUITAR_EJEMPLO_RE = re.compile(r"^\s*quitar\s+ejemplo\s+(\d{4,7})", re.I)
QUITAR_REGLA_RE = re.compile(r"^\s*quitar\s+regla\s+(\d+)", re.I)
LISTAR_RE = re.compile(r"^\s*listar\s+(ejemplos|reglas)", re.I)
FICHA_RE = re.compile(r"ENVIO:\s*(.+?)\nNAME:\s*(.+?)\nCOMMENT:\s*(.+?)\nRESTRICCIONES:\s*(.+?)(?:\n|$)", re.S | re.I)
API = "https://api.github.com"


class GitHub:
    def __init__(self, repo, token):
        self.repo = repo
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def issues_abiertas(self):
        r = self.s.get(f"{API}/repos/{self.repo}/issues", params={"state": "open", "per_page": 30}, timeout=30)
        r.raise_for_status()
        return [i for i in r.json() if "pull_request" not in i]

    def comentar(self, numero_issue, texto):
        r = self.s.post(f"{API}/repos/{self.repo}/issues/{numero_issue}/comments", json={"body": texto}, timeout=30)
        r.raise_for_status()

    def comentarios(self, numero_issue):
        r = self.s.get(f"{API}/repos/{self.repo}/issues/{numero_issue}/comments",
                       params={"per_page": 50}, timeout=30)
        r.raise_for_status()
        return r.json()

    def issues_recientes(self, estado="all", n=30):
        r = self.s.get(f"{API}/repos/{self.repo}/issues",
                       params={"state": estado, "per_page": n, "sort": "updated"}, timeout=30)
        r.raise_for_status()
        return [i for i in r.json() if "pull_request" not in i]

    def editar_comentario(self, id_comentario, texto):
        r = self.s.patch(f"{API}/repos/{self.repo}/issues/comments/{id_comentario}", json={"body": texto}, timeout=30)
        r.raise_for_status()

    def cerrar(self, numero_issue):
        r = self.s.patch(f"{API}/repos/{self.repo}/issues/{numero_issue}", json={"state": "closed"}, timeout=30)
        r.raise_for_status()


def trabajos_de(issue):
    """Extrae (numero, fecha) del titulo y del cuerpo; sin duplicados, en orden."""
    texto = (issue.get("title") or "") + "\n" + (issue.get("body") or "")
    vistos, out = set(), []
    for m in JOB_RE.finditer(texto):
        clave = (m.group(1), m.group(2))
        if clave not in vistos:
            vistos.add(clave)
            out.append(clave)
    return out


def fichas_de_comentario(texto):
    """Extrae las fichas (bloques de cuatro lineas) de un comentario del worker."""
    limpio = (texto or "").replace("```text", "").replace("```", "")
    salida = []
    for m in FICHA_RE.finditer(limpio):
        salida.append({
            "ENVIO": m.group(1).strip(),
            "NAME": m.group(2).strip(),
            "COMMENT": " ".join(m.group(3).split()),
            "RESTRICCIONES": m.group(4).strip(),
        })
    return salida


def atender_comandos(gh, vistos):
    """Revisa los comentarios recientes en busca de comandos del catalogador."""
    for issue in gh.issues_recientes():
        comentarios = gh.comentarios(issue["number"])
        fichas = []
        for c in comentarios:
            cuerpo = c.get("body") or ""
            if c.get("id") in vistos:
                fichas = fichas_de_comentario(cuerpo) or fichas
                continue
            fichas_aqui = fichas_de_comentario(cuerpo)
            if fichas_aqui:
                fichas = fichas_aqui
                vistos.add(c.get("id"))
                continue

            respuesta = None
            m = BUENA_RE.match(cuerpo)
            if m:
                numero = m.group(1)
                candidatas = [f for f in fichas if not numero or f["ENVIO"].split("·")[0].strip().lstrip("0") == numero.lstrip("0")]
                if not candidatas:
                    respuesta = "No encuentro la ficha en esta issue. Indica el número: BUENA 4682902"
                else:
                    n, aviso = anadir_ejemplo(candidatas[-1])
                    respuesta = f"Ficha aprobada y añadida como referencia. Total: {n}." + (f"\n\n{aviso}" if aviso else "")
                    log(f"Issue #{issue['number']}: ficha aprobada ({candidatas[-1]['ENVIO'][:40]})")
            elif QUITAR_EJEMPLO_RE.match(cuerpo):
                num = QUITAR_EJEMPLO_RE.match(cuerpo).group(1)
                respuesta = "Ficha de referencia borrada." if quitar_ejemplo(num) else "No hay ninguna ficha aprobada con ese número."
            elif QUITAR_REGLA_RE.match(cuerpo):
                idx = int(QUITAR_REGLA_RE.match(cuerpo).group(1))
                reglas = listar_reglas_extra()
                if 1 <= idx <= len(reglas):
                    cab = [l for l in REGLAS_EXTRA_PATH.read_text(encoding="utf-8").splitlines() if l.startswith("#")]
                    quedan = [r for i, r in enumerate(reglas, 1) if i != idx]
                    REGLAS_EXTRA_PATH.write_text("\n".join(cab + quedan) + "\n", encoding="utf-8")
                    respuesta = f"Regla {idx} borrada."
                else:
                    respuesta = f"No hay regla {idx}. Usa LISTAR REGLAS para verlas."
            elif LISTAR_RE.match(cuerpo):
                que = LISTAR_RE.match(cuerpo).group(1).lower()
                if que == "reglas":
                    reglas = listar_reglas_extra()
                    respuesta = ("Reglas añadidas:\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(reglas, 1))) if reglas else "Sin reglas añadidas."
                else:
                    ejemplos = listar_ejemplos()
                    respuesta = ("Fichas aprobadas:\n" + "\n".join(f"{i}. {e['envio'] or e['numero']} — {e['name']}" for i, e in enumerate(ejemplos, 1))) if ejemplos else "Sin fichas aprobadas."

            if respuesta:
                vistos.add(c.get("id"))
                try:
                    gh.comentar(issue["number"], respuesta)
                except Exception as e:
                    log(f"No se pudo responder al comando: {e}")


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    cfg = cargar_config()
    configurar(cfg)
    if not cfg["github_repo"] or not cfg["github_token"]:
        print("Falta github_repo o github_token en config.json")
        sys.exit(1)
    gh = GitHub(cfg["github_repo"], cfg["github_token"])
    procesadas = set()
    comandos_vistos = set()
    sesion_caducada = False
    log(f"Vigilando issues de {cfg['github_repo']} cada {cfg['poll_seconds']} s. Ctrl+C para parar.")

    if cfg.get("miniaturas", 0) and cfg.get("escenas", True):
        import escenas as _escenas
        _escenas.precargar()
    ex = Extractor(headless=cfg["headless"], canal=cfg.get("navegador", "auto"), ruta=cfg.get("navegador_ruta"),
                   miniaturas=cfg.get("miniaturas", 0), espera_login=0)
    ex.open()
    try:
        while True:
            try:
                issues = [i for i in gh.issues_abiertas() if i["number"] not in procesadas]
            except Exception as e:
                log(f"GitHub no responde: {e}")
                time.sleep(cfg["poll_seconds"])
                continue

            # comandos en comentarios de issues ya cerradas (BUENA, QUITAR EJEMPLO, LISTAR ...)
            try:
                atender_comandos(gh, comandos_vistos)
            except Exception as e:
                log(f"No se pudieron revisar los comandos: {e}")

            for issue in issues:
                # issue de regla: "REGLA: texto" en el titulo (y detalle opcional en el cuerpo)
                m = REGLA_RE.match(issue.get("title") or "")
                if m:
                    texto = m.group(1).strip()
                    cuerpo_issue = (issue.get("body") or "").strip()
                    if cuerpo_issue:
                        texto += " " + " ".join(cuerpo_issue.split())
                    try:
                        linea = anadir_regla(texto)
                        gh.comentar(issue["number"], "Regla añadida a reglas_extra.md y activa desde ahora:\n\n> " + linea)
                        gh.cerrar(issue["number"])
                        log(f"Issue #{issue['number']}: regla añadida")
                    except Exception as e:
                        log(f"Issue #{issue['number']}: no se pudo añadir la regla: {e}")
                    procesadas.add(issue["number"])
                    continue
                # comandos en el titulo de la issue: LISTAR EJEMPLOS / LISTAR REGLAS / QUITAR ...
                titulo = (issue.get("title") or "").strip()
                respuesta = None
                if LISTAR_RE.match(titulo):
                    que = LISTAR_RE.match(titulo).group(1).lower()
                    if que == "reglas":
                        reglas = listar_reglas_extra()
                        respuesta = ("REGLAS:\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(reglas, 1))) if reglas else "REGLAS:\n(ninguna)"
                    else:
                        ejemplos = listar_ejemplos()
                        respuesta = ("EJEMPLOS:\n" + "\n".join(f"{i}. {e['envio'] or e['numero']} — {e['name']}" for i, e in enumerate(ejemplos, 1))) if ejemplos else "EJEMPLOS:\n(ninguno)"
                elif QUITAR_EJEMPLO_RE.match(titulo):
                    num = QUITAR_EJEMPLO_RE.match(titulo).group(1)
                    respuesta = "Ficha de referencia borrada." if quitar_ejemplo(num) else "No hay ninguna ficha aprobada con ese número."
                elif QUITAR_REGLA_RE.match(titulo):
                    idx = int(QUITAR_REGLA_RE.match(titulo).group(1))
                    reglas = listar_reglas_extra()
                    if 1 <= idx <= len(reglas):
                        cab = [l for l in REGLAS_EXTRA_PATH.read_text(encoding="utf-8").splitlines() if l.startswith("#")]
                        quedan = [r for i, r in enumerate(reglas, 1) if i != idx]
                        REGLAS_EXTRA_PATH.write_text("\n".join(cab + quedan) + "\n", encoding="utf-8")
                        respuesta = f"Regla {idx} borrada."
                    else:
                        respuesta = f"No hay regla {idx}."
                if respuesta:
                    try:
                        gh.comentar(issue["number"], respuesta)
                        gh.cerrar(issue["number"])
                    except Exception as e:
                        log(f"No se pudo responder a la issue #{issue['number']}: {e}")
                    procesadas.add(issue["number"])
                    log(f"Issue #{issue['number']}: comando {titulo[:30]}")
                    continue

                trabajos = trabajos_de(issue)
                if not trabajos:
                    continue
                if sesion_caducada:
                    break
                log(f"Issue #{issue['number']}: {len(trabajos)} envio(s)")
                bloques = []
                total = len(trabajos)
                id_comentario = None

                def publicar(parcial=True):
                    """Crea o actualiza el comentario con lo que haya hecho hasta ahora."""
                    nonlocal id_comentario
                    cabecera = "" if not parcial else f"Procesando {len(bloques)} de {total}...\n\n"
                    cuerpo = cabecera + "```text\n" + "\n\n".join(bloques) + "\n```"
                    try:
                        if id_comentario is None:
                            r = gh.s.post(f"{API}/repos/{gh.repo}/issues/{issue['number']}/comments",
                                          json={"body": cuerpo}, timeout=30)
                            r.raise_for_status()
                            id_comentario = r.json()["id"]
                        else:
                            gh.editar_comentario(id_comentario, cuerpo)
                    except Exception as e:
                        log(f"No se pudo publicar el avance: {e}")

                for numero, fecha in trabajos:
                    try:
                        ficha = ex.fetch(numero, fecha)
                        campos, avisos, _ = redactar(
                            ficha, model=cfg["claude_model"], extra_args=cfg["claude_extra_args"],
                            timeout=cfg["claude_timeout"], acortar=cfg.get("acortar_comment", True),
                        )
                        bloques.append(formatear(campos, list(avisos) + list(ficha.get("avisos", []))))
                        log(f"  {numero} OK ({len(bloques)}/{total})")
                        publicar()
                    except (NeedsLogin, AntiBot) as e:
                        sesion_caducada = True
                        bloques.append(f"ENVIO: {numero}\nSESION CADUCADA ({e}). Inicia sesion en casa con login.py y reabre esta issue.")
                        log(f"  {numero}: {e}")
                        break
                    except NotFound as e:
                        bloques.append(f"ENVIO: {numero}\n{e}")
                        log(f"  {numero}: {e}")
                        publicar()
                    except RedactorError as e:
                        bloques.append(f"ENVIO: {numero}\nERROR DE REDACCION: {e}")
                        log(f"  {numero}: {e}")
                        publicar()
                    except Exception:
                        bloques.append(f"ENVIO: {numero}\nERROR INESPERADO:\n{traceback.format_exc()[-1200:]}")
                        log(f"  {numero}: error inesperado")
                        publicar()
                        try:
                            ex.close(); ex.open()
                        except Exception:
                            pass
                publicar(parcial=False)
                if not sesion_caducada:
                    try:
                        gh.cerrar(issue["number"])
                    except Exception as e:
                        log(f"No se pudo cerrar la issue #{issue['number']}: {e}")
                    procesadas.add(issue["number"])
            if sesion_caducada:
                log("Sesion caducada: ejecuta login.py y reinicia el worker.")
                break
            time.sleep(cfg["poll_seconds"])
    except KeyboardInterrupt:
        log("Parado.")
    finally:
        ex.close()


if __name__ == "__main__":
    main()
