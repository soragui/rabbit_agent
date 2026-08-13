# TUI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the scrollback CLI with a full-screen prompt_toolkit TUI — header, split chat/activity panes, footer input, and a status bar with context/git/env info.

**Architecture:** Agent turns run in a worker thread; all output flows through one thread-safe event queue (`harness/ui_bridge.py`) consumed by the prompt_toolkit `Application` via an asyncio background task. `harness/render.py` becomes a facade that emits events in TUI mode and keeps today's Rich/print behavior otherwise, so every existing render call site works unchanged.

**Tech Stack:** Python 3.13, prompt_toolkit ≥3.0.53 (already a dependency), pytest for unit tests. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-13-tui-redesign-design.md` — the plan argues from the spec; read both.

## Global Constraints

- Python >=3.13; run everything with `uv run` (e.g. `uv run pytest tests/test_ui_bridge.py -v`).
- **No new dependencies.** prompt_toolkit, rich, and pytest are already installed.
- Non-TTY / piped runs must behave exactly as today (plain mode); TTY runs show nothing before the TUI exists.
- All cross-thread traffic goes through `harness.ui_bridge` — never touch prompt_toolkit objects from a worker thread.
- Ctrl+C mid-turn aborts the turn; Ctrl+C at the idle footer exits the app.
- Stage scripts `s01_loop.py`, `s02_tools.py`, `s03_permission.py` are untouched.
- ruff conventions: line-length 100, double quotes. Tests live flat in `tests/`, plain pytest, no fixtures beyond what's shown.
- Commit after every task. Message pattern: `feat:` / `refactor:` / `test:` prefix + short description, ending with the Co-Authored-By trailer shown in each task.

---

### Task 1: Thread-safe UI bridge

**Files:**
- Create: `harness/ui_bridge.py`
- Test: `tests/test_ui_bridge.py`

**Interfaces:**
- Produces (used by every later task):
  - `Event(kind: str, payload: str = "", style: str = "")` — dataclass. Kinds: `"chat"`, `"activity"`, `"stream"`, `"clear_stream"`, `"question"`, `"state"`.
  - `class TurnAborted(Exception)` — raised inside a turn when the abort flag is set.
  - `class Bridge` with methods:
    - `emit(kind, payload="", style="") -> None` (worker thread)
    - `drain() -> list[Event]` (UI thread, non-blocking)
    - `request_abort() / clear_abort() / is_abort_requested() -> bool`
    - `ask_question(text: str, default: bool = False, timeout: float | None = None) -> bool` — blocks the calling thread; empty answer or timeout returns `default`; timeout also emits an `"activity"` auto-denied note; raises `RuntimeError` if a question is already pending.
    - `has_pending_question() -> bool` / `answer_question(line: str) -> bool` (UI thread)
  - `bridge = Bridge()` — module-level singleton all production code uses.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ui_bridge.py`:

```python
"""Unit tests for the UI bridge — event queue, pending question, abort flag."""
import threading
import time

import pytest

from harness.ui_bridge import Bridge, Event, TurnAborted


def test_emit_and_drain_roundtrip():
    b = Bridge()
    b.emit("chat", "hello", style="agent")
    b.emit("activity", "tool ran", style="ok")
    assert b.drain() == [
        Event("chat", "hello", "agent"),
        Event("activity", "tool ran", "ok"),
    ]
    assert b.drain() == []


def test_abort_flag():
    b = Bridge()
    assert b.is_abort_requested() is False
    b.request_abort()
    assert b.is_abort_requested() is True
    b.clear_abort()
    assert b.is_abort_requested() is False


def test_turn_aborted_is_an_exception():
    assert issubclass(TurnAborted, Exception)


def test_ask_question_answered_yes():
    b = Bridge()

    def ui():
        while not b.has_pending_question():
            time.sleep(0.01)
        assert b.drain()[-1].kind == "question"
        b.answer_question("y")

    t = threading.Thread(target=ui)
    t.start()
    assert b.ask_question("Allow bash?") is True
    t.join()


def test_ask_question_empty_answer_uses_default():
    b = Bridge()

    def ui():
        while not b.has_pending_question():
            time.sleep(0.01)
        b.answer_question("")

    t = threading.Thread(target=ui)
    t.start()
    assert b.ask_question("Allow bash?", default=False) is False
    t.join()


def test_ask_question_timeout_returns_default_and_notes_it():
    b = Bridge()
    assert b.ask_question("Allow bash?", default=False, timeout=0.05) is False
    events = b.drain()
    assert events[0].kind == "question"
    assert events[-1].kind == "activity"
    assert "auto-denied" in events[-1].payload


def test_ask_question_rejects_nested():
    b = Bridge()

    def inner():
        b.ask_question("second?", timeout=2)

    t = threading.Thread(target=inner)
    t.start()
    while not b.has_pending_question():
        time.sleep(0.01)
    with pytest.raises(RuntimeError):
        b.ask_question("first?", timeout=0.2)
    b.answer_question("y")
    t.join()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_ui_bridge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.ui_bridge'`

- [ ] **Step 3: Write the implementation**

Create `harness/ui_bridge.py`:

```python
"""Thread-safe bridge between agent worker threads and the TUI thread.

All cross-thread traffic between the agent (worker threads) and the
full-screen UI goes through this module:
  - render events flow worker -> UI via emit()/drain()
  - permission questions block the worker until the UI answers
  - the abort flag lets Ctrl+C stop a running turn

Plain threading code — no prompt_toolkit, no terminal I/O.
"""
import queue
import threading
from dataclasses import dataclass


class TurnAborted(Exception):
    """Raised inside a turn when the user pressed Ctrl+C (abort flag set)."""


@dataclass
class Event:
    """One render event, consumed by the UI thread.

    kind    — where the payload renders:
              "chat"         append to the chat pane
              "activity"     append to the activity pane
              "stream"       replace the in-flight chat block with payload
              "clear_stream" finalize the in-flight chat block
              "question"     a question is waiting in the activity pane
              "state"        agent state text (header/status bar)
    style   — hint for the pane's style class ("user", "agent", "info",
              "error", "tool", "ok", "fail", "running", "inbox", ...)
    """

    kind: str
    payload: str = ""
    style: str = ""


class Bridge:
    """Queue + pending question + abort flag."""

    def __init__(self):
        self._queue: queue.Queue[Event] = queue.Queue()
        self._abort = threading.Event()
        self._lock = threading.Lock()
        self._pending = False
        self._question_text = ""
        self._answered = threading.Event()
        self._answer = ""
        self._default = False

    # -- event queue (worker -> UI) ----------------------------------------
    def emit(self, kind: str, payload: str = "", style: str = "") -> None:
        self._queue.put(Event(kind=kind, payload=payload, style=style))

    def drain(self) -> list[Event]:
        events: list[Event] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                return events

    # -- abort flag (UI -> worker) -----------------------------------------
    def request_abort(self) -> None:
        self._abort.set()

    def clear_abort(self) -> None:
        self._abort.clear()

    def is_abort_requested(self) -> bool:
        return self._abort.is_set()

    # -- pending question (worker blocks, UI answers) -----------------------
    def ask_question(self, text: str, default: bool = False, timeout: float | None = None) -> bool:
        """Block the calling thread until the UI answers `text`.

        Empty answer or timeout -> `default` (timeout also emits an
        "auto-denied" activity note). Exactly one question may be
        pending at a time.
        """
        with self._lock:
            if self._pending:
                raise RuntimeError("a question is already pending")
            self._pending = True
            self._question_text = text
            self._answer = ""
            self._default = default
            self._answered.clear()
        self.emit("question", text)
        got = self._answered.wait(timeout)
        with self._lock:
            self._pending = False
            answer = self._answer
        if not got:
            self.emit("activity", f"⏱ auto-denied: {text}", style="fail")
            return default
        answer = answer.strip().lower()
        if not answer:
            return default
        return answer in ("y", "yes")

    def has_pending_question(self) -> bool:
        with self._lock:
            return self._pending

    def answer_question(self, line: str) -> bool:
        """UI thread: answer the pending question (empty line = default).

        Returns False when no question is pending.
        """
        with self._lock:
            if not self._pending:
                return False
            self._answer = line
        self._answered.set()
        return True


bridge = Bridge()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ui_bridge.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add harness/ui_bridge.py tests/test_ui_bridge.py
git commit -m "feat: thread-safe UI bridge — event queue, pending question, abort flag

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Status bar collectors

**Files:**
- Create: `harness/status.py`
- Test: `tests/test_status.py`

**Interfaces:**
- Consumes: `config.CONTEXT_LIMIT` (int, already exists = 50000), `tools.cron.cron_queue` and `tools.cron.cron_lock` (already exist).
- Produces (used by Task 7):
  - `git_summary(workdir: str, timeout: float = 2.0) -> str | None` — `"main ✓"` or `"main ✗3"` or `None`.
  - `python_version() -> str` — e.g. `"py 3.13.2"`.
  - `node_version(timeout: float = 2.0) -> str | None` — e.g. `"node 22.11.0"`.
  - `class TokenTracker` with `update(usage) -> None` (usage is any object with `.input_tokens`/`.output_tokens`; `None` is a no-op) and `display` property (`"ctx –/50k"` before any call, `"ctx 12.4k/50k"` after).
  - `tracker = TokenTracker()` — module singleton.
  - `cron_count() -> int`.
  - `class _TTLCache` with `get(fn, now=None) -> Any` (injectable clock for tests).
  - `collect_status(workdir: Path, state: str = "idle") -> list[str]` — ordered segments, falsy segments omitted.

- [ ] **Step 1: Write the failing test**

Create `tests/test_status.py`:

```python
"""Unit tests for status bar collectors — no subprocesses are really spawned."""
from pathlib import Path
from types import SimpleNamespace

