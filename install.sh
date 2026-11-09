#!/usr/bin/env bash
# Instala asus-kbd-light para el usuario actual.
# La regla udev (permiso de acceso al teclado) requiere sudo y se instala aparte:
#   ./install.sh            -> instala la app para el usuario y, si hace falta, pide sudo para la regla
#   ./install.sh --no-udev  -> solo la parte de usuario
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPDIR="$HOME/.local/share/kbd-light"
BINDIR="$HOME/.local/bin"
DESKTOPDIR="$HOME/.local/share/applications"
UNITDIR="$HOME/.config/systemd/user"
RULE=/etc/udev/rules.d/70-kbd-light.rules
DESKTOP_ID=es.rvaamonde.KbdLight.desktop

echo "==> Instalando la aplicación en $APPDIR"
mkdir -p "$APPDIR" "$BINDIR" "$DESKTOPDIR" "$UNITDIR"
install -m 755 "$SRC/src/kbd_light.py" "$APPDIR/kbd_light.py"
install -m 644 "$SRC/src/kbd_light_gui.py" "$APPDIR/kbd_light_gui.py"
install -m 644 "$SRC/data/70-kbd-light.rules" "$APPDIR/70-kbd-light.rules"
rm -rf "$APPDIR/__pycache__"

cat > "$BINDIR/kbd-light" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 "$HOME/.local/share/kbd-light/kbd_light.py" "$@"
EOF
chmod 755 "$BINDIR/kbd-light"

sed "s|@BINDIR@|$BINDIR|" "$SRC/data/$DESKTOP_ID.in" > "$DESKTOPDIR/$DESKTOP_ID"
update-desktop-database "$DESKTOPDIR" 2>/dev/null || true

echo "==> Activando el servicio de usuario (efectos y restauración al iniciar/suspender)"
install -m 644 "$SRC/data/kbd-light.service" "$UNITDIR/kbd-light.service"
systemctl --user daemon-reload
systemctl --user enable kbd-light.service >/dev/null
systemctl --user restart kbd-light.service

if [[ "${1:-}" != "--no-udev" ]]; then
    if cmp -s "$SRC/data/70-kbd-light.rules" "$RULE" 2>/dev/null; then
        echo "==> Regla udev ya instalada"
    else
        echo "==> Instalando la regla udev (requiere sudo)"
        sudo install -m 644 "$SRC/data/70-kbd-light.rules" "$RULE"
        sudo udevadm control --reload
        sudo udevadm trigger --action=change --subsystem-match=hidraw
    fi
fi

echo "==> Listo. Abre «Iluminación del teclado» desde el menú o ejecuta: kbd-light"
