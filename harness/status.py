"""Status bar collectors — pure functions, subprocess-based with timeouts.

Every collector must be cheap, never raise, and return None when the
information is unavailable so the status bar can omit that segment.
"""
import shutil
import subprocess
import sys
import time as _time
from pathlib import Path

from config import CONTEXT_LIMIT


def _run(cmd: list[str], cwd: str, timeout: float) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None


def git_summary(workdir: str, timeout: float = 2.0) -> str | None:
    """'{branch} ✓' or '{branch} ✗{n}' — None when not a repo / no git."""
    if shutil.which("git") is None:
        return None
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], workdir, timeout)
    if branch is None or branch.returncode != 0:
        return None
    st = _run(["git", "status", "--porcelain"], workdir, timeout)
    if st is None or st.returncode != 0:
        return None
    dirty = len([line for line in st.stdout.splitlines() if line.strip()])
    name = branch.stdout.strip()
    return f"{name} ✓" if dirty == 0 else f"{name} ✗{dirty}"


def python_version() -> str:
    v = sys.version_info
    return f"py {v.major}.{v.minor}.{v.micro}"


def node_version(timeout: float = 2.0) -> str | None:
    if shutil.which("node") is None:
        return None
    r = _run(["node", "--version"], ".", timeout)
    if r is None or r.returncode != 0:
        return None
    return f"node {r.stdout.strip().lstrip('v')}"


class TokenTracker:
    """Tracks the most recent API call's usage for the 'ctx' segment."""

    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0

    def update(self, usage) -> None:
        """usage: object with .input_tokens/.output_tokens (or None, a no-op)."""
        if usage is None:
            return
        self.input_tokens = getattr(usage, "input_tokens", 0) or 0
        self.output_tokens = getattr(usage, "output_tokens", 0) or 0

    @property
    def display(self) -> str:
        total = self.input_tokens + self.output_tokens
        if total == 0:
            return f"ctx –/{CONTEXT_LIMIT // 1000}k"
        return f"ctx {total / 1000:.1f}k/{CONTEXT_LIMIT // 1000}k"


tracker = TokenTracker()


def cron_count() -> int:
    from tools import cron

    with cron.cron_lock:
        return len(cron.cron_queue)


class _TTLCache:
    """Memoize a callable for `ttl` seconds (injectable clock for tests)."""

    def __init__(self, ttl: float):
        self.ttl = ttl
        self._value = None
        self._expires = 0.0

    def get(self, fn, now: float | None = None):
        now = _time.monotonic() if now is None else now
        if now < self._expires:
            return self._value
        self._value = fn()
        self._expires = now + self.ttl
        return self._value


_git_cache = _TTLCache(2.0)
_node_cache = _TTLCache(2.0)


def collect_status(workdir: Path, state: str = "idle") -> list[str]:
    """Ordered status-bar segments; None/empty segments are omitted."""
    queued = cron_count()
    segments = [
        tracker.display,
        state or "idle",
        str(workdir),
        _git_cache.get(lambda: git_summary(str(workdir))),
        python_version(),
        _node_cache.get(node_version),
        f"{queued} cron" if queued else None,
    ]
    return [s for s in segments if s]
