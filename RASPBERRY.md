# Catalogator en la Raspberry Pi, con un pendrive

El pendrive hace de disco de la Raspberry: no hace falta tarjeta SD. Unos 30 minutos, casi todo esperando.

## Lo que necesitas

- La Raspberry Pi 4, su **cargador oficial** y un **cable de red** al router.
- Un **pendrive USB 3** (el conector suele ser azul por dentro) de **32 GB o más**, de marca (SanDisk, Samsung,
  Kingston). Se borra entero.
- Tu PC con Catalogator.

## Paso 1. En el PC: guarda tus datos

Catalogator → **Admin → Estado → Descargar copia de mis datos**. Se guarda un `.zip` en Descargas.

(Si no lo has hecho ya: **Admin → Ajustes → Redacción → Token de Claude Code**. Así el token viaja en la copia.)

## Paso 2. En el PC: prepara el pendrive

1. Descarga e instala **Raspberry Pi Imager**: https://www.raspberrypi.com/software/
2. Pincha el pendrive en el PC y abre Raspberry Pi Imager.
3. Elige, en este orden:
   - **Dispositivo**: Raspberry Pi 4.
   - **Sistema operativo**: Raspberry Pi OS (other) → **Raspberry Pi OS Lite (64-bit)**.
   - **Almacenamiento**: tu pendrive (fíjate bien en el nombre y el tamaño).
4. Pulsa **Siguiente** y, cuando pregunte si quieres personalizar, **Editar ajustes**:
   - **Nombre del equipo**: `catalogator`
   - **Usuario**: `adrian` y una **contraseña** que recuerdes (apúntala).
   - Zona horaria: `Europe/Madrid`.
   - Pestaña **Servicios**: marca **Activar SSH** → *Usar contraseña*.
   - **Guardar**.
5. Pulsa **Sí** para aplicar los ajustes y **Sí** para borrar el pendrive. Espera a que diga que ha terminado
   (5–10 minutos) y sácalo.

## Paso 3. En la Raspberry: pincha y enciende

1. **Sin tarjeta SD** dentro. Pincha el pendrive en un puerto **azul** de la Raspberry.
2. Conecta el **cable de red**.
3. Conecta el **cargador**. Espera **3 minutos**.

## Paso 4. En el PC: instala Catalogator en la Raspberry

1. Abre **PowerShell** (tecla Windows, escribe `powershell`, Enter).
2. Escribe y pulsa Enter:
   ```
   ssh adrian@catalogator.local
   ```
   - Si pregunta *Are you sure you want to continue connecting*, escribe `yes` y Enter.
   - Escribe tu contraseña (no se ve mientras escribes) y Enter.
3. Pega esta línea (botón derecho para pegar) y pulsa Enter:
   ```
   curl -fsSL https://raw.githubusercontent.com/malevolisco/catalogar/main/instalar_pi.sh | bash
   ```
4. Espera (10–20 minutos). Solo te pide una cosa: un **enlace de Tailscale**. Cópialo, ábrelo en el navegador
   y entra con la **misma cuenta de Tailscale que en el PC**.
5. Al final sale un recuadro con la **Dirección** y la **Clave**. Apúntalas.

## Paso 5. En el PC: lleva tus datos a la Raspberry

1. Abre en el navegador la **Dirección** del recuadro y entra con la **Clave**.
2. **Admin → Estado → Cargar una copia…** → elige el `.zip` del paso 1 → **Reiniciar ahora**.
3. Desde aquí, la clave vuelve a ser la tuya de siempre.

## Paso 6. Inicia sesión en las agencias

En esa misma página: **Navegador → Iniciar sesión agencias**. Entra en Reuters, AP y EBU ahí dentro y pulsa
**Ya he iniciado sesión**.

## Paso 7. Apaga Catalogator en el PC

Ciérralo (**Salir**). Si el PC y la Raspberry miran el correo a la vez, **los dos contestan**.

Listo: manda un correo con números de envío y la Raspberry te contesta con las fichas.

---

## Si algo no va

- **`ssh` dice que no encuentra `catalogator.local`**: espera 2 minutos más. Si sigue, mira en el router la IP de
  la Raspberry y usa `ssh adrian@192.168.X.X`.
- **La Raspberry no arranca del pendrive** (luz verde quieta, nada más): tiene el arranque antiguo. En Raspberry Pi
  Imager, con una tarjeta SD cualquiera: Sistema operativo → *Misc utility images* → *Bootloader (Pi 4 family)* →
  *USB Boot*. Grábala, métela en la Raspberry, enciende 1 minuto (la luz parpadea rápido), apaga, quita la tarjeta y
  vuelve al paso 3.
- **Ver qué está haciendo**: en la página, **Admin → Registro**.
- **Reparar o reinstalar**: repite el paso 4. Tus datos no se tocan.

## Con pantalla: Catalogator a la vista

El instalador pregunta **¿Vas a conectar una pantalla (HDMI)?** Contesta `s` y, cuando la Raspberry tenga un
monitor o una tele conectada (cable **micro-HDMI** a HDMI, en el puerto de al lado del USB-C), arranca sola con
el panel de Catalogator a pantalla completa, sin escritorio ni clave. Con un ratón USB se maneja como en el PC.

Si ya la instalaste sin pantalla, repite la línea del paso 4 y contesta `s`.

## Llevarla a otro sitio (por ejemplo, a la oficina)

Necesita internet para catalogar. Si allí no puedes enchufar el cable de red, usa el **wifi del móvil**:

1. En casa, activa el **punto de acceso** del móvil.
2. Por SSH, con el nombre y la contraseña de ese wifi:
   ```
   sudo nmcli dev wifi connect "NOMBRE_DEL_WIFI" password "CONTRASEÑA"
   ```
3. Ya está: allí enciende el punto de acceso del móvil antes que la Raspberry y se conectará sola.

Sin internet arranca igual y enseña el panel con lo que ya tiene; solo no puede catalogar ni contestar correos.

## Del día a día

- Se **actualiza sola** cada noche a las 05:10 (o al pulsar **Admin → Estado → Reiniciar el servidor**).
- Si se va la luz, al volver arranca sola.
- Para apagarla bien (por ejemplo, para moverla): `ssh adrian@catalogator.local` y luego `sudo poweroff`.
