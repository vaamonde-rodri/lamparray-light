#!/usr/bin/env python3
"""kbd-light: iluminación del teclado ASUS ITE51368 (0B05:19B6) vía HID LampArray.

Uso:
  kbd-light                    abre la aplicación gráfica
  kbd-light info               muestra el dispositivo y nº de LEDs
  kbd-light color RRGGBB [0-100]  color fijo e intensidad (%)
  kbd-light effect NOMBRE [1-10]  efecto y velocidad. Efectos: static, breathing,
                     rainbow, alternate, heartbeat, candle, aurora, strobe, cputemp
  kbd-light color2 RRGGBB      segundo color (para "alternate")
  kbd-light auto               control del firmware (Fn + ↑/↓)
  kbd-light off                apaga
  kbd-light restore            aplica la última configuración guardada
  kbd-light daemon             restaura al iniciar sesión y tras suspender
"""
import colorsys
import fcntl
import glob
import json
import math
import os
import struct
import sys
import time

TARGET_ID = "0018:00000B05:000019B6"
CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "kbd-light"
)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
DEFAULT_CONFIG = {
    "enabled": True, "mode": "color", "color": "ffffff", "color2": "0033ff",
    "intensity": 100, "effect": "static", "speed": 5,
}
# efecto: (nombre, usa color, usa 2º color, usa velocidad)
EFFECT_INFO = {
    "static":    ("Estático",             True,  False, False),
    "breathing": ("Respiración",          True,  False, True),
    "rainbow":   ("Arcoíris",             False, False, True),
    "alternate": ("Alternar dos colores", True,  True,  True),
    "heartbeat": ("Latido",               True,  False, True),
    "candle":    ("Vela",                 True,  False, True),
    "aurora":    ("Aurora",               False, False, True),
    "strobe":    ("Destellos",            True,  False, True),
    "cputemp":   ("Temperatura de la CPU", False, False, False),
}
EFFECTS = tuple(EFFECT_INFO)


def _range(key, label, lo, hi, default, unit="", step=1):
    return {"key": key, "kind": "range", "label": label, "lo": lo, "hi": hi,
            "default": default, "unit": unit, "step": step}


def _choice(key, label, options, default):
    return {"key": key, "kind": "choice", "label": label, "options": options,
            "default": default}


AURORA_PALETTES = {  # centro de tono, amplitud, saturación
    "boreal": (0.42, 0.17, 0.90),
    "sunset": (0.97, 0.09, 0.90),
    "ocean": (0.58, 0.08, 0.90),
    "forest": (0.30, 0.07, 0.85),
    "lava": (0.03, 0.04, 1.00),
}

# Opciones específicas de cada efecto
EFFECT_PARAMS = {
    "breathing": [
        _range("min", "Brillo mínimo", 0, 80, 0, " %", 5),
        _choice("shape", "Curva", [("smooth", "Suave"), ("sine", "Senoidal"),
                                   ("sharp", "Rápida y corta")], "smooth"),
    ],
    "rainbow": [
        _range("saturation", "Saturación", 20, 100, 100, " %", 5),
        _choice("direction", "Sentido", [("fwd", "Rojo → verde → azul"),
                                         ("rev", "Rojo → azul → verde")], "fwd"),
    ],
    "alternate": [
        _choice("transition", "Transición", [("smooth", "Fundido"), ("cut", "Corte")],
                "smooth"),
    ],
    "heartbeat": [
        _range("rest", "Brillo en reposo", 0, 60, 8, " %", 2),
        _choice("beats", "Pulsos", [("double", "Doble (lub-dub)"), ("single", "Simple")],
                "double"),
    ],
    "candle": [
        _range("flicker", "Intensidad del parpadeo", 10, 100, 50, " %", 5),
    ],
    "aurora": [
        _choice("palette", "Paleta", [("boreal", "Boreal"), ("sunset", "Atardecer"),
                                      ("ocean", "Océano"), ("forest", "Bosque"),
                                      ("lava", "Lava")], "boreal"),
    ],
    "strobe": [
        _range("flash", "Duración del destello", 30, 300, 70, " ms", 10),
        _range("burst", "Destellos por ráfaga", 1, 4, 1),
    ],
    "cputemp": [
        _range("cold", "Temperatura «fría» (verde)", 30, 70, 40, " °C"),
        _range("hot", "Temperatura «caliente» (rojo)", 60, 100, 90, " °C"),
        _choice("alarm", "Al superar la caliente", [("none", "Nada"),
                                                    ("blink", "Parpadear en rojo")], "blink"),
    ],
}