from harness import status


class FakeRunner:
    """Replaces subprocess.run for collector tests."""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        key = cmd[1]  # e.g. "rev-parse", "status", "--version"
        rc, out = self.results.get(key, (0, ""))
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")


class TestGitSummary:
    def test_clean_repo(self, monkeypatch):
        runner = FakeRunner({"rev-parse": (0, "main\n"), "status": (0, "")})
        monkeypatch.setattr(status.subprocess, "run", runner)
        monkeypatch.setattr(status.shutil, "which", lambda x: "/usr/bin/git")
        assert status.git_summary("/repo") == "main ✓"

    def test_dirty_repo_counts_changes(self, monkeypatch):
        runner = FakeRunner({"rev-parse": (0, "dev\n"), "status": (0, "M a.py\n?? b.py\n")})
        monkeypatch.setattr(status.subprocess, "run", runner)
        monkeypatch.setattr(status.shutil, "which", lambda x: "/usr/bin/git")
        assert status.git_summary("/repo") == "dev ✗2"

    def test_not_a_repo_returns_none(self, monkeypatch):
        runner = FakeRunner({"rev-parse": (128, "")})
        monkeypatch.setattr(status.subprocess, "run", runner)
        monkeypatch.setattr(status.shutil, "which", lambda x: "/usr/bin/git")
        assert status.git_summary("/repo") is None

    def test_no_git_returns_none(self, monkeypatch):
        monkeypatch.setattr(status.shutil, "which", lambda x: None)
        assert status.git_summary("/repo") is None


class TestNodeVersion:
    def test_strips_leading_v(self, monkeypatch):
        runner = FakeRunner({"--version": (0, "v22.11.0\n")})
        monkeypatch.setattr(status.subprocess, "run", runner)
        monkeypatch.setattr(status.shutil, "which", lambda x: "/usr/bin/node")
        assert status.node_version() == "node 22.11.0"

    def test_missing_node_returns_none(self, monkeypatch):
        monkeypatch.setattr(status.shutil, "which", lambda x: None)
        assert status.node_version() is None


class TestPythonVersion:
    def test_matches_sys_version(self):
        assert status.python_version().startswith("py 3.")


class TestTokenTracker:
    def test_display_before_first_call(self):
        t = status.TokenTracker()
        assert t.display.startswith("ctx –/")

    def test_update_and_display(self):
        t = status.TokenTracker()
        t.update(SimpleNamespace(input_tokens=9400, output_tokens=3000))
        assert t.display == "ctx 12.4k/50k"

    def test_update_with_none_is_noop(self):
        t = status.TokenTracker()
        t.update(None)
        assert t.display.startswith("ctx –/")


class TestTTLCache:
    def test_recomputes_after_ttl(self):
        calls = []

        def fn():
            calls.append(1)
            return len(calls)

        cache = status._TTLCache(2.0)
        assert cache.get(fn, now=0.0) == 1
        assert cache.get(fn, now=1.9) == 1  # cached
        assert cache.get(fn, now=2.1) == 2  # expired, recomputed


class TestCollectStatus:
    def test_segments_ordered_and_none_omitted(self, monkeypatch):
        monkeypatch.setattr(status, "tracker", status.TokenTracker())
        monkeypatch.setattr(status, "_git_cache", status._TTLCache(0.0))
        monkeypatch.setattr(status, "_node_cache", status._TTLCache(0.0))
        monkeypatch.setattr(status, "git_summary", lambda w, timeout=2.0: None)
        monkeypatch.setattr(status, "node_version", lambda timeout=2.0: "node 22.11.0")
        monkeypatch.setattr(status, "cron_count", lambda: 0)
        segments = status.collect_status(Path("/repo"), state="thinking")
        assert segments[0].startswith("ctx ")
        assert segments[1] == "thinking"
        assert segments[2] == "/repo"
        assert any(s.startswith("py 3.") for s in segments)
        assert any(s.startswith("node ") for s in segments)
        # git was None and cron was 0 -> both omitted
        assert not any("git" in s for s in segments)
        assert not any("cron" in s for s in segments)

    def test_cron_segment_when_jobs_queued(self, monkeypatch):
        monkeypatch.setattr(status, "tracker", status.TokenTracker())
        monkeypatch.setattr(status, "_git_cache", status._TTLCache(0.0))
        monkeypatch.setattr(status, "_node_cache", status._TTLCache(0.0))
        monkeypatch.setattr(status, "git_summary", lambda w, timeout=2.0: "main ✓")
        monkeypatch.setattr(status, "node_version", lambda timeout=2.0: None)
        monkeypatch.setattr(status, "cron_count", lambda: 3)
        segments = status.collect_status(Path("/repo"))
        assert segments[-1] == "3 cron"
```

Note: `monkeypatch.setattr(status, "git_summary", ...)` rebinds the name inside `status`, which is where `collect_status` looks it up — that is why `collect_status` must call the module-level names, not imported aliases.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_status.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.status'`

- [ ] **Step 3: Write the implementation**

Create `harness/status.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_status.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add harness/status.py tests/test_status.py
git commit -m "feat: status bar collectors — git/env/token/cron segments with TTL cache

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Render facade — TUI branch

**Files:**
- Modify: `harness/render.py` (keep the Rich/print branches byte-for-byte; add `_TUI_ACTIVE` branches)
- Test: `tests/test_render_facade.py`

**Interfaces:**
- Consumes: `bridge.emit` from Task 1.
- Produces (used by Tasks 5, 6, 8, 9):
  - `set_tui_active(active: bool) -> None`
  - `tui_active() -> bool`
  - `render_tool_result(tool_name: str, output: str, ok: bool = True) -> None` — new; TUI: activity event `✓ name` / `✗ name — detail`; plain mode: silent (results go to the model only).
  - `render_activity(text: str, style: str = "") -> None` — new; TUI: activity event; plain mode: silent.
  - Existing functions (`render_markdown`, `render_tool_use`, `render_error`, `render_info`, `render_inbox`, `render_banner`, `render_help`, `spinner`, `streaming_renderer`) gain TUI branches. Mapping: tool use + inbox → activity; everything else → chat; `spinner` emits `state` label on enter / `"idle"` on exit; `render_tool_use` additionally emits `state "running {tool}"`; `streaming_renderer` emits `stream` events (empty start, then accumulated text) and `clear_stream` at the end.

- [ ] **Step 1: Write the failing test**

Create `tests/test_render_facade.py`:

```python
"""Facade tests — TUI mode must emit events, plain mode must print."""
import pytest

from harness import render
from harness.ui_bridge import bridge


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    render.set_tui_active(False)
    bridge.drain()
    bridge.clear_abort()
    yield
    render.set_tui_active(False)
    bridge.drain()


def test_tui_render_markdown_emits_chat():
    render.set_tui_active(True)
    render.render_markdown("hello **world**")
    events = bridge.drain()
    assert len(events) == 1
    assert events[0].kind == "chat"
    assert events[0].payload == "hello **world**"
    assert events[0].style == "agent"


def test_tui_render_tool_use_emits_activity_and_state():
    render.set_tui_active(True)
    render.render_tool_use("bash", "ls -la")
    events = bridge.drain()
    kinds = [(e.kind, e.style) for e in events]
    assert ("activity", "tool") in kinds
    assert ("state", "") in kinds
    state_ev = [e for e in events if e.kind == "state"][0]
    assert state_ev.payload == "running bash"


def test_tui_render_error_and_info_go_to_chat():
    render.set_tui_active(True)
    render.render_error("boom")
    render.render_info("note")
    events = bridge.drain()
    assert all(e.kind == "chat" for e in events)
    assert events[0].style == "error"
    assert events[1].style == "info"


