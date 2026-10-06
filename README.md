# catalogar — catalogación automática de envíos de Reuters Connect, AP Newsroom y EBU News Exchange

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
| `servidor.py` | **Uso remoto (recomendado).** Servidor web propio en el PC de casa: recibe numeros de envio (o una lista de MediaCentral) desde la pagina, los cataloga uno tras otro y mantiene la pagina al dia. Sustituye a `worker.py`. |
| `arrancar.bat` | Doble clic en Windows: publica el puerto con Tailscale Funnel y arranca `servidor.py`. |
| `demo/` | Demo aparte con un modelo local (Ollama) en vez de Claude. No toca la herramienta: ver `demo/LEEME.md`. |
| `volcar.py` | Saca a `debug/` el texto y el HTML de un envio tal como los ve la herramienta, para mirarlos o para la demo. |
| `paginas_word.py` | Mide cuantas paginas de Word ocupa el script de un envio y pone la ALERTA de script cortado (apartado 3l). |
| `mediacentral.py` | Relleno de MediaCentral desde el equipo de teletrabajo: recorre la busqueda "tratar agencias" abriendo cada envio con doble clic, cambia el Name (que traia el numero) por el NAME de la ficha, y escribe el mismo NAME en el Titulo, el COMMENT y el catalogador con las fichas del servidor (MediaCentral guarda al pasar al siguiente) y al final comprueba que se han guardado y lista las que tienen restricciones o alertas (apartado 3n). Va en su propio zip. |
| `situaciones.py` | Lee en el texto de la agencia lo que cambia el uso del material (embargo, no archivar, credito, no usar en España...) y lo que cambia como se cataloga (archivo, mudo, en bruto, IA, obituario): pistas para el modelo, cruce con lo que escribe en RESTRICCIONES, ALERTAS y avisos (apartado 3m). |
| `actos.py`, `escenas.py`, `entrenar_escenas.py` | Tipo de acto (rueda de prensa, declaraciones, entrevista, comparecencia...): lo lee en el shotlist, lo reconoce en los fotogramas con un clasificador que aprende solo de las fichas que apruebas y avisa si la ficha no cuadra (apartado 3o). |
| `catalogator.py`, `catalogator.spec`, `empaquetar.py`, `EXE_MIN`, `.github/workflows/release.yml` | Catalogator: el lanzador en un solo .exe que se actualiza solo desde GitHub (apartado 0), el zip de la app y el flujo que compila y publica cada version. |
| `listas.py` | Lee las listas de trabajo que se exportan de MediaCentral (Search Results, .csv o .xlsx): coge el numero que abre la columna Name, traduce la columna Catalogador a una persona de la agenda (por su `id`) y no mira la columna de fecha. Lo usan el panel, el buzon de correo y `catalogar.py --lista`. |
| `excel.py` | Excel de fichas, solo en consola (`catalogar.py --excel`): lee las columnas NAME y CONT y devuelve una copia del libro con las fichas. La pagina ya no lo tiene. |
| `worker.py` | Uso remoto anterior, por Issues de GitHub. Se mantiene como plan B; no hace falta si usas `servidor.py`. |
| `config.py`, `config.example.json` | Configuración. La clave `miniaturas` (0 = apagado, N = fotogramas por envío) enciende el envío de imágenes al modelo (con Claude Code, el redactor le da la herramienta Read solo en ese caso). |
| `panel/index.html` | La pagina web. La sirve `servidor.py`; no se abre suelta ni se publica en GitHub Pages. |
| `exportar_sesion.py`, `importar_sesion.py`, `instalar_pi.sh`, `catalogar.service` | Llevar la sesión a otro equipo, instalar en la Raspberry y arrancar el worker solo. |
| `reglas_extra.md`, `regla.py` | Reglas añadidas sobre la marcha, sin tocar `reglas.md`. |
| `ejemplos.md`, `bueno.py` | Fichas aprobadas que el redactor usa como modelo. |
| `comparar_reglas.py` | Enfrenta dos ficheros de criterio sobre los mismos envíos y genera una comparación a ciegas en HTML. |
| `probar_fotogramas.py` | Comprueba si el reproductor de la agencia deja capturar fotogramas o devuelve negro. |
| `medir_dudas.py` | Cuenta cuántos envíos de un lote necesitarían fotogramas para decidir el descriptor. Solo extrae, no gasta cuota. |

## 0. Catalogator: la forma facil (un solo .exe que se actualiza solo)

`Catalogator.exe` es la herramienta entera en un fichero: lleva dentro Python y todas las librerias, y el codigo
(servidor, criterio, panel) lo descarga y lo mantiene al dia desde las versiones publicadas en GitHub. Para quien lo
usa: descargar `Catalogator.exe` una vez de la pagina de Releases del repositorio, dejarlo donde quiera (el
Escritorio vale) y doble clic. La primera vez Windows avisa de que es un programa sin firmar: "Mas informacion" y
"Ejecutar de todas formas"; luego ya no.

Al abrirse sale una ventana pequeña que dice en que va: comprueba si hay version nueva y la instala, arranca el
servidor, lo publica con Tailscale si esta instalado y abre la pagina en el navegador. Botones: **Abrir la pagina**,
**Carpeta** (donde estan config.json, las fichas aprobadas, la cola), **Registro**, **Buscar actualizaciones**,
**MediaCentral** (el relleno del equipo de teletrabajo, en su consola) y **Salir** (para el servidor).

La primera vez, si encuentra una instalacion antigua de catalogar (junto al exe o en el Escritorio) pregunta si
se trae su configuracion, fichas aprobadas y sesiones. Si no la hay, crea `config.json` con una clave de pagina
nueva (la enseña) y, si no encuentra Claude Code instalado, pide una clave de API de Anthropic para redactar.
Todo vive en `%LOCALAPPDATA%\Catalogator\app` (boton Carpeta). Para ponerlo en otro sitio, un `catalogator.json`
junto al exe: `{"carpeta": "D:\\catalogar"}`; ahi tambien se puede cambiar el repositorio (`"repo"`) o poner un
token (`"token"`) si el repositorio es privado.

Al actualizar nunca se tocan los datos del usuario (`config.json`, `cola/`, `ejemplos.md`, `reglas_extra.md`,
`fichas.csv`, sesiones, fotogramas); solo el codigo y el criterio, y antes se guarda la version anterior en
`_anterior/`. Si una version nueva necesita un lanzador mas nuevo (una libreria nueva), lo descarga y lo cambia
solo al salir.

