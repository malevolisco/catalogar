# -*- coding: utf-8 -*-
"""
empaquetar.py - Hace el zip de la app (catalogator-app.zip) que Catalogator.exe descarga de GitHub.

Lleva el codigo y el criterio, nunca lo del usuario (config.json, cola, fichas aprobadas, sesiones).
Lo usa GitHub Actions al publicar una version; tambien vale a mano:

    python empaquetar.py v2026.10.04          -> dist/catalogator-app.zip con VERSION = v2026.10.04
"""
import sys
import zipfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent

# lo que es de la herramienta (codigo y criterio)
INCLUIR = [
    "*.py", "reglas_patrones.md", "reglas.md", "reglas_ligeras.md", "README.md", "requirements.txt",
    "config.example.json", "mediacentral.example.json", "arrancar.bat", "arrancar_mediacentral.bat",
    "EXE_MIN", "panel/index.html", "demo/*",
]
# lo que es del usuario o de la compilacion: fuera aunque casara con lo de arriba
EXCLUIR = {"empaquetar.py", "catalogator_version.py", "config.json", "ejemplos.md", "reglas_extra.md"}
EXCLUIR_PREFIJOS = ("cola/", "perfil_", "miniaturas/", "escenas", "modelos/", "debug/", "dist/", "build/", ".git", "_anterior/")


def ficheros():
    vistos = set()
    for patron in INCLUIR:
        for p in sorted(AQUI.glob(patron)):
            if not p.is_file():
                continue
            rel = p.relative_to(AQUI).as_posix()
            if rel in EXCLUIR or rel.startswith(EXCLUIR_PREFIJOS) or rel in vistos:
                continue
            vistos.add(rel)
            yield rel, p


def empaquetar(version, destino=None):
    destino = Path(destino) if destino else AQUI / "dist" / "catalogator-app.zip"
    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        n = 0
        for rel, p in ficheros():
            z.write(p, rel)
            n += 1
        z.writestr("VERSION", version.strip() + "\n")
    print(f"{destino}: {n} ficheros, version {version}")
    return destino


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    empaquetar(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