def test_tui_render_inbox_goes_to_activity():
    render.set_tui_active(True)
    render.render_inbox([{"from": "teammate", "type": "message", "content": "hi"}])
    events = bridge.drain()
    assert events[0].kind == "activity"
    assert events[0].style == "inbox"
    assert "teammate" in events[0].payload


def test_tui_render_tool_result_ok_and_fail():
    render.set_tui_active(True)
    render.render_tool_result("bash", "output", ok=True)
    render.render_tool_result("bash", "Error: boom", ok=False)
    events = bridge.drain()
    assert any(e.style == "ok" and "✓ bash" in e.payload for e in events)
    assert any(e.style == "fail" and "✗ bash" in e.payload for e in events)


def test_tui_render_activity():
    render.set_tui_active(True)
    render.render_activity("⏳ background", style="running")
    events = bridge.drain()
    assert events[0].kind == "activity"
    assert events[0].style == "running"


def test_plain_render_markdown_prints(capsys):
    render.set_tui_active(False)
    render.render_markdown("plain text")
    assert "plain text" in capsys.readouterr().out


def test_plain_render_tool_result_and_activity_are_silent(capsys):
    render.set_tui_active(False)
    render.render_tool_result("bash", "x", ok=True)
    render.render_activity("hello")
    assert capsys.readouterr().out == ""


def test_tui_spinner_emits_state_enter_and_exit():
    render.set_tui_active(True)
    with render.spinner("Thinking..."):
        pass
    events = bridge.drain()
    states = [e.payload for e in events if e.kind == "state"]
    assert states == ["Thinking...", "idle"]


def test_tui_streaming_renderer_emits_incremental():
    render.set_tui_active(True)
    with render.streaming_renderer() as r:
        r("Hel")
        r("Hello")
    events = bridge.drain()
    streams = [e for e in events if e.kind == "stream"]
    assert streams[0].payload == ""          # block opens empty
    assert streams[-1].payload == "Hello"    # accumulated text
    assert events[-1].kind == "clear_stream"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_render_facade.py -v`
Expected: FAIL — `AttributeError: module 'harness.render' has no attribute 'set_tui_active'`

- [ ] **Step 3: Write the implementation**

Modify `harness/render.py`. Keep all existing functions and imports; add `from harness.ui_bridge import bridge` near the top; add these functions:

```python
_TUI_ACTIVE = False


def set_tui_active(active: bool) -> None:
    """Switch rendering between TUI events (True) and Rich/print (False)."""
    global _TUI_ACTIVE
    _TUI_ACTIVE = active


def tui_active() -> bool:
    return _TUI_ACTIVE


def render_tool_result(tool_name: str, output: str, ok: bool = True) -> None:
    """Activity-pane result marker. Plain mode stays silent (results go to the model)."""
    if not _TUI_ACTIVE:
        return
    if ok:
        bridge.emit("activity", f"✓ {tool_name}", style="ok")
    else:
        detail = " ".join(str(output)[:120].splitlines())
        bridge.emit("activity", f"✗ {tool_name} — {detail}", style="fail")


def render_activity(text: str, style: str = "") -> None:
    """Append a line to the activity pane (TUI) — no-op in plain mode."""
    if _TUI_ACTIVE:
        bridge.emit("activity", text, style=style)
```

Then add a TUI branch at the top of each existing function. Example for `render_markdown`:

```python
def render_markdown(content: str) -> None:
    if _TUI_ACTIVE:
        bridge.emit("chat", content, style="agent")
        return
    # ... existing body unchanged ...
```

The full branch table:

| Function | TUI branch |
|---|---|
| `render_banner(model, workdir)` | `bridge.emit("chat", f"51agent — {model}\nWorkdir: {workdir}", style="banner"); return` |
| `render_help()` | `bridge.emit("chat", <the plain-ASCII rabbit text used in its current else-branch>, style="help"); return` |
| `render_markdown(content)` | `bridge.emit("chat", content, style="agent"); return` |
| `render_tool_use(tool_name, tool_input)` | `bridge.emit("activity", f"{tool_name}: {tool_input[:160]}", style="tool"); bridge.emit("state", f"running {tool_name}"); return` |
| `render_error(message)` | `bridge.emit("chat", message, style="error"); return` |
| `render_info(message)` | `bridge.emit("chat", message, style="info"); return` |
| `render_inbox(messages)` | build the same per-message lines as the else-branch, join, `bridge.emit("activity", text, style="inbox"); return` |
| `spinner(label)` | `if _TUI_ACTIVE: bridge.emit("state", label); try: yield; finally: bridge.emit("state", "idle"); return` (keep the existing generator/contextmanager shape) |
| `streaming_renderer()` | `if _TUI_ACTIVE: buf=[]; bridge.emit("stream", "", style="agent"); def _add(text): buf.append(text); bridge.emit("stream", "".join(buf), style="agent"); yield _add; bridge.emit("clear_stream"); return` (keep existing plain branch untouched) |

Do not change `use_color()` or `prompt()`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_render_facade.py -v`
Expected: PASS (10 tests). Also run the existing suite to confirm no regression: `uv run pytest -q` (all green — the facade is inactive by default).

- [ ] **Step 5: Commit**

```bash
git add harness/render.py tests/test_render_facade.py
git commit -m "feat: render facade — TUI event branches alongside Rich/print fallback

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Bash abort support

**Files:**
- Modify: `tools/__init__.py` (only `run_bash`)
- Test: extend `tests/test_tools.py`

**Interfaces:**
- Consumes: `bridge.is_abort_requested()` from Task 1.
- Produces: `run_bash(command, cwd=None) -> str` — same signature and semantics (300s deadline, 50k cap, "(no output)"); additionally returns `"[aborted]"` (plus up to 2000 chars of partial output) when the abort flag is set. Internal helper `_kill_tree(proc)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools.py`:

```python
import threading
import time as _time

from harness.ui_bridge import bridge


class TestBashAbort:
    def test_abort_kills_long_command(self):
        bridge.clear_abort()
        result = {}

        def worker():
            result["value"] = run_bash("sleep 5 && echo done")

        t = threading.Thread(target=worker)
        t.start()
        _time.sleep(0.5)  # let the subprocess start
        bridge.request_abort()
        t.join(timeout=5)
        assert not t.is_alive(), "run_bash did not abort within 5s"
        assert result["value"] == "[aborted]"
        bridge.clear_abort()

    def test_normal_execution_unchanged(self):
        bridge.clear_abort()
        assert run_bash("echo hello") == "hello"

    def test_normal_execution_unchanged(self):
        bridge.clear_abort()
        assert run_bash("echo hello") == "hello"
```

Note: `run_bash` is already imported at the top of `tests/test_tools.py` (`from tools import ...` — check the existing import line and match it). The 300s timeout path is unchanged from the old `subprocess.run(timeout=300)` semantics; do not add a test that sleeps 300 seconds.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tools.py::TestBashAbort -v`
Expected: FAIL — the worker thread finishes `sleep 5` normally under the old implementation, so `result["value"]` is `"done"` and the `"[aborted]"` assertion fails (the run takes ~5s).

- [ ] **Step 3: Write the implementation**

In `tools/__init__.py`: add imports (`os as _os`, `signal as _signal`, `time as _time`) and `from harness.ui_bridge import bridge`. Replace `run_bash` with:

```python
def _kill_tree(proc: _subprocess.Popen) -> None:
    """Kill the process group (shell + children), falling back to the process."""
    try:
        _os.killpg(_os.getpgid(proc.pid), _signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def run_bash(command: str, cwd: str = None) -> str:
    try:
        proc = _subprocess.Popen(
            command, shell=True, cwd=cwd or str(WORKDIR),
            stdout=_subprocess.PIPE, stderr=_subprocess.PIPE, text=True,
            start_new_session=True)
    except Exception as e:
        return f"Error: {e}"

    deadline = _time.monotonic() + 300
    while True:
        if bridge.is_abort_requested():
            _kill_tree(proc)
            out, err = proc.communicate()
            partial = (out + err).strip()[:2000]
            return f"[aborted]\n{partial}" if partial else "[aborted]"
        try:
            out, err = proc.communicate(timeout=0.1)
            break
        except _subprocess.TimeoutExpired:
            if _time.monotonic() > deadline:
                _kill_tree(proc)
                proc.communicate()
                return "Error: Timeout (300s)"

    combined = (out + err).strip()
    return combined[:50000] if combined else "(no output)"
```

