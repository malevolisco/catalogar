#!/bin/bash
# instalar_pi.sh - Deja una Raspberry Pi (4 o 5, Raspberry Pi OS de 64 bits) con Catalogator funcionando sola:
# el servidor, el buzon de correo y el navegador de las agencias, sin depender de ningun PC.
#
# Uso (en la Raspberry, con el usuario normal, no como root):
#     curl -fsSL https://raw.githubusercontent.com/malevolisco/catalogar/main/instalar_pi.sh | bash
#
# Se puede repetir sin miedo: lo que ya esta hecho se salta y lo tuyo (config.json, reglas, fichas) no se toca.
#
# Queda asi:
#   ~/catalogator/app      la herramienta (se actualiza sola desde GitHub, como el exe de Windows)
#   ~/catalogator/venv     el Python con las librerias
#   catalogator.service    arranca al encender y se levanta solo si se cae
#   catalogator-noche      cada noche a las 05:10 reinicia (y con eso se actualiza)
set -e

REPO="malevolisco/catalogar"
BASE="$HOME/catalogator"
APP="$BASE/app"
VENV="$BASE/venv"
YO="$(id -un)"
TTY=/dev/tty                     # las preguntas se leen del teclado aunque el script venga por curl | bash

pregunta() {  # pregunta "texto" -> 0 si contesta s
  local r
  read -r -p "$1 [s/n] " r < "$TTY" || true
  [[ "$r" =~ ^[sSyY] ]]
}

echo
echo "== Catalogator para Raspberry Pi =="
if [ "$(id -u)" = "0" ]; then
  echo "Ejecutalo con tu usuario normal, no como root (sin sudo delante)."; exit 1
fi
if [ "$(uname -m)" != "aarch64" ]; then
  echo "Este sistema es $(uname -m). Hace falta Raspberry Pi OS de 64 bits (aarch64)."; exit 1
fi
free -h | head -2

echo
echo "== 1/7  Paquetes del sistema =="
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip chromium xvfb curl unzip ca-certificates \
     fonts-dejavu-core fonts-liberation fonts-noto-cjk >/dev/null
CHROMIUM="$(command -v chromium || command -v chromium-browser || true)"
[ -n "$CHROMIUM" ] || { echo "No se ha instalado Chromium"; exit 1; }
echo "Chromium: $($CHROMIUM --version 2>/dev/null || echo "$CHROMIUM")"

echo
echo "== 2/7  Memoria de reserva (2 GB) =="
if [ -f /etc/dphys-swapfile ]; then
  sudo sed -i 's/^#\?CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile
  sudo sed -i 's/^#\?CONF_MAXSWAP=.*/CONF_MAXSWAP=2048/' /etc/dphys-swapfile
  sudo systemctl restart dphys-swapfile || true
fi
free -h | sed -n 3p

echo
echo "== 3/7  La herramienta =="
mkdir -p "$APP"
if [ ! -f "$APP/servidor.py" ]; then
  URL="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
        | python3 -c 'import json,sys; d=json.load(sys.stdin); print(next(a["browser_download_url"] for a in d["assets"] if a["name"]=="catalogator-app.zip"))')"
  curl -fsSL "$URL" -o "$BASE/app.zip"
  unzip -oq "$BASE/app.zip" -d "$APP" && rm -f "$BASE/app.zip"
fi
echo "Version: $(cat "$APP/VERSION" 2>/dev/null || echo '?')  en $APP"

echo
echo "== 4/7  Python y librerias (la primera vez tarda unos minutos) =="
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$APP/requirements.txt"
echo "Librerias instaladas."

echo
echo "== 5/7  Configuracion =="
CATALOGATOR_DIR="$APP" "$VENV/bin/python" - "$CHROMIUM" <<'PY'
import json, sys, secrets, pathlib, os
app = pathlib.Path(os.environ["CATALOGATOR_DIR"])
ruta = app / "config.json"
if ruta.exists():
    cfg = json.loads(ruta.read_text(encoding="utf-8"))
    nuevo = False
else:
    cfg = json.loads((app / "config.example.json").read_text(encoding="utf-8"))
    for k in ("github_repo", "github_token", "api_key", "correo_clave", "correo_usuario", "correo_copia"):
        cfg[k] = ""
    cfg["documentalistas"] = {}
    cfg["correo_buzon_minutos"] = 0          # el buzon se enciende al cargar tu copia (o en Admin → Ajustes)
    cfg["servidor_clave"] = secrets.token_urlsafe(12)
    nuevo = True
