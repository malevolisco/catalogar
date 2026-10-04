# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: un solo Catalogator.exe con Python, las librerias y una copia del zip de la app por si
# la primera vez no hay red. Lo compila GitHub Actions en Windows (release.yml); a mano:
#     pip install -r requirements.txt pyinstaller
#     python empaquetar.py vX && pyinstaller catalogator.spec
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas, binaries, hiddenimports = [("dist/catalogator-app.zip", ".")], [], []
# webview: la ventana de escritorio (pywebview, con WebView2 de Windows por pythonnet)
for paquete in ("playwright", "onnxruntime", "fontTools", "openpyxl", "PIL", "numpy", "webview"):
    d, b, h = collect_all(paquete)
    datas += d; binaries += b; hiddenimports += h
hiddenimports += collect_submodules("uvicorn") + collect_submodules("fastapi") + collect_submodules("multipart") \
    + ["requests", "email.mime.text", "email.mime.multipart", "imaplib", "smtplib", "tkinter", "tkinter.ttk",
       "tkinter.scrolledtext", "tkinter.messagebox", "tkinter.simpledialog", "clr", "clr_loader"]

a = Analysis(["catalogator.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             hookspath=[], excludes=["matplotlib", "scipy", "pandas", "IPython", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="Catalogator", console=False, upx=False,
          icon="catalogator.ico" if __import__("os").path.exists("catalogator.ico") else None)
