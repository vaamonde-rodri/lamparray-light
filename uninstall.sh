#!/usr/bin/env bash
# Desinstala asus-kbd-light. La configuración (~/.config/kbd-light) se conserva
# salvo que se pase --purge.
set -euo pipefail

systemctl --user disable --now kbd-light.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/kbd-light.service"
systemctl --user daemon-reload

rm -rf "$HOME/.local/share/kbd-light"
rm -f "$HOME/.local/bin/kbd-light"
rm -f "$HOME/.local/share/applications/es.rvaamonde.KbdLight.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true

if [[ "${1:-}" == "--purge" ]]; then
    rm -rf "$HOME/.config/kbd-light"
fi

if [[ -e /etc/udev/rules.d/70-kbd-light.rules ]]; then
    echo "==> Eliminando la regla udev (requiere sudo)"
    sudo rm -f /etc/udev/rules.d/70-kbd-light.rules
    sudo udevadm control --reload
fi

echo "==> asus-kbd-light desinstalado"