`subprocess.communicate(timeout=…)` raises `TimeoutExpired` without killing the process, so it can be called repeatedly until the process exits.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tools.py -v`
Expected: PASS (both abort tests: the abort one finishes in ~1s, the normal one unchanged). Then `uv run pytest -q` — full suite green.

- [ ] **Step 5: Commit**

```bash
git add tools/__init__.py tests/test_tools.py
git commit -m "feat: bash handler honors Ctrl+C abort flag, kills process group

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: Loop integration — abort checks, usage tracking, activity events

**Files:**
- Modify: `loop.py`
- Test: extend `tests/test_loop.py`

**Interfaces:**
- Consumes: `TurnAborted` + `bridge` (Task 1), `tracker` (Task 2), `render_tool_result` / `render_activity` (Task 3).
- Produces:
  - `_sleep_interruptible(seconds: float) -> None` — sleeps in 0.1s slices, raises `TurnAborted` early if the abort flag is set.
  - `_stream_llm(...)` — unchanged signature; raises `TurnAborted` on abort; records final-message `usage` into `tracker`.
  - `agent_loop_full(...)` — unchanged signature; catches `TurnAborted` (renders "Interrupted." and returns); renders tool results into the activity pane and background notifications.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_loop.py`:

```python
import types
import threading
import time as _time

import pytest

from harness.ui_bridge import TurnAborted, bridge
from harness.recovery import RecoveryState


class TestSleepInterruptible:
    def test_raises_when_abort_requested(self):
        bridge.request_abort()
        start = _time.monotonic()
        with pytest.raises(TurnAborted):
            from loop import _sleep_interruptible
            _sleep_interruptible(5.0)
        assert _time.monotonic() - start < 2.0
        bridge.clear_abort()

    def test_sleeps_when_no_abort(self):
        bridge.clear_abort()
        from loop import _sleep_interruptible
        start = _time.monotonic()
        _sleep_interruptible(0.2)
        assert _time.monotonic() - start >= 0.2


class TestStreamAbort:
    def test_stream_raises_turn_aborted_mid_chunks(self, monkeypatch):
        from harness import loop as loop_mod
        from loop import _stream_llm

        class Evt:
            def __init__(self):
                self.type = "content_block_delta"
                self.delta = types.SimpleNamespace(type="text_delta", text="x")

        class FakeStream:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def __iter__(self):
                for _ in range(50):
                    yield Evt()
                raise AssertionError("stream should have aborted")

            def get_final_message(self):
                return None

        monkeypatch.setattr(
            loop_mod.provider, "create_message_stream", lambda **kw: FakeStream())
        monkeypatch.setattr(
            loop_mod, "streaming_renderer", loop_mod.streaming_renderer)
        bridge.request_abort()
        with pytest.raises(TurnAborted):
            _stream_llm([], [], RecoveryState(), "sys", 100)
        bridge.clear_abort()
```

Careful with the `streaming_renderer` import in `loop.py` — the mock must patch the name `loop.py` looks up (it currently imports `streaming_renderer` into its module namespace, so patching `loop_mod.streaming_renderer` is correct).

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_loop.py -v`
Expected: FAIL — `ImportError: cannot import name '_sleep_interruptible'` (and the stream abort test fails after).

- [ ] **Step 3: Write the implementation**

In `loop.py`, update the imports:

```python
from harness.render import (
    render_activity,
    render_error,
    render_info,
    render_tool_result,
    streaming_renderer,
)
from harness.status import tracker
from harness.ui_bridge import TurnAborted, bridge
```

Add after the imports:

```python
def _sleep_interruptible(seconds: float) -> None:
    """Sleep in 0.1s slices, aborting early on Ctrl+C."""
    deadline = _time.monotonic() + seconds
    while _time.monotonic() < deadline:
        if bridge.is_abort_requested():
            raise TurnAborted()
        _time.sleep(min(0.1, deadline - _time.monotonic()))
```

In `_stream_llm`:
1. Inside the `for event in event_stream:` loop, before the `content_block_delta` check, add:
   `if bridge.is_abort_requested(): raise TurnAborted()`
2. After the stream exits, before `return`, record usage and return:

```python
                msg = event_stream.get_final_message()
                tracker.update(getattr(msg, "usage", None))
                return msg
```

(This replaces the current `return event_stream.get_final_message()`.)
3. In the retry handler, replace `_time.sleep(delay)` with `_sleep_interruptible(delay)` — so a Ctrl+C during a retry backoff also aborts.

In `agent_loop_full`, add `TurnAborted` handling right after the existing `except KeyboardInterrupt`:

```python
        try:
            response = _stream_llm(messages, tools, state, system, max_tokens)
        except TurnAborted:
            render_info("Interrupted.")
            return
        except KeyboardInterrupt:
```

And in the tool-execution loop (inside `for block in response.content:`):

- After the blocked-permission branch's `results.append(...)`, add:
  `render_tool_result(block.name, str(blocked), ok=False)`
- In the background-dispatch branch, after `results.append(...)`, add:
  `render_activity(f"⏳ {block.name} → background ({bg_id})", style="running")`
- In the normal-execution branch, after `results.append(...)`, add:

```python
            ok = not (str(output).startswith("Error")
                      or str(output).startswith("Permission denied"))
            render_tool_result(block.name, str(output), ok=ok)
```

- Right after `bg_notifications = collect_background_results()`, add:

```python
        for n in bg_notifications:
            render_activity(" ".join(n.splitlines())[:160], style="bg")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_loop.py -v`
Expected: PASS (existing + new tests). Then run `uv run pytest -q` — full suite must stay green.

- [ ] **Step 5: Commit**

```bash
git add loop.py tests/test_loop.py
git commit -m "feat: loop aborts on Ctrl+C, tracks usage, renders tool results to activity

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Permission prompts via the pending question

**Files:**
- Modify: `harness/permissions.py` (only `_prompt_user`)
- Test: extend `tests/test_permissions.py`

**Interfaces:**
- Consumes: `tui_active()` (Task 3), `bridge.ask_question` (Task 1).
- Produces: `_prompt_user(tool_name, detail="") -> bool` — same signature/behavior; in TUI mode blocks on `bridge.ask_question(msg, default=False, timeout=300)` instead of `input()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_permissions.py`:

```python
import threading
import time as _time

from harness import render
from harness.permissions import _prompt_user
from harness.ui_bridge import bridge


class TestPromptUserTui:
    def test_tui_prompt_answered_yes(self, monkeypatch):
        monkeypatch.setattr(render, "_TUI_ACTIVE", True)

        def ui():
            while not bridge.has_pending_question():
                _time.sleep(0.01)
            bridge.answer_question("y")

        t = threading.Thread(target=ui)
        t.start()
        assert _prompt_user("bash") is True
        t.join()
        bridge.drain()

    def test_tui_prompt_empty_denies(self, monkeypatch):
        monkeypatch.setattr(render, "_TUI_ACTIVE", True)

        def ui():
            while not bridge.has_pending_question():
                _time.sleep(0.01)
            bridge.answer_question("")

        t = threading.Thread(target=ui)
        t.start()
        assert _prompt_user("bash") is False
        t.join()
        bridge.drain()
```

(If `test_permissions.py` already has a fixture pattern, follow it; the monkeypatch of `render._TUI_ACTIVE` is required because `permissions.py` must read the flag through a function, not a captured copy.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_permissions.py -v`
Expected: FAIL — the tests hang would be a failure too, but concretely the old `_prompt_user` calls `input()`, which reads stdin in pytest (EOF) → returns False → first test's `assert True` fails.

- [ ] **Step 3: Write the implementation**

In `harness/permissions.py`, change the import line to include the flag function:

```python
from harness.render import render_info, render_tool_use, tui_active
```

Replace `_prompt_user` with:

```python
def _prompt_user(tool_name: str, detail: str = "") -> bool:
    """Ask the user to approve a tool. Returns True if approved."""
    msg = f"Allow {tool_name}?"
    if detail:
        msg += f" [{detail[:80]}]"
    msg += " (y/N)"
    if tui_active():
        from harness.ui_bridge import bridge

        return bridge.ask_question(msg, default=False, timeout=300)
    try:
        decision = input(msg).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("  [auto-denied]")
        return False
    return decision in ("y", "yes")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_permissions.py -v`
Expected: PASS. Then `uv run pytest -q` — full suite green.

- [ ] **Step 5: Commit**

