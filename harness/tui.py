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
from prompt_toolkit.document import Document
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
    # Read-only buffers: wholesale replacement via set_document with
    # bypass_readonly (direct `.text` assignment raises EditReadOnlyBuffer).
    new_chat = "\n".join(_chat_lines)
    cursor = (
        len(new_chat)
        if chat_follow
        else min(chat.cursor_position, len(new_chat))
    )
    chat.set_document(Document(new_chat, cursor), bypass_readonly=True)
    new_activity = "\n".join(_activity_lines)
    activity.set_document(
        Document(new_activity, len(new_activity)), bypass_readonly=True)
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
