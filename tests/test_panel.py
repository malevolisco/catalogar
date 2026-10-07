# -*- coding: utf-8 -*-
"""La pagina: que el JavaScript no tenga errores de sintaxis (una coma de mas deja la pagina en blanco)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PANEL = Path(__file__).resolve().parent.parent / "panel" / "index.html"


@pytest.mark.skipif(not shutil.which("node"), reason="sin node en este equipo")
def test_javascript_del_panel(tmp_path):
    js = "\n".join(re.findall(r"<script>(.*?)</script>", PANEL.read_text(encoding="utf-8"), re.S))
    fichero = tmp_path / "panel.js"
    fichero.write_text(js, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(fichero)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:500]