def get_params(cfg, effect):
    """Valores de las opciones del efecto (por defecto + guardados)."""
    values = {p["key"]: p["default"] for p in EFFECT_PARAMS.get(effect, [])}
    values.update(cfg.get("params", {}).get(effect, {}))
    return values
FPS = 30

PERMISSION_HELP = (
    "Sin permiso para acceder al teclado. Instala la regla udev:\n"
    "  sudo install -m 644 ~/.local/share/kbd-light/70-kbd-light.rules /etc/udev/rules.d/\n"
    "  sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw"
)


class DeviceError(Exception):
    pass


class PermissionDenied(DeviceError):
    pass


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


def find_device():
    for d in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            with open(os.path.join(d, "device/uevent")) as f:
                if f"HID_ID={TARGET_ID}" in f.read():
                    return "/dev/" + os.path.basename(d)
        except OSError:
            pass
    raise DeviceError("No se encontró el teclado ITE51368 (0B05:19B6).")


class Keyboard:
    def __init__(self):
        self.path = find_device()
        try:
            self.fd = os.open(self.path, os.O_RDWR)
        except PermissionError as e:
            raise PermissionDenied(PERMISSION_HELP) from e
        self._count = None

    def close(self):
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _get_feature(self, report_id, length):
        buf = bytearray(length)
        buf[0] = report_id
        fcntl.ioctl(self.fd, _ioc(3, 0x07, length), buf)
        return bytes(buf)

    def _set_feature(self, data):
        buf = bytearray(data)
        fcntl.ioctl(self.fd, _ioc(3, 0x06, len(buf)), buf)

    @property
    def lamp_count(self):
        if self._count is None:
            # 0x41 LampArrayAttributes: LampCount(u16) + 5 x u32
            self._count = struct.unpack_from("<H", self._get_feature(0x41, 23), 1)[0]
        return self._count

    def set_autonomous(self, on):
        # 0x46 LampArrayControl: AutonomousMode(u8)
        self._set_feature(bytes([0x46, 1 if on else 0]))

    def write_rgb(self, r, g, b):
        """Escribe un color a todos los LEDs (requiere modo autónomo desactivado)."""
        # 0x45 LampRangeUpdate: flags(u8) start(u16) end(u16) R G B I
        end = max(self.lamp_count - 1, 0)
        self._set_feature(struct.pack("<BBHHBBBB", 0x45, 0x01, 0, end, r, g, b, 255))

    def set_color(self, hexcolor, intensity=100):
        r, g, b = parse_hex(hexcolor)
        k = max(0, min(100, int(intensity))) / 100
        self.set_autonomous(False)
        self.write_rgb(*(round(c * k) for c in (r, g, b)))

    def off(self):
        self.set_color("000000", 0)

    def apply(self, cfg, t=0.0):
        if not cfg.get("enabled", True):
            self.off()
        elif cfg.get("mode") == "auto":
            self.set_autonomous(True)
        else:
            self.set_autonomous(False)
            self.write_rgb(*frame(cfg, t))


def parse_hex(h):
    h = h.strip().lstrip("#")
    if len(h) != 6:
        raise ValueError(f"Color no válido: {h!r} (usa RRGGBB)")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def is_animated(cfg):
    return (cfg.get("enabled", True) and cfg.get("mode") != "auto"
            and cfg.get("effect", "static") in EFFECTS[1:])


def _noise(t):
    """Ruido suave y determinista en [-1, 1] (suma de senos inconmensurables)."""
    return (math.sin(t) + 0.6 * math.sin(2.17 * t + 1.3)
            + 0.3 * math.sin(4.73 * t + 0.7)) / 1.9


_temp_cache = {"t": -1.0, "value": None, "path": None}


def cpu_temp():
    """Temperatura de la CPU en °C (k10temp/coretemp), leída como mucho 1 vez/s."""
    now = time.monotonic()
    if now - _temp_cache["t"] < 1.0:
        return _temp_cache["value"]
    _temp_cache["t"] = now
    if _temp_cache["path"] is None:
        for h in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            try:
                with open(os.path.join(h, "name")) as f:
                    if f.read().strip() in ("k10temp", "coretemp", "zenpower"):
                        _temp_cache["path"] = os.path.join(h, "temp1_input")
                        break
            except OSError:
                pass
    try:
        with open(_temp_cache["path"] or "") as f:
            _temp_cache["value"] = int(f.read()) / 1000
    except (OSError, ValueError):
        _temp_cache["value"] = None
    return _temp_cache["value"]


def _scale(rgb, k):
    return tuple(max(0, min(255, round(c * k))) for c in rgb)


def _hsv(h, s=1.0, v=1.0):
    return tuple(c * 255 for c in colorsys.hsv_to_rgb(h % 1.0, s, v))


