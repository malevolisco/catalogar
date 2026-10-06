# CONTEXTO DE CATALOGATOR (leer esto en lugar de toda la conversacion)

Ultima actualizacion: 6 oct 2026. Si algo de aqui no coincide con el codigo, manda el codigo.

## Que es
Herramienta del archivo de RTVE (Adrian, documentalista) que convierte material de agencias
(Reuters, AP, EBU) en fichas de archivo: ENVIO, NAME, COMMENT, RESTRICCIONES. Se llama **Catalogator**.

## Como esta montada
- `servidor.py`: servidor FastAPI + panel web (`panel/index.html`, diseño claro con pestañas Fichas / Reglas / Fichas aprobadas;
  el del repositorio era uno antiguo que hablaba con GitHub y se sustituyo el 4 oct) + un unico hilo de trabajo (cola en `cola/estado.json`).
- Buzon IMAP/SMTP: recibe peticiones por correo y responde con las fichas (no incluye las de otros catalogadores).
- `redactor.py`: redacta con `claude -p` (Claude Code) o con API key. Sesion: `claude_token` (de `claude setup-token`, un año)
  va como CLAUDE_CODE_OAUTH_TOKEN (y se quita ANTHROPIC_API_KEY del entorno); sin sesion, `api_reserva` redacta con la API
  si hay api_key, o la cola se pausa 10 min y reintenta sola, con un correo de aviso a correo_copia. Si el modelo no esta disponible
  (limite de uso, 429/529/503, login) pausa la cola en vez de marcar errores en cascada.
- `extractor.py`, `listas.py`, `catalogar.py`: sacan el texto del envio. En los CSV se usa la columna Created
  (fecha y hora) para elegir el envio correcto de Reuters ("ultimo enviado antes de entrar en MediaCentral + 30 min").
- `situaciones.py` (situaciones), `actos.py` (reconocimiento de actos), `escenas.py` + `entrenar_escenas.py`
  (clasificador de escenas con CLIP ONNX), `paginas_word.py` (medida de paginas: Reuters linea a linea, AP un solo parrafo).
- `mediacentral.py`: rellena MediaCentral (Avid) con Playwright. Doble clic en el item, se guarda solo al pasar al
  siguiente, campos Name / Titulo / Comments / Catalogador aprendidos por fila (el editor solo existe al hacer clic).
  Las restricciones NO se automatizan (proceso complejo con pestañas y desplegables).
- Criterio editorial: `reglas_patrones.md` (reglas de la herramienta, van en la app), `reglas_extra.md` y `ejemplos.md`
  (del usuario, se editan desde el panel, nunca se pisan al actualizar). `criterio.py`: los cambios de Adrian sobre el
  criterio base (editar/quitar/añadir lineas) van en `criterio_cambios.json` (suyo) y se aplican encima al redactar; si una
  version nueva cambia una linea tocada, el cambio queda "suelto" y el panel pregunta.
- Prompt dinamico (6 oct): el system es fijo (criterio + Mis reglas + escritura normal, cacheable); las fichas aprobadas
  van en el user y solo las `ejemplos_por_ficha` (8) mas parecidas al envio (`redactor.elegir_ejemplos`, palabras comunes
  por raiz de 6 letras pesadas por rareza). `aprender.py`: "Pasar al criterio" (el modelo propone sitio, texto y lineas
  que sobran; se aplica con criterio.anadir/quitar) y sugerencias al aprobar una ficha corregida (borrador del modelo en
  `ficha["borrador"]`, cola/sugerencias.json, pestaña Reglas → Sugerencias; nunca se añaden solas).
- `imagenes.py` (6 oct): pestaña Imagenes. Categorias = tipos de acto + propias (`escenas_clases.json`), subir/pegar/mover/
  quitar imagenes (`escenas/<clase>/`), entrenar en segundo plano con `entrenar_escenas.entrenar()` (boton o solo, cada
  10 min si cambio la firma de imagenes, `escenas_entreno.json`). Las imagenes viajan en la copia de datos.
