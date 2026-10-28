# Catalogator en la nube

La versión de Catalogator que funciona **sin ningún equipo encendido**. Mandas un correo con el CSV de
MediaCentral y te contesta con las fichas, igual que Catalogator.

## Cómo se usa

1. En MediaCentral, exporta la búsqueda como CSV o Excel. Tiene que traer la columna **Name** y el texto
   de la agencia en inglés (el plano a plano, la historia y las restricciones) en **Cont.** o en
   **Comments**. Si vienen las dos, manda Cont.
2. Manda el fichero adjunto a la dirección de Catalogator con **+csv** delante de la arroba.
   Si la cuenta es `catalogator@gmail.com`, a **`catalogator+csv@gmail.com`**.
3. En unos 5 minutos llega la respuesta, con el mismo formato de siempre (con el timbre puesto; sin él, GitHub
   puede tardar horas, ver «El timbre»).

Las filas que no traen el texto de la agencia ni en Cont. ni en Comments (por ejemplo «TRATAR AGENCIAS») no
se redactan.
La respuesta dice cuáles son.

## Si Catalogator no funciona (respaldo)

No hace falta cambiar nada: se sigue mandando a la **dirección normal**. Si la Raspberry está apagada o no
funciona y a los **15 minutos** el correo sigue sin leer, la nube lo contesta en su lugar, con el mismo formato.

- Solo si el correo trae un CSV o Excel con el texto de la agencia (Cont. o Comments): la nube no entra en
  Reuters, AP ni EBU. Un correo con solo números se queda sin leer y lo contesta la Raspberry cuando vuelva.
- Antes de redactar, la nube lo marca como leído: si la Raspberry vuelve en ese rato, no lo contesta otra vez.
- Lo que lleva más de un día sin leer no se contesta (son correos olvidados, no peticiones de hoy).

## Cómo funciona por dentro

- GitHub ejecuta Catalogator en sus servidores, gratis. No hace falta ningún equipo encendido: ni la Raspberry
  ni ningún ordenador. Lo arranca el timbre en cuanto llega un correo; además GitHub tiene una vuelta
  programada de respaldo, que en la práctica pasa cada pocas horas.
- Redacta Claude Code con **tu suscripción de Claude**, como la Raspberry. No hace falta la API ni se
  paga por ficha.
- Redacta con **el mismo programa** que la Raspberry: las mismas reglas, el mismo criterio, las mismas
  comprobaciones y la misma forma de elegir las fichas aprobadas parecidas.
- Lo aprendido llega desde la Raspberry. Cada noche se manda a sí misma un correo «Cerebro para la nube»
  con las fichas aprobadas y los ajustes, **sin ninguna contraseña**. La nube lee el último. Si la
  Raspberry está apagada unos días, la nube sigue funcionando con lo último que recibió.
- Los correos a la dirección con +csv solo los contesta la nube. La Raspberry los deja sin tocar, así
  que no se contestan dos veces. Los de la dirección normal, la Raspberry; la nube solo si siguen sin leer a
  los 15 minutos (ver «Si Catalogator no funciona»).

## Lo que no hace

- No entra en Reuters, AP ni EBU: el texto tiene que venir en el CSV (Cont. o Comments).
- No tiene panel: no se aprueban fichas desde aquí. Se siguen aprobando en Catalogator.
- No reutiliza fichas ya hechas ni manda cada ficha a su catalogador: todo vuelve a quien mandó el
  correo.

## Puesta en marcha (una vez, unos 10 minutos)

Hacen falta tres datos. Se guardan como **secretos** de GitHub: van cifrados y nadie puede verlos, ni
siquiera tú una vez guardados. No los escribas en ningún otro sitio ni en ningún chat.

1. Entra en **github.com/malevolisco/catalogar**, pestaña **Settings**.
2. En el menú de la izquierda, **Secrets and variables → Actions**.
3. Pulsa **New repository secret** y crea estos tres, uno a uno (el nombre tal cual, en mayúsculas):

| Nombre | Qué va dentro |
|---|---|
| `CATALOGATOR_CORREO` | La dirección de la cuenta de Catalogator (la de Admin → Correo) |
| `CATALOGATOR_CLAVE` | Su contraseña de aplicación de Gmail (la misma de Admin → Correo) |
| `CATALOGATOR_CLAUDE_TOKEN` | El token de un año de tu suscripción de Claude (el de Admin → Redacción → Token de Claude Code) |

Si en Admin no tienes ese token, se saca una vez en un ordenador con Claude Code instalado: abre la
ventana de comandos, escribe `claude setup-token`, entra con tu cuenta de Claude y copia el código que
empieza por `sk-ant-oat`. Dura un año.

Solo si algún día preferís pagar por uso en vez de usar la suscripción: en lugar del token, se puede
crear `CATALOGATOR_API_KEY` (clave de la API de Claude) o `CATALOGATOR_OPENAI_KEY` (ChatGPT).

