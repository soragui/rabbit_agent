"""Configuration, constants, and shared state for the coding agent."""
import os
import re
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

# -- env ------------------------------------------------------------------
load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ.get("MODEL_ID", "claude-sonnet-4-6")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL_ID")

# -- directories -----------------------------------------------------------
SKILLS_DIR = WORKDIR / "skills"
MEMORY_DIR = WORKDIR / ".memory"
TASKS_DIR = WORKDIR / ".tasks"
MAILBOX_DIR = WORKDIR / ".mailboxes"
WORKTREES_DIR = WORKDIR / ".worktrees"
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
TOOL_RESULTS_DIR = WORKDIR / ".task_outputs" / "tool-results"
CRON_JOBS_FILE = WORKDIR / ".scheduled_tasks.json"

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
