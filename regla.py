# -*- coding: utf-8 -*-
"""
regla.py - Añade una regla de catalogacion sobre la marcha a reglas_extra.md.

Uso:
    python regla.py "En cumbres y festivales, el NAME empieza por PAIS y el evento en forma corta"
    python regla.py --listar
"""
import sys
from redactor import anadir_regla, listar_reglas_extra


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] in ("--listar", "-l"):
        reglas = listar_reglas_extra()
        print("\n".join(f"{i+1}. {r}" for i, r in enumerate(reglas)) if reglas else "Sin reglas extra.")
        return
    linea = anadir_regla(" ".join(sys.argv[1:]))
    print("Añadida:", linea)


if __name__ == "__main__":
    main()
