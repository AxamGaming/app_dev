#!/usr/bin/env bash
set -e

# Resolve the true directory of the script, even when double-clicked from Desktop
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$APP_DIR/venv"

# 1. Auto-activate local venv if it exists
if [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
fi

# 2. Check Python dependencies
check_deps() {
    python3 -c "import PySide6, evdev" 2>/dev/null
}

# 3. Check kernel input permissions
check_perms() {
    test -w /dev/uinput
}

if ! check_deps || ! check_perms; then
    echo "⚠️  Setup required: Missing dependencies or input permissions."
    echo "🔒 Requesting administrator privileges to configure the environment..."

    sudo bash "$APP_DIR/setup_permissions.sh"

    echo ""
    echo "✅ Environment configured successfully."
    echo "⚠️  CRITICAL: You MUST log out of KDE Plasma and log back in for the 'input' group change to take effect."
    echo "Press Enter to log out now, or Ctrl+C to abort and log out manually later."
    read -p ""

    qdbus org.kde.ksmserver /KSMServer logout 0 0 0
    exit 0
fi

# 4. Launch the application
echo "🚀 Launching OP Auto Clicker..."
exec python3 "$APP_DIR/op_autoclicker.py" "$@"
echo ""
read -p "Press Enter to close this window..."
