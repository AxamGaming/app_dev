#!/usr/bin/env bash
# Installer for OP Auto Clicker 4.1 - Wayland Edition (EndeavourOS / Arch).
# Installs dependencies (pacman, pip fallback), sets up Wayland/uinput
# permissions and creates a menu entry.
set -euo pipefail
cd "$(dirname "$0")"

echo "== OP Auto Clicker 4.1 (Wayland Edition) installer =="

# 1. dependencies
if command -v pacman > /dev/null 2>&1; then
    sudo pacman -Sy --needed --noconfirm python python-pyside6 python-evdev
else
    echo "pacman not found - falling back to pip."
    python3 -m pip install --user -r requirements.txt
fi

# 2. permissions (udev rule + input group); needs sudo, run once
./setup_permissions.sh || echo "WARNING: permission setup failed - run ./setup_permissions.sh manually"

# 3. executable + desktop entry
chmod +x op_autoclicker.py
DESK_DIR="$HOME/.local/share/applications"
mkdir -p "$DESK_DIR"
sed "s|@APP_PATH@|$PWD/op_autoclicker.py|" op-autoclicker.desktop \
    > "$DESK_DIR/op-autoclicker.desktop"
chmod +x "$DESK_DIR/op-autoclicker.desktop" 2>/dev/null || true

echo
echo "Installation complete."
echo "  Launch from the application menu ('OP Auto Clicker'), or:"
echo "    python3 $PWD/op_autoclicker.py"
echo
echo "IMPORTANT: log out and back in ONCE so the input-group permissions"
echo "apply (required for Wayland synthetic clicks + global hotkey)."
echo "Diagnostics any time:  python3 op_autoclicker.py --selftest"
