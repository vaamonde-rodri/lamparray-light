#!/usr/bin/env bash
# Install kbd-light for the current user.
#   ./install.sh            install the app for the user and, if needed, the udev rule (sudo)
#   ./install.sh --no-udev  user part only
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPDIR="$HOME/.local/share/kbd-light"
BINDIR="$HOME/.local/bin"
DESKTOPDIR="$HOME/.local/share/applications"
UNITDIR="$HOME/.config/systemd/user"
RULE=/etc/udev/rules.d/70-kbd-light.rules
DESKTOP_ID=es.rvaamonde.KbdLight.desktop

echo "==> Installing the application into $APPDIR"
mkdir -p "$APPDIR" "$BINDIR" "$DESKTOPDIR" "$UNITDIR"
install -m 755 "$SRC/src/kbd_light.py" "$APPDIR/kbd_light.py"
install -m 644 "$SRC/src/kbd_light_gui.py" "$SRC/src/i18n.py" "$APPDIR/"
install -m 644 "$SRC/data/70-kbd-light.rules" "$APPDIR/70-kbd-light.rules"
rm -rf "$APPDIR/__pycache__" "$APPDIR/locale"

echo "==> Compiling translations"
if [[ ! -f "$SRC/po/LINGUAS" ]]; then
    echo "    no translations available"
elif command -v msgfmt >/dev/null; then
    for lang in $(cat "$SRC/po/LINGUAS"); do
        mkdir -p "$APPDIR/locale/$lang/LC_MESSAGES"
        msgfmt -o "$APPDIR/locale/$lang/LC_MESSAGES/kbd-light.mo" "$SRC/po/$lang.po"
    done
else
    echo "    msgfmt not found (package 'gettext'): the interface will be in English"
fi

cat > "$BINDIR/kbd-light" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 "$HOME/.local/share/kbd-light/kbd_light.py" "$@"
EOF
chmod 755 "$BINDIR/kbd-light"

sed "s|@BINDIR@|$BINDIR|" "$SRC/data/$DESKTOP_ID.in" > "$DESKTOPDIR/$DESKTOP_ID"
update-desktop-database "$DESKTOPDIR" 2>/dev/null || true

echo "==> Enabling the user service (effects, restore on login and resume)"
install -m 644 "$SRC/data/kbd-light.service" "$UNITDIR/kbd-light.service"
systemctl --user daemon-reload
systemctl --user enable kbd-light.service >/dev/null
systemctl --user restart kbd-light.service

if [[ "${1:-}" != "--no-udev" ]]; then
    if cmp -s "$SRC/data/70-kbd-light.rules" "$RULE" 2>/dev/null; then
        echo "==> udev rule already installed"
    else
        echo "==> Installing the udev rule (requires sudo)"
        sudo install -m 644 "$SRC/data/70-kbd-light.rules" "$RULE"
        sudo udevadm control --reload
        sudo udevadm trigger --action=change --subsystem-match=hidraw
    fi
fi

echo "==> Done. Open \"Keyboard Lighting\" from the app menu or run: kbd-light"
