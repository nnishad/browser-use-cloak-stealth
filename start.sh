#!/usr/bin/env bash
set -e

# 1. Verify that all required system commands are available in PATH
MISSING=()
for cmd in Xvfb x11vnc websockify; do
    if ! command -v "$cmd" &>/dev/null; then
        MISSING+=("$cmd")
    fi
done

if [ ${#MISSING[@]} -ne 0 ]; then
    echo "Error: Missing required system commands: ${MISSING[*]}" >&2
    echo "Please install these prerequisites on your distribution first." >&2
    exit 1
fi

# Terminate any existing background processes on our ports/displays to clean state
pkill -f "Xvfb :99" || true
pkill -f "x11vnc -display :99" || true
pkill -f "websockify --web" || true

# Start Xvfb in the background (relying on PATH resolution)
Xvfb :99 -screen 0 1280x800x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Wait briefly for Xvfb virtual framebuffer to initialize
sleep 1.5

# Start x11vnc in the background (unsetting Wayland variables to isolate from host)
env -u WAYLAND_DISPLAY -u XDG_SESSION_TYPE x11vnc -display :99 -forever -shared -bg -nopw -rfbport 5900 &

# Detect noVNC web assets folder path (multi-distro fallback scan)
NOVNC_DIR=""
for d in "/usr/share/novnc" "/usr/share/webapps/novnc" "/usr/local/share/novnc" "/var/lib/novnc" "/usr/share/novnc-no-ssl"; do
    if [ -d "$d" ]; then
        NOVNC_DIR="$d"
        break
    fi
done

if [ -z "$NOVNC_DIR" ]; then
    echo "Error: Could not locate noVNC web assets directory." >&2
    echo "Please install 'novnc' using your package manager." >&2
    exit 1
fi

# Start websockify in the background to serve noVNC on port 6080
websockify --web "$NOVNC_DIR" 6080 localhost:5900 &

# Query the exact dynamic binary path of CloakBrowser Chromium
WORKER_VENV_PYTHON="$HOME/tool/browser-use-cloak-stealth/.venv/bin/python3"
if [ ! -f "$WORKER_VENV_PYTHON" ]; then
    echo "Error: Worker virtual environment Python not found at $WORKER_VENV_PYTHON" >&2
    echo "Please run './install.sh' first." >&2
    exit 1
fi
CHROME_BIN=$($WORKER_VENV_PYTHON -c "import cloakbrowser; print(cloakbrowser.binary_info()['binary_path'])")

# Execute CloakBrowser Chromium under the display :99 (blocking process)
export DISPLAY=:99
exec "$CHROME_BIN" --remote-debugging-port=9377 --ozone-platform=x11 --window-size=1280,800 --window-position=0,0
