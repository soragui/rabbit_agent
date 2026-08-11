#!/usr/bin/env bash
# install.sh — install rabbit-agent to ~/.rabbit-agent
#
# Remote install (when published):
#   curl -fsSL https://<release-url>/install.sh | bash
#
# Local install (from repo dir):
#   bash install.sh
#
# Options:
#   --local <path>   Install from a local repo path
#   --bin-dir <dir>  Where to place the rabbit-agent wrapper (default: ~/.local/bin)

set -euo pipefail

RABBIT_HOME="${RABBIT_HOME:-$HOME/.rabbit-agent}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
SOURCE_DIR=""

# -- parse args ------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --local) SOURCE_DIR="$2"; shift 2 ;;
        --bin-dir) BIN_DIR="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# -- determine source ------------------------------------------------------
if [ -z "$SOURCE_DIR" ]; then
    # Auto-detect: if install.sh is run from within the repo, use that dir
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -f "$SCRIPT_DIR/agent.py" ] && [ -f "$SCRIPT_DIR/config.py" ]; then
        SOURCE_DIR="$SCRIPT_DIR"
    else
        echo "Error: cannot auto-detect source directory."
        echo "Run from the repo:  bash install.sh"
        echo "Or specify path:    bash install.sh --local /path/to/repo"
        exit 1
    fi
fi

echo "╭──────────────────────────────────────────────╮"
echo "│         Rabbit Agent Installer                │"
echo "├──────────────────────────────────────────────┤"
echo "│  Agent home : $RABBIT_HOME"
echo "│  Source     : $SOURCE_DIR"
echo "│  Bin dir    : $BIN_DIR"
echo "╰──────────────────────────────────────────────╯"
echo ""

# -- check prerequisites ---------------------------------------------------
command -v python3 >/dev/null 2>&1 || {
    echo "Error: python3 is required but not found."; exit 1; }
command -v uv >/dev/null 2>&1 || {
    echo "Error: uv is required but not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }

# -- install agent files ---------------------------------------------------
echo "→ Installing agent files to $RABBIT_HOME ..."
mkdir -p "$RABBIT_HOME"

# Core modules
FILES=(
    agent.py config.py loop.py
)
for f in "${FILES[@]}"; do
    cp "$SOURCE_DIR/$f" "$RABBIT_HOME/"
done

# Packages
for pkg in harness tools; do
    mkdir -p "$RABBIT_HOME/$pkg"
    cp "$SOURCE_DIR/$pkg"/*.py "$RABBIT_HOME/$pkg/"
done

# Optional: skills directory
if [ -d "$SOURCE_DIR/skills" ]; then
    cp -r "$SOURCE_DIR/skills" "$RABBIT_HOME/"
else
    mkdir -p "$RABBIT_HOME/skills"
fi

echo "  ✓ Files installed"

# -- venv & dependencies ---------------------------------------------------
echo "→ Setting up virtual environment ..."
if [ ! -f "$RABBIT_HOME/pyproject.toml" ]; then
    cp "$SOURCE_DIR/pyproject.toml" "$RABBIT_HOME/"
fi
(cd "$RABBIT_HOME" && uv sync --quiet 2>&1 | tail -1)
echo "  ✓ Dependencies installed"

# -- settings --------------------------------------------------------------
if [ ! -f "$RABBIT_HOME/settings.json" ]; then
    cat > "$RABBIT_HOME/settings.json" <<'SETTINGS'
{
    "api_key": "sk-your-api-key-here",
    "api_base_url": "https://api.anthropic.com",
    "model": "claude-sonnet-4-6",
    "fallback_model": null
}
SETTINGS
    echo "  ✓ Created settings.json (edit with your API key)"
else
    echo "  • settings.json already exists (skipped)"
fi

# -- wrapper script --------------------------------------------------------
echo "→ Installing rabbit-agent command ..."
mkdir -p "$BIN_DIR"

WRAPPER="$BIN_DIR/rabbit-agent"
cat > "$WRAPPER" <<'WRAPPER'
#!/usr/bin/env bash
# rabbit-agent — global launcher for the Rabbit coding agent.
export RABBIT_HOME="${RABBIT_HOME:-$HOME/.rabbit-agent}"
if [ ! -d "$RABBIT_HOME" ]; then
    echo "rabbit-agent: not found at $RABBIT_HOME" >&2
    exit 1
fi
exec "$RABBIT_HOME/.venv/bin/python" -u "$RABBIT_HOME/agent.py" "$@"
WRAPPER

chmod +x "$WRAPPER"
echo "  ✓ Wrapper installed to $WRAPPER"

# -- PATH check ------------------------------------------------------------
if ! echo "$PATH" | tr ':' '\n' | grep -qxF "$BIN_DIR"; then
    echo ""
    echo "  ⚠ $BIN_DIR is not in your PATH."
    echo "    Add this to your shell profile (~/.bashrc, ~/.zshrc):"
    echo ""
    echo "    export PATH=\"$BIN_DIR:\$PATH\""
fi

# -- done ------------------------------------------------------------------
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Rabbit Agent installed!"
echo ""
echo "  1. Edit settings:    $RABBIT_HOME/settings.json"
echo "  2. Run anywhere:     rabbit-agent"
echo ""
echo "  Or run the agent directly from its home:"
echo "    cd $RABBIT_HOME && uv run python agent.py"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