- `admin.py`: pestaña Admin (estado, registro en vivo, ajustes de config.json con agenda y Probar correo, historial de
  correos en `cola/correos.jsonl`, estadisticas), reiniciar (sale con codigo 75 y el lanzador lo relanza) y `/local?t=`
  (la ventana de escritorio entra sin clave con el token de CATALOGATOR_TOKEN_LOCAL).
  La direccion de fuera (`tailscale funnel status --json`) sale arriba con boton Copiar y en Admin → Estado con la
  clave, que solo se entrega a peticiones de este PC (sin X-Forwarded-For).

## El exe (lo nuevo)
- `catalogator.py`: lanzador. Abre una ventana de escritorio propia (pywebview + WebView2) con el panel, sin navegador;
  si no puede, la ventana tkinter de antes con el navegador (`--clasica` la fuerza). Desde fuera, Tailscale + clave.
  El navegador de agencias trabaja fuera de la pantalla (`navegador_oculto`, no headless) y se ve y se maneja desde la
  pestaña **Navegador** del panel (`visor.py`: imagen en directo por Page.startScreencast + clics/teclas reenviados; todo lo
  de Playwright lo hace el hilo del Worker en `VISOR.bombear()`). El login de agencias tambien va ahi. "Sacar la ventana
  real" la trae a la pantalla si algo no responde.
  La imagen llega como MJPEG (`/api/visor/directo`, ~20 cuadros/s, ~0,2 s de tecla a imagen); si falla, sondeo.
- Aspecto (4 oct): barra lateral oscura con iconos, cabecera con titulo de seccion, icono propio `catalogator.ico`
  (lo usa catalogator.spec) y pantalla de arranque a juego. EXE_MIN = v2026.10.04.6 por el icono y el arranque. Se baja de GitHub Releases `catalogator-app.zip` y se actualiza solo al abrir.
  La app vive en `%LOCALAPPDATA%\Catalogator\app`; los datos del usuario no se tocan; copia de seguridad en `_anterior/`.
- `empaquetar.py` hace el zip de la app; `catalogator.spec` compila con PyInstaller; `.github/workflows/release.yml`
  compila en Windows al publicar una etiqueta `v*` (o a mano con "Run workflow" indicando la version).
- `EXE_MIN` = version minima del exe que admite esta app.
- El `config.json` NO va dentro del exe (llevaria contraseñas): se crea en el primer arranque con valores por defecto
  y una clave aleatoria, y las actualizaciones no lo sobreescriben.

## Raspberry Pi (5 oct)
Adrian va a pasar Catalogator a una Pi 4 de 4 GB para no depender del PC (correo con numeros → fichas por correo).
`instalar_pi.sh` (rehecho, se lanza con curl | bash): Chromium del sistema, venv, Claude Code, Tailscale, servicio
`catalogator.service` (xvfb-run + `catalogator.py --consola --servicio`, al reiniciar sale con 75 y systemd lo relanza y
actualiza) y `catalogator-noche.timer` (05:10). Datos del PC → Pi con Admin → Estado → Descargar/Cargar copia (los perfiles
del navegador no viajan; en la Pi se inicia sesion en la pestaña Navegador). Guia para Adrian: `RASPBERRY.md`.
OJO: si PC y Pi miran el buzon a la vez, contestan los dos.

## Reglas de trabajo de Adrian (fijas)
- Respuestas en español (en ingles si escribe en ingles, corrigiendo solo gramatica y vocabulario).
- Codigo siempre entero, nunca fragmentos. Varios ficheros = un zip; un solo fichero = suelto.
- Instrucciones tecnicas: comandos exactos + una frase de lo que hace cada uno. Sin jerga densa y sin tono infantil.
- Al cambiar codigo o reglas: dejar todo en su sitio, sin duplicados ni restos que contradigan lo nuevo. Revisar SIEMPRE antes de entregar.
- No sabe programar; tiene Python en Windows.
- GitHub lo lleva Claude de principio a fin: cambios, pull request, unirla a `main` y publicar version (Actions).
  Adrian solo sube lo que existe unicamente en su PC (nunca `config.json`, `cola`, `perfil_*`) y prueba el exe.

