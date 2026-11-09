#!/usr/bin/env python3
"""kbd-light: lighting control for HID LampArray keyboards and devices
(Windows "Dynamic Lighting") on Linux.

This module holds the LampArray protocol, the effects, the command line
interface and the background service. The GTK user interface lives in
kbd_light_gui.py.
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

from i18n import N_, _

USAGE = N_("""Usage:
  kbd-light                       open the application
  kbd-light info                  list the detected LampArray devices
  kbd-light color RRGGBB [0-100]  static color and brightness (%)
  kbd-light effect NAME [1-10]    effect and speed. Effects: static, breathing,
                     rainbow, alternate, heartbeat, candle, aurora, strobe, cputemp
  kbd-light color2 RRGGBB         second color (for "alternate")
  kbd-light auto                  hand control back to the firmware (Fn + ↑/↓)
  kbd-light off                   turn the lighting off
  kbd-light restore               apply the last saved settings
  kbd-light daemon                run the background service (effects, login, resume)
""")

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "kbd-light"
)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
DEFAULT_CONFIG = {
    "enabled": True, "mode": "color", "color": "ffffff", "color2": "0033ff",
    "intensity": 100, "effect": "static", "speed": 5,
}
FPS = 30

# effect: (label, uses color, uses second color, uses speed)
EFFECT_INFO = {
    "static":    (N_("Static"),               True,  False, False),
    "breathing": (N_("Breathing"),            True,  False, True),
    "rainbow":   (N_("Rainbow"),              False, False, True),
    "alternate": (N_("Alternate two colors"), True,  True,  True),
    "heartbeat": (N_("Heartbeat"),            True,  False, True),
    "candle":    (N_("Candle"),               True,  False, True),
    "aurora":    (N_("Aurora"),               False, False, True),
    "strobe":    (N_("Strobe"),               True,  False, True),
    "cputemp":   (N_("CPU temperature"),      False, False, False),
}
EFFECTS = tuple(EFFECT_INFO)


def _range(key, label, lo, hi, default, unit="", step=1):
    return {"key": key, "kind": "range", "label": label, "lo": lo, "hi": hi,
            "default": default, "unit": unit, "step": step}


def _choice(key, label, options, default):
    return {"key": key, "kind": "choice", "label": label, "options": options,
            "default": default}


AURORA_PALETTES = {  # hue center, hue spread, saturation
    "boreal": (0.42, 0.17, 0.90),
    "sunset": (0.97, 0.09, 0.90),
    "ocean": (0.58, 0.08, 0.90),
    "forest": (0.30, 0.07, 0.85),
    "lava": (0.03, 0.04, 1.00),
}

# Per-effect options (labels are translated by the UI)
EFFECT_PARAMS = {
    "breathing": [
        _range("min", N_("Minimum brightness"), 0, 80, 0, " %", 5),
        _choice("shape", N_("Curve"), [("smooth", N_("Smooth")), ("sine", N_("Sine")),
                                       ("sharp", N_("Short and sharp"))], "smooth"),
    ],
    "rainbow": [
        _range("saturation", N_("Saturation"), 20, 100, 100, " %", 5),
        _choice("direction", N_("Direction"), [("fwd", N_("Red → green → blue")),
                                               ("rev", N_("Red → blue → green"))], "fwd"),
    ],
    "alternate": [
        _choice("transition", N_("Transition"), [("smooth", N_("Fade")), ("cut", N_("Cut"))],
                "smooth"),
    ],
    "heartbeat": [
        _range("rest", N_("Resting brightness"), 0, 60, 8, " %", 2),
        _choice("beats", N_("Beats"), [("double", N_("Double (lub-dub)")),
                                       ("single", N_("Single"))], "double"),
    ],
    "candle": [
        _range("flicker", N_("Flicker strength"), 10, 100, 50, " %", 5),
    ],
    "aurora": [
        _choice("palette", N_("Palette"), [("boreal", N_("Boreal")), ("sunset", N_("Sunset")),
                                           ("ocean", N_("Ocean")), ("forest", N_("Forest")),
                                           ("lava", N_("Lava"))], "boreal"),
    ],
    "strobe": [
        _range("flash", N_("Flash duration"), 30, 300, 70, " ms", 10),
        _range("burst", N_("Flashes per burst"), 1, 4, 1),
    ],
    "cputemp": [
        _range("cold", N_("“Cold” temperature (green)"), 30, 70, 40, " °C"),
        _range("hot", N_("“Hot” temperature (red)"), 60, 100, 90, " °C"),
        _choice("alarm", N_("Above the hot temperature"), [("none", N_("Nothing")),
                                                           ("blink", N_("Blink red"))], "blink"),
    ],
}


def get_params(cfg, effect):
    """Option values of an effect (defaults updated with the saved ones)."""
    values = {p["key"]: p["default"] for p in EFFECT_PARAMS.get(effect, [])}
    values.update(cfg.get("params", {}).get(effect, {}))
    return values


# --- Errors ----------------------------------------------------------------------
def permission_help():
    return _(
        "No permission to access the lighting device. Install the udev rule:\n"
        "  sudo install -m 644 ~/.local/share/kbd-light/70-kbd-light.rules /etc/udev/rules.d/\n"
        "  sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw"
    )


class DeviceError(Exception):
    pass


class PermissionDenied(DeviceError):
    pass


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


# --- HID report descriptor -------------------------------------------------------
# "Lighting and Illumination" usage page (0x59) and its LampArray collections
LIGHTING_PAGE = 0x59
LAMPARRAY_USAGE = (LIGHTING_PAGE << 16) | 0x01
LAMP_REPORTS = {
    0x02: "attributes",      # LampArrayAttributesReport
    0x20: "attr_request",    # LampAttributesRequestReport
    0x22: "attr_response",   # LampAttributesResponseReport
    0x50: "multi_update",    # LampMultiUpdateReport
    0x60: "range_update",    # LampRangeUpdateReport
    0x70: "control",         # LampArrayControlReport
}
REQUIRED_REPORTS = ("attributes", "range_update", "control")


def parse_lamparray(desc):
    """Parse a HID report descriptor.

    Return {name: (report_id, length_in_bytes)} for the reports of the first
    LampArray collection, or None if the device is not a usable LampArray.
    The length includes the report ID byte.
    """
    page = report_id = rsize = rcount = 0
    gstack, cstack, usages = [], [], []
    in_lamparray = None
    reports, bits = {}, {}
    i = 0
    while i < len(desc):
        b = desc[i]
        if b == 0xFE:  # long item: unused in practice, skip it
            i += 3 + (desc[i + 1] if i + 1 < len(desc) else 0)
            continue
        size = (0, 1, 2, 4)[b & 3]
        kind, tag = (b >> 2) & 3, b >> 4
        data = int.from_bytes(desc[i + 1:i + 1 + size], "little")
        i += 1 + size

        if kind == 1:  # global item
            if tag == 0:
                page = data
            elif tag == 7:
                rsize = data
            elif tag == 8:
                report_id = data
            elif tag == 9:
                rcount = data
            elif tag == 10:
                gstack.append((page, report_id, rsize, rcount))
            elif tag == 11 and gstack:
                page, report_id, rsize, rcount = gstack.pop()
        elif kind == 2:  # local item
            if tag == 0:
                usages.append(data if size == 4 else (page << 16) | data)
        elif kind == 0:  # main item
            if tag == 0xA:  # Collection
                usage = usages[0] if usages else 0
                name = None
                if in_lamparray is None and usage == LAMPARRAY_USAGE and data == 1:
                    in_lamparray = len(cstack) + 1
                elif in_lamparray is not None and usage >> 16 == LIGHTING_PAGE:
                    name = LAMP_REPORTS.get(usage & 0xFFFF)
                cstack.append(name)
            elif tag == 0xC:  # End Collection
                if in_lamparray is not None and len(cstack) == in_lamparray:
                    break  # end of the LampArray collection
                if cstack:
                    cstack.pop()
            elif tag == 0xB and in_lamparray is not None:  # Feature
                name = next((n for n in reversed(cstack) if n), None)
                if name:
                    reports.setdefault(name, report_id)
                    if reports[name] == report_id:
                        bits[name] = bits.get(name, 0) + rsize * rcount
            usages = []

    if not all(r in reports for r in REQUIRED_REPORTS):
        return None
    return {n: (rid, (1 if rid else 0) + (bits.get(n, 0) + 7) // 8)
            for n, rid in reports.items()}


def find_devices():
    """List the hidraw devices that are LampArrays: [{path, name, id, reports}]."""
    found = []
    for d in sorted(glob.glob("/sys/class/hidraw/hidraw*"),
                    key=lambda p: int(p.rsplit("hidraw", 1)[1])):
        try:
            with open(os.path.join(d, "device/report_descriptor"), "rb") as f:
                reports = parse_lamparray(f.read())
            if not reports:
                continue
            info = {}
            with open(os.path.join(d, "device/uevent")) as f:
                for line in f:
                    k, _sep, v = line.strip().partition("=")
                    info[k] = v
        except OSError:
            continue
        found.append({
            "path": "/dev/" + os.path.basename(d),
            "name": info.get("HID_NAME", os.path.basename(d)),
            "id": info.get("HID_ID", ""),
            "reports": reports,
        })
    return found


# --- LampArray device ------------------------------------------------------------
class LampArray:
    def __init__(self, dev):
        self.path, self.name, self.reports = dev["path"], dev["name"], dev["reports"]
        try:
            self.fd = os.open(self.path, os.O_RDWR)
        except PermissionError as e:
            raise PermissionDenied(permission_help()) from e
        self._attrs = None

    def close(self):
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _get_feature(self, name):
        rid, length = self.reports[name]
        buf = bytearray(max(length, 2))
        buf[0] = rid
        fcntl.ioctl(self.fd, _ioc(3, 0x07, len(buf)), buf)
        return bytes(buf)

    def _set_feature(self, name, payload):
        rid, length = self.reports[name]
        buf = bytearray([rid]) + payload
        if len(buf) < length:
            buf += bytes(length - len(buf))
        fcntl.ioctl(self.fd, _ioc(3, 0x06, len(buf)), buf)

    @property
    def attributes(self):
        """LampArrayAttributes: LampCount(u16), width, height, depth (µm), kind, interval (µs)."""
        if self._attrs is None:
            data = self._get_feature("attributes")
            off = 1 if self.reports["attributes"][0] else 0
            values = struct.unpack_from("<HIIIII", data.ljust(off + 22, b"\0"), off)
            self._attrs = dict(zip(("lamp_count", "width", "height", "depth", "kind",
                                    "min_interval_us"), values))
        return self._attrs

    @property
    def lamp_count(self):
        return self.attributes["lamp_count"]

    def set_autonomous(self, on):
        # LampArrayControl: AutonomousMode(u8)
        self._set_feature("control", bytes([1 if on else 0]))

    def write_rgb(self, r, g, b):
        """Set every lamp to one color (autonomous mode must be off)."""
        # LampRangeUpdate: flags(u8) start(u16) end(u16) R G B I
        end = max(self.lamp_count - 1, 0)
        self._set_feature("range_update", struct.pack("<BHHBBBB", 0x01, 0, end, r, g, b, 255))

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


def open_devices():
    """Open every accessible LampArray.

    Raise DeviceError if there is none, and PermissionDenied if there are some
    but none of them can be opened."""
    devs = find_devices()
    if not devs:
        raise DeviceError(_("No LampArray lighting device found."))
    opened, denied = [], None
    for d in devs:
        try:
            opened.append(LampArray(d))
        except PermissionDenied as e:
            denied = e
        except OSError:
            pass
    if not opened:
        raise denied or DeviceError(_("Could not open any LampArray device."))
    return opened


def close_all(devices):
    for d in devices:
        try:
            d.close()
        except OSError:
            pass


# --- Effects ---------------------------------------------------------------------
def parse_hex(h):
    h = h.strip().lstrip("#")
    if len(h) != 6:
        raise ValueError(_("Invalid color: {color!r} (use RRGGBB)").format(color=h))
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def is_animated(cfg):
    return (cfg.get("enabled", True) and cfg.get("mode") != "auto"
            and cfg.get("effect", "static") in EFFECTS[1:])


def _noise(t):
    """Smooth deterministic noise in [-1, 1] (sum of incommensurable sines)."""
    return (math.sin(t) + 0.6 * math.sin(2.17 * t + 1.3)
            + 0.3 * math.sin(4.73 * t + 0.7)) / 1.9


_temp_cache = {"t": -1.0, "value": None, "path": None}


def cpu_temp():
    """CPU temperature in °C (k10temp/coretemp/zenpower), read at most once per second."""
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
    """Color (r, g, b) of the configured effect at time t (seconds)."""
    k = max(0, min(100, int(cfg.get("intensity", 100)))) / 100
    speed = max(1, min(10, int(cfg.get("speed", 5))))
    s = (speed - 1) / 9  # 0 (slow) … 1 (fast)
    effect = cfg.get("effect", "static")
    color = parse_hex(cfg.get("color", "ffffff"))
    p = get_params(cfg, effect)

    if effect == "rainbow":
        period = 20 - 18 * s  # 20 s … 2 s per cycle
        direction = -1 if p["direction"] == "rev" else 1
        return _scale(_hsv(direction * t / period, p["saturation"] / 100), k)

    if effect == "breathing":
        period = 8 - 7 * s
        x = (1 - math.cos(2 * math.pi * t / period)) / 2
        if p["shape"] == "smooth":
            x = x * x  # looks more natural
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
        # blend in linear light: avoids a dull grey dip halfway through the fade
        mix = tuple(255 * (((a / 255) ** 2.2) * (1 - x) + ((b / 255) ** 2.2) * x) ** (1 / 2.2)
                    for a, b in zip(color, c2))
        return _scale(mix, k)

    if effect == "heartbeat":
        period = 1.8 - 1.2 * s  # ~33 … 100 beats per minute
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
        period = max(1.5 - 1.25 * s, burst * gap + 0.15)  # at most ~4 bursts per second
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


# --- Settings --------------------------------------------------------------------
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
    """Apply the settings to every accessible LampArray device."""
    cfg = cfg or load_config()
    devices = open_devices()
    try:
        _apply_all(devices, cfg)
    finally:
        close_all(devices)


def _apply_all(devices, cfg, t=0.0):
    """Apply to each device and return those that responded. Fails only if all fail."""
    ok, error = [], None
    for d in devices:
        try:
            d.apply(cfg, t)
            ok.append(d)
        except OSError as e:
            error = e
    if not ok and error:
        raise error
    return ok


def restore_with_retries(attempts=30, delay=1.0):
    """After login or resume, the device or its permission may take a moment."""
    last = None
    for _i in range(attempts):
        try:
            apply_config()
            return True
        except (DeviceError, OSError) as e:
            last = e
            time.sleep(delay)
    print("kbd-light: " + _("could not restore the settings: {error}").format(error=last),
          file=sys.stderr)
    return False


# --- Background service ----------------------------------------------------------
class Daemon:
    """Applies the settings, animates effects and reacts to changes and suspend."""

    def __init__(self):
        from gi.repository import GLib
        self.GLib = GLib
        self.kbs = []
        self.cfg = load_config()
        self.t0 = time.monotonic()
        self.last = None
        self.anim = 0
        self.retry = 0
        self.retries_left = 0
        self.paused = False
        self.reload_pending = 0

    def _close(self):
        close_all(self.kbs)
        self.kbs = []

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
            self.kbs = open_devices()
            working = _apply_all(self.kbs, self.cfg, time.monotonic() - self.t0)
            close_all(d for d in self.kbs if d not in working)
            self.kbs = working
        except (DeviceError, OSError) as e:
            self._close()
            if self.retries_left > 0:
                self.retries_left -= 1
                self.retry = self.GLib.timeout_add_seconds(1, self._start)
            else:
                print("kbd-light: " + _("could not apply the settings: {error}").format(error=e),
                      file=sys.stderr)
            return False
        if is_animated(self.cfg):
            # honor the minimum update interval of the slowest device
            min_ms = max(d.attributes["min_interval_us"] for d in self.kbs) / 1000
            self.anim = self.GLib.timeout_add(max(1000 // FPS, math.ceil(min_ms)), self._tick)
        else:
            self._close()
        return False

    def _tick(self):
        rgb = frame(self.cfg, time.monotonic() - self.t0)
        if rgb == self.last:
            return True
        try:
            for kb in self.kbs:
                kb.write_rgb(*rgb)
            self.last = rgb
        except OSError:
            # the device may have been re-enumerated: start over
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
        if params.unpack()[0]:  # about to suspend
            self.paused = True
            self._stop_anim()
            self._close()
        else:  # resumed
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


# --- Command line ----------------------------------------------------------------
def print_info():
    devs = find_devices()
    if not devs:
        raise DeviceError(_("No LampArray lighting device found."))
    for dev in devs:
        print(f"{dev['path']}  {dev['name']}  [{dev['id']}]")
        print("  " + _("reports:") + " " + ", ".join(
            f"{n}=0x{rid:02x}" for n, (rid, _len) in sorted(dev["reports"].items())))
        try:
            with LampArray(dev) as la:
                a = la.attributes
                print("  " + _("LEDs: {count}  size: {width:.0f}×{height:.0f} mm  "
                               "min. interval: {interval:g} ms").format(
                    count=a["lamp_count"], width=a["width"] / 1000,
                    height=a["height"] / 1000, interval=a["min_interval_us"] / 1000))
        except PermissionDenied:
            print("  " + _("(no permission: install the udev rule)"))
        except OSError as e:
            print("  " + _("(could not read the attributes: {error})").format(error=e.strerror))


def main(argv):
    if len(argv) < 2 or argv[1] == "gui":
        import kbd_light_gui
        return kbd_light_gui.main()

    cmd = argv[1]
    try:
        if cmd == "info":
            print_info()
        elif cmd == "color":
            if len(argv) < 3:
                sys.exit(_(USAGE))
            parse_hex(argv[2])
            cfg = load_config()
            cfg.update(enabled=True, mode="color", color=argv[2].lstrip("#").lower())
            if len(argv) > 3:
                cfg["intensity"] = int(argv[3])
            apply_config(cfg)
            save_config(cfg)
        elif cmd == "color2":
            if len(argv) < 3:
                sys.exit(_(USAGE))
            parse_hex(argv[2])
            cfg = load_config()
            cfg["color2"] = argv[2].lstrip("#").lower()
            save_config(cfg)
        elif cmd == "effect":
            if len(argv) < 3 or argv[2] not in EFFECTS:
                sys.exit(_(USAGE))
            cfg = load_config()
            cfg.update(enabled=True, mode="color", effect=argv[2])
            if len(argv) > 3:
                cfg["speed"] = int(argv[3])
            save_config(cfg)  # the background service picks it up and animates it
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
            sys.exit(_(USAGE))
    except (DeviceError, ValueError) as e:
        sys.exit(str(e))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