Para el equipo de teletrabajo basta el mismo `Catalogator.exe`: `Catalogator.exe --mediacentral` (o el boton
MediaCentral) abre el relleno de MediaCentral en una consola, sin Python instalado; admite los argumentos de
`mediacentral.py` (`--aprender`, `--max 3`, `--revisar`, `--comprobar`).

Como se publica una version (lo hace GitHub Actions, `.github/workflows/release.yml`): una etiqueta `vAAAA.MM.DD`
en el repositorio compila `Catalogator.exe` en Windows, hace `catalogator-app.zip` con `empaquetar.py` (el codigo
y el criterio, nunca lo del usuario) y publica la release con los dos. `EXE_MIN` dice que version minima del
lanzador necesita el codigo (se sube solo cuando cambian las librerias de `requirements.txt`). En desarrollo,
`python catalogator.py` hace lo mismo con el Python instalado, y `--consola` enseña todo en texto.

Lo que sigue es la instalacion a mano, con Python, que sigue valiendo igual.

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
4. Configuración: copia `config.example.json` a `config.json`. Para el uso en casa no hace falta nada más; para el uso remoto, cambia `servidor_clave` por una contraseña larga tuya (ver apartado 3).

## 2. Uso en casa

```
python catalogar.py 0624
python catalogar.py 8999 30/08/2026
python catalogar.py 0624 0611 0605 0532 --fecha 06/09/2026     (lote con fecha comun)
python catalogar.py 8999=30/08/2026 8783=29/08/2026 0624        (fecha por envio)
python catalogar.py --lote lote.txt                             (un envio por linea)
python catalogar.py --lista "Search Results.csv"                (lista de MediaCentral)
```

En los lotes, un solo navegador para todo, y un error en un envío no detiene a los demás. Además del CSV, cada lote deja `salida/lote_FECHA_HORA.txt` con todos los bloques seguidos para copiar.

Primera ejecución, siempre con volcado de depuración y navegador visible:

```
python catalogar.py 0624 --debug --headed
```

Si falla la localización de la ficha, la carpeta `debug/` contiene captura, HTML y texto de la página de resultados y de la ficha. Esa carpeta es lo que hay que enviar para ajustar el extractor.

Cada ficha generada se añade a `fichas.csv` (separador `;`).

## 3. Uso remoto desde el trabajo (servidor propio)

Tres piezas: el **PC de casa** hace el trabajo, `servidor.py` se queda encendido en el escuchando, y `panel/index.html` es la pagina que abres desde el navegador del trabajo. Sin GitHub, sin token y sin abrir puertos en el router.

**1. Preparar (una vez).**
```
pip install -r requirements.txt
```
Anade al `config.json` dos claves, con una contrasena larga inventada por ti:
```
"servidor_puerto": 8765,
"servidor_clave": "una-frase-larga-que-solo-sepas-tu"
```

**2. Arrancar en casa.**
```
python servidor.py
```
Debe imprimir `Servidor en http://127.0.0.1:8765`. Abre esa direccion en el navegador del propio PC, entra con la clave y prueba un envio antes de salir a internet. El servidor solo escucha en `127.0.0.1`: nadie de la red local lo ve.

**3. Publicarlo hacia fuera con Tailscale Funnel.** Instala Tailscale (tailscale.com) e inicia sesion. Despues, en una consola:
```
tailscale funnel --bg 8765
```
Publica el puerto y deja la publicacion activa aunque reinicies el PC. La primera vez da un enlace para activar Funnel en tu cuenta: pulsalo, acepta y repite el comando. Al final imprime la direccion `https://nombre-del-pc.algo.ts.net`, que es la que usas desde el trabajo. El cifrado termina en tu PC: el relevo de Tailscale solo ve bytes cifrados.

Con `arrancar.bat` los pasos 2 y 3 se hacen de un doble clic.

**4. Que el PC no se duerma.** Configuracion - Sistema - Energia: suspension "Nunca" con el equipo enchufado y, si es portatil, "Al cerrar la tapa: no hacer nada".

**5. Trabajar.** Abres la direccion `.ts.net`, metes la clave (la sesion dura 30 dias en ese navegador) y pegas los numeros como vengan: separados por espacios, comas o saltos de linea, con texto alrededor o pegados a letras ("hazme el 7612 y el 7613", "3213,2314", "AP4681323"). Se queda solo con los numeros de envio (4 cifras Reuters, 7 AP, `2026_10420363` EBU), cada uno a su agencia, y avisa de lo que ha ignorado (fechas, horas, cifras de otra longitud). Para fijar la fecha de un envio, detras del numero: `0624=06/09/2026` o `0624 06/09/2026`. Boton **Catalogar**.
Un envio que ya esta catalogado en otro lote no se vuelve a hacer: se copia la ficha tal cual, con su version
normal y sus avisos, y la pagina lo marca con "ya hecha el ...". Con fecha, vale la hecha con esa misma fecha; sin
fecha, un numero de Reuters se da por hecho si la ficha anterior es de los ultimos 3 dias (`ya_hecha_dias`; los
numeros de Reuters vuelven a usarse cada pocos dias, asi que un "0624" de hace dos semanas es otro envio), y los de
AP y EBU, que son unicos, valen siempre. Las fichas antiguas que no guardaban la fecha cuentan por el dia del lote.
Si el envio viene de una lista de MediaCentral, los 3 dias se cuentan desde su entrada en MediaCentral (columna
Created) y no vale una hecha de un envio posterior a esa entrada (apartado 3j).
La ficha guarda la fecha real que trae la agencia, que es con la que se reconoce despues. Para rehacerlo de verdad,
la casilla **Rehacer las ya hechas** junto a la fecha. Los que fallaron o se pararon no cuentan como hechos, asi
que siempre se reintentan. El PC de casa abre la agencia con tu sesion y va sacando las fichas; cada una aparece en la pagina en cuanto termina, sin esperar al lote. Cada ficha tiene *Copiar todo*, copiar campo a campo pulsando sobre el texto, **Buena** y **Corregir**. Las pestanas Reglas y Fichas aprobadas funcionan igual que antes, pero al instante.

Para varios envios de golpe, la **lista de MediaCentral** (.csv o .xlsx), apartado 3j. El Excel de fichas con columnas NAME y CONT ya no esta en la pagina; sigue en consola: `python catalogar.py --excel envios.xlsx`.

