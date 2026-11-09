"""GTK4 / libadwaita user interface for kbd-light."""
import copy
import math
import subprocess
import sys
import time

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_foreign("cairo")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

import kbd_light as core  # noqa: E402
from i18n import N_, _, ngettext  # noqa: E402

APP_ID = "es.rvaamonde.KbdLight"
EFFECTS = list(core.EFFECTS)

PRESETS = [
    ("ffffff", N_("White")), ("ffd9a0", N_("Warm white")), ("ff0000", N_("Red")),
    ("ff6600", N_("Orange")), ("ffcc00", N_("Yellow")), ("00ff40", N_("Green")),
    ("00ffcc", N_("Turquoise")), ("00aaff", N_("Light blue")), ("0033ff", N_("Blue")),
    ("8000ff", N_("Purple")), ("ff00aa", N_("Pink")),
]

CSS = """
.preview-card {
  background: #0d0e11;
  border-radius: 20px;
  box-shadow: 0 1px 3px alpha(black, 0.3);
}
.preview-title { color: white; font-weight: 800; font-size: 1.25em; }
.preview-subtitle { color: alpha(white, 0.6); font-feature-settings: "tnum"; }
.effect-card {
  padding: 10px 4px 8px 4px;
  border-radius: 14px;
  min-width: 92px;
}
.effect-card:checked {
  background: color-mix(in srgb, var(--accent-bg-color) 18%, transparent);
  box-shadow: inset 0 0 0 2px var(--accent-bg-color);
  color: inherit;
}
.effect-card label { font-size: 0.9em; }
.swatch {
  min-width: 30px; min-height: 30px; padding: 0;
  border: 2px solid alpha(currentColor, 0.15);
}
.mode-toggle { margin: 2px 0 4px 0; }
""" + "".join(f".swatch-{h} {{ background: #{h}; }}\n" for h, _ in PRESETS)


def rgba_to_hex(rgba):
    return "".join(f"{round(c * 255):02x}" for c in (rgba.red, rgba.green, rgba.blue))


def hex_to_rgba(h):
    rgba = Gdk.RGBA()
    rgba.parse("#" + h)
    return rgba


