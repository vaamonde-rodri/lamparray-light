# asus-kbd-light

**English** · [Español](README.es.md)

RGB keyboard backlight control for **ASUS** laptops whose internal keyboard is an
**I2C HID LampArray** device (the Windows 11 *Dynamic Lighting* standard), which on
Linux is handled by neither `asusctl` nor OpenRGB.

Tested on an **ASUS TUF Gaming A16 (FA608UM)** running Fedora 45 / GNOME.

![Keyboard lighting app](docs/screenshot.png)

> The user interface is currently in Spanish.

## Does it work on my laptop?

Check whether your laptop has this keyboard:

```sh
grep -l "HID_ID=0018:00000B05:000019B6" /sys/class/hidraw/*/device/uevent
```

If it prints a path, yes. Other models with an I2C LampArray keyboard may work by
changing `TARGET_ID` in `src/kbd_light.py` and the rule in `data/70-kbd-light.rules`.

## Features

- **GTK4 / libadwaita app** with a live preview of the keyboard.
- **Color and brightness**, with a color picker and quick presets.
- **9 effects**, each with its own options:

  | Effect | Options |
  |---|---|
  | Static | — |
  | Breathing | minimum brightness, curve |
  | Rainbow | saturation, direction |
  | Alternate two colors | second color, fade / cut |
  | Heartbeat | resting brightness, single / double beat |
  | Candle | flicker strength |
  | Aurora | palette (Boreal, Sunset, Ocean, Forest, Lava) |
  | Strobe | flash duration, flashes per burst |
  | CPU temperature | cold / hot thresholds, alarm |

- **Laptop mode**: hands control back to the firmware (Fn + ↑/↓).
- **User service** that animates the effects and restores your settings on login
  and after resuming from suspend.
- **Command line** tool `kbd-light`.

## Requirements

- Python 3, PyGObject with GTK 4 and libadwaita ≥ 1.7, pycairo
  (preinstalled on Fedora with GNOME).
- systemd (user service) and udev.

## Installation

```sh
git clone https://github.com/<user>/asus-kbd-light.git
cd asus-kbd-light
./install.sh
```

The script installs the app for your user under `~/.local` and only asks for `sudo` to
install a udev rule that gives the active session access to the keyboard (no root
needed afterwards).

To uninstall: `./uninstall.sh` (add `--purge` to also remove your settings).

## Command line usage

```sh
kbd-light                      # open the app
kbd-light info                 # device and number of LEDs
kbd-light color ff6600 80      # static color at 80 %
kbd-light effect rainbow 7     # effect and speed (1-10)
kbd-light color2 00aaff        # second color (alternate)
kbd-light auto                 # firmware control (Fn + ↑/↓)
kbd-light off                  # turn off
```

Effects: `static`, `breathing`, `rainbow`, `alternate`, `heartbeat`, `candle`,
`aurora`, `strobe`, `cputemp`.

Settings are stored in `~/.config/kbd-light/config.json`.

## How it works

The keyboard (`ITE51368`, `0B05:19B6`) is connected over I2C and Linux binds it to
`hid-generic`; the `hid-asus` driver only recognizes it over USB. The WMI backlight
interface (`asus::kbd_backlight`, used by `asusctl`) accepts commands but has no effect.

The device implements the HID *Lighting and Illumination* usage page (`0x59`), i.e. a
**LampArray**. `kbd-light` talks to it directly through `/dev/hidrawN` using feature
reports:

| Report | Purpose |
|---|---|
| `0x41` LampArrayAttributes | LED count, size, minimum update interval |
| `0x42` / `0x43` LampAttributes | position and capabilities of each LED |
| `0x45` LampRangeUpdate | RGBI color for a range of LEDs |
| `0x46` LampArrayControl | autonomous (firmware) mode on/off |

This model exposes a **single lighting zone**, so effects change the color of the whole
keyboard; the service renders them at ~30 fps (~2-3 % CPU).

## Project layout

```
src/kbd_light.py        core: LampArray protocol, effects, CLI and service
src/kbd_light_gui.py    GTK4 / libadwaita user interface
data/                   udev rule, systemd user service and .desktop launcher
install.sh              per-user installation
uninstall.sh            uninstallation
```

## License

[MIT](LICENSE) © 2026 Rodrigo Vaamonde
