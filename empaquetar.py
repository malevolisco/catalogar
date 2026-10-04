# -*- coding: utf-8 -*-
"""
empaquetar.py - Hace el zip de la app (catalogator-app.zip) que Catalogator.exe descarga de GitHub.

Lleva el codigo y el criterio, nunca lo del usuario (config.json, cola, fichas aprobadas, sesiones).
Lo usa GitHub Actions al publicar una version; tambien vale a mano:

    python empaquetar.py v2026.10.04          -> dist/catalogator-app.zip con VERSION = v2026.10.04
"""
import ast
import importlib.util
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
EXCLUIR = {"empaquetar.py", "catalogator_version.py", "config.json", "ejemplos.md", "reglas_extra.md",
           "criterio_cambios.json"}
# (ojo: "escenas/" y "escenas_", no "escenas", que dejaria fuera escenas.py)
EXCLUIR_PREFIJOS = ("cola/", "perfil_", "miniaturas/", "escenas/", "escenas_", "modelos/", "debug/", "dist/", "build/", ".git", "_anterior/")


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


def modulos_que_faltan(incluidos):
    """Modulos que importa el codigo del zip y que no estan ni en el zip ni instalados (ej. actos.py sin subir)."""
    en_zip = {Path(rel).stem for rel in incluidos if rel.endswith(".py")}
    ruta_sin_repo = [d for d in sys.path if Path(d or ".").resolve() != AQUI]
    faltan = {}
    for rel in incluidos:
        if not rel.endswith(".py"):
            continue
        arbol = ast.parse((AQUI / rel).read_text(encoding="utf-8"), rel)
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                nombres = [a.name.split(".")[0] for a in nodo.names]
            elif isinstance(nodo, ast.ImportFrom) and nodo.module and not nodo.level:
                nombres = [nodo.module.split(".")[0]]
            else:
                continue
            for m in nombres:
                if m in en_zip or m in sys.stdlib_module_names or m in EXCLUIR_MODULOS:
                    continue
                # lo que esta en el repositorio pero no entra en el zip tambien falta
                if (AQUI / f"{m}.py").exists() or not _instalado(m, ruta_sin_repo):
                    faltan.setdefault(m, set()).add(rel)
    return faltan


# modulos que se importan con plan B si no estan (los escribe la compilacion, no van en el zip)
EXCLUIR_MODULOS = {"catalogator_version"}


def _instalado(modulo, ruta):
    guardada = sys.path[:]
    try:
        sys.path[:] = ruta
        return importlib.util.find_spec(modulo) is not None
    except (ImportError, ValueError):
        return False
    finally:
        sys.path[:] = guardada


def empaquetar(version, destino=None):
    faltan = modulos_que_faltan([rel for rel, _ in ficheros()])
    if faltan:
        for m, quien in sorted(faltan.items()):
            print(f"ERROR: falta {m}.py (lo usan: {', '.join(sorted(quien))})")
        sys.exit("No se publica: sube los ficheros que faltan al repositorio y vuelve a lanzar la compilacion.")
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
