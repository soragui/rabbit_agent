#!/usr/bin/env bash
# install.sh — install 51agent to ~/.51agent
#
# Remote install (when published):
#   curl -fsSL https://raw.githubusercontent.com/soragui/rabbit_agent/main/install.sh | bash
#
# Local install (from repo dir):
#   bash install.sh
#
# Options:
#   --local <path>   Install from a local repo path (skips GitHub download)
#   --version <tag>  Install a specific release tag (default: latest, read from VERSION file)
#   --bin-dir <dir>  Where to place the 51agent wrapper (default: ~/.local/bin)

set -euo pipefail

GITHUB_REPO="${GITHUB_REPO:-soragui/rabbit_agent}"
AGENT_HOME="${AGENT_HOME:-$HOME/.51agent}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
SOURCE_DIR=""
VERSION="${VERSION:-latest}"
CLEANUP_SOURCE=""

# -- parse args ------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --local) SOURCE_DIR="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        --bin-dir) BIN_DIR="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# -- helpers ---------------------------------------------------------------
_cleanup() {
    if [ -n "$CLEANUP_SOURCE" ] && [ -d "$CLEANUP_SOURCE" ]; then
        rm -rf "$CLEANUP_SOURCE"
    fi
}
trap _cleanup EXIT

_download_release() {
    local dest="$1"

    # First, determine the version if not explicitly set
    if [ "$VERSION" = "latest" ]; then
        echo "→ Fetching latest version from GitHub ..."
        local version_url="https://raw.githubusercontent.com/$GITHUB_REPO/main/VERSION"
        VERSION=$(curl -fsSL "$version_url" 2>/dev/null | head -1 | tr -d '[:space:]')
        if [ -z "$VERSION" ]; then
            echo "Error: could not determine latest version from $GITHUB_REPO"
            echo "Try installing from a local repo: bash install.sh --local /path/to/repo"
            exit 1
        fi
        echo "  Latest version: $VERSION"
    fi

    # Download the versioned tarball
    local tarball="$dest/release.tar.gz"
    local url="https://github.com/$GITHUB_REPO/archive/refs/tags/$VERSION.tar.gz"
    echo "  Downloading $url ..."
    curl -fsSL "$url" -o "$tarball" || {
        echo "Error: download failed for version $VERSION"; exit 1; }

    echo "  Extracting ..."
    tar -xzf "$tarball" -C "$dest" --strip-components=1
    rm -f "$tarball"

    # Verify extract
    if [ ! -f "$dest/agent.py" ]; then
        echo "Error: extracted tarball missing agent.py. Contents:"
        ls -la "$dest"
        exit 1
    fi
    echo "  ✓ Release $VERSION extracted"
}

# -- determine source ------------------------------------------------------
if [ -z "$SOURCE_DIR" ]; then
    # Auto-detect: if install.sh is run from within the repo, use that dir
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -f "$SCRIPT_DIR/agent.py" ] && [ -f "$SCRIPT_DIR/config.py" ]; then
        SOURCE_DIR="$SCRIPT_DIR"
    else
        # Download from GitHub to a temp directory
        SOURCE_DIR="$(mktemp -d /tmp/51agent-XXXXXX)"
        CLEANUP_SOURCE="$SOURCE_DIR"
        _download_release "$SOURCE_DIR"
    fi
fi

echo "╭──────────────────────────────────────────────╮"
echo "│         51agent Installer                     │"
echo "├──────────────────────────────────────────────┤"
echo "│  Agent home : $AGENT_HOME"
echo "│  Source     : $SOURCE_DIR"
echo "│  Bin dir    : $BIN_DIR"
echo "│  Version    : $VERSION"
echo "╰──────────────────────────────────────────────╯"
echo ""

# -- check prerequisites ---------------------------------------------------
command -v python3 >/dev/null 2>&1 || {
    echo "Error: python3 is required but not found."; exit 1; }
command -v uv >/dev/null 2>&1 || {
    echo "Error: uv is required but not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }

# -- install agent files ---------------------------------------------------
echo "→ Installing agent files to $AGENT_HOME ..."
mkdir -p "$AGENT_HOME"

# Core modules
FILES=(agent.py config.py loop.py VERSION)
for f in "${FILES[@]}"; do
    cp "$SOURCE_DIR/$f" "$AGENT_HOME/"
done

# Packages
for pkg in harness tools; do
    mkdir -p "$AGENT_HOME/$pkg"
    cp "$SOURCE_DIR/$pkg"/*.py "$AGENT_HOME/$pkg/"
done

# Optional: skills directory
if [ -d "$SOURCE_DIR/skills" ]; then
    cp -r "$SOURCE_DIR/skills" "$AGENT_HOME/"
else
    mkdir -p "$AGENT_HOME/skills"
fi

echo "  ✓ Files installed"

# -- venv & dependencies ---------------------------------------------------
echo "→ Setting up virtual environment ..."
if [ ! -f "$AGENT_HOME/pyproject.toml" ]; then
    cp "$SOURCE_DIR/pyproject.toml" "$AGENT_HOME/"
fi
# Suppress uv output unless there's an error
(cd "$AGENT_HOME" && uv sync 2>&1) | tail -3
echo "  ✓ Dependencies installed"

# -- settings --------------------------------------------------------------
if [ ! -f "$AGENT_HOME/settings.json" ]; then
    cat > "$AGENT_HOME/settings.json" <<'SETTINGS'
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
echo "→ Installing 51agent command ..."
mkdir -p "$BIN_DIR"

WRAPPER="$BIN_DIR/51agent"
cat > "$WRAPPER" <<'WRAPPER'
#!/usr/bin/env bash
# 51agent — global launcher for the 51 coding agent.
export AGENT_HOME="${AGENT_HOME:-$HOME/.51agent}"
if [ ! -d "$AGENT_HOME" ]; then
    echo "51agent: not found at $AGENT_HOME" >&2
    exit 1
fi
exec "$AGENT_HOME/.venv/bin/python" -u "$AGENT_HOME/agent.py" "$@"
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
echo "  51agent installed!"
echo ""
echo "  1. Edit settings:    $AGENT_HOME/settings.json"
echo "  2. Run anywhere:     51agent"
echo ""
echo "  Or run directly from its home:"
echo "    cd $AGENT_HOME && uv run python agent.py"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
