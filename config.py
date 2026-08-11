"""Configuration, constants, and shared state for the coding agent."""
import json
import os
import re
from pathlib import Path

from anthropic import Anthropic

# -- agent home vs workdir --------------------------------------------------
# RABBIT_HOME: where agent files, settings, and runtime data live
# WORKDIR: where the agent operates (the user's current directory)
RABBIT_HOME = Path(os.environ.get("RABBIT_HOME", Path.cwd()))
WORKDIR = Path.cwd()


# -- settings ---------------------------------------------------------------
def _load_settings() -> dict:
    """Load settings from RABBIT_HOME/settings.json, falling back to .env."""
    settings_path = RABBIT_HOME / "settings.json"
    if settings_path.exists():
        with open(settings_path) as f:
            return json.load(f)
    # Dev mode: load from .env
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
        if os.getenv("ANTHROPIC_BASE_URL"):
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
        return {
            "api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
            "api_base_url": os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
            "model": os.environ.get("MODEL_ID", "claude-sonnet-4-6"),
            "fallback_model": os.environ.get("FALLBACK_MODEL_ID") or None,
        }
    except ImportError:
        return {}


_settings = _load_settings()

# -- client ----------------------------------------------------------------
_api_key = _settings.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "MISSING_API_KEY")
_base_url = _settings.get("api_base_url") or os.environ.get("ANTHROPIC_BASE_URL")
client = Anthropic(api_key=_api_key, base_url=_base_url) if _base_url else Anthropic(api_key=_api_key)

MODEL = _settings.get("model") or os.environ.get("MODEL_ID", "claude-sonnet-4-6")
FALLBACK_MODEL = _settings.get("fallback_model") or os.environ.get("FALLBACK_MODEL_ID") or None

# -- directories -----------------------------------------------------------
SKILLS_DIR = RABBIT_HOME / "skills"
MEMORY_DIR = RABBIT_HOME / ".memory"
TASKS_DIR = RABBIT_HOME / ".tasks"
MAILBOX_DIR = RABBIT_HOME / ".mailboxes"
WORKTREES_DIR = RABBIT_HOME / ".worktrees"
TRANSCRIPT_DIR = RABBIT_HOME / ".transcripts"
TOOL_RESULTS_DIR = RABBIT_HOME / ".task_outputs" / "tool-results"
CRON_JOBS_FILE = RABBIT_HOME / ".scheduled_tasks.json"

for d in [MEMORY_DIR, TASKS_DIR, MAILBOX_DIR, WORKTREES_DIR,
          TRANSCRIPT_DIR, TOOL_RESULTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# -- constants -------------------------------------------------------------
DEFAULT_MAX_TOKENS = 8000
ESCALATED_MAX_TOKENS = 64000
MAX_RETRIES = 10
MAX_RECOVERY_RETRIES = 3
BASE_DELAY_MS = 500
CONTEXT_LIMIT = 50000
KEEP_RECENT_TOOL_RESULTS = 3
PERSIST_THRESHOLD = 200000


# -- helpers ---------------------------------------------------------------
def safe_path(path: str) -> Path:
    """Resolve and validate a path stays inside WORKDIR."""
    p = (WORKDIR / path).resolve()
    if not str(p).startswith(str(WORKDIR.resolve())):
        raise ValueError(f"Path outside workspace: {path}")
    return p


def normalize_name(name: str) -> str:
    """Sanitize a name for use in tool/mcp prefixes."""
    return re.sub(r'[^a-zA-Z0-9_-]', '_', name)