def frame(cfg, t):
    """Color (r, g, b) del efecto configurado en el instante t (segundos)."""
    k = max(0, min(100, int(cfg.get("intensity", 100)))) / 100
    speed = max(1, min(10, int(cfg.get("speed", 5))))
    s = (speed - 1) / 9  # 0 (lento) … 1 (rápido)
    effect = cfg.get("effect", "static")
    color = parse_hex(cfg.get("color", "ffffff"))
    p = get_params(cfg, effect)

    if effect == "rainbow":
        period = 20 - 18 * s  # 20 s … 2 s por vuelta
        direction = -1 if p["direction"] == "rev" else 1
        return _scale(_hsv(direction * t / period, p["saturation"] / 100), k)

    if effect == "breathing":
        period = 8 - 7 * s
        x = (1 - math.cos(2 * math.pi * t / period)) / 2
        if p["shape"] == "smooth":
            x = x * x  # más natural a la vista
        elif p["shape"] == "sharp":
            x = x ** 6
        lo = p["min"] / 100
        return _scale(color, k * (lo + (1 - lo) * x))

    if effect == "alternate":
        period = 10 - 9 * s
        c2 = parse_hex(cfg.get("color2", "0033ff"))
        if p["transition"] == "cut":
            x = 0.0 if (t % period) < period / 2 else 1.0
        else:
            x = (1 - math.cos(2 * math.pi * t / period)) / 2
        # mezcla en luz lineal: evita el "bache" gris y oscuro a mitad del fundido
        mix = tuple(255 * (((a / 255) ** 2.2) * (1 - x) + ((b / 255) ** 2.2) * x) ** (1 / 2.2)
                    for a, b in zip(color, c2))
        return _scale(mix, k)

    if effect == "heartbeat":
        period = 1.8 - 1.2 * s  # ~33 … 100 pulsaciones/min
        ph = (t % period) / period
        beat = math.exp(-((ph - 0.08) / 0.07) ** 2)
        if p["beats"] == "double":
            beat += 0.6 * math.exp(-((ph - 0.32) / 0.08) ** 2)
        rest = p["rest"] / 100
        return _scale(color, k * (rest + (1 - rest) * min(beat, 1.0)))

    if effect == "candle":
        f = 1 + 4 * s
        amp = p["flicker"] / 100
        n = 0.7 * _noise(t * f) + 0.3 * _noise(t * f * 3.7 + 11)  # [-1, 1]
        return _scale(color, k * (1 - 0.8 * amp * (0.5 + 0.5 * n)))

    if effect == "aurora":
        f = 0.15 + 0.6 * s
        center, spread, sat = AURORA_PALETTES.get(p["palette"], AURORA_PALETTES["boreal"])
        hue = center + spread * _noise(t * f)
        val = 0.75 + 0.25 * _noise(t * f * 1.9 + 5)
        return _scale(_hsv(hue, sat, val), k)

    if effect == "strobe":
        flash = p["flash"] / 1000
        burst = int(p["burst"])
        gap = flash * 2.5
        period = max(1.5 - 1.25 * s, burst * gap + 0.15)  # máx. ~4 ráfagas/s
        ph = t % period
        on = any(i * gap <= ph < i * gap + flash for i in range(burst))
        return _scale(color, k if on else 0)

    if effect == "cputemp":
        temp = cpu_temp()
        if temp is None:
            return _scale((255, 255, 255), k)
        cold, hot = p["cold"], max(p["hot"], p["cold"] + 5)
        if temp >= hot and p["alarm"] == "blink":
            return _scale((255, 0, 0), k if (t % 0.8) < 0.4 else 0.1 * k)
        x = max(0.0, min(1.0, (temp - cold) / (hot - cold)))
        return _scale(_hsv(0.33 * (1 - x)), k)

    return _scale(color, k)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


def apply_config(cfg=None):
    with Keyboard() as kb:
        kb.apply(cfg or load_config())


def restore_with_retries(attempts=30, delay=1.0):
    """Al iniciar sesión o volver de suspensión el dispositivo/permiso puede tardar."""
    for i in range(attempts):
        try:
            apply_config()
            return True
        except (DeviceError, OSError) as e:
            last = e
            time.sleep(delay)
    print(f"kbd-light: no se pudo restaurar: {last}", file=sys.stderr)
    return False


