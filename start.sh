#!/usr/bin/env bash
# e2n bootstrap — checks dependencies, installs if needed, launches wizard.
# Supported: macOS, Linux, WSL. Not supported: native Windows (use WSL).

set -euo pipefail

MIN_PYTHON="3.11"
VENV_DIR=".venv"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}!${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; }

echo ""
echo "═══════════════════════════════════════════════"
echo "  e2n — Evernote to Notion Migration Tool"
echo "═══════════════════════════════════════════════"
echo ""

# --- Platform check ---
OS="$(uname -s)"
case "$OS" in
    Linux*)  PLATFORM="linux" ;;
    Darwin*) PLATFORM="macos" ;;
    MINGW*|CYGWIN*|MSYS*)
        fail "Native Windows is not supported. Please use WSL."
        echo "  See: https://learn.microsoft.com/en-us/windows/wsl/install"
        exit 1 ;;
    *) PLATFORM="unknown" ;;
esac
ok "Platform: $PLATFORM ($OS)"

# --- Python check ---
PYTHON=""
for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        version=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
        if [ "$(echo -e "$version\n$MIN_PYTHON" | sort -V | head -1)" = "$MIN_PYTHON" ]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    fail "Python >= $MIN_PYTHON not found."
    echo ""
    if [ "$PLATFORM" = "macos" ]; then
        echo "  Install with: brew install python@3.12"
    else
        echo "  Install with: sudo apt install python3.12 python3.12-venv"
    fi
    echo "  Then re-run: ./start.sh"
    exit 1
fi
ok "Python: $($PYTHON --version)"

# --- Pip check ---
if ! "$PYTHON" -m pip --version &>/dev/null; then
    fail "pip not available for $PYTHON"
    echo "  Install with: $PYTHON -m ensurepip --upgrade"
    exit 1
fi
ok "pip: available"

# --- Notion app check (required) ---
NOTION_FOUND=false
if [ -d "/Applications/Notion.app" ]; then
    ok "Notion app: installed (/Applications/Notion.app)"
    NOTION_FOUND=true
elif [ -d "$HOME/Applications/Notion.app" ]; then
    ok "Notion app: installed (~$HOME/Applications/Notion.app)"
    NOTION_FOUND=true
elif [ "$PLATFORM" = "linux" ] && command -v notion-app &>/dev/null; then
    ok "Notion app: installed (notion-app)"
    NOTION_FOUND=true
elif [ "$PLATFORM" = "linux" ] && (snap list 2>/dev/null | grep -q notion || flatpak list 2>/dev/null | grep -qi notion); then
    ok "Notion app: installed (snap/flatpak)"
    NOTION_FOUND=true
fi

if [ "$NOTION_FOUND" = false ]; then
    fail "Notion app is not installed."
    echo ""
    echo "  The Notion desktop app is required to view your imported content."
    if [ "$PLATFORM" = "macos" ]; then
        echo "  Download: https://www.notion.so/desktop"
    else
        echo "  Download: https://www.notion.so/desktop"
        echo "  Or: sudo snap install notion-snap-reborn"
    fi
    exit 1
fi

# --- Venv check ---
if [ -d "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/python" ]; then
    ok "Virtual environment: exists ($VENV_DIR)"
    NEEDS_INSTALL=false
    if ! "$VENV_DIR/bin/python" -c "import e2n" &>/dev/null; then
        NEEDS_INSTALL=true
    fi
else
    warn "Virtual environment: not found"
    NEEDS_INSTALL=true
fi

# --- Install prompt ---
if [ "$NEEDS_INSTALL" = true ]; then
    echo ""
    echo "e2n needs to install its dependencies into a local virtual environment."
    echo "This will NOT affect your system Python."
    echo ""
    read -rp "Install now? [Y/n] " response
    response="${response:-Y}"
    if [[ ! "$response" =~ ^[Yy] ]]; then
        echo ""
        echo "Manual installation instructions: docs/INSTALL.md"
        exit 0
    fi

    echo ""
    echo "Installing..."
    if [ ! -d "$VENV_DIR" ]; then
        "$PYTHON" -m venv "$VENV_DIR"
    fi
    "$VENV_DIR/bin/pip" install --quiet -e ".[dev]"
    ok "Installation complete"
fi

# --- Launch wizard ---
echo ""
echo "═══════════════════════════════════════════════"
echo "  Launching e2n wizard..."
echo "═══════════════════════════════════════════════"
echo ""
exec "$VENV_DIR/bin/python" -m e2n.webui.server --open
