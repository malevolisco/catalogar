# -*- coding: utf-8 -*-
"""Pruebas de Catalogator: python -m pytest tests -q. Corren solas antes de publicar (release.yml) y en
cada PR (pruebas.yml); si alguna falla, no se publica la version."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