def ensure_service():
    """The user service animates the effects: start it if it is not running."""
    subprocess.Popen(["systemctl", "--user", "start", "kbd-light.service"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def rounded_rect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


# Keyboard rows: relative width of each key
KEY_ROWS = [
    [1] * 14,
    [1.5] + [1] * 12 + [1.5],
    [1.8] + [1] * 11 + [2.2],
    [2.3] + [1] * 10 + [2.7],
    [1.2, 1.2, 1.2, 6.4, 1.2, 1.2, 1, 1, 1],
]


def draw_keyboard(cr, w, full_h, rgb, bottom=0):
    """Draw a stylized keyboard lit with the rgb color (0-255).

    `bottom` reserves room at the bottom (for the overlaid caption)."""
    h = full_h - bottom
    r, g, b = (c / 255 for c in rgb)
    lum = max(r, g, b)

    # Background glow
    cx, cy = w / 2, h / 2 + 6
    glow = cairo.RadialGradient(cx, cy, 0, cx, cy, max(w, full_h) * 0.6)
    glow.add_color_stop_rgba(0, r, g, b, 0.45 * lum)
    glow.add_color_stop_rgba(1, r, g, b, 0)
    cr.set_source(glow)
    cr.paint()

    # Chassis
    kw = min(w - 48, (h - 28) * 2.6)
    kh = kw / 2.6
    x0, y0 = (w - kw) / 2, (h - kh) / 2 + 6
    rounded_rect(cr, x0, y0, kw, kh, 14)
    cr.set_source_rgb(0.11, 0.115, 0.13)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.06)
    cr.set_line_width(1)
    cr.stroke()

    # Keys
    pad = kw * 0.035
    gap = kw * 0.008
    unit = (kw - 2 * pad - 13 * gap) / 15
    row_h = (kh - 2 * pad - 4 * gap) / 5
    base = (0.17, 0.175, 0.19)
    for i, row in enumerate(KEY_ROWS):
        scale = (kw - 2 * pad - (len(row) - 1) * gap) / (sum(row) * unit)
        x = x0 + pad
        y = y0 + pad + i * (row_h + gap)
        for kwid in row:
            wk = kwid * unit * scale
            rounded_rect(cr, x, y, wk, row_h, 4)
            # key: dark base + light leaking around the edges
            cr.set_source_rgb(*(bc + 0.25 * lc for bc, lc in zip(base, (r, g, b))))
            cr.fill_preserve()
            cr.set_source_rgba(r, g, b, 0.85 * lum)
            cr.set_line_width(1.6)
            cr.stroke()
            # legend
            if kwid < 3:
                cr.set_source_rgba(r * 0.6 + 0.4 * lum, g * 0.6 + 0.4 * lum,
                                   b * 0.6 + 0.4 * lum, 0.9 * lum)
                s = row_h * 0.16
                cr.rectangle(x + wk / 2 - s / 2, y + row_h / 2 - s / 2, s, s)
                cr.fill()
            x += wk + gap


def draw_dot(cr, w, h, rgb, dim=False):
    r, g, b = (c / 255 for c in rgb)
    cx, cy, rad = w / 2, h / 2, min(w, h) / 2 - 2
    halo = cairo.RadialGradient(cx, cy, rad * 0.3, cx, cy, rad)
    halo.add_color_stop_rgba(0, r, g, b, 0.9)
    halo.add_color_stop_rgba(1, r, g, b, 0.0)
    cr.set_source(halo)
    cr.arc(cx, cy, rad, 0, 2 * math.pi)
    cr.fill()
    cr.set_source_rgb(0.12 + 0.88 * r, 0.12 + 0.88 * g, 0.12 + 0.88 * b)
    cr.arc(cx, cy, rad * 0.55, 0, 2 * math.pi)
    cr.fill()
    cr.set_source_rgba(0.5, 0.5, 0.5, 0.35)
    cr.set_line_width(1)
    cr.arc(cx, cy, rad * 0.55, 0, 2 * math.pi)
    cr.stroke()


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=_("Keyboard Lighting"))
        self.set_default_size(520, 860)
        self.cfg = core.load_config()
        self.cfg.setdefault("params", {})
        if self.cfg.get("effect") not in EFFECTS:
            self.cfg["effect"] = "static"
        if self.cfg.get("mode") not in ("color", "auto"):
            self.cfg["mode"] = "color"
        self._loading = True
        self._pending = 0
        self.t0 = time.monotonic()
        self.param_rows = []

        # --- Layout
        toolbar = Adw.ToolbarView()
        self.win_title = Adw.WindowTitle(title=_("Keyboard Lighting"))
        header = Adw.HeaderBar(title_widget=self.win_title)
        self.power = Gtk.Switch(valign=Gtk.Align.CENTER,
                                tooltip_text=_("Turn the lighting on or off"))
        self.power.connect("notify::active", self.on_change)
        header.pack_start(self.power)
        toolbar.add_top_bar(header)

        self.banner = Adw.Banner(button_label=_("Retry"))
        self.banner.connect("button-clicked", lambda *_: self.check_device())
        toolbar.add_top_bar(self.banner)

        self.toasts = Adw.ToastOverlay()
        toolbar.set_content(self.toasts)
        self.set_content(toolbar)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.toasts.set_child(scroller)
        clamp = Adw.Clamp(maximum_size=600, tightening_threshold=480)
        scroller.set_child(clamp)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                       margin_top=18, margin_bottom=24, margin_start=16, margin_end=16)
        clamp.set_child(body)

        # --- Preview
        preview = Gtk.Overlay(css_classes=["preview-card"], overflow=Gtk.Overflow.HIDDEN)
        self.preview = Gtk.DrawingArea(content_height=270, hexpand=True)
        self.preview.set_draw_func(self.draw_preview)
        self.preview.add_tick_callback(lambda w, _c: w.queue_draw() or True)
        preview.set_child(self.preview)
        caption = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                          halign=Gtk.Align.START, valign=Gtk.Align.END,
                          margin_start=18, margin_bottom=12)
        self.preview_title = Gtk.Label(xalign=0, css_classes=["preview-title"])
        self.preview_subtitle = Gtk.Label(xalign=0, css_classes=["preview-subtitle"])
        caption.append(self.preview_title)
        caption.append(self.preview_subtitle)
        preview.add_overlay(caption)
        body.append(preview)

        # --- Mode
        self.mode = Adw.ToggleGroup(css_classes=["mode-toggle", "round"],
                                    homogeneous=True)
        self.mode.add(Adw.Toggle(name="color", label=_("Custom"),
                                 icon_name="applications-graphics-symbolic"))
        self.mode.add(Adw.Toggle(name="auto", label=_("Laptop (Fn ↑/↓)"),
                                 icon_name="computer-symbolic"))
        self.mode.connect("notify::active-name", self.on_change)
        body.append(self.mode)

        # --- Effects
        self.effects_group = Adw.PreferencesGroup(title=_("Effect"))
        body.append(self.effects_group)
        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                           min_children_per_line=3, max_children_per_line=3,
                           column_spacing=10, row_spacing=10)
        self.effect_buttons = {}
        first = None
        for e in EFFECTS:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            dot = Gtk.DrawingArea(content_width=44, content_height=44,
                                  halign=Gtk.Align.CENTER)
            dot.set_draw_func(self.draw_effect_dot, e)
            dot.add_tick_callback(lambda w, _c: w.queue_draw() or True)
            box.append(dot)
            box.append(Gtk.Label(label=_(core.EFFECT_INFO[e][0]), wrap=True,
                                 justify=Gtk.Justification.CENTER, max_width_chars=12))
            btn = Gtk.ToggleButton(child=box, css_classes=["flat", "effect-card"])
            if first:
                btn.set_group(first)
            else:
                first = btn
            btn.connect("toggled", self.on_effect_toggled, e)
            self.effect_buttons[e] = btn
            grid.append(btn)
        self.effects_group.add(grid)

        # --- Color
        self.color_group = Adw.PreferencesGroup(title=_("Color"))
        body.append(self.color_group)

        self.color_row = Adw.ActionRow(title=_("Main color"))
        self.color_btn = Gtk.ColorDialogButton(
            dialog=Gtk.ColorDialog(with_alpha=False, title=_("Choose a color")),
            valign=Gtk.Align.CENTER)
        self.color_btn.connect("notify::rgba", self.on_change)
        self.color_row.add_suffix(self.color_btn)
        self.color_row.set_activatable_widget(self.color_btn)
        self.color_group.add(self.color_row)

        self.color2_row = Adw.ActionRow(title=_("Second color"))
        self.color2_btn = Gtk.ColorDialogButton(
            dialog=Gtk.ColorDialog(with_alpha=False, title=_("Choose the second color")),
            valign=Gtk.Align.CENTER)
        self.color2_btn.connect("notify::rgba", self.on_change)
        self.color2_row.add_suffix(self.color2_btn)
        self.color2_row.set_activatable_widget(self.color2_btn)
        self.color_group.add(self.color2_row)

        presets = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                              max_children_per_line=11, min_children_per_line=6,
                              column_spacing=8, row_spacing=8, margin_top=12)
        for h, name in PRESETS:
            b = Gtk.Button(tooltip_text=_(name), halign=Gtk.Align.CENTER,
                           css_classes=["circular", "swatch", f"swatch-{h}"])
            b.connect("clicked", self.on_preset, h)
            presets.append(b)
        self.color_group.add(presets)

        # --- Settings
        self.settings_group = Adw.PreferencesGroup()
        body.append(self.settings_group)

        self.intensity = self.make_scale(1, 100, 1, lambda v: f"{int(v)} %")
        row = Adw.ActionRow(title=_("Brightness"), icon_name="display-brightness-symbolic")
        row.add_suffix(self.intensity)
        self.settings_group.add(row)

        self.speed = self.make_scale(1, 10, 1, lambda v: f"{int(v)}")
        self.speed_row = Adw.ActionRow(title=_("Speed"),
                                       icon_name="media-seek-forward-symbolic")
        self.speed_row.add_suffix(self.speed)
        self.settings_group.add(self.speed_row)

        # --- Initial state from the saved settings
        self.power.set_active(self.cfg.get("enabled", True))
        self.mode.set_active_name(self.cfg["mode"])
        self.effect_buttons[self.cfg["effect"]].set_active(True)
        self.color_btn.set_rgba(hex_to_rgba(self.cfg.get("color", "ffffff")))
        self.color2_btn.set_rgba(hex_to_rgba(self.cfg.get("color2", "0033ff")))
        self.intensity.set_value(self.cfg.get("intensity", 100))
        self.speed.set_value(self.cfg.get("speed", 5))
        self.rebuild_params()
        self._loading = False
        self.update_ui()
        self.check_device()
        ensure_service()
        GLib.timeout_add_seconds(2, lambda: self.update_ui() or True)  # e.g. CPU temperature

    # ---------- building helpers
    def make_scale(self, lo, hi, step, fmt, callback=None):
        s = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        s.set_size_request(210, -1)
        s.set_digits(0)
        s.set_draw_value(True)
        s.set_value_pos(Gtk.PositionType.RIGHT)
        s.set_format_value_func(lambda _s, v: fmt(v))
        s.connect("value-changed", callback or self.on_change)
        return s

    def rebuild_params(self):
        for r in self.param_rows:
            self.settings_group.remove(r)
        self.param_rows = []
        effect = self.cfg["effect"]
        values = core.get_params(self.cfg, effect)
        for spec in core.EFFECT_PARAMS.get(effect, []):
            key = spec["key"]
            if spec["kind"] == "range":
                unit = spec["unit"]
                scale = self.make_scale(
                    spec["lo"], spec["hi"], spec["step"], lambda v, u=unit: f"{int(v)}{u}",
                    lambda s, k=key: self.set_param(k, int(s.get_value())))
                self._loading, was = True, self._loading
                scale.set_value(values[key])
                self._loading = was
                row = Adw.ActionRow(title=_(spec["label"]))
                row.add_suffix(scale)
            else:
                ids = [o[0] for o in spec["options"]]
                row = Adw.ComboRow(
                    title=_(spec["label"]),
                    model=Gtk.StringList.new([_(o[1]) for o in spec["options"]]))
                row.set_selected(ids.index(values[key]) if values[key] in ids else 0)
                row.connect("notify::selected",
                            lambda r, _p, k=key, ids=ids: self.set_param(k, ids[r.get_selected()]))
            self.settings_group.add(row)
            self.param_rows.append(row)

    # ---------- drawing
    def current_rgb(self, cfg, effect=None):
        if effect:
            cfg = dict(cfg, effect=effect, enabled=True, mode="color")
        if not cfg.get("enabled", True):
            return (0, 0, 0)
        if cfg.get("mode") == "auto":
            return (235, 235, 235)
        return core.frame(cfg, time.monotonic() - self.t0)

    def draw_preview(self, _area, cr, w, h):
        draw_keyboard(cr, w, h, self.current_rgb(self.cfg), bottom=62)

    def draw_effect_dot(self, _area, cr, w, h, effect):
        cfg = self.cfg
        if not cfg.get("enabled", True) or cfg.get("mode") == "auto":
            draw_dot(cr, w, h, (90, 90, 90))
        else:
            draw_dot(cr, w, h, self.current_rgb(cfg, effect))

    # ---------- state
    def update_ui(self):
        on = self.power.get_active()
        custom = on and self.cfg["mode"] == "color"
        effect = self.cfg["effect"]
        label, uses_color, uses_color2, uses_speed = core.EFFECT_INFO[effect]
        name = _(label)

        self.mode.set_sensitive(on)
        for g in (self.effects_group, self.color_group, self.settings_group):
            g.set_sensitive(custom)
        self.color_group.set_visible(uses_color)
        self.color2_row.set_visible(uses_color2)
        self.speed_row.set_visible(uses_speed)
        self.settings_group.set_title(_("Settings · {effect}").format(effect=name))

        if not on:
            title, sub = _("Off"), _("The keyboard lighting is turned off")
        elif self.cfg["mode"] == "auto":
            title, sub = _("Laptop"), _("Brightness is controlled with Fn + ↑ / ↓")
        else:
            title = name
            if effect == "cputemp":
                temp = core.cpu_temp()
                sub = (_("CPU at {temp:.0f} °C").format(temp=temp) if temp is not None
                       else _("Sensor not available"))
            elif uses_color:
                sub = "#" + self.cfg.get("color", "ffffff").upper()
                if uses_color2:
                    sub += "  →  #" + self.cfg.get("color2", "0033ff").upper()
            else:
                sub = _("Speed {speed}").format(speed=self.cfg.get("speed", 5))
            sub += f"  ·  {self.cfg.get('intensity', 100)} %"
        self.preview_title.set_label(title)
        self.preview_subtitle.set_label(sub)

    def sync_cfg(self):
        self.cfg.update(
            enabled=self.power.get_active(),
            mode=self.mode.get_active_name() or "color",
            color=rgba_to_hex(self.color_btn.get_rgba()),
            color2=rgba_to_hex(self.color2_btn.get_rgba()),
            intensity=int(self.intensity.get_value()),
            speed=int(self.speed.get_value()),
        )

    def check_device(self):
        devices = core.find_devices()
        names = ", ".join(d["name"] for d in devices)
        if len(devices) > 1:
            names = ngettext("{n} device: {names}", "{n} devices: {names}",
                             len(devices)).format(n=len(devices), names=names)
        self.win_title.set_subtitle(names)
        self.win_title.set_tooltip_text(
            "\n".join(f"{d['path']} · {d['name']}" for d in devices) or None)
        try:
            core.close_all(core.open_devices())
        except core.PermissionDenied:
            self.banner.set_title(_("No permission to access the device: the udev rule is not installed"))
            self.banner.set_revealed(True)
            return False
        except core.DeviceError as e:
            self.banner.set_title(str(e))
            self.banner.set_revealed(True)
            return False
        self.banner.set_revealed(False)
        return True

    # ---------- events
    def on_preset(self, _btn, h):
        self.color_btn.set_rgba(hex_to_rgba(h))

    def on_effect_toggled(self, btn, effect):
        if not btn.get_active() or self._loading:
            return
        self.cfg["effect"] = effect
        self.rebuild_params()
        self.on_change()

    def set_param(self, key, value):
        if self._loading:
            return
        self.cfg["params"].setdefault(self.cfg["effect"], {})[key] = value
        self.on_change()

    def on_change(self, *_):
        if self._loading:
            return
        self.sync_cfg()
        self.update_ui()
        if self._pending:
            GLib.source_remove(self._pending)
        self._pending = GLib.timeout_add(80, self.apply)

    def apply(self):
        self._pending = 0
        cfg = copy.deepcopy(self.cfg)
        core.save_config(cfg)
        if core.is_animated(cfg):
            ensure_service()  # the service picks up the change and animates
            return False
        try:
            core.apply_config(cfg)
            self.banner.set_revealed(False)
        except core.DeviceError:
            self.check_device()
        except OSError as e:
            self.toasts.add_toast(Adw.Toast(title=_("Could not apply: {error}").format(error=e.strerror)))
        return False


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_startup(self):
        Adw.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        win = self.get_active_window() or Window(self)
        win.present()


def main():
    return App().run(sys.argv[:1])