```bash
git add harness/permissions.py tests/test_permissions.py
git commit -m "feat: permission prompts route through the TUI pending question

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: TUI core — layout, buffers, event application

**Files:**
- Create: `harness/tui.py`
- Test: `tests/test_tui.py`

**Interfaces:**
- Consumes: `bridge` + `Event` (Task 1), `collect_status` (Task 2), `get_plan_state` (existing `harness.plan`), `config.MODEL/WORKDIR`.
- Produces (used by Tasks 8 and 9):
  - `AGENT_COMMANDS: list[str]`, `AGENT_TOOLS: list[str]` — the completer word lists (moved here from `agent.py`).
  - `apply_events(events: list[Event], chat: Buffer, activity: Buffer) -> str` — consumes one batch; returns the new state text. Pure — operates on plain prompt_toolkit `Buffer` objects, unit-testable headless.
  - `_header_text() -> list[tuple[str, str]]`, `_status_text() -> list[tuple[str, str]]` — formatted-text callbacks.
  - `_build_app() -> Application` — the full-screen app (layout, buffers, key bindings, styles). Takes no arguments; reads/writes module globals.
  - `_reset_for_tests() -> None` — clears module state (chat lines, state text, inflight flag).
  - Module globals `_state: str`, `_turn_running: bool`, `_chat_buffer: Buffer | None`, `_activity_buffer: Buffer | None`, `_input_buffer: Buffer | None` — maintained by `apply_events` / `_build_app` / Task 8.

Note on imports: `harness/tui.py` must **not** import `agent.py` (Task 9 passes `handle_input` in as `on_line`) — this breaks the dependency cycle.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tui.py`:

```python
"""TUI unit tests — headless; no terminal, no Application.run()."""
from prompt_toolkit.buffer import Buffer

from harness import tui
from harness.ui_bridge import Event


def test_apply_events_chat_and_stream():
    tui._reset_for_tests()
    chat, activity = Buffer(read_only=True), Buffer(read_only=True)
    events = [
        Event("chat", "You: hi"),
        Event("stream", "Hel"),
        Event("stream", "Hello"),
        Event("clear_stream"),
        Event("activity", "✓ bash", "ok"),
        Event("state", "running bash"),
    ]
    state = tui.apply_events(events, chat, activity)
    assert "You: hi" in chat.text
    assert "Hello" in chat.text
    assert "✓ bash" in activity.text
    assert state == "running bash"


def test_stream_block_lands_on_one_line():
    tui._reset_for_tests()
    chat, activity = Buffer(read_only=True), Buffer(read_only=True)
    tui.apply_events(
        [Event("stream", "a"), Event("stream", "ab"), Event("clear_stream")],
        chat, activity)
    assert chat.text.splitlines() == ["ab"]
    tui.apply_events([Event("chat", "next")], chat, activity)
    assert chat.text.splitlines() == ["ab", "next"]


def test_autoscroll_follows_only_when_at_bottom():
    tui._reset_for_tests()
    chat = Buffer(read_only=True)
    activity = Buffer(read_only=True)
    chat.text = "line1\nline2\nline3"
    chat.cursor_position = 0  # user scrolled up
    tui.apply_events([Event("chat", "line4")], chat, activity)
    assert chat.cursor_position == 0  # stay put
    chat.cursor_position = len(chat.text)  # user at bottom
    tui.apply_events([Event("chat", "line5")], chat, activity)
    assert chat.cursor_position == len(chat.text)


def test_question_events_prefixed_in_activity():
    tui._reset_for_tests()
    chat, activity = Buffer(read_only=True), Buffer(read_only=True)
    tui.apply_events([Event("question", "Allow bash? (y/N)")], chat, activity)
    assert "? Allow bash? (y/N)" in activity.text


def test_header_includes_model_and_state(monkeypatch):
    tui._reset_for_tests()
    tui._state = "thinking"
    text = tui._header_text()
    joined = " ".join(t for _, t in text)
    assert "51agent" in joined
    assert "thinking" in joined


def test_status_text_joins_segments(monkeypatch):
    monkeypatch.setattr(
        tui, "collect_status", lambda workdir, state: ["seg1", "seg2"])
    text = tui._status_text()
    assert "seg1 │ seg2" in text[0][1]


def test_build_app_returns_full_screen_app():
    tui._reset_for_tests()
    app = tui._build_app()
    assert app.full_screen is True
    assert app.layout is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tui.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness.tui'`

- [ ] **Step 3: Write the implementation**

Create `harness/tui.py` (version 1 — Task 8 adds the run loop):

```python
"""Full-screen TUI — prompt_toolkit Application.

Layout (top to bottom):
    header   — model, agent state, plan phase
    chat     — conversation (read-only buffer)
    activity — tool calls, questions, background/cron notes (read-only buffer)
    input    — footer prompt buffer (history + completers)
    status   — context usage, workdir, git, python, node, cron

Threading model (completed in Task 8): agent turns run in a worker
thread; render events arrive on the bridge queue and are consumed by
an asyncio background task inside the application loop.
"""
from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import PathCompleter, WordCompleter, merge_completers
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.styles import Style

from config import MODEL, WORKDIR
from harness.plan import get_plan_state
from harness.status import collect_status
from harness.ui_bridge import Event, bridge

AGENT_COMMANDS = ["q", "exit", "quit", "?"]
AGENT_TOOLS = [
    "bash", "read_file", "write_file", "edit_file", "glob",
    "todo_write", "structured_output", "task", "load_skill", "compact",
    "create_task", "list_tasks", "get_task", "claim_task", "complete_task",
    "schedule_cron", "list_crons", "cancel_cron",
    "spawn_teammate", "send_message", "check_inbox",
    "request_shutdown", "request_plan", "review_plan",
    "create_worktree", "remove_worktree", "keep_worktree",
    "connect_mcp",
]

_HELP_TEXT = r"""
      (\_/)
      ( -.-)
      o_(")(")
       ╰─ 51agent
  Type '?' for help, 'q' to quit. /plan <task> enters plan mode.
"""

_STYLE = Style.from_dict({
    "prompt": "cyan bold",
    "user": "bold cyan",
    "agent": "",
    "info": "dim",
    "error": "bold red",
    "help": "yellow",
    "tool": "bold yellow",
    "ok": "green",
    "fail": "red",
    "running": "yellow",
    "inbox": "magenta",
    "question": "bold cyan",
    "bg": "dim",
    "cron": "dim",
    "status": "reverse",
    "header": "bold",
})

# -- module state (owned by apply_events / the run loop) --------------------
_state = "idle"            # last state event payload
_turn_running = False
_chat_lines: list[str] = []
_activity_lines: list[str] = []
_inflight = False          # last chat entry is a live stream block
_input_buffer: Buffer | None = None
_chat_buffer: Buffer | None = None
_activity_buffer: Buffer | None = None


def _reset_for_tests() -> None:
    global _state, _turn_running, _chat_lines, _activity_lines, _inflight
    _state = "idle"
    _turn_running = False
    _chat_lines = []
    _activity_lines = []
    _inflight = False


def _append_chat(text: str) -> None:
    global _inflight
    if _inflight:
        _chat_lines[-1] = text
        _inflight = False
    else:
        _chat_lines.append(text)


def _update_stream(text: str) -> None:
    global _inflight
    if not _inflight:
        _chat_lines.append("")
        _inflight = True
    _chat_lines[-1] = text


def _commit_stream() -> None:
    global _inflight
    _inflight = False
    if _chat_lines and not _chat_lines[-1].strip():
        _chat_lines.pop()


def _follow_if_at_bottom(buf: Buffer) -> bool:
    """True when the buffer view was already at the end (auto-scroll allowed)."""
    return buf.cursor_position >= len(buf.text)


def apply_events(events: list[Event], chat: Buffer, activity: Buffer) -> str:
    """Consume one batch of bridge events into the buffers.

    Pure — operates on plain Buffer objects, unit-testable headless.
    Returns the new agent-state text.
    """
    global _state
    chat_follow = _follow_if_at_bottom(chat)
    for ev in events:
        if ev.kind == "chat":
            _append_chat(ev.payload)
        elif ev.kind == "stream":
            _update_stream(ev.payload)
        elif ev.kind == "clear_stream":
            _commit_stream()
        elif ev.kind == "activity":
            _activity_lines.append(ev.payload)
        elif ev.kind == "question":
            _activity_lines.append(f"? {ev.payload}")
        elif ev.kind == "state":
            _state = ev.payload
    chat.text = "\n".join(_chat_lines)
    if chat_follow:
        chat.cursor_position = len(chat.text)
    activity.text = "\n".join(_activity_lines)
    activity.cursor_position = len(activity.text)
    return _state


# -- header / status formatted text ----------------------------------------
def _header_text() -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = [("class:header", f" 51agent · {MODEL} ")]
    plan = get_plan_state()
    if plan.phase != "idle":
        parts.append(("class:info", f" [{plan.phase}] "))
    parts.append(("class:info", f" · {_state} "))
    return parts


def _status_text() -> list[tuple[str, str]]:
    segments = collect_status(WORKDIR, state=_state)
    return [("class:status", " " + " │ ".join(segments) + " ")]


# -- input buffer -----------------------------------------------------------
def _make_input_buffer() -> Buffer:
    completer = merge_completers([
        WordCompleter(AGENT_COMMANDS + AGENT_TOOLS, ignore_case=True, sentence=True),
        PathCompleter(expanduser=True),
    ])
    try:
        history = FileHistory(str(WORKDIR / ".agent_history"))
    except Exception:
        history = InMemoryHistory()
    buf = Buffer(multiline=True, completer=completer, history=history)

    def _accept(b: Buffer) -> bool:
        line = b.text
        b.reset()
        from harness.tui import _route_line  # defined in Task 8
        _route_line(line)
        return True

    buf.accept_handler = _accept
    return buf


def _input_prefix(_window, _line_number):
    return [("class:prompt", "51agent >> ")]


# -- key bindings -----------------------------------------------------------
_kb = KeyBindings()


@_kb.add("enter")
def _submit(event):
    if event.current_buffer is _input_buffer:
        event.current_buffer.validate_and_handle()


@_kb.add("escape", "enter")
def _newline(event):
    event.current_buffer.insert_text("\n")


@_kb.add("c-c")
def _ctrl_c(event):
    if bridge.has_pending_question():
        bridge.answer_question("")
        event.app.invalidate()
    elif _turn_running:
        bridge.request_abort()
        event.app.invalidate()
    else:
        event.app.exit()


@_kb.add("tab")
def _focus_next(event):
    event.app.layout.focus_next()


# -- application ------------------------------------------------------------
def _build_app() -> Application:
    """Build the full-screen application (no arguments — module globals)."""
    global _chat_buffer, _activity_buffer, _input_buffer
    _chat_buffer = Buffer(read_only=True)
    _activity_buffer = Buffer(read_only=True)
    _input_buffer = _make_input_buffer()

    header_window = Window(FormattedTextControl(_header_text), height=1,
                           style="class:header")
    divider = Window(height=1, char="─", style="class:info")
    chat_window = Window(BufferControl(_chat_buffer, focusable=True))
    activity_title = Window(
        FormattedTextControl([("class:info", "─ Activity ─")]), height=1)
    activity_window = Window(BufferControl(_activity_buffer, focusable=True),
                             height=8)
    input_window = Window(
        BufferControl(_input_buffer),
        get_line_prefix=_input_prefix,
        wrap_lines=True)
    status_window = Window(FormattedTextControl(_status_text), height=1,
                           style="class:status")

    root = HSplit([
        header_window,
        divider,
        chat_window,
        activity_title,
        activity_window,
        divider,
        input_window,
        status_window,
    ])

    app = Application(
        layout=Layout(root),
        key_bindings=_kb,
        style=_STYLE,
        full_screen=True,
        mouse_support=False,
    )
    app.layout.focus(input_window)
    return app
```

