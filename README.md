# catalogar — catalogación automática de envíos de Reuters Connect y AP Newsroom

Dado un número de envío (Edit No de Reuters, 4 cifras, o Story No de AP, 7 cifras) y, opcionalmente, una fecha, el sistema localiza la ficha en la agencia con tu sesión, extrae el texto y pide a Claude Code (dentro del plan Pro) las cuatro líneas de archivo: ENVIO, NAME, COMMENT y RESTRICCIONES.

Piezas:

| Fichero | Qué hace |
|---|---|
| `login.py` | Abre el navegador para que inicies sesión en Reuters (y AP). Guarda la sesión en `perfil_chromium/`. |
| `extractor.py` | Busca el envío por número y fecha, abre la ficha y vuelca el texto. |
| `redactor.py` | Manda texto y reglas a `claude -p`, parsea, normaliza y valida. |
| `reglas_patrones.md` | **El criterio en uso.** Organizado por patrones de tipo de envío. Es el fichero que se toca para ajustar criterio. |
| `reglas.md`, `reglas_ligeras.md` | Versiones anteriores del criterio, congeladas. No se actualizan. |
| `catalogar.py` | Uso en casa desde consola, de uno en uno o por lotes. |
| `worker.py` | Uso remoto: vigila las Issues de tu repositorio en GitHub y responde en ellas. |
| `config.py`, `config.example.json` | Configuración. La clave `miniaturas` (0 = apagado, N = fotogramas por envío) enciende el envío de imágenes al modelo; con Claude Code hay que quitar `Read` de `claude_extra_args` para que pueda abrirlas. |
| `panel/index.html` | Interfaz web para la cola de GitHub (Pages o fichero local). |
| `exportar_sesion.py`, `importar_sesion.py`, `instalar_pi.sh`, `catalogar.service` | Llevar la sesión a otro equipo, instalar en la Raspberry y arrancar el worker solo. |
| `reglas_extra.md`, `regla.py` | Reglas añadidas sobre la marcha, sin tocar `reglas.md`. |
| `ejemplos.md`, `bueno.py` | Fichas aprobadas que el redactor usa como modelo. |
| `comparar_reglas.py` | Enfrenta dos ficheros de criterio sobre los mismos envíos y genera una comparación a ciegas en HTML. |
| `probar_fotogramas.py` | Comprueba si el reproductor de la agencia deja capturar fotogramas o devuelve negro. |
| `medir_dudas.py` | Cuenta cuántos envíos de un lote necesitarían fotogramas para decidir el descriptor. Solo extrae, no gasta cuota. |

## 1. Instalación (PC de casa, Windows)

1. Python 3.10 o superior. En una consola dentro de la carpeta:
   ```
   pip install -r requirements.txt
   playwright install chromium
   ```
2. Claude Code. Consulta la página de instalación de Claude Code para Windows (instalador nativo en PowerShell); si tienes Node.js, también vale `npm install -g @anthropic-ai/claude-code`. Después:
   ```
   claude
   ```
   Entra con tu cuenta Pro cuando lo pida y sal. Comprueba el modo no interactivo:
   ```
   claude -p "responde solo OK"
   ```
3. Sesión de Reuters:
   ```
   python login.py
   ```
   Inicia sesión en las pestañas que se abren, vuelve a la consola y pulsa Intro.
4. Configuración: copia `config.example.json` a `config.json`. Para el uso en casa no hace falta nada más.

## 2. Uso en casa

```
python catalogar.py 0624
python catalogar.py 8999 30/08/2026
python catalogar.py 0624 0611 0605 0532 --fecha 06/09/2026     (lote con fecha comun)
python catalogar.py 8999=30/08/2026 8783=29/08/2026 0624        (fecha por envio)
python catalogar.py --lote lote.txt                             (un envio por linea)
```

En los lotes, un solo navegador para todo, y un error en un envío no detiene a los demás. Además del CSV, cada lote deja `salida/lote_FECHA_HORA.txt` con todos los bloques seguidos para copiar.

