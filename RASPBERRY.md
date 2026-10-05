# Catalogator en una Raspberry Pi

Para que Catalogator funcione sin depender del PC: mandas un correo con los números y la Raspberry lo cataloga
y contesta. Sirve una **Raspberry Pi 4 de 4 GB** (o una Pi 5).

## Lo que hace falta

- Raspberry Pi 4 (4 GB) o Pi 5, con su **fuente de alimentación oficial** y un disipador o caja con ventilador.
- Una **tarjeta microSD buena** (A2, 32 GB o más) o, mejor, un **SSD por USB** (dura mucho más).
- **Cable de red** al router (mejor que wifi).

## 1. Preparar la tarjeta (en el PC)

1. Descarga **Raspberry Pi Imager**: https://www.raspberrypi.com/software/ e instálalo.
2. Ábrelo y elige:
   - Dispositivo: tu Raspberry Pi.
   - Sistema operativo: **Raspberry Pi OS (other) → Raspberry Pi OS Lite (64-bit)**.
   - Almacenamiento: la tarjeta (o el SSD).
3. Pulsa **Siguiente → Editar ajustes** y rellena:
   - Nombre del equipo: `catalogator`
   - Usuario y contraseña: los que quieras (apúntalos).
   - Pestaña **Servicios**: marca **Activar SSH** con contraseña.
4. Guarda, acepta y espera a que termine. Mete la tarjeta en la Raspberry, conecta el cable de red y enciéndela.
   Espera 2 minutos.

## 2. Entrar en la Raspberry desde el PC

En Windows, abre **PowerShell** (tecla Windows, escribe `powershell`, Enter) y escribe, cambiando `usuario` por
el que pusiste:

```
ssh usuario@catalogator.local
```

La primera vez pregunta si te fías del equipo: escribe `yes` y Enter. Luego, tu contraseña (no se ve al escribir).

## 3. Instalar Catalogator

Pega esta línea y pulsa Enter (tarda unos 10–20 minutos la primera vez):

```
curl -fsSL https://raw.githubusercontent.com/malevolisco/catalogar/main/instalar_pi.sh | bash
```

Por el camino te preguntará dos cosas:

- **Claude Code**: contesta `s` para instalarlo y `s` para entrar con tu cuenta. Sale un enlace: ábrelo en el
  móvil o en el PC, entra con tu cuenta de Claude y vuelve a la ventana negra.
- **Tailscale**: sale otro enlace. Ábrelo y entra con **la misma cuenta de Tailscale que usas en el PC**.

Al final te dice la **dirección** y la **clave** de la Raspberry. Apúntalas.

## 4. Llevar tus datos del PC a la Raspberry

1. En el PC, en Catalogator: **Admin → Estado → Descargar copia de mis datos**. Se guarda un `.zip`.
2. Abre en el navegador la dirección de la Raspberry y entra con la clave que te dio el instalador.
3. **Admin → Estado → Cargar una copia…** y elige ese `.zip`. Pulsa **Reiniciar ahora**.
4. A partir de aquí la clave es la de siempre (la de tu PC).

## 5. Iniciar sesión en las agencias

En la página de la Raspberry: pestaña **Navegador → Iniciar sesión agencias**. Entra en Reuters, AP y EBU
ahí mismo y pulsa **Ya he iniciado sesión**. (Las sesiones del PC no sirven en la Raspberry.)

## 6. Apagar el buzón en el PC

Si el PC y la Raspberry miran a la vez el correo, **los dos contestan**. Cierra Catalogator en el PC, o en el PC
pon **Admin → Ajustes → Correo → Mirar el buzón cada (minutos)** a `0`.

## Del día a día

- **Actualizaciones**: solas, cada noche a las 05:10, o al pulsar **Admin → Estado → Reiniciar el servidor**.
- **Si se va la luz**: al volver, la Raspberry arranca Catalogator sola.
- **Ver qué pasa**: **Admin → Registro** en la página. O, por SSH: `journalctl -u catalogator -f`
  (para salir, Ctrl+C).
- **Reiniciar a mano** (por SSH): `sudo systemctl restart catalogator`
- **Volver a instalar o reparar**: repite la línea del paso 3. Tus datos no se tocan.