**La sesion de las agencias.** Al arrancar, el servidor entra en las agencias y comprueba que la sesion sirve (`comprobar_sesion_al_arrancar`); si esta caducada, abre solo la ventana de inicio de sesion en el PC de casa (`login_al_arrancar`). Desde la pagina hay tres botones en la cabecera: **Comprobar sesion** (entra en las agencias y dice si sirve), **Iniciar sesion agencias** (abre en el PC de casa la ventana de las agencias, como `login.py`; hay que estar delante del PC, o entrar a el por escritorio remoto) y, mientras esa ventana esta abierta, **Ya he iniciado sesion**, que la cierra y guarda el perfil. Despues, **Reintentar errores** en el lote afectado. `python login.py` sigue funcionando igual.

**Donde queda todo.** Los lotes y las listas subidas se guardan en `cola/` en tu PC y no salen de ahi. A Anthropic llega lo mismo que en el uso local: el script y las reglas. Borrar un lote desde la pagina borra tambien su carpeta.

## 3a. Plan B: cola de Issues de GitHub (`worker.py`)

Sigue funcionando y no estorba, pero ya no hace falta. Requiere repositorio privado, token fine-grained con permiso Issues: Read and write en `config.json`, y `python worker.py` en casa. Se crea una issue con el numero en el titulo y el worker responde en un comentario. La pagina actual **no** habla con GitHub: si quieres esta via, se usa desde la web de GitHub.

## 3b. AP Newsroom

Los números de AP tienen siete cifras y son únicos, así que no necesitan fecha: `python catalogar.py 4681323`. Los lotes admiten mezcla de agencias.

En AP el extractor navega directo a la búsqueda (`/home/search?query=NÚMERO&mediaType=video`), localiza la tarjeta que lleva el número, pulsa el titular o la miniatura (la ficha aparece como ventana superpuesta) y, si existe el enlace "Open in a new tab", lee la página completa para no perder shotlist en el panel con scroll. La caja de búsqueda del sitio queda como respaldo.

## 3c. La pagina (carpeta `panel/`)

`panel/index.html` la sirve `servidor.py` en la raiz (`/`). No se abre suelta desde el disco ni se publica en GitHub Pages: sin servidor no tiene con quien hablar. No contiene ningun secreto; la clave se escribe al entrar y queda como cookie de sesion.

Tres pestanas:
- **Fichas**: pegar numeros o subir una lista de MediaCentral, y revisar lo que va saliendo. Cada ficha tiene *Copiar todo*, *Buena* (la guarda como modelo) y *Corregir* (editas y al guardar se aprueba tu version). Por lote: *Parar* (lo que esta en cola no se hace, la ficha en curso termina sola, y no se manda ningun correo automatico; lo parado se recupera con *Volver a intentarlo*), *Volver a intentarlo* (solo con el lote terminado o parado: en curso se rechaza, porque reencolarlo a medias mandaba los correos dos veces; y a quien ya recibio sus fichas no se le repiten) y *Borrar*. Si el hilo de trabajo se muriera, la cabecera de la pagina lo dice en vez de dejar los lotes en cola en silencio.
- **Reglas**: anadir una regla, ver las guardadas y quitarlas.
- **Fichas aprobadas**: ver las que sirven de modelo y quitarlas.

La cabecera dice que esta redactando en cada momento, cuantas fichas quedan en cola, el estado de la sesion de la agencia, cuantas fichas aprobadas y reglas hay y que modelo se esta usando. La pagina pregunta al servidor cada 5 segundos.

## 3d. Fichas aprobadas (el sistema aprende de tus fichas buenas)

Cuando una ficha sale perfecta, se marca y pasa a ser modelo para las siguientes. En cada envío el redactor añade solo las más parecidas a él (`ejemplos_por_ficha`, 8 por defecto): palabras en común entre el envío y la ficha aprobada (nombres, lugares, slug y titular de la agencia), pesando más las raras.

- En casa: `python bueno.py 4682902` (la lee de `fichas.csv`), `python bueno.py --listar`, `python bueno.py --quitar 4682902`.
- Desde el panel: botón **Buena** en cada ficha. Si primero la corriges (botón **Corregir**), al guardar se aprueba tu versión corregida, que es la que sirve de modelo.
- Desde la pagina, con el servidor propio, es instantaneo; por la via antigua de GitHub, con un comentario `BUENA` en la issue.

La pestaña **Fichas aprobadas** del panel las lista y permite editarlas y quitarlas. No hay tope: como solo entran las parecidas, se pueden aprobar todas las buenas sin alargar la redacción.

Si la ficha que apruebas la has corregido, `aprender.py` compara tu versión con la del modelo y, si la corrección enseña algo general (no un dato de ese envío), deja una regla propuesta en **Reglas → Sugerencias**. No se añade sola: la aceptas (pasa a Mis reglas), la retocas o la descartas (y no vuelve). Se apaga con `aprender_correcciones`.

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
Debe decir que la sesión es válida en las agencias. `sesion.json` da acceso a tus cuentas: no lo subas a ningún repositorio (ya está en `.gitignore`) y bórralo de la Pi cuando termines si quieres.

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

## 3f. Como cambiar cosas (tres capas)

Cada cosa vive en una capa. Empieza siempre por la de arriba.

1. **Ajuste rapido**, pestana Reglas de la pagina. Una frase, boton Anadir regla. Va a `reglas_extra.md`
   y manda sobre el resto del criterio. Para probar algo o corregir un vicio concreto. Sin tocar ficheros
   ni reiniciar. Si tras cinco o seis fichas el ajuste funciona y es un patron de verdad, boton **Pasar al
   criterio**: el modelo propone en que seccion va, como queda redactada y que lineas del criterio deja
   sobrando; al aceptarlo pasa al criterio base (capa 2, en `criterio_cambios.json`) y sale de Mis reglas.
2. **El criterio**: `reglas_patrones.md` (el criterio en uso) y `ejemplos.md` (las fichas aprobadas). Son
   ficheros de texto: se abren con el Bloc de notas o Notepad++, se busca la seccion con Ctrl+F, se cambia
   y se guarda en UTF-8. El redactor los lee en cada envio. Antes de tocar, copia de seguridad.
   Formato de `ejemplos.md`: cada ficha con sus lineas ENVIO, NAME, COMMENT y RESTRICCIONES, separadas por
   una linea en blanco; las lineas que empiezan por `#` son comentarios.
3. **El codigo**: lo que no es criterio sino mecanica (que avisa el validador, que se limpia antes de
   entregar, que muestra la pagina) esta en `redactor.py`, `servidor.py` y `panel/index.html`. Sin saber
   programar, la forma practica es abrir una consola en la carpeta, escribir `claude` y pedir el cambio
   describiendo el fichero y el comportamiento. Antes: copia de seguridad o subirlo al repositorio.