The import inside `_accept` (`from harness.tui import _route_line`) is a function-local import so version 1 of this module imports cleanly before Task 8 adds `_route_line`. Task 8 replaces it with a plain reference.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tui.py -v`
Expected: PASS (7 tests). Then `uv run pytest -q` — full suite green.

- [ ] **Step 5: Commit**

```bash
git add harness/tui.py tests/test_tui.py
git commit -m "feat: TUI core — full-screen layout, panes, key bindings, event application

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: TUI run loop — routing, consumer, startup, resume

**Files:**
- Modify: `harness/tui.py` (extend the Task 7 file)
- Test: extend `tests/test_tui.py`
- Test: `tests/test_tui_route.py` (new — routing branch tests)

**Interfaces:**
- Consumes: `bridge` (Task 1), `collect_status` (Task 2), `set_tui_active` (Task 3), `find_latest_session` / `load_session` (existing `harness.session`), everything from Task 7.
- Produces:
  - `_route_line(line: str) -> None` — footer accept routing: pending question → answer; busy → note; else worker thread runs `on_line(line, history)`.
  - `run_tui(history: list, on_line) -> None` — sets TUI active, emits startup banner/help, asks the resume question from a worker thread, starts the event consumer and status refresher, runs the app, and restores plain rendering on exit.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tui_route.py`:

```python
"""Route-line branching — headless, using the real bridge singleton."""
import threading
import time as _time

from harness import tui
from harness.ui_bridge import bridge


def _wait_for_question():
    while not bridge.has_pending_question():
        _time.sleep(0.01)


def test_route_line_answers_pending_question():
    tui._reset_for_tests()
    got = {}

    def asker():
        got["answer"] = bridge.ask_question("Allow bash?", timeout=5)

    t = threading.Thread(target=asker)
    t.start()
    _wait_for_question()
    tui._route_line("y")
    t.join()
    assert got["answer"] is True
    bridge.drain()


def test_route_line_busy_ignores_input(monkeypatch):
    tui._reset_for_tests()
    tui._turn_running = True
    tui._route_line("hello")
    events = bridge.drain()
    assert any("busy" in e.payload for e in events)


def test_route_line_empty_line_answers_default():
    tui._reset_for_tests()
    got = {}

    def asker():
        got["answer"] = bridge.ask_question("Resume session? (y/N)", timeout=5)

    t = threading.Thread(target=asker)
    t.start()
    _wait_for_question()
    tui._route_line("")
    t.join()
    assert got["answer"] is False
    bridge.drain()
```

And append to `tests/test_tui.py` (also add `import pytest` to that file's imports, alongside the existing `from prompt_toolkit.buffer import Buffer`):

```python
def test_run_tui_requires_tty(monkeypatch):
    monkeypatch.setattr(tui.sys, "stdin", tui.sys.stdout)
    with pytest.raises(RuntimeError):
        tui.run_tui([], on_line=lambda q, h: True)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_tui_route.py tests/test_tui.py -v`
Expected: FAIL — `AttributeError: module 'harness.tui' has no attribute '_route_line'`

- [ ] **Step 3: Write the implementation**

Extend `harness/tui.py`. Changes:

1. Imports: add `import asyncio`, `import sys`, `import threading`, `import time as _time`, `from prompt_toolkit.application import get_app`, `from harness import render as _render`, `from harness.session import find_latest_session, load_session`, and drop the unused `AGENT_COMMANDS`-related duplication (none — they stay).

2. In `_make_input_buffer`, replace the function-local import:

```python
    def _accept(b: Buffer) -> bool:
        line = b.text
        b.reset()
        _route_line(line)
        return True
```

3. Add the routing, consumer, and startup code:

```python
# -- input routing ----------------------------------------------------------
_history: list = []
_on_line = None
_app: Application | None = None


def _route_line(line: str) -> None:
    """Footer accept handler — answer a pending question, or start a turn."""
    global _turn_running
    if bridge.has_pending_question():
        bridge.answer_question(line)
        return
    if _turn_running:
        bridge.emit("chat", "⏳ agent busy — wait for the current turn to finish",
                    style="info")
        return

    _turn_running = True
    bridge.clear_abort()
    bridge.emit("chat", line, style="user")

    def worker():
        global _turn_running
        try:
            ok = _on_line(line, _history)
            if not ok:
                _app.call_from_executor(_app.exit)
        except Exception as e:
            bridge.emit("chat", f"✗ turn crashed: {e}", style="error")
        finally:
            _turn_running = False
            bridge.clear_abort()

    threading.Thread(target=worker, daemon=True, name="turn").start()


# -- application background tasks -------------------------------------------
async def _event_consumer() -> None:
    while True:
        events = bridge.drain()
        if events:
            apply_events(events, _chat_buffer, _activity_buffer)
            get_app().invalidate()
        await asyncio.sleep(0.05)


async def _status_loop() -> None:
    """Re-render once a second so header/status pick up new state."""
    while True:
        get_app().invalidate()
        await asyncio.sleep(1.0)


# -- startup ----------------------------------------------------------------
def run_tui(history: list, on_line) -> None:
    """Start the full-screen app. Caller guarantees stdin/stdout are TTYs."""
    global _app, _history, _on_line
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise RuntimeError("TUI requires a terminal (stdin and stdout must be TTYs)")

    _render.set_tui_active(True)
    _history = history
    _on_line = on_line
    _app = _build_app()

    # startup content — queued before the consumer starts; order preserved
    bridge.emit("chat", f"51agent — {MODEL}\nWorkdir: {WORKDIR}", style="banner")
    bridge.emit("chat", _HELP_TEXT, style="help")

    latest = find_latest_session()

    def _startup_worker():
        if latest and bridge.ask_question(
                f"Resume session from {_time.ctime(latest.stat().st_mtime)}? (y/N)"):
            loaded = load_session(latest)
            if loaded:
                history[:] = loaded
                bridge.emit("chat", f"Resumed session with {len(history)} messages.",
                            style="info")

    if latest:
        threading.Thread(target=_startup_worker, daemon=True, name="resume").start()

    _app.create_background_task(_event_consumer())
    _app.create_background_task(_status_loop())

    try:
        _app.run()
    finally:
        _render.set_tui_active(False)