Primera ejecución, siempre con volcado de depuración y navegador visible:

```
python catalogar.py 0624 --debug --headed
```

Si falla la localización de la ficha, la carpeta `debug/` contiene captura, HTML y texto de la página de resultados y de la ficha. Esa carpeta es lo que hay que enviar para ajustar el extractor.

Cada ficha generada se añade a `fichas.csv` (separador `;`).

## 3. Uso remoto desde el trabajo (Issues de GitHub)

1. Crea un repositorio **privado** en GitHub, por ejemplo `catalogar`. Puedes subir este código o dejarlo vacío: solo se usan sus Issues.
2. Crea un token fine-grained (Settings → Developer settings → Personal access tokens → Fine-grained) limitado a ese repositorio, con permiso **Issues: Read and write**. Pégalo en `config.json` junto con `github_repo`.
3. En casa, deja el worker corriendo en una consola:
   ```
   python worker.py
   ```
4. Desde el trabajo o el móvil: en el repositorio, **New issue**, título `0624` o `0624 06/09/2026`. Para varios envíos, uno por línea en el cuerpo. En menos de un minuto el worker responde con un comentario que contiene las cuatro líneas y cierra la issue.

Si la sesión de Reuters caduca, el worker lo dice en un comentario, deja la issue abierta y se detiene. En casa: `python login.py` y `python worker.py` de nuevo; reabre la issue si quieres que la procese.

El PC de casa tiene que estar encendido y sin suspensión mientras el worker corre. No hay ningún puerto abierto: el worker solo hace peticiones salientes a GitHub, Reuters y Anthropic.

## 3b. AP Newsroom

Los números de AP tienen siete cifras y son únicos, así que no necesitan fecha: `python catalogar.py 4681323`. Los lotes admiten mezcla de agencias.

En AP el extractor navega directo a la búsqueda (`/home/search?query=NÚMERO&mediaType=video`), localiza la tarjeta que lleva el número, pulsa el titular o la miniatura (la ficha aparece como ventana superpuesta) y, si existe el enlace "Open in a new tab", lee la página completa para no perder shotlist en el panel con scroll. La caja de búsqueda del sitio queda como respaldo.

## 3c. Panel web (carpeta `panel/`)

`panel/index.html` es la interfaz para usar la cola desde el trabajo sin pasar por la web de GitHub. Dos formas de tenerla:

- **GitHub Pages**: crea un repositorio **público** (por ejemplo `catalogar-panel`), sube solo `index.html` y activa Pages en Settings → Pages. La página no contiene ningún secreto: cada usuario escribe su repositorio privado y su token en Ajustes, y quedan guardados solo en su navegador.
- **Fichero local**: abrir `index.html` directamente desde el disco también funciona; la API de GitHub acepta llamadas desde cualquier origen.

Uso: Ajustes (repositorio de la cola y token; Probar conexión), números uno por línea, Catalogar. El panel crea la Issue, espera el comentario del worker y pinta cada envío como ficha con botón de copiar por campo. El historial de los últimos cien envíos queda en el navegador.

Tres pestañas:
- **Fichas**: catalogar y revisar. Cada ficha tiene *Copiar las cuatro líneas*, *Buena* (la guarda como modelo) y *Corregir* (editas NAME, COMMENT o RESTRICCIONES y al guardar se aprueba tu versión).
- **Reglas**: añadir una regla nueva, ver las guardadas y quitarlas.
- **Fichas aprobadas**: ver las que sirven de modelo y quitarlas.

Las pestañas de reglas y fichas aprobadas consultan al worker creando una issue de comando, así que tardan unos segundos y necesitan el worker encendido.

Varios usuarios: cada uno con su repositorio privado, su token y su worker en casa. El panel es común.

## 3d. Fichas aprobadas (el sistema aprende de tus fichas buenas)

Cuando una ficha sale perfecta, se marca y pasa a ser modelo para las siguientes. El redactor las añade al final de las reglas.