Para comprobar un cambio de criterio sin gastar cuota, este comando pasa una ficha inventada por el validador
y dice que avisos saltan:
```
python -c "import redactor,config;redactor.configurar(config.cargar_config());print(redactor.validar({'NAME':'PAIS PRUEBA','COMMENT':'MADRID. RECURSOS DE PRUEBA CON MOTIVO DE ALGO.','RESTRICCIONES':'NO ARCHIVAR.'}))"
```

## 3g. Presentacion de las fichas (mayusculas o texto normal)

Las fichas se redactan siempre en mayusculas sin tildes, que es lo que pide el formulario del archivo, y esa es
la version canonica: la que se guarda, se corrige, se aprueba y va al CSV y al Excel. La version en escritura
normal (mayuscula inicial, nombres propios, tildes) la genera el modelo **en la misma llamada**, como tres lineas
mas despues de las cuatro de siempre, con una red de seguridad: si cambia, añade o quita una palabra respecto a
la version en mayusculas, se descarta con aviso. Cuesta unos cientos de tokens mas por ficha, nada apreciable.

Tres claves en `config.json`:
```
"generar_normal": true,               pedir la version normal al redactar (si es false, se pide aparte al pulsar el boton)
"presentacion": "mayusculas",         modo por defecto de la pagina: "mayusculas" o "normalizado"
"correo_presentacion": "mayusculas"   modo de los correos (reparto, seleccion y respuestas del buzon)
```
En la pagina, el selector de la cabecera manda sobre lo que se ve y lo que copian los botones, y se recuerda en
cada navegador; por defecto arranca en el modo de `presentacion`. Corregir edita siempre la version en mayusculas.
Si una ficha no trae version normal (generada con `generar_normal` en false, o descartada), el boton
**Pasar a texto normal** la pide en una segunda llamada.

## 3h. Reparto por correo a los documentalistas

Al meter un lote (numeros o lista) se puede escribir un reparto; cuando el lote termina, cada persona recibe
un correo con sus fichas en las cuatro lineas de siempre, limpias. Los AVISOS del validador solo van a tu direccion
(`correo_copia`): a los demas un aviso sin contexto les confunde mas de lo que les ayuda. Tu direccion no recibe nada
que no pidas: ni resumenes ni copias. Solo le llega correo si el reparto dice `yo`, si envias una seleccion a `yo` o
si escribes tu al buzon. Lo que queda sin repartir se ve en la pagina, debajo del lote, y en la consola.

Reparto, una linea por persona:
```
andrea: 10               diez fichas, las primeras que queden libres
pepe: trump, eeuu        las fichas cuyo NAME, COMMENT o titular contengan alguna de esas palabras
resto: yo                lo que no case con nadie (yo = correo_copia)
```
Primero se aplican las lineas de palabras, en su orden; luego las de cantidad; el resto va a "resto". Los nombres
salen de la agenda `documentalistas` de `config.json`; tambien vale una direccion completa. Un lote ya terminado
se puede repartir o reenviar desde la pagina (boton "Repartir por correo" / "Volver a enviar").

Tambien se puede repartir a mano, despues, sin haber escrito nada al meter el lote: en la pestana Fichas hay
una caja **Filtrar por tema** (una o varias palabras separadas por comas; busca en NAME, COMMENT, titular y numero),
cada ficha hecha lleva una casilla, y **Seleccionar las visibles** marca todas las que pasan el filtro. Con fichas
seleccionadas aparece una barra abajo para elegir a quien enviarlas; el boton derecho sobre una ficha abre el mismo
menu. Las fichas enviadas quedan marcadas con "enviada a X fecha". La seleccion puede mezclar fichas de varios lotes.

Cuenta de correo (una cuenta nueva de Gmail u Outlook.com solo para esto), en `config.json`:
```
"correo_servidor": "smtp.gmail.com",          (Outlook.com: smtp-mail.outlook.com)
"correo_puerto": 587,
"correo_usuario": "catalogar.archivo@gmail.com",
"correo_clave": "contrasena-de-aplicacion",
"correo_copia": "tu.direccion@rtve.es",
"documentalistas": {
  "Adrián": { "correo": "tu.direccion@rtve.es", "id": "I23785" },
  "Javier": { "correo": "javier.apellido@rtve.es", "id": "I24082", "presentacion": "normalizado" },
  "Andrea": "andrea.apellido@rtve.es",
  "Silvia": { "correo": "silvia.apellido@rtve.es", "formal": true }
}
```
**La agenda.** Todo lo de cada persona va en su entrada de `documentalistas`: la direccion a secas si no hace
falta mas, o un bloque con `correo` (obligatorio), `id` (su usuario de MediaCentral, el de la columna Catalogador
de las listas; uno o una lista), `formal` (true: siempre en tono formal) y `presentacion` (`"normalizado"` o
`"mayusculas"` si quiere las fichas distintas al `correo_presentacion` general). Las claves sueltas de antes
(`catalogadores`, `correo_formal`, `correo_presentacion_personas`) siguen valiendo, pero ya no hacen falta.
Si a un bloque le falta el correo, o el JSON tiene una coma de menos, el servidor lo dice al arrancar con la linea
y no arranca.

**Tono: formal o informal.** Cada correo lleva el tono de quien lo recibe:

- Quien **no esta en la agenda** (una direccion puesta a mano, o alguien de rtve.es que escribe al buzon): formal,
  siempre.
- Quien **esta en la agenda**: informal, que es lo predeterminado, salvo que tenga `"formal": true` en su entrada.
  A esas personas les llega siempre en formal, tambien cuando escriben al buzon.
- La casilla **Formal** (junto al reparto y en la barra de envio) fuerza el formal para todos los de ese envio.
  Empieza siempre desmarcada. Un lote guarda la que tenia al meterlo, y en la pagina pone "(formal)" junto al
  reparto pendiente.

Formal es un correo seco: sin saludo ni despedida, asunto y titulo "Lote completado" en vez de "Catalogacion", una
sola linea de cabecera ("Lote completado: 3 fichas catalogadas el 23/09/2026."), sin el pie de "generado
automaticamente" y sin AVISOS, tampoco a ti. Las fichas son las mismas. La pagina marca con "· formal" a quien le va a
llegar asi, en la lista de destinos y en el "conoce a".

**A direcciones que no estan en la agenda.** En el reparto, una linea con la direccion completa
(`fulano@rtve.es: 5`, o `fulano@rtve.es:` para todas las que queden). Para un lote entero a una sola persona, el
boton **Enviar lote a…** de cada lote, que abre la lista de destinos con "otra direccion…" al final; lo mismo con el
boton derecho sobre una ficha o con la barra de fichas seleccionadas.