```

4. `_build_app` takes no arguments (fixed in Task 7) — `run_tui` sets the `_history`/`_on_line` module globals before calling it, as shown above. No further change needed there.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_tui.py tests/test_tui_route.py -v`
Expected: PASS. Then `uv run pytest -q` — full suite green.

- [ ] **Step 5: Manual smoke (real terminal)**

Run `uv run python agent.py` and check:

1. Screen goes full-screen instantly: header (`51agent · {model} · idle`), chat pane, `─ Activity ─` pane, `51agent >> ` input, reverse-video status bar with `ctx –/50k │ <dir> │ <git> │ py 3.x │ node …`.
2. Status bar updates when the agent runs: state flips to `thinking`/`running bash`/`streaming`, `ctx` fills after the first response.
3. Type a short prompt ("say hi"), Enter — streamed reply appears incrementally in the chat pane.
4. Ask something that runs bash (`run: echo hello`) — tool call and ✓ marker appear in the activity pane; permission question appears; answering `y` proceeds, empty Enter denies.
5. Tab cycles focus input → chat → activity; PgUp/PgDn scrolls a focused pane; new output does not yank a scrolled-up chat pane.
6. Ctrl+C during a long `bash sleep 30` aborts the tool (`[aborted]`), input returns; Ctrl+C again at the idle prompt exits.
7. `q` exits and saves the session; rerun, answer `y` to the resume question — history restored into the chat pane.
8. Resize the terminal — layout adapts without corruption.

Also confirm plain mode: `echo q | uv run python agent.py` behaves exactly as before.

- [ ] **Step 6: Lint the changed files**

Run: `uv run ruff check harness/tui.py agent.py harness/render.py harness/permissions.py harness/ui_bridge.py harness/status.py loop.py tools/__init__.py`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add harness/tui.py tests/test_tui.py tests/test_tui_route.py
git commit -m "feat: TUI run loop — input routing, event consumer, status refresh, resume prompt

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 9: agent.py refactor — `handle_input`, plain loop, TTY dispatch

**Files:**
- Modify: `agent.py` (restructure; behavior of plain mode unchanged)
- Test: `tests/test_agent_input.py`

**Interfaces:**
- Consumes: `run_tui` + `AGENT_COMMANDS`/`AGENT_TOOLS` (Tasks 7 and 8 — keep the `run_tui` import inside the `__main__` block; the word lists import at module level), `render_activity` (Task 3).
- Produces:
  - `handle_input(query: str, history: list) -> bool` — one user line in, session-continue out. **No terminal I/O** except through `harness.render`. Called by both the plain loop (same thread) and the TUI worker thread.
  - `_run_plain_loop(history: list) -> None` — the pre-TUI scrollback flow, byte-for-byte equivalent to today's.
  - The `__main__` block dispatches: TTY → `run_tui(history, on_line=handle_input)`; non-TTY → `_run_plain_loop(history)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_input.py`:

```python
"""handle_input routing — no terminal, no network."""
from unittest.mock import MagicMock

import pytest

import agent


@pytest.fixture(autouse=True)
def _mocks(monkeypatch):
    monkeypatch.setattr(agent, "agent_loop_full", MagicMock())
    monkeypatch.setattr(agent, "save_session", MagicMock())
    agent.get_plan_state().reset()
    agent._agent_idle = True
    yield


def test_q_returns_false_and_saves():
    assert agent.handle_input("q", []) is False
    agent.save_session.assert_called_once()


def test_help_returns_true_without_turn():
    assert agent.handle_input("?", []) is True
    agent.agent_loop_full.assert_not_called()


def test_plain_query_appends_and_runs_once():
    history = []
    assert agent.handle_input("hello world", history) is True
    assert history[-1] == {"role": "user", "content": "hello world"}
    agent.agent_loop_full.assert_called_once()


def test_plan_approval_routes_decision():
    plan = agent.get_plan_state()
    plan.start_planning("write tests")
    plan.submit_plan("the plan")
    assert plan.phase == "awaiting_approval"
    assert agent.handle_input("y", []) is True
    assert plan.phase == "idle"  # approved -> executed (mocked) -> reset


def test_plan_rejection_routes_decision():
    plan = agent.get_plan_state()
    plan.start_planning("write tests")
    plan.submit_plan("the plan")
    assert agent.handle_input("n", []) is True
    assert plan.phase == "idle"
    agent.agent_loop_full.assert_not_called()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_agent_input.py -v`
Expected: FAIL — `AttributeError: module 'agent' has no attribute 'handle_input'` (importing `agent` itself works today).

- [ ] **Step 3: Write the implementation**

Rewrite `agent.py` to the structure below. Everything that exists today is preserved — the while-loop body becomes `handle_input`, and the startup/menu code becomes `_run_plain_loop`. Changes to watch:

1. Module-level imports gain: `from harness.render import render_activity` (add to the existing render import line), `from harness.tui import AGENT_COMMANDS, AGENT_TOOLS` (drop the local `_AGENT_COMMANDS`/`_AGENT_TOOLS` definitions), `from loop import agent_loop_full` (moved up from the `__main__` block).
2. `_deliver_cron_tasks` gains a render line inside the `for job in fired:` loop:
   `render_activity(f"[cron] {job.prompt[:60]}", style="cron")`
3. `handle_input` is the current loop body with `continue` → `return True` and `break` → `return False`, plus the cron render line in its `for job in _cron.consume_queue():` loop.
4. `_run_plain_loop` is the current startup + input loop, calling `handle_input`.
5. `__main__` starts the cron threads, then:

```python
    if sys.stdin.isatty() and sys.stdout.isatty():
        from harness.tui import run_tui

        run_tui(history, on_line=handle_input)
    else:
        _run_plain_loop(history)
```

The full expected file:

```python
#!/usr/bin/env python3
"""
agent.py — 51agent.

Usage:
    51agent                  # if installed globally
    uv run python agent.py   # from the repo (dev mode)

Config:
    Installed: ~/.51agent/settings.json
    Dev mode:  .env in the current directory
"""
import atexit
import sys
import threading
import time
from pathlib import Path

_script_dir = Path(__file__).resolve().parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from config import MODEL, WORKDIR
from harness import trigger_hooks
from harness.memory import inject_memories
from harness.permissions import install as install_permissions
from harness.plan import get_plan_state
from harness.render import (
    render_activity,
    render_banner,
    render_help,
    render_inbox,
    render_info,
    spinner,
)
from harness.session import find_latest_session, load_session, save_session
from harness.tool_pool import assemble_tool_pool
from harness.tui import AGENT_COMMANDS, AGENT_TOOLS
from loop import agent_loop_full
from tools import cron as _cron
from tools import mcp as _mcp
from tools import teams as _teams

# -- prompt_toolkit input (plain mode only) ---------------------------------
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import PathCompleter, WordCompleter, merge_completers
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False

_PROMPT_STYLE = Style.from_dict({
    "prompt": "cyan bold",
})

_history_file = WORKDIR / ".agent_history"


def _create_prompt_session() -> PromptSession | None:
    """Create a prompt_toolkit session with completers and history.

    Enter submits. Escape then Enter inserts a newline for multi-line input.
    """
    if not _HAS_PROMPT_TOOLKIT:
        return None

    bindings = KeyBindings()

    @bindings.add("enter")
    def _submit(event):
        """Enter submits the buffer."""
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    def _newline(event):
        """Escape+Enter inserts a literal newline."""
        event.current_buffer.insert_text("\n")

    completer = merge_completers([
        WordCompleter(AGENT_COMMANDS + AGENT_TOOLS, ignore_case=True, sentence=True),
        PathCompleter(expanduser=True),
    ])
    history = FileHistory(str(_history_file)) if _history_file else None
    return PromptSession(
        completer=completer,
        history=history,
        style=_PROMPT_STYLE,
        multiline=True,
        key_bindings=bindings,
        message=[("class:prompt", "51agent >> ")],
    )


_prompt_session = _create_prompt_session()

install_permissions()

# -- helpers ---------------------------------------------------------------
def _build_context(allowed: set[str] | None = None) -> tuple[dict, list[dict], dict]:
    """Assemble the context dict, tool list, and handler map for an agent turn.

    If `allowed` is provided, only those tools are included (plan-mode restriction).
    """
    ctx = {
        "workspace": str(WORKDIR),
        "memories": "",
        "mcp_servers": ", ".join(_mcp.mcp_clients.keys()),
    }
    ctx = inject_memories(ctx)
    tools, handlers = assemble_tool_pool(allowed=allowed)
    ctx["enabled_tools"] = [t["name"] for t in tools]
    return ctx, tools, handlers


# -- cron queue processor --------------------------------------------------
_agent_idle = True
agent_lock = threading.Lock()
_history_ref: list = []


def _deliver_cron_tasks():
    global _history_ref
    fired = _cron.consume_queue()
    if not fired:
        return
    context, tools, handlers = _build_context()
    for job in fired:
        render_activity(f"[cron] {job.prompt[:60]}", style="cron")
        _history_ref.append({"role": "user", "content": f"[Scheduled] {job.prompt}"})
    agent_loop_full(_history_ref, context, tools, handlers)


def queue_processor_loop():
    global _agent_idle
    while True:
        time.sleep(0.5)
        if not _cron.has_queue():
            continue
        if not _agent_idle:
            continue
        if not agent_lock.acquire(blocking=False):
            continue
        try:
            if _cron.has_queue():
                _agent_idle = False
                _deliver_cron_tasks()
        finally:
            agent_lock.release()


# -- one line of user input ------------------------------------------------
def handle_input(query: str, history: list) -> bool:
    """Process one user line. Returns False when the session should exit.

    Called from the plain-mode loop (same thread) and from the TUI's
    worker thread. No terminal I/O here — all output via harness.render.
    """
    if query.strip().lower() in ("q", "exit", "quit"):
        save_session(history)
        return False
    if query.strip().lower() == "?":
        render_help()
        return True

    if not query.strip():
        inbox = _teams.consume_lead_inbox()
        if inbox:
            render_inbox(inbox)
            inbox_text = "\n".join(
                f"From {m['from']} ({m.get('type', 'message')}): {m['content'][:300]}"
                for m in inbox)
            history.append({"role": "user", "content": f"[Inbox]\n{inbox_text}"})
            _agent_idle = False
            ctx, tools, handlers = _build_context()
            agent_loop_full(history, ctx, tools, handlers)
            _agent_idle = True
        return True

    _agent_idle = False
    trigger_hooks("UserPromptSubmit", query)

    for job in _cron.consume_queue():
        render_activity(f"[cron] {job.prompt[:60]}", style="cron")
        history.append({"role": "user", "content": f"[Scheduled] {job.prompt}"})

    inbox = _teams.consume_lead_inbox()
    if inbox:
        render_inbox(inbox)
        inbox_text = "\n".join(
            f"From {m['from']} ({m.get('type', 'message')}): {m['content'][:300]}"
            for m in inbox)
        history.append({"role": "user", "content": f"[Inbox]\n{inbox_text}"})

    plan = get_plan_state()

    # -- plan-mode: handle approval / revision ---------------------------
    if plan.phase == "awaiting_approval":
        decision = query.strip().lower()
        if decision in ("y", "yes", ""):
            plan.approve()
            render_info("Plan approved. Executing...")
            # Inject plan context and execute with full tools
            history.append({"role": "user", "content": plan.plan_context})
            ctx, tools, handlers = _build_context()
            _agent_idle = False
            with spinner("Executing plan..."):
                agent_loop_full(history, ctx, tools, handlers)
            _agent_idle = True
            plan.reset()
            return True
        if decision.startswith("r:") or decision.startswith("r "):
            feedback = decision[2:].strip() or "Revise the plan."
            render_info(f"Revising: {feedback}")
            allowed = plan.revise(feedback)
            history.append({"role": "user", "content": f"Revise the plan: {feedback}"})
            ctx, tools, handlers = _build_context(allowed=set(allowed))
            _agent_idle = False
            with spinner("Revising plan..."):
                agent_loop_full(history, ctx, tools, handlers)
            _agent_idle = True
            # Check if agent produced a new plan
            if plan.phase == "planning":
                # Extract plan from last response
                last = history[-1].get("content", []) if history else []
                if isinstance(last, list):
                    for block in last:
                        if getattr(block, "type", None) == "text":
                            plan.submit_plan(block.text)
            if plan.phase == "awaiting_approval":
                render_info("Approve? (y/Enter = yes, n = no, r: feedback)")
            return True
        if decision in ("n", "no"):
            plan.reject()
            render_info("Plan rejected.")
            return True
        # Any other input during approval: treat as feedback
        render_info("Approve? (y/Enter = yes, n = no, r: feedback)")
        return True

    # -- plan-mode: start planning ---------------------------------------
    if query.strip().lower().startswith("/plan "):
        task = query.strip()[6:]
        plan.start_planning(task)
        allowed = list(plan.READONLY_TOOLS)
        history.append({"role": "user",
            "content": f"Create a plan for: {task}. Use read-only tools to explore the codebase, then output your plan as a text response. Do NOT make any changes — just propose the approach."})
        ctx, tools, handlers = _build_context(allowed=set(allowed))
        _agent_idle = False
        with spinner("Planning..."):
            agent_loop_full(history, ctx, tools, handlers)
        _agent_idle = True
        # Extract plan text from response
        last = history[-1].get("content", []) if history else []
        if isinstance(last, list):
            for block in last:
                if getattr(block, "type", None) == "text":
                    plan.submit_plan(block.text)
        if plan.phase == "awaiting_approval":
            render_info("Approve? (y/Enter = yes, n = no, r: feedback)")
        return True

    # -- normal execution ------------------------------------------------
    history.append({"role": "user", "content": query})

    # If we're in executing phase, inject plan context
    if plan.phase == "executing":
        pass  # plan already injected on approval

    ctx, tools, handlers = _build_context()

    try:
        with spinner("Thinking..."):
            agent_loop_full(history, ctx, tools, handlers)
    except KeyboardInterrupt:
        print()
        render_info("Interrupted. Type 'q' to quit.")

    for msg in history[-2:]:
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if hasattr(block, "name") and block.name == "connect_mcp":
                    tools, handlers = assemble_tool_pool()
                    ctx["enabled_tools"] = [t["name"] for t in tools]
                    ctx["mcp_servers"] = ", ".join(_mcp.mcp_clients.keys())
                    break

    _agent_idle = True
    return True


# -- plain (non-TTY) mode --------------------------------------------------
def _run_plain_loop(history: list) -> None:
    """The pre-TUI scrollback experience, unchanged."""
    render_banner(MODEL, str(WORKDIR))
    render_help()

    # Offer to resume last session
    latest = find_latest_session()
    if latest:
        try:
            choice = input(
                f"\n  Resume session from {time.ctime(latest.stat().st_mtime)}? (y/N): "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "n"
        if choice in ("y", "yes"):
            loaded = load_session(latest)
            if loaded:
                history[:] = loaded
                render_info(f"Resumed session with {len(history)} messages.")

    while True:
        try:
            if _prompt_session:
                try:
                    query = _prompt_session.prompt()
                except (EOFError, OSError):
                    # Non-TTY or closed pipe — fall through to input()
                    query = input("\n51agent >> ")
            else:
                query = input("\n51agent >> ")
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not handle_input(query, history):
            break


# -- main ------------------------------------------------------------------
if __name__ == "__main__":
    history: list = []
    _history_ref = history
    atexit.register(lambda: save_session(history))

    threading.Thread(target=_cron.scheduler_loop, daemon=True, name="cron").start()
    threading.Thread(target=queue_processor_loop, daemon=True, name="cron-queue").start()

    if sys.stdin.isatty() and sys.stdout.isatty():
        from harness.tui import run_tui

        run_tui(history, on_line=handle_input)
    else:
        _run_plain_loop(history)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_agent_input.py -v`
Expected: PASS (5 tests). Then `uv run pytest -q` — full suite green. Then a quick plain-mode smoke:

`echo q | uv run python agent.py` — Expected: banner + help print, then the session saves and it exits 0.

- [ ] **Step 5: Commit**

```bash
git add agent.py tests/test_agent_input.py
git commit -m "refactor: extract handle_input, dispatch TTY to TUI and pipes to plain loop

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review Notes (done at plan-writing time)

- **Spec coverage:** header/chat/activity/footer/status (T7), status segments incl. git/python/node/cron/ctx (T2), pending-question mechanism (T1, T6, T9), Ctrl+C mid-turn vs idle (T1/T4/T5/T7 bindings), non-TTY fallback (T8), resume prompt in-UI (T9), token usage (T2/T5), plain-mode zero-change (T3/T8), testing plan sections all mapped.
- **Type consistency:** `apply_events(events, chat, activity) -> str` is called exactly as defined in T9; `collect_status(workdir, state=...)` keyword matches T2's signature; `bridge.ask_question(text, default=False, timeout=...)` used consistently in T6/T9; `_route_line` referenced from T7's `_accept` via function-local import that T9 removes.
- **No placeholders:** every task carries complete runnable code and tests.