# lo propio de la Raspberry: su Chromium, en la pantalla virtual (Xvfb)
cfg["navegador"] = "sistema"
cfg["navegador_ruta"] = sys.argv[1]
cfg["headless"] = False
ruta.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
print(("Creado" if nuevo else "Ajustado") + " config.json para la Raspberry.")
(app.parent / "clave.txt").write_text(cfg["servidor_clave"] + "\n", encoding="utf-8")
PY
chmod 600 "$BASE/clave.txt" "$APP/config.json"

echo
echo "== 6/7  Claude Code (redacta las fichas) =="
export PATH="$HOME/.local/bin:$PATH"
if command -v claude >/dev/null 2>&1; then
  echo "Ya esta instalado: $(claude --version 2>/dev/null | head -1)"
elif pregunta "No esta instalado. ¿Lo instalo? (si dices que no, habra que redactar con la API: Admin → Ajustes)"; then
  curl -fsSL https://claude.ai/install.sh | bash
  export PATH="$HOME/.local/bin:$PATH"
fi
if command -v claude >/dev/null 2>&1 && pregunta "¿Entrar ahora con tu cuenta de Claude? (sale un enlace: abrelo en el movil o en el PC)"; then
  claude < "$TTY" || true
  echo "(Si has entrado bien, ya esta. Si no, luego: claude)"
fi

echo
echo "== 7/7  Tailscale (para entrar desde fuera) y arranque automatico =="
if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
if ! tailscale status >/dev/null 2>&1; then
  echo "Abre el enlace que sale y entra con la misma cuenta de Tailscale que usas en el PC:"
  sudo tailscale up
fi
sudo tailscale set --operator="$YO" || true     # para que Catalogator pueda publicar la pagina sin sudo

sudo tee /etc/systemd/system/catalogator.service >/dev/null <<EOF
[Unit]
Description=Catalogator (fichas de agencia)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$YO
WorkingDirectory=$APP
Environment=CATALOGATOR_DIR=$APP
Environment=PYTHONUNBUFFERED=1
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/xvfb-run -a -s "-screen 0 1400x1000x24" $VENV/bin/python $APP/catalogator.py --consola --servicio
Restart=always
RestartSec=10
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/catalogator-noche.service >/dev/null <<EOF
[Unit]
Description=Reinicia Catalogator por la noche (y con eso se actualiza)

[Service]
Type=oneshot
ExecStart=/bin/systemctl restart catalogator.service
EOF

sudo tee /etc/systemd/system/catalogator-noche.timer >/dev/null <<EOF
[Unit]
Description=Cada noche a las 05:10

[Timer]
OnCalendar=*-*-* 05:10
Persistent=false

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable -q --now catalogator-noche.timer
sudo systemctl enable -q catalogator.service
sudo systemctl restart catalogator.service

echo
echo "Arrancando..."
PUERTO="$("$VENV/bin/python" -c "import json;print(json.load(open('$APP/config.json')).get('servidor_puerto') or 8765)")"
for i in $(seq 1 60); do
  curl -fs -o /dev/null "http://127.0.0.1:$PUERTO/" && break
  sleep 2
done
DIR="$(tailscale funnel status --json 2>/dev/null | python3 -c 'import json,sys
d=json.load(sys.stdin)
print(next(("https://"+k.rsplit(":",1)[0]) for k,v in (d.get("AllowFunnel") or {}).items() if v))' 2>/dev/null || true)"

echo
echo "=============================================================="
if curl -fs -o /dev/null "http://127.0.0.1:$PUERTO/"; then
  echo " Catalogator esta en marcha en la Raspberry."
else
  echo " Catalogator no responde todavia. Mira que pasa con:"
  echo "     journalctl -u catalogator -n 50"
fi
echo
if [ -n "$DIR" ]; then
  echo " Direccion:  $DIR"
else
  echo " Direccion:  http://$(hostname -I | awk '{print $1}'):$PUERTO  (solo desde casa)"
  echo "   Para entrar desde fuera falta activar Funnel: ejecuta  tailscale funnel --bg $PUERTO"
  echo "   y abre el enlace que te de (solo la primera vez)."
fi
echo " Clave:      $(cat "$BASE/clave.txt")"
echo
echo " Siguientes pasos:"
echo "   1. En el PC: Catalogator → Admin → Estado → Descargar copia de mis datos."
echo "   2. Abre la direccion de arriba, entra con la clave, y en Admin → Estado → Cargar una copia"
echo "      elige ese fichero. Pulsa Reiniciar ahora."
echo "   3. Pestaña Navegador → Iniciar sesion agencias, entra en Reuters, AP y EBU y pulsa"
echo "      Ya he iniciado sesion."
echo "   4. Cierra Catalogator en el PC (o pon alli el buzon a 0): si no, los dos contestan los correos."
echo "=============================================================="