En Gmail la contrasena normal no sirve: hay que activar la verificacion en dos pasos y crear una "contrasena de
aplicacion" (myaccount.google.com, Seguridad). Se envia sin revisar, en cuanto termina el lote; por eso los AVISOS
te llegan a ti (en la pagina siempre, y en el correo si el reparto te incluye) y no a quien recibe las fichas.

## 3i. Trabajo que llega por correo (buzon)

Con `"correo_buzon_minutos": 2` en `config.json`, el servidor mira cada dos minutos la bandeja de entrada de la
cuenta de catalogar. De cada correo nuevo saca los numeros de envio del asunto y del cuerpo (cualquier numero de
4 o 7 cifras, o de formato EBU) y los de los adjuntos .csv, .txt o .xlsx, que se leen como listas de trabajo con
`listas.py` y no como texto suelto: de cada fila se coge el numero que abre la columna Name, la columna Catalogador
decide a quien se le mandan las fichas (3j) y de la columna Created se lee solo cuando entro el envio en MediaCentral,
nunca numeros. Con eso cataloga un lote mas y responde al remitente con las fichas en las cuatro lineas de siempre,
con el asunto original detras de la marca `[Catalogacion]`; la respuesta dice tambien cuantas filas se han quedado
fuera y por que. El reparto va por la columna Catalogador, mande el correo quien lo mande: cada persona de la agenda
recibe solo sus fichas, y al remitente le llegan las suyas y las que no tienen dueño, con una linea que dice cuantas
son de otros catalogadores y que se les han mandado a ellos. Las tuyas (tu `id` de la agenda) no se mandan: se
quedan en la pagina.
Si el correo traia lista pero no ha salido de ella ningun numero, se contesta explicandolo en vez de callar.
Un numero con su fecha al lado (`0624 del 25/09/2026`) va con esa fecha; si el correo trae una sola fecha suelta
(DD/MM/AAAA), se aplica a los numeros que no traigan la suya. Solo se lee el mensaje: lo que va debajo de la
firma (`--`), de un `De:` o de un correo citado (`>`) no cuenta, y los años escritos en prosa ("26 de septiembre de
2026") no se toman por envios. Cada correo se trata por separado: uno que no se pueda leer (un adjunto corrupto,
una codificacion rara) se anota en la consola, se marca como leido y no impide leer los demas.

Las respuestas y los reenvios de los correos que manda la propia herramienta (las fichas de un lote, las
respuestas del buzon) no cuentan: se reconocen por el Message-ID que citan y por el asunto (`RE: Fichas de
archivo...`, `RV: Lote completado...`, la marca `[Catalogacion]`) y se marcan como leidos sin hacer nada. Asi un
"gracias" con las fichas citadas debajo no vuelve a encolar esos numeros; para pedir trabajo, correo nuevo.

Solo se atienden remitentes permitidos: cualquier direccion de los dominios de `correo_dominios` (por defecto
`rtve.es` y sus subdominios), este o no en la agenda, mas la agenda `documentalistas` y `correo_copia` aunque sean
de fuera. Los demas correos se dejan sin leer y se anotan en la consola; nadie de fuera puede poner el servidor a
trabajar. A quien escribe se le contesta con su tono: formal si no esta en la agenda, informal si esta (salvo
quien tenga `formal: true`), con el nombre de la agenda en el saludo. Un correo (o una lista, desde la pagina o por correo) con
mas envios que `lote_max_envios` (90) no se recorta: se trocea en lotes seguidos, "(1/4)", "(2/4)"..., que se hacen
en orden y contestan cada uno al terminar. El buzon usa las mismas claves que el envio (`correo_usuario`, `correo_clave`) y, en Gmail,
IMAP tiene que estar activado (Configuracion - Ver todos los ajustes - Reenvio y correo POP/IMAP). Con
`correo_buzon_minutos` a 0 el buzon esta apagado.

## 3j. Listas de trabajo de MediaCentral

En la pagina, debajo de la caja de numeros, hay un segundo cuadro para subir la lista que se exporta de la busqueda
de MediaCentral (Search Results, `.csv` o `.xlsx`). No es el Excel de fichas: de la lista solo se saca que envios hay
que hacer, y la ficha se sigue leyendo de la web de la agencia.

- El numero es el que abre la columna **Name** (`4359 USA-MAMDANI LULA-PRESSER: ...`, `4685905 US TX ICE ...`).
  Lo que va detras se guarda solo como etiqueta para reconocer la ficha en el panel.
- La columna **Catalogador** dice de quien es cada fila, y el `id` de cada persona en la agenda `documentalistas`
  (3h) traduce ese usuario de MediaCentral a alguien con correo. Las filas sin catalogador y las tuyas (el `id` de
  la entrada cuyo correo es `correo_copia`) se hacen y se quedan en la pagina; las de alguien mas de la agenda se
  hacen tambien y, al acabar el lote, cada uno recibe por correo solo sus fichas (en la pagina llevan la marca
  `para Javier`, y el reparto escrito a mano no las vuelve a mandar). Las filas de un usuario que no tiene entrada
  en la agenda se saltan, y el panel o la respuesta del buzon dicen que identificador falta para que lo añadas.
- En la columna **Created** nunca se buscan numeros (su 2026 no es un envio de Reuters), pero si se lee cuando
  entro cada envio en MediaCentral. Los numeros de Reuters se repiten cada pocos dias, y en la agencia suele haber
  varios envios con el mismo numero: sin mas datos se cogia el mas reciente, que a menudo no era el de la lista.
  Ahora, con ese momento, se coge el ultimo que la agencia mando ANTES de que el envio entrara en MediaCentral (un
  envio no puede entrar antes de mandarse), con media hora de margen. La regla de "ya hecha" tambien lo usa: una ficha
  hecha de un envio posterior a esa entrada no vale. Si en la lista hay una fecha escrita a mano, la fecha manda.
  El aviso de la ficha dice cual se ha cogido y por que; si todos los envios con ese numero son posteriores a la
  entrada en MediaCentral (raro), se coge el mas reciente y se avisa de que hay que comprobarlo.
- Los numeros repetidos entran una sola vez, y al subirla el panel dice cuantas filas se han quedado fuera y por que.

Si ninguna fila lleva numero de envio (por ejemplo una lista de material propio de RTVE, `br.td1.fib. ...`), la lista
se rechaza entera con ese motivo en vez de encolar basura.

## 3l. Alerta de script cortado

El campo de script del archivo se corta pasadas dos paginas de Word. Al extraer un envio, la herramienta mide
cuantas paginas ocuparia su script pegado en un Word en blanco y, si pasa de dos, la ficha lleva una linea mas:

```
ALERTA: Script cortado (2.4 paginas de Word, el archivo admite 2). Copiar script de este link: https://www.reutersconnect.com/...
```

Sale en la pagina (en rojo, debajo de la ficha), en los correos (a todo el mundo, tambien en formal, porque es
para quien pega) y en la consola. No es un aviso del validador: no se filtra.

No cuenta caracteres, reproduce la maquetacion del Word en español con su plantilla por defecto (A4, margenes de
2,5 cm arriba y abajo y 3 cm a los lados, Calibri 11, interlineado 1,08 y 8 pt tras cada parrafo) con las anchuras
reales de la fuente Calibri. Esta comprobado contra un documento real: acierta la pagina en la que cae el corte. Si
tu Word tiene otros margenes, en `config.json`: `word_margen_vertical_cm` y `word_margen_horizontal_cm`. El tope,
`script_max_paginas` (2). Cada agencia pega distinto: Reuters, linea a linea (un parrafo por plano, y se mide solo
el script); AP pega todo el texto en UN solo parrafo (el aviso "Editors / Producers...", SHOTLIST, STORYLINE y el
pie "Clients are reminded..." seguidos), que cabe mucho mas en dos paginas, y se mide asi, todo junto; si el texto
extraido no trae ese aviso y ese pie fijos, se suman sus caracteres (`ap_script_extra_caracteres`, 360). Para comprobar un envio a mano: `python volcar.py 4359` y luego
`python paginas_word.py debug\4359_volcado.txt`, que dice las paginas, y se compara pegando el script en Word.

## 3m. Deteccion automatica de situaciones y cruce de RESTRICCIONES

RESTRICCIONES es el campo con consecuencias legales y hasta ahora nadie comprobaba que lo que decia la pagina y lo
que escribia el modelo coincidieran. `situaciones.py` busca en el texto de la agencia, con expresiones fijas sacadas
de fichas reales, lo que tiene formula en el criterio (seccion 5 de `reglas_patrones.md`): credito obligatorio (en
sus tres formas, tambien el `PLEASE CREDIT: CNS` de la dateline de Paid Content), no archivar, embargo, no usar en
España o Europa, limites de tiempo y de duracion, un solo uso, no revender, musica, no reeditar, solo en el contexto
de esta noticia, no digital / solo digital, redes sociales, promocion, derechos deportivos, y las lineas de EBU para
sus miembros (`NEWS ACCESS ONLY`, `NO USE / ARCHIVE AFTER X DAYS`, y cualquier linea que nombre a RTVE aunque
empiece por asterisco). Tambien reconoce lo que cambia como se cataloga: planos FILE o con fecha antigua en la
dateline, mudo, avisos de audio, contenido explicito, material en bruto o directo, imagenes generadas por
inteligencia artificial, señal pool, obituarios, envios corregidos o reenviados (REFILE, CORRECTION) y retirados
(KILL), y el proveedor externo de un Paid Content.

Con eso hace tres cosas. Al modelo le pasa las frases de RESTRICCIONES que espera, bajo un epigrafe "detectado
automaticamente, compruebalo": reduce las omisiones sin sustituir al criterio. Despues cruza lo escrito con lo
detectado y avisa (solo a ti) si falta una restriccion que la pagina dice, si hay una que la pagina no respalda, si
una frase esta fuera de formula, si mezcla SIN AVISO con otras, o si recoge un territorio que no afecta a España
(NO USAR EN CHINA). Y lo que todo el mundo tiene que ver antes de usar el material va como ALERTA, igual que la
del script cortado y encadenada con ella: embargo, no archivar, no usar en España o Europa, derechos deportivos,
envio retirado, linea de EBU que nombra a RTVE.

En la misma tanda el validador ha ganado comprobaciones que salen del shotlist y no solo del COMMENT: quien habla
segun los SOUNDBITE tiene que constar en el COMMENT (son la clave de busqueda), los planos de recurso se cuentan en
el shotlist descontando los de sala (cutaways, periodistas, atril) para decidir si el envio es solo hablado, el
INCLUYE ROTULOS y ROTULAR CORTESIA solo valen con respaldo: si el modelo pone INCLUYE ROTULOS y la agencia no dice
que la imagen lleve texto (GRAPHICS, CAPTIONS, BURNT-IN, LOWER THIRD, SUBTITLES, AS AIRED, CCTV...), se quita y se
avisa (con fotogramas adjuntos se deja y se avisa, porque el modelo puede haberlo visto); material de cadena (IRIB,
CGTN, "aired on") solo da un aviso de "comprobar en el video". Si pone ROTULAR CORTESIA y la agencia no pide el
credito (must credit, courtesy of, please credit, credit:), se quita de RESTRICCIONES y se avisa: la fuente del
material no es un credito obligatorio.
LUGAR se contrasta con la dateline (UNKNOWN LOCATION, varias datelines), y se avisa del cargo delante del nombre,
de PRESUNTO y SUPUESTO, de los dias de la semana, de los simbolos (% $ € KM), de las cantidades con unidad en letra
y de RECURSOS DE con declaraciones. La linea ENVIO la compone siempre el programa con los datos del extractor,
para que el modelo no pueda cambiar el numero, la fecha ni el slug.

`python situaciones.py debug\2656_volcado.txt` enseña lo que detecta en un envio guardado.

## 3n. Rellenar MediaCentral (equipo de teletrabajo)

`mediacentral.py` trabaja en el Chrome del equipo desde el que entras en MediaCentral (no hace falta que sea el
del servidor): habla con el servidor por la direccion `.ts.net` y la clave de la pagina. Va en un zip aparte
(`mediacentral_teletrabajo.zip`: `mediacentral.py`, `listas.py`, `mediacentral.example.json`,
`arrancar_mediacentral.bat`) que se descomprime en cualquier carpeta de ese equipo. Solo necesita Python y
Playwright, y usa un perfil propio de Chrome donde se queda la sesion de MediaCentral.

