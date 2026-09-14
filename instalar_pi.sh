#!/bin/bash
# instalar_pi.sh - Prepara una Raspberry Pi 4 (64 bits) para ejecutar el worker.
# Uso:  bash instalar_pi.sh
set -e

echo "== 1. Comprobaciones =="
ARCH=$(uname -m)
if [ "$ARCH" != "aarch64" ]; then
  echo "Este sistema es $ARCH, no 64 bits (aarch64). Reinstala Raspberry Pi OS de 64 bits."
  exit 1
fi
echo "Arquitectura: $ARCH. Memoria:"; free -h | head -2

echo "== 2. Paquetes =="
sudo apt update
sudo apt install -y python3-pip chromium xvfb git curl

echo "== 3. Librerias de Python =="
pip3 install --break-system-packages --upgrade playwright requests

echo "== 4. Navegador =="
CHROMIUM=$(command -v chromium || command -v chromium-browser || true)
if [ -z "$CHROMIUM" ]; then echo "No se instalo chromium"; exit 1; fi
echo "Chromium del sistema: $CHROMIUM"
$CHROMIUM --version || true

echo "== 5. Memoria de intercambio (2 GB) =="
if [ -f /etc/dphys-swapfile ]; then
  sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile
  sudo systemctl restart dphys-swapfile || true
fi

echo "== 6. Configuracion =="
cd "$(dirname "$0")"
if [ ! -f config.json ]; then
  cp config.example.json config.json
  echo "Creado config.json a partir del ejemplo: falta poner github_repo y github_token."
fi
python3 - <<'PY'
import json, pathlib, shutil
p = pathlib.Path("config.json"); cfg = json.loads(p.read_text(encoding="utf-8"))
cfg["navegador"] = "sistema"
cfg["navegador_ruta"] = shutil.which("chromium") or shutil.which("chromium-browser") or ""
cfg["headless"] = False          # ventana en la pantalla virtual de Xvfb
p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
print("config.json ajustado para la Raspberry:", cfg["navegador_ruta"])
PY

echo
echo "Listo. Siguientes pasos:"
echo "  1. Copia sesion.json desde el PC y ejecuta:"
echo "       export DISPLAY=:99 && (Xvfb :99 -screen 0 1400x1000x24 &)"
echo "       python3 importar_sesion.py --comprobar"
echo "  2. Instala Claude Code y entra con tu cuenta, o pon redactor=api y tu clave en config.json"
echo "  3. Prueba:  python3 catalogar.py 0624 --debug"
echo "  4. Servicio:  sudo cp catalogar.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now catalogar"
