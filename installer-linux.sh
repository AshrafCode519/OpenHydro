#!/usr/bin/env bash
# ==============================================================================
# OpenHydro One-Line Linux Installer
# Usage: curl -sSL https://raw.githubusercontent.com/openhydro/openhydro/main/installer-linux.sh | bash
# ==============================================================================

set -e

INSTALL_DIR="$HOME/.local/share/openhydro"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"

echo "⚡ Installing OpenHydro to $INSTALL_DIR..."

mkdir -p "$INSTALL_DIR"
mkdir -p "$BIN_DIR"
mkdir -p "$DESKTOP_DIR"

# Download or copy latest files
if [ -d "./openhydro" ]; then
    cp -r ./* "$INSTALL_DIR/"
else
    git clone https://github.com/openhydro/openhydro.git "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
chmod +x openhydro.sh

# Create global symlink
ln -sf "$INSTALL_DIR/openhydro.sh" "$BIN_DIR/openhydro"

# Install desktop entry
sed -i "s|Exec=.*|Exec=$BIN_DIR/openhydro|g" openhydro.desktop
sed -i "s|Icon=.*|Icon=$INSTALL_DIR/assets/icon.png|g" openhydro.desktop
cp openhydro.desktop "$DESKTOP_DIR/"

echo "✅ OpenHydro installed successfully!"
echo "🚀 You can launch it by typing 'openhydro' in terminal or from your Application Menu."