Tu haces la busqueda "tratar agencias" y el programa hace el resto: recorre la lista y abre los envios uno a uno con
DOBLE CLIC, como se hace a mano. El numero lo lee del Name de MediaCentral al abrir cada envio ("5619 POPE-FRANCE
..."), no de la lista, asi que da igual que la columna Name no este a la vista (con un envio abierto la lista
queda estrecha); si la lista si la enseña, encarga todos los numeros al servidor de golpe, y si no, cada uno al
abrirlo (para no esperar, sube antes el CSV de la busqueda a la pagina). Si la lista enseña el Comments, se salta
sin abrir lo que no dice TRATAR AGENCIAS; si no, lo abre y lo deja al ver que ya esta tratado. Las listas que solo
pintan las filas que se ven se van bajando. En cada envio que siga diciendo TRATAR AGENCIAS en Comments cambia el
Name por el NAME de la ficha (sin numero), pone el mismo NAME en el Titulo, el COMMENT en Comments y tu catalogador,
y comprueba que se han quedado. Si el Comments decia algo mas ("TRATAR AGENCIAS (AUN ...)"), lo sustituye igual pero
te lo enseña al final. No pulsa ningun boton: MediaCentral guarda al pasar a otro envio. Al terminar comprueba cada
rellenado en la lista (con el NAME nuevo y sin TRATAR AGENCIAS) y, si la lista no lo deja claro, lo abre y mira sus
campos; los que ya no salen en la busqueda cuentan como guardados. Si el ultimo es el unico que queda en la lista,
no hay a donde pasar: te avisa para que pases tu a otro. Si algo falla (la ficha no ha salido, un campo no acepta el
texto, no se abre el envio) deja ese envio como estaba (con su numero en el Name y TRATAR AGENCIAS en Comments), lo
apunta y sigue.

Las RESTRICCIONES no las escribe: van en otra pestaña y con un desplegable, y las rellenas tu. Al terminar lista en
la consola, y en un recuadro en la propia pagina, las fichas con restricciones, las que tienen ALERTA, las no
guardadas y las saltadas. Cada envio queda anotado en `mediacentral_registro.csv` (cuando, numero, resultado, NAME,
restricciones, alerta, avisos).

La primera vez hay que enseñarle la pantalla (`arrancar_mediacentral.bat` lo pide solo, o
`python mediacentral.py --aprender`): con la lista "tratar agencias" a la vista, abres el primer envio con DOBLE
CLIC y luego pones el cursor en el Name de los metadatos (el que empieza por el numero), en Comments (el que dice
TRATAR AGENCIAS), en el Titulo (mas abajo) y en el Catalogador. Aprender no escribe nada. Se guarda cada campo por
la etiqueta que tiene delante, no por su posicion, para que aguante recargas. El Chrome de la herramienta acepta el
certificado interno de MediaCentral (`"ignorar_certificado": true`); tu Chrome de siempre no cambia.

    python mediacentral.py              todo seguido
    python mediacentral.py --max 3      solo los tres primeros (para probar)
    python mediacentral.py --revisar    rellena y espera: revisas tu y pasas al siguiente (asi se guarda)
    python mediacentral.py --comprobar  solo comprueba que los campos aprendidos se vuelven a encontrar

