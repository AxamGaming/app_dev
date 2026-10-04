#!/usr/bin/env bash
set -e
REAL_USER=${SUDO_USER:-$USER}
echo "🔧 Configuring OP Auto Clicker for user: $REAL_USER"

if command -v pacman &> /dev/null; then
    echo "📦 Installing system dependencies via pacman..."
    sudo pacman -Sy --needed --noconfirm python-pyside6 python-evdev || {
        echo "⚠️  Pacman failed. Attempting AUR via yay..."
        if command -v yay &> /dev/null; then
            yay -S --needed --noconfirm python-pyside6 python-evdev
        else
            echo "❌ Failed to install dependencies. Please run: yay -S python-pyside6 python-evdev"
            exit 1
        fi
    }
else
    echo "⚠️  Non-Arch system detected. Please ensure PySide6 and evdev are installed."
fi

echo "🔐 Setting up uinput permissions for $REAL_USER..."
sudo usermod -aG input "$REAL_USER"
sudo tee /etc/udev/rules.d/99-uinput.rules > /dev/null << 'UDEV_EOF'
KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"
UDEV_EOF
sudo udevadm control --reload-rules
sudo udevadm trigger
echo "✅ Setup complete."
