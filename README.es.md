# asus-kbd-light

[English](README.md) · **Español**

Control de la retroiluminación RGB del teclado para portátiles **ASUS** cuyo teclado
interno es un dispositivo **I2C HID LampArray** (el estándar de *Iluminación dinámica*
de Windows 11), que en Linux no gestionan ni `asusctl` ni OpenRGB.

Probado en un **ASUS TUF Gaming A16 (FA608UM)** con Fedora 45 / GNOME.

![Iluminación del teclado](docs/screenshot.png)

## ¿Me sirve?

Comprueba si tu portátil tiene este teclado:

```sh
grep -l "HID_ID=0018:00000B05:000019B6" /sys/class/hidraw/*/device/uevent
```

Si devuelve una ruta, sí. Otros modelos con teclado LampArray por I2C pueden funcionar
cambiando `TARGET_ID` en `src/kbd_light.py` y la regla de `data/70-kbd-light.rules`.

## Funcionalidades

- **Aplicación GTK4 / libadwaita** con vista previa en directo del teclado.
- **Color e intensidad** con selector de color y colores rápidos.
- **9 efectos**, cada uno con sus opciones:

  | Efecto | Opciones |
  |---|---|
  | Estático | — |
  | Respiración | brillo mínimo, curva |
  | Arcoíris | saturación, sentido |
  | Alternar dos colores | segundo color, fundido / corte |
  | Latido | brillo en reposo, pulso simple / doble |
  | Vela | intensidad del parpadeo |
  | Aurora | paleta (Boreal, Atardecer, Océano, Bosque, Lava) |
  | Destellos | duración, destellos por ráfaga |
  | Temperatura de la CPU | umbrales frío / caliente, alarma |

- **Modo portátil**: devuelve el control al firmware (Fn + ↑/↓).
- **Servicio de usuario** que anima los efectos y restaura la configuración al iniciar
  sesión y al volver de suspensión.
- **Línea de comandos** `kbd-light`.

## Requisitos

- Python 3, PyGObject con GTK 4 y libadwaita ≥ 1.7, pycairo
  (en Fedora con GNOME ya vienen instalados).
- systemd (servicio de usuario) y udev.

## Instalación

```sh
git clone https://github.com/<usuario>/asus-kbd-light.git
cd asus-kbd-light
./install.sh
```

El script instala la app para tu usuario en `~/.local` y pide `sudo` solo para la regla
udev que da acceso al teclado a la sesión activa (sin necesidad de root después).

Para desinstalar: `./uninstall.sh` (añade `--purge` para borrar también la configuración).

## Uso por línea de comandos

```sh
kbd-light                      # abre la aplicación
kbd-light info                 # dispositivo y nº de LEDs
kbd-light color ff6600 80      # color fijo al 80 %
kbd-light effect rainbow 7     # efecto y velocidad (1-10)
kbd-light color2 00aaff        # segundo color (alternate)
kbd-light auto                 # control del firmware (Fn + ↑/↓)
kbd-light off                  # apagar
```

Efectos: `static`, `breathing`, `rainbow`, `alternate`, `heartbeat`, `candle`,
`aurora`, `strobe`, `cputemp`.

La configuración se guarda en `~/.config/kbd-light/config.json`.

## Cómo funciona

El teclado (`ITE51368`, `0B05:19B6`) se conecta por I2C y Linux lo enlaza a
`hid-generic`; el driver `hid-asus` solo lo reconoce por USB. El control de la luz
vía WMI (`asus::kbd_backlight`, que usa `asusctl`) acepta órdenes pero no tiene efecto.

El dispositivo implementa la página HID *Lighting and Illumination* (`0x59`), es decir,
un **LampArray**. `kbd-light` habla con él directamente por `/dev/hidrawN` con *feature
reports*:

| Report | Uso |
|---|---|
| `0x41` LampArrayAttributes | nº de LEDs, tamaño, intervalo mínimo |
| `0x42` / `0x43` LampAttributes | posición y capacidades de cada LED |
| `0x45` LampRangeUpdate | color RGBI para un rango de LEDs |
| `0x46` LampArrayControl | modo autónomo (firmware) on/off |

Este modelo expone **una sola zona** de luz, así que los efectos son de color global; el
servicio los genera a ~30 fps (~2-3 % de CPU).

## Estructura

```
src/kbd_light.py        núcleo: protocolo LampArray, efectos, CLI y servicio
src/kbd_light_gui.py    interfaz GTK4 / libadwaita
data/                   regla udev, servicio systemd de usuario y lanzador .desktop
install.sh              instalación para el usuario actual
uninstall.sh            desinstalación
```

## Licencia

[MIT](LICENSE) © 2026 Rodrigo Vaamonde