En MediaCentral el valor de un campo es un texto normal y el editor (la caja donde se escribe) solo existe mientras
el campo tiene el cursor. Por eso al aprender se guarda la FILA del campo: su etiqueta ("Name", "Comments",
"Titulo", "Catalogador") y donde esta la celda del valor respecto a ella. Para leer se lee el texto de la celda;
para escribir se pulsa en la celda, se espera al editor, se escribe y se sale de el para que se confirme, que es lo
que se hace con el raton. Si el editor ya esta abierto se usa directamente. Ademas cada campo se busca por otras
vias (data-testid, name, aria-label, placeholder, etiqueta, id, ruta CSS), asi que aguanta que el panel se
reconstruya con ids nuevos al abrir cada envio. Lo aprendido se guarda antes de
comprobarlo, y si algo no se encuentra lo dice y deja en `debug\` un `mediacentral_diagnostico.txt` (que se
encuentra, por que via, y todos los campos visibles de cada marco) y un `mediacentral_pantalla.png`, que es lo que
hay que mandar para ajustar la busqueda. Un error inesperado queda en pantalla y en `debug\mediacentral_error.txt`;
la ventana no se cierra sola.

Para no esperar: sube antes el CSV de la busqueda a la pagina, o deja que lo encargue el propio programa al
empezar (tarda lo que tarde el servidor en hacer la primera; mientras, las demas se van haciendo).

## 3o. Tipo de acto: shotlist, imagen y fichas aprobadas

Lo que decide el descriptor (RUEDA DE PRENSA, DECLARACIONES, COMPARECENCIA, ENTREVISTA, INTERVENCION, SALUDO Y MUDO, RECURSOS, VIDEO PROMOCIONAL) se comprueba por tres lados:

1. **El shotlist.** `actos.py` mira el titular, las datelines y las lineas de plano (no el STORY, que habla de otros dias): NEWS CONFERENCE, JOINT NEWS CONFERENCE, SPEAKING TO REPORTERS, INTERVIEW WITH, TESTIFYING BEFORE, SPEECH AT, SHAKE HANDS sin SOUNDBITE, PROMOTIONAL VIDEO; sin ningun SOUNDBITE, recursos. Se le da al modelo como pista ("Tipo de acto segun el shotlist") y, si la ficha dice otra cosa, sale un aviso.
2. **La imagen.** El servidor saca `escenas_fotogramas` fotogramas de cada envio (6 por defecto; no se mandan al modelo ni gastan cuota, salvo que `miniaturas` sea mayor que 0). Cada fotograma vota una escena y se descartan el primero y el ultimo. La imagen solo cuenta si la mayoria coincide (`escenas_umbral`) y si esa clase acerto al menos un 85 % al entrenar (`escenas_fiabilidad_min`). Si el shotlist no dice el acto, se le cuenta al modelo; si lo dice, manda el shotlist. Si la ficha no cuadra con la imagen, aviso "Por la imagen parece...: revisar". Nunca cambia la ficha sola.
3. **Aprende de ti.** Al pulsar **Buena** en una ficha, sus fotogramas se copian a `escenas_auto/<tipo de acto>/`, con el tipo que dice la ficha aprobada (si la corriges, el de tu correccion). Las de archivo y resumenes no se usan. Si se vuelve a aprobar con otro descriptor, sus fotogramas cambian de carpeta. Reuters repite numeros: si la carpeta `miniaturas/<numero>` ya se ha sobrescrito con otro envio, no se copia nada.

Lo normal es hacerlo todo desde la pestaña **Imágenes** del panel: las categorías (los tipos de acto y las que crees tú, por ejemplo DESFILE DE MODA, en `escenas_clases.json`), sus imágenes (las de fichas aprobadas y las tuyas, que se suben arrastrándolas, eligiéndolas o pegándolas con Ctrl+V), y el entrenamiento, que se lanza con un botón o solo unos minutos después de que cambien las imágenes (`escenas_entrenar_auto`). Las categorías tuyas no son tipos de acto: se le cuentan al modelo como pista de lo que se ve ("Los fotogramas parecen: DESFILE DE MODA"). Si subes varios fotogramas del mismo vídeo, marca "Son fotogramas del mismo vídeo": cuentan como un solo ejemplo y el acierto que se mide no sale inflado. Funciona igual en la Raspberry Pi (medio segundo por imagen la primera vez; después solo se calculan las nuevas).

Desde la consola (con el servidor en marcha o parado; la primera vez baja el modelo de vision, 352 MB):

```
pip install numpy onnxruntime pillow
python entrenar_escenas.py --desde-aprobadas     (la primera vez: recoge tambien las fichas aprobadas antes de esta version)
python entrenar_escenas.py                       (las siguientes, cada semana o cada 100 aprobadas)
```

Al terminar enseña una tabla por tipo de acto: cuantos envios tiene, cuantas veces acierta cuando lo dice (probando cada envio sin haberlo visto) y si se va a usar. Las clases por debajo del 85 % se siguen aprendiendo pero no se usan; hacen falta al menos 5 pruebas por clase. Se pueden añadir imagenes a mano en `escenas/<tipo>/` con los mismos nombres de carpeta. `python escenas.py miniaturas\4683554` dice que ve en un envio concreto.

Sacar fotogramas añade unos segundos por envio. Si no interesa, `"escenas_fotogramas": 0`.

## 4. Ajustes

- Criterio de redacción: edita el fichero de reglas. Hay dos con el mismo criterio: `reglas.md` (completo, con todos los ejemplos) y `reglas_ligeras.md` (consolidado, la mitad de tamaño, más rápido). Se elige con `"reglas"` en `config.json`. No hay que tocar código.
- Reglas sobre la marcha: `reglas_extra.md`, una por línea en español normal. El redactor las añade al final de las reglas y prevalecen. Tres formas de añadirlas: a mano; `python regla.py "texto de la regla"` (y `python regla.py --listar`); o desde el trabajo con una Issue cuyo título empiece por `REGLA:` (el panel tiene un campo para ello). El worker la guarda y confirma en la issue. Conviene consolidarlas en `reglas.md` de vez en cuando.
- Modelo y esfuerzo: `claude_model` en `config.json` (`sonnet`, `haiku` u `opus`) y `claude_extra_args` para flags adicionales de Claude Code (ni las herramientas ni `--max-turns` van ahi: el redactor los fija el, ninguna herramienta y un turno, o Read y cuatro turnos con fotogramas; si vienen de un config antiguo se ignoran). `claude_thinking_tokens` limita el razonamiento de Claude Code (1024 por defecto: sin él, una ficha puede tardar más de dos minutos).
- Redacción por API en lugar de Claude Code: `"redactor": "api"` y `api_key` con tu clave de la Consola de Anthropic. Modelo en `api_model` (Haiku 4.5 por defecto, el más rápido; `claude-sonnet-5` para más calidad). Las reglas se envían cacheadas, así que el coste por envío es de céntimos. No consume el plan Pro.
- `headless: false` (por defecto) muestra el navegador; conviene dejarlo así, porque sin ventana el navegador cambia de identidad y los sistemas antibot vuelven a pedir verificación. La ventana se puede minimizar.
- `navegador`: `auto` usa tu Google Chrome instalado (perfil separado), después Edge y por último el Chromium de Playwright. Un navegador real es más difícil de detectar como automatizado.

## 5. Seguridad

- `config.json` (clave del servidor y, si la usas, token de GitHub), `perfil_chromium/` (sesion de las agencias) y `cola/` (lo que subes desde el trabajo) estan en `.gitignore`. No los subas nunca a ningun repositorio.
- La clave del servidor es lo unico que separa tu PC de internet mientras Funnel esta activo: larga, exclusiva y no reutilizada. Tras varios intentos fallidos el servidor tarda cada vez mas en contestar. Para dejar de publicar: `tailscale funnel --bg off`.
- El acceso automatizado a Reuters Connect con tu usuario es responsabilidad tuya frente a la agencia.
- El texto de los envíos sale a Anthropic a través de tu cuenta Pro, igual que con la extensión.

## 6. Problemas frecuentes

| Mensaje | Causa | Qué hacer |
|---|---|---|
| `Sesion de Reuters Connect caducada` | La sesión guardada ya no vale | `python login.py` |
| `... muestra una verificacion antibot` | La agencia ha detectado navegación automatizada | `python login.py`, superar la verificación en la ventana, Intro, y repetir. Mantener `headless: false` y `navegador: auto` (Chrome real) en `config.json` |
| `NO ENCONTRADO` | El número no aparece en la búsqueda, o la estructura de la página no coincide | Ejecutar con `--debug --headed` y enviar la carpeta `debug/` |
| `NO ENCONTRADO EN ESA FECHA` | El número existe con otras fechas | Se indican las fechas disponibles |
| La pagina dice `No hay conexion con el servidor` | `servidor.py` parado, PC dormido o Funnel caido | Arrancar `python servidor.py` en casa; comprobar `tailscale funnel status` |
| La direccion `.ts.net` no carga desde el trabajo | El proxy de RTVE bloquea el dominio | Probarla en el movil con datos para descartar; si solo falla en el trabajo, hay que cambiar de via de publicacion |
| `Pon en config.json una clave de al menos 12 caracteres` | Falta `servidor_clave` | Anadirla al `config.json` |
| `Claude Code devolvio error` | Claude Code no instalado, sin sesión, o límite de uso | El mensaje incluye lo que dijo Claude Code (tambien lo que escribe por la salida normal). `claude -p "responde solo OK"` en PowerShell (no dentro de Claude) para aislar. Si `claude` no está en el PATH, el script usa `C:\Users\TU_USUARIO\.local\bin\claude.exe`; también admite la variable de entorno `CLAUDE_EXE` |
| `MODELO NO DISPONIBLE ... en pausa hasta las HH:MM` (consola y cabecera de la pagina) | Limite de uso agotado ("hit your limit", 429), servicio saturado (529, 503), sin sesion de Claude Code ("not logged in"), o tres redacciones seguidas fallidas sin motivo reconocido | No es un error de las fichas: la que estaba en curso vuelve a pendiente, el lote vuelve a la cola sin mandar correos y el worker espera (15 min por limite, o hasta la hora de "resets at" si la dice; 5 min por caida) y reintenta solo. Sin sesion, espera a que entres (`claude /login`) y pulses **Volver a intentarlo** en el lote, que tambien levanta cualquier pausa a mano |
| `No se han encontrado las lineas NAME/COMMENT` | El modelo no respetó el formato | Revisar la salida bruta; suele bastar con repetir |
