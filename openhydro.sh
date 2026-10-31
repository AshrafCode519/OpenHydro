#!/usr/bin/env bash
# ==============================================================================
# OpenHydro - Modern Dynamic Download Manager Launcher for Linux
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================================"
echo "⚡ OpenHydro - Modern Dynamic Download Manager (Linux)"
echo "============================================================"

# Detect Python 3
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
else
    echo "❌ Error: Python 3 is not installed. Please install Python 3 (e.g. sudo apt install python3 python3-pip)"
    exit 1
fi

# Ensure Virtual Environment exists for isolation
VENV_DIR="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating Python virtual environment in .venv..."
    $PYTHON_CMD -m venv "$VENV_DIR" || {
        echo "⚠️ Could not create virtual environment. Running with global python..."
        VENV_DIR=""
    }
fi

if [ -n "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/activate" ]; then
    source "$VENV_DIR/bin/activate"
    PYTHON_RUNNER="python"
else
    PYTHON_RUNNER="$PYTHON_CMD"
fi

# Install dependencies if needed
if [ -f "requirements.txt" ] && [ ! -f "$SCRIPT_DIR/.deps_installed" ]; then
    echo "📥 Installing required packages..."
    $PYTHON_RUNNER -m pip install --upgrade pip
    $PYTHON_RUNNER -m pip install -r requirements.txt
    touch "$SCRIPT_DIR/.deps_installed"
fi

# Launch OpenHydro
exec $PYTHON_RUNNER run.py "$@"

