# CONTEXTO DE CATALOGATOR (leer esto en lugar de toda la conversacion)

Ultima actualizacion: 4 oct 2026. Si algo de aqui no coincide con el codigo, manda el codigo.

## Que es
Herramienta del archivo de RTVE (Adrian, documentalista) que convierte material de agencias
(Reuters, AP, EBU) en fichas de archivo: ENVIO, NAME, COMMENT, RESTRICCIONES. Se llama **Catalogator**.

## Como esta montada
- `servidor.py`: servidor FastAPI + panel web (`panel/index.html`) + un unico hilo de trabajo (cola en `cola/estado.json`).
- Buzon IMAP/SMTP: recibe peticiones por correo y responde con las fichas (no incluye las de otros catalogadores).
- `redactor.py`: redacta con `claude -p` (Claude Code) o con API key. Si el modelo no esta disponible
  (limite de uso, 429/529/503, login) pausa la cola en vez de marcar errores en cascada.
- `extractor.py`, `listas.py`, `catalogar.py`: sacan el texto del envio. En los CSV se usa la columna Created
  (fecha y hora) para elegir el envio correcto de Reuters ("ultimo enviado antes de entrar en MediaCentral + 30 min").
- `situaciones.py` (situaciones), `actos.py` (reconocimiento de actos), `escenas.py` + `entrenar_escenas.py`
  (clasificador de escenas con CLIP ONNX), `paginas_word.py` (medida de paginas: Reuters linea a linea, AP un solo parrafo).
- `mediacentral.py`: rellena MediaCentral (Avid) con Playwright. Doble clic en el item, se guarda solo al pasar al
  siguiente, campos Name / Titulo / Comments / Catalogador aprendidos por fila (el editor solo existe al hacer clic).
  Las restricciones NO se automatizan (proceso complejo con pestañas y desplegables).
- Criterio editorial: `reglas_patrones.md` (reglas de la herramienta, van en la app), `reglas_extra.md` y `ejemplos.md`
  (del usuario, se editan desde el panel, nunca se pisan al actualizar).

## El exe (lo nuevo)
- `catalogator.py`: lanzador (ventana tkinter). Se baja de GitHub Releases `catalogator-app.zip` y se actualiza solo al abrir.
  La app vive en `%LOCALAPPDATA%\Catalogator\app`; los datos del usuario no se tocan; copia de seguridad en `_anterior/`.
- `empaquetar.py` hace el zip de la app; `catalogator.spec` compila con PyInstaller; `.github/workflows/release.yml`
  compila en Windows al publicar una etiqueta `v*` (o a mano con "Run workflow" indicando la version).
- `EXE_MIN` = version minima del exe que admite esta app.
- El `config.json` NO va dentro del exe (llevaria contraseñas): se crea en el primer arranque con valores por defecto
  y una clave aleatoria, y las actualizaciones no lo sobreescriben.

## Reglas de trabajo de Adrian (fijas)
- Respuestas en español (en ingles si escribe en ingles, corrigiendo solo gramatica y vocabulario).
- Codigo siempre entero, nunca fragmentos. Varios ficheros = un zip; un solo fichero = suelto.
- Instrucciones tecnicas: comandos exactos + una frase de lo que hace cada uno. Sin jerga densa y sin tono infantil.
- Al cambiar codigo o reglas: dejar todo en su sitio, sin duplicados ni restos que contradigan lo nuevo. Revisar SIEMPRE antes de entregar.
- No sabe programar; tiene Python en Windows.

## Estado
HECHO: reconocimiento de actos, MediaCentral (doble clic, autoguardado, campos por fila), falsos positivos de KILL,
rotulos y cortesia, Created en CSV, pausa por modelo no disponible, reglas editoriales nuevas (daños, NAME mas corto,
formula de COMMENT, testimonios cortos, visitas, Groenlandia), respuestas por correo sin fichas de otros catalogadores,
lanzador Catalogator completo (probado con un servidor GitHub simulado), primera compilacion en GitHub Actions.

PRIMERA COMPILACION (4 oct): correcta. Release `v2026.10.04` publicada con `Catalogator.exe` (~103 MB, lleva Python,
Playwright, onnxruntime y numpy dentro) y `catalogator-app.zip` (33 ficheros). Avisos sin importancia: `onnx`, `pytest`
y `tzdata` no encontrados (no se usan). Se renombro `main.yml` -> `release.yml` y se subieron checkout@v5 / setup-python@v6
(las versiones anteriores usan Node 20, que GitHub retira). Dentro del exe Playwright NO tiene su Chromium propio
(en modo compilado lo busca dentro del exe): `extractor.py` y `mediacentral.py` abren Chrome o Edge, que si funcionan.

## Pendiente
1. Probar `Catalogator.exe` en el PC de Adrian (primer arranque, actualizacion, navegador Edge/Chrome).
2. Decidir repositorio publico o privado (privado exige token de solo lectura en el exe).
3. Menu **Ajustes** en la ventana (agenda, correo con boton Probar, redaccion, clave de la pagina, MediaCentral). Propuesto, falta el OK.
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