4. En Catalogator, **Admin → Catalogator en la nube → Mandar a la nube ahora**. Así la nube tiene ya lo
   aprendido, sin esperar a la noche.
5. Pon el timbre (abajo). Para probar sin él: en GitHub, pestaña **Actions → Catalogator en la nube →
   Run workflow**.

## El timbre (para que conteste en minutos)

GitHub promete lanzar la nube cada 10 minutos, pero con los repositorios gratuitos la retrasa: en la práctica,
cada 2 a 6 horas. El timbre lo arregla: es un programita de Google (Apps Script) que vive en la cuenta de Gmail de
Catalogator y que Google ejecuta cada minuto, gratis. Si ha llegado un correo a la dirección con +csv, o si un
correo con adjunto a la dirección normal sigue sin leer a los 15 minutos, cambia el texto del issue «Timbre de la
nube» del repositorio (el 64) y eso arranca la nube en ese momento. No lee el contenido de los correos: solo mira
si los hay, si están leídos, su hora y a quién van. Ese issue no hay que tocarlo ni cerrarlo.

Si ya tenías el timbre puesto y se actualiza `nube_timbre.gs`, vuelve a pegar el código entero, guarda y ejecuta
otra vez **instalar** (la llave no hay que tocarla).

Se instala una vez (unos 10 minutos):

**A. La llave de GitHub** (solo sirve para escribir en los issues de este repositorio)
1. En GitHub, arriba a la derecha, tu foto → **Settings** → abajo del todo, **Developer settings** →
   **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. **Token name**: Catalogator timbre. **Expiration**: 1 año (o lo que prefieras; cuando caduque, se crea otra).
3. **Repository access**: **Only select repositories** → elige **malevolisco/catalogar**.
4. **Permissions** → **Repository permissions** → **Issues**: **Read and write**. Nada más (GitHub añade solo
   «Metadata: Read-only», que es obligatorio).
5. **Generate token** y copia la llave (empieza por `github_pat_`). Solo se enseña una vez.

**B. El programita, en la cuenta de Gmail de Catalogator**
1. Entra en **script.google.com** con la cuenta de Catalogator (no con la tuya personal).
2. **Nuevo proyecto**. Arriba, cámbiale el nombre a **Catalogator timbre**.
3. Borra lo que trae y pega entero el contenido de `nube_timbre.gs` (está en el repositorio). Guarda (el disquete).
4. A la izquierda, la rueda (**Configuración del proyecto**) → abajo, **Propiedades del script** → **Añadir
   propiedad**: nombre `GITHUB_TOKEN`, valor la llave del paso A. **Guardar**.
5. Vuelve al código (los corchetes `< >` de la izquierda). Arriba, en el desplegable de funciones, elige
   **instalar** y pulsa **Ejecutar**.
6. Google pide permiso: **Revisar permisos** → la cuenta de Catalogator → si sale «Google no ha verificado esta
   aplicación», **Configuración avanzada** → **Ir a Catalogator timbre** → **Permitir**. Es tu propio programa:
   pide leer el correo (para ver si hay CSV) y conectarse fuera (para avisar a GitHub).
7. Abajo debe salir «Timbre instalado». Desde ahí, solo.

Para comprobarlo: mándate un CSV a la dirección con +csv; en un par de minutos, en GitHub → **Actions** sale una
vuelta nueva de «Catalogator en la nube», y unos minutos después llega la respuesta. Si no, en script.google.com →
**Ejecuciones** se ve qué ha dicho.

La llave solo puede escribir en los issues: no puede lanzar tareas de GitHub, ni publicar versiones, ni tocar el
código. Lo peor que podría hacer alguien que la consiguiera es escribir o borrar issues, o lanzar la nube más veces
de la cuenta. Aun así, vive solo en las Propiedades del script de la cuenta de Catalogator. Si alguna vez crees que
se ha visto, bórrala en GitHub (**Fine-grained tokens** → **Delete**) y crea otra.

## Coste

- GitHub: gratis.
- Redacción: nada aparte de tu suscripción de Claude. Cuenta para el mismo límite de uso que la
  Raspberry y que tu propio uso de Claude.

## Seguridad

- Las contraseñas solo están en los secretos de GitHub, cifradas; la llave del timbre, en las Propiedades del
  script de la cuenta de Catalogator. El correo «Cerebro para la nube» no
  lleva ninguna.
- El registro de GitHub de cada vuelta es público, porque el repositorio lo es. Solo dice cuántos
  correos y cuántas fichas, nunca textos, nombres ni direcciones.
- El texto de los envíos pasa por los servidores de GitHub mientras se redacta y no se guarda.
- Solo se contesta a quien puede pedir a Catalogator (dominio rtve.es y la agenda).
