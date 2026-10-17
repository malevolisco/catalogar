# Catalogator en la nube

La versión de Catalogator que funciona **sin ningún equipo encendido**. Mandas un correo con el CSV de
MediaCentral y te contesta con las fichas, igual que Catalogator.

## Cómo se usa

1. En MediaCentral, exporta la búsqueda como CSV o Excel. Tiene que traer las columnas **Name** y
   **Comments**, y en Comments el texto de la agencia en inglés (el plano a plano, la historia y las
   restricciones).
2. Manda el fichero adjunto a la dirección de Catalogator con **+csv** delante de la arroba.
   Si la cuenta es `catalogator@gmail.com`, a **`catalogator+csv@gmail.com`**.
3. En unos 10 o 20 minutos llega la respuesta, con el mismo formato de siempre.

Las filas cuyo Comments no trae el texto de la agencia (por ejemplo «TRATAR AGENCIAS») no se redactan.
La respuesta dice cuáles son.

## Cómo funciona por dentro

- GitHub ejecuta Catalogator cada 10 minutos, en sus servidores y gratis. No hace falta ningún equipo
  encendido.
- Redacta con **el mismo programa** que la Raspberry: las mismas reglas, el mismo criterio, las mismas
  comprobaciones y la misma forma de elegir las fichas aprobadas parecidas.
- Lo aprendido llega desde la Raspberry. Cada noche se manda a sí misma un correo «Cerebro para la nube»
  con las fichas aprobadas y los ajustes, **sin ninguna contraseña**. La nube lee el último. Si la
  Raspberry está apagada unos días, la nube sigue funcionando con lo último que recibió.
- Los correos a la dirección con +csv solo los contesta la nube. La Raspberry los deja sin tocar, así
  que no se contestan dos veces.

## Lo que no hace

- No entra en Reuters, AP ni EBU: el texto tiene que venir en el CSV.
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
| `CATALOGATOR_API_KEY` | La clave de la API de Claude (la de Admin → Redacción → Clave de la API) |

Si preferís ChatGPT, en vez de `CATALOGATOR_API_KEY` cread `CATALOGATOR_OPENAI_KEY` con la clave de
OpenAI.

4. En Catalogator, **Admin → Catalogator en la nube → Mandar a la nube ahora**. Así la nube tiene ya lo
   aprendido, sin esperar a la noche.
5. Para probarlo sin esperar a los 10 minutos: en GitHub, pestaña **Actions → Catalogator en la nube →
   Run workflow**.

## Coste

- GitHub: gratis.
- Redacción: lo que cueste la API de Claude o de ChatGPT, céntimos por ficha.

## Seguridad

- Las contraseñas solo están en los secretos de GitHub, cifradas. El correo «Cerebro para la nube» no
  lleva ninguna.
- El registro de GitHub de cada vuelta es público, porque el repositorio lo es. Solo dice cuántos
  correos y cuántas fichas, nunca textos, nombres ni direcciones.
- El texto de los envíos pasa por los servidores de GitHub mientras se redacta y no se guarda.
- Solo se contesta a quien puede pedir a Catalogator (dominio rtve.es y la agenda).