- En casa: `python bueno.py 4682902` (la lee de `fichas.csv`), `python bueno.py --listar`, `python bueno.py --quitar 4682902`.
- Desde el panel: botón **Buena** en cada ficha. Si primero la corriges (botón **Corregir**), al guardar se aprueba tu versión corregida, que es la que sirve de modelo.
- Desde una issue: comentario `BUENA` (o `BUENA 4682902` si la issue tiene varias fichas).

La pestaña **Fichas aprobadas** del panel las lista y permite quitarlas. No hay descarte automático: eliges tú cuáles se quedan. A partir de unas doce el prompt se alarga y la redacción tarda más; el worker avisa al superarlas. Conviene tener pocas y variadas: una de declaraciones, una de recursos, una de deportes, una de evento, una de recopilación.

## 3e. Llevar el worker a una Raspberry Pi (sin monitor)

Requisitos: Raspberry Pi 4 con 4 GB o Pi 5 y Raspberry Pi OS **de 64 bits**, mejor la versión con escritorio aunque no se use (trae las librerías gráficas que Chromium necesita). Arranque desde SSD si es posible. En 32 bits no funciona.

**1. Preparar la tarjeta desde el PC.** En Raspberry Pi Imager, antes de grabar, entra en el engranaje de ajustes: nombre de equipo `catalogar`, usuario y contraseña, wifi, y marca **Activar SSH**. Graba, mete la tarjeta y enciende.

**2. Entrar desde el PC.** En PowerShell: `ssh pi@catalogar.local` (o la IP que dé tu router). A partir de aquí todo se hace por SSH, sin pantalla.

**3. Instalar.** Clona el repositorio y ejecuta el instalador, que hace todo lo demás:
```
git clone https://github.com/TU_USUARIO/catalogar.git ~/catalogar
cd ~/catalogar && bash instalar_pi.sh
```
Instala Chromium del sistema (el de Playwright no existe para esta arquitectura), Xvfb, las librerías de Python, sube la memoria de intercambio a 2 GB y deja `config.json` apuntando al Chromium instalado.

**4. Configurar.** Copia tu `config.json` del PC (lleva el token de GitHub) con `scp config.json pi@catalogar.local:~/catalogar/` desde PowerShell, y vuelve a ejecutar `bash instalar_pi.sh` para que reajuste las claves del navegador. En la Pi, `navegador` debe quedar en `sistema` y `headless` en `false`: la ventana existe, pero en una pantalla virtual que nadie ve.

**5. Sesión de las agencias, sin pantalla.** En el PC: `python exportar_sesion.py`, que crea `sesion.json`. Cópialo a la Pi (`scp sesion.json pi@catalogar.local:~/catalogar/`) y allí:
```
cd ~/catalogar
export DISPLAY=:99 && (Xvfb :99 -screen 0 1400x1000x24 &)
python3 importar_sesion.py --comprobar
```
Debe decir que la sesión es válida en las dos agencias. `sesion.json` da acceso a tus cuentas: no lo subas a ningún repositorio (ya está en `.gitignore`) y bórralo de la Pi cuando termines si quieres.

**6. Claude Code en ARM.** Instálalo y ejecuta `claude` una vez. El inicio de sesión con cuenta Pro ha dado problemas en ARM64: si la URL que muestra no funciona, copia el fichero de credenciales desde el PC (`~/.claude/.credentials.json` en Linux; en Windows está en tu carpeta de usuario) a `~/.claude/` en la Pi. Si aun así no va, pon `"redactor": "api"` y tu clave en `api_key`: el resto del sistema no cambia.

**7. Prueba.** `python3 catalogar.py 0624 --debug`. Si extrae y redacta, la Pi ya hace el trabajo entero.

**8. Que arranque sola.**
```
sudo cp ~/catalogar/catalogar.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now catalogar
systemctl status catalogar
```
El registro queda en `~/catalogar/worker.log`. Si tu usuario no es `pi`, edita `User=` y las rutas del fichero antes de copiarlo.