class Daemon:
    """Aplica la configuración, anima los efectos y reacciona a cambios/suspensión."""

    def __init__(self):
        from gi.repository import GLib
        self.GLib = GLib
        self.kb = None
        self.cfg = load_config()
        self.t0 = time.monotonic()
        self.last = None
        self.anim = 0
        self.retry = 0
        self.retries_left = 0
        self.paused = False
        self.reload_pending = 0

    def _close(self):
        if self.kb:
            try:
                self.kb.close()
            except OSError:
                pass
            self.kb = None

    def _stop_anim(self):
        if self.anim:
            self.GLib.source_remove(self.anim)
            self.anim = 0

    def reload(self, retries=30):
        self.cfg = load_config()
        self.last = None
        self._stop_anim()
        self._close()
        if self.retry:
            self.GLib.source_remove(self.retry)
            self.retry = 0
        self.retries_left = retries
        if not self.paused:
            self._start()

    def _start(self):
        self.retry = 0
        try:
            self.kb = Keyboard()
            self.kb.apply(self.cfg, time.monotonic() - self.t0)
        except (DeviceError, OSError) as e:
            self._close()
            if self.retries_left > 0:
                self.retries_left -= 1
                self.retry = self.GLib.timeout_add_seconds(1, self._start)
            else:
                print(f"kbd-light: no se pudo aplicar: {e}", file=sys.stderr)
            return False
        if is_animated(self.cfg):
            self.anim = self.GLib.timeout_add(1000 // FPS, self._tick)
        else:
            self._close()
        return False

    def _tick(self):
        rgb = frame(self.cfg, time.monotonic() - self.t0)
        if rgb == self.last:
            return True
        try:
            self.kb.write_rgb(*rgb)
            self.last = rgb
        except OSError:
            # El dispositivo pudo reenumerarse: reintentar desde cero
            self.anim = 0
            self.reload(retries=10)
            return False
        return True

    def on_config_changed(self, _mon, file, _other, _event):
        if file.get_basename() != os.path.basename(CONFIG_FILE):
            return
        if self.reload_pending:
            self.GLib.source_remove(self.reload_pending)
        self.reload_pending = self.GLib.timeout_add(50, self._debounced_reload)

    def _debounced_reload(self):
        self.reload_pending = 0
        self.reload()
        return False

    def on_sleep(self, _conn, _sender, _path, _iface, _signal, params):
        if params.unpack()[0]:  # va a suspender
            self.paused = True
            self._stop_anim()
            self._close()
        else:  # vuelve de suspensión
            self.paused = False
            self.GLib.timeout_add_seconds(2, lambda: self.reload(retries=10) and False)

    def run(self):
        from gi.repository import Gio
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.monitor = Gio.File.new_for_path(CONFIG_DIR).monitor_directory(
            Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect("changed", self.on_config_changed)

        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        bus.signal_subscribe(
            "org.freedesktop.login1", "org.freedesktop.login1.Manager", "PrepareForSleep",
            "/org/freedesktop/login1", None, Gio.DBusSignalFlags.NONE, self.on_sleep,
        )
        self.reload()
        self.GLib.MainLoop().run()


def daemon():
    Daemon().run()


def main(argv):
    if len(argv) < 2 or argv[1] == "gui":
        import kbd_light_gui
        return kbd_light_gui.main()

    cmd = argv[1]
    try:
        if cmd == "info":
            with Keyboard() as kb:
                print(f"Dispositivo: {kb.path}\nLEDs: {kb.lamp_count}")
        elif cmd == "color":
            if len(argv) < 3:
                sys.exit(__doc__)
            parse_hex(argv[2])
            cfg = load_config()
            cfg.update(enabled=True, mode="color", color=argv[2].lstrip("#").lower())
            if len(argv) > 3:
                cfg["intensity"] = int(argv[3])
            apply_config(cfg)
            save_config(cfg)
        elif cmd == "color2":
            if len(argv) < 3:
                sys.exit(__doc__)
            parse_hex(argv[2])
            cfg = load_config()
            cfg["color2"] = argv[2].lstrip("#").lower()
            save_config(cfg)
        elif cmd == "effect":
            if len(argv) < 3 or argv[2] not in EFFECTS:
                sys.exit(__doc__)
            cfg = load_config()
            cfg.update(enabled=True, mode="color", effect=argv[2])
            if len(argv) > 3:
                cfg["speed"] = int(argv[3])
            save_config(cfg)  # el servicio en segundo plano se encarga de animarlo
            if not is_animated(cfg):
                apply_config(cfg)
        elif cmd in ("auto", "off"):
            cfg = load_config()
            if cmd == "auto":
                cfg.update(enabled=True, mode="auto")
            else:
                cfg["enabled"] = False
            apply_config(cfg)
            save_config(cfg)
        elif cmd == "restore":
            return 0 if restore_with_retries() else 1
        elif cmd == "daemon":
            daemon()
        else:
            sys.exit(__doc__)
    except (DeviceError, ValueError) as e:
        sys.exit(str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