## Estado
HECHO: reconocimiento de actos, MediaCentral (doble clic, autoguardado, campos por fila), falsos positivos de KILL,
rotulos y cortesia, Created en CSV, pausa por modelo no disponible, reglas editoriales nuevas (daños, NAME mas corto,
formula de COMMENT, testimonios cortos, visitas, Groenlandia; 5 oct: MODA en el NAME, GUERRA solo para material
de combate (aviso en redactor si sobra o falta), sin nombres de quien llega en photocall/desfile/llegadas salvo reyes
y presidentes relevantes, preposiciones justas en el NAME sin pasar de 65, principio de ruido en la busqueda;
6 oct, por indicacion de su jefa: jefes de Estado y de Gobierno en el NAME siempre CARGO APELLIDO (PRESIDENTE ZELENSKI;
dos del mismo cargo, PRESIDENTES ZELENSKI Y MACRON), y en photocall/llegadas el COMMENT nombra hasta unos seis, no la lista entera), respuestas por correo sin fichas de otros catalogadores,
lanzador Catalogator completo (probado con un servidor GitHub simulado), primera compilacion en GitHub Actions,
panel nuevo, app de escritorio con Admin y criterio editable (4 oct), prompt dinamico, sugerencias aprendidas y pestaña
Imagenes (6 oct; probado en Linux con modelo simulado: el CLIP real no se puede bajar desde el entorno de Claude).

PRIMERA COMPILACION (4 oct): correcta. Release `v2026.10.04` publicada con `Catalogator.exe` (~103 MB, lleva Python,
Playwright, onnxruntime y numpy dentro) y `catalogator-app.zip` (33 ficheros). Avisos sin importancia: `onnx`, `pytest`
y `tzdata` no encontrados (no se usan). Se renombro `main.yml` -> `release.yml` y se subieron checkout@v5 / setup-python@v6
(las versiones anteriores usan Node 20, que GitHub retira). Dentro del exe Playwright NO tiene su Chromium propio
(en modo compilado lo busca dentro del exe): `extractor.py` y `mediacentral.py` abren Chrome o Edge, que si funcionan.
PRIMERA PRUEBA EN EL PC (4 oct): el exe instala y arranca, pero `servidor.py` cae con `No module named 'actos'`:
`actos.py` nunca se subio al repositorio. Ademas `empaquetar.py` dejaba fuera `escenas.py` (excluia el prefijo
"escenas"; ahora "escenas/" y "escenas_"). `empaquetar.py` comprueba ahora que todo lo que se importa esta en el zip
o instalado, y si no, para la compilacion con el nombre del fichero que falta.

## Pendiente
1. Instalar la Raspberry (RASPBERRY.md) cuando Adrian la encuentre; probar alli login en agencias y un correo real.
2. Probar en Windows la version de escritorio (v2026.10.04.6): ventana propia, Admin, criterio editable, pestaña Navegador
   (login de agencias dentro de la app).
   No se puede ver desde Linux: la ventana se probo con un pywebview simulado.
3. Decidir repositorio publico o privado (privado exige token de solo lectura en el exe).
4. CSV: Adrian dijo "ahora no me reconoce los csv, hasta ayer si"; no se pudo reproducir con los tres CSV reales. Falta el mensaje exacto y el fichero.
5. `ejemplos.md` tiene fichas aprobadas que contradicen reglas nuevas (RECURSOS en NAME, SECUELAS): revisar.
6. Limpiar del repositorio ficheros antiguos que `empaquetar.py` mete en el zip (`bueno.py`, `worker.py`, `login.py`, `regla.py`, `volcar.py`, `atajo_catalogar_v45.md`…).
7. Seguridad: regenerar el token de GitHub y la contraseña de aplicación de Gmail que se compartieron antes.
8. Revisar `config.json` del usuario: agenda de documentalistas, `lote_max_envios`, `claude_extra_args: []`.
9. Ideas sueltas: etiquetas de valor frente a VALORAR de AP, idioma de las declaraciones, excepcion de titulos de rango militar.

## Lo que NO se puede comprobar desde el entorno de Claude (Linux)
La compilacion real en Windows y la pantalla real de MediaCentral: se prueban con simulaciones.

## Como mantener este fichero
Al terminar cada tarea: mover lo hecho de "Pendiente" a "Hecho", añadir decisiones nuevas, mantenerlo por debajo de ~100 lineas.