**Lo que hay que vigilar**: la Pi usa Chromium del sistema en vez de Chrome, así que el antibot de Reuters puede volver a aparecer; se ve en el primer envío. Y cuando caduque la sesión, se repite el paso 5 desde el PC.

## 4. Ajustes

- Criterio de redacción: edita el fichero de reglas. Hay dos con el mismo criterio: `reglas.md` (completo, con todos los ejemplos) y `reglas_ligeras.md` (consolidado, la mitad de tamaño, más rápido). Se elige con `"reglas"` en `config.json`. No hay que tocar código.
- Reglas sobre la marcha: `reglas_extra.md`, una por línea en español normal. El redactor las añade al final de las reglas y prevalecen. Tres formas de añadirlas: a mano; `python regla.py "texto de la regla"` (y `python regla.py --listar`); o desde el trabajo con una Issue cuyo título empiece por `REGLA:` (el panel tiene un campo para ello). El worker la guarda y confirma en la issue. Conviene consolidarlas en `reglas.md` de vez en cuando.
- Modelo y esfuerzo: `claude_model` en `config.json` (`sonnet`, `haiku` u `opus`) y `claude_extra_args` para flags adicionales de Claude Code. `claude_thinking_tokens` limita el razonamiento de Claude Code (1024 por defecto: sin él, una ficha puede tardar más de dos minutos).
- Redacción por API en lugar de Claude Code: `"redactor": "api"` y `api_key` con tu clave de la Consola de Anthropic. Modelo en `api_model` (Haiku 4.5 por defecto, el más rápido; `claude-sonnet-5` para más calidad). Las reglas se envían cacheadas, así que el coste por envío es de céntimos. No consume el plan Pro.
- `headless: false` (por defecto) muestra el navegador; conviene dejarlo así, porque sin ventana el navegador cambia de identidad y los sistemas antibot vuelven a pedir verificación. La ventana se puede minimizar.
- `navegador`: `auto` usa tu Google Chrome instalado (perfil separado), después Edge y por último el Chromium de Playwright. Un navegador real es más difícil de detectar como automatizado.

## 5. Seguridad

- `config.json` (token de GitHub) y `perfil_chromium/` (sesión de Reuters) están en `.gitignore`. No los subas nunca a ningún repositorio.
- El acceso automatizado a Reuters Connect con tu usuario es responsabilidad tuya frente a la agencia.
- El texto de los envíos sale a Anthropic a través de tu cuenta Pro, igual que con la extensión.

## 6. Problemas frecuentes

| Mensaje | Causa | Qué hacer |
|---|---|---|
| `Sesion de Reuters Connect caducada` | La sesión guardada ya no vale | `python login.py` |
| `... muestra una verificacion antibot` | La agencia ha detectado navegación automatizada | `python login.py`, superar la verificación en la ventana, Intro, y repetir. Mantener `headless: false` y `navegador: auto` (Chrome real) en `config.json` |
| `NO ENCONTRADO` | El número no aparece en la búsqueda, o la estructura de la página no coincide | Ejecutar con `--debug --headed` y enviar la carpeta `debug/` |
| `NO ENCONTRADO EN ESA FECHA` | El número existe con otras fechas | Se indican las fechas disponibles |
| El panel dice que el worker no ha respondido | `worker.py` parado o el PC dormido | Arrancar `python worker.py` en casa |
| `Claude Code devolvio error` | Claude Code no instalado, sin sesión, o límite de uso | `claude -p "responde solo OK"` en PowerShell (no dentro de Claude) para aislar. Si `claude` no está en el PATH, el script usa `C:\Users\TU_USUARIO\.local\bin\claude.exe`; también admite la variable de entorno `CLAUDE_EXE` |
| `No se han encontrado las lineas NAME/COMMENT` | El modelo no respetó el formato | Revisar la salida bruta; suele bastar con repetir |
