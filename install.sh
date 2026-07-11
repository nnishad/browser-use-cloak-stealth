#!/usr/bin/env bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=== Starting browser-use (Local, no API) Integration Setup ===${NC}"

# 1. Resolve Hermes directory
HERMES_DIR="${1:-$HOME/.hermes/hermes-agent}"
if [ ! -d "$HERMES_DIR" ]; then
    echo -e "${RED}Error: hermes-agent directory not found at $HERMES_DIR${NC}"
    echo "Usage: ./install.sh [/path/to/hermes-agent]"
    exit 1
fi

# 2. Copy the worker files to the local tools dir
WORKER_DIR="$HOME/tool/browser-use-cloak-stealth"
echo -e "${BLUE}Creating worker environment at ${WORKER_DIR}...${NC}"
mkdir -p "$WORKER_DIR"
cp browser_worker.py "$WORKER_DIR/"
cp pyproject.toml "$WORKER_DIR/"

# 3. Setup Python virtual environment
cd "$WORKER_DIR"
if command -v uv &> /dev/null; then
    echo "Syncing dependencies with uv..."
    uv sync
else
    echo "uv not found, fallback to standard virtualenv..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r <(python3 -c "import tomllib; f=open('pyproject.toml','rb'); d=tomllib.load(f); print('\n'.join(d['project']['dependencies']))")
fi
cd - > /dev/null

# 4. Install systemd user service
echo -e "${BLUE}Installing systemd user service...${NC}"
mkdir -p "$HOME/.config/systemd/user"
cp stealth-browser.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable stealth-browser.service
systemctl --user restart stealth-browser.service
echo -e "${GREEN}✓ systemd service stealth-browser.service restarted successfully.${NC}"

# 5. Apply the Git patch to Hermes
echo -e "${BLUE}Applying patch to hermes-agent at $HERMES_DIR...${NC}"
cd "$HERMES_DIR"
if git apply --check "$OLDPWD/hermes.patch" &> /dev/null; then
    git apply "$OLDPWD/hermes.patch"
    echo -e "${GREEN}✓ Patch applied successfully to hermes-agent.${NC}"
else
    echo -e "${RED}Warning: Could not automatically apply patch. It might already be applied or diverged.${NC}"
fi

echo -e "\n${GREEN}=== Setup Complete! ===${NC}"
echo -e "To configure Hermes, run:"
echo -e "  ${BLUE}hermes tools${NC}"
echo -e "Choose 'browser-use (Local, no API)' from the menu to perform onboarding checks."
