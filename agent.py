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
    render_banner,
    render_help,
    render_inbox,
    render_info,
    render_markdown,
    spinner,
)
from harness.session import find_latest_session, load_session, save_session
from harness.tool_pool import assemble_tool_pool
from tools import cron as _cron
from tools import mcp as _mcp
from tools import teams as _teams

# -- prompt_toolkit input --------------------------------------------------
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import PathCompleter, WordCompleter, merge_completers
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False

_AGENT_COMMANDS = ["q", "exit", "quit", "?"]
_AGENT_TOOLS = [
    "bash", "read_file", "write_file", "edit_file", "glob",
    "todo_write", "structured_output", "task", "load_skill", "compact",
    "create_task", "list_tasks", "get_task", "claim_task", "complete_task",
    "schedule_cron", "list_crons", "cancel_cron",
    "spawn_teammate", "send_message", "check_inbox",
    "request_shutdown", "request_plan", "review_plan",
    "create_worktree", "remove_worktree", "keep_worktree",
    "connect_mcp",
]

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
        WordCompleter(_AGENT_COMMANDS + _AGENT_TOOLS, ignore_case=True, sentence=True),
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


def _render_last_response(history: list) -> None:
    """Render text blocks from the last assistant message, if any."""
    if not history:
        return
    last = history[-1].get("content", "") if isinstance(history[-1], dict) else ""
    if isinstance(last, list):
        for block in last:
            if getattr(block, "type", None) == "text":
                render_markdown(block.text)

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


# -- main ------------------------------------------------------------------
if __name__ == "__main__":
    from loop import agent_loop_full

    render_banner(MODEL, str(WORKDIR))
    render_help()

    import atexit

    history: list = []
    _history_ref = history
    atexit.register(lambda: save_session(history))

    # Offer to resume last session
    latest = find_latest_session()
    if latest:
        try:
            choice = input(f"\n  Resume session from {time.ctime(latest.stat().st_mtime)}? (y/N): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "n"
        if choice in ("y", "yes"):
            loaded = load_session(latest)
            if loaded:
                history[:] = loaded
                render_info(f"Resumed session with {len(history)} messages.")

    threading.Thread(target=_cron.scheduler_loop, daemon=True, name="cron").start()
    threading.Thread(target=queue_processor_loop, daemon=True, name="cron-queue").start()

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

        if query.strip().lower() in ("q", "exit", "quit"):
            save_session(history)
            break
        if query.strip().lower() == "?":
            render_help()
            continue

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
            continue

        _agent_idle = False
        trigger_hooks("UserPromptSubmit", query)

        for job in _cron.consume_queue():
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
                continue
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
                continue
            if decision in ("n", "no"):
                plan.reject()
                render_info("Plan rejected.")
                continue
            # Any other input during approval: treat as feedback
            render_info("Approve? (y/Enter = yes, n = no, r: feedback)")

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
            continue

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
        print()
