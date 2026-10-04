#!/usr/bin/env bash
# Removes OP Auto Clicker (Wayland Edition): udev rule, module autoload,
# desktop entry. Your copy of op_autoclicker.py is just a file - delete the
# folder yourself. Group membership is left alone (harmless); remove with:
#   sudo gpasswd -d "$USER" input
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

echo "== OP Auto Clicker: uninstall =="
$SUDO rm -f /etc/udev/rules.d/99-op-autoclicker.rules
$SUDO rm -f /etc/modules-load.d/op-autoclicker-uinput.conf
rm -f "$HOME/.local/share/applications/op-autoclicker.desktop"
rm -rf "$HOME/.config/op-autoclicker" || true
$SUDO udevadm control --reload-rules || true
echo "Removed. (Optional: sudo gpasswd -d \$USER input)"
