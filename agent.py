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
import sys, time, threading, os
from pathlib import Path

try:
    import readline
    readline.parse_and_bind('set bind-tty-special-chars off')
    readline.parse_and_bind('set input-meta on')
    readline.parse_and_bind('set output-meta on')
    readline.parse_and_bind('set convert-meta off')
except ImportError:
    pass

_script_dir = Path(__file__).resolve().parent
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from config import MODEL, WORKDIR
from tools import mcp as _mcp
from tools import cron as _cron
from tools import teams as _teams

from harness import trigger_hooks
from harness.permissions import install as install_permissions
from harness.memory import inject_memories
from harness.tool_pool import assemble_tool_pool
from harness.render import render_banner, render_help, render_markdown, render_inbox, render_error, render_info, render_tool_use, spinner, use_color, prompt as _prompt

install_permissions()

# -- cron queue processor --------------------------------------------------
_agent_idle = True
agent_lock = threading.Lock()
_history_ref: list = []


def _deliver_cron_tasks():
    global _history_ref
    fired = _cron.consume_queue()
    if not fired:
        return
    context = {"workspace": str(WORKDIR), "memories": "",
               "mcp_servers": ", ".join(_mcp.mcp_clients.keys())}
    context = inject_memories(context)
    tools, handlers = assemble_tool_pool()
    context["enabled_tools"] = [t["name"] for t in tools]
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
    atexit.register(lambda: print("\n51agent shut down. Goodbye!"))

    history: list = []
    _history_ref = history

    threading.Thread(target=_cron.scheduler_loop, daemon=True, name="cron").start()
    threading.Thread(target=queue_processor_loop, daemon=True, name="cron-queue").start()

    while True:
        try:
            query = _prompt("51agent >> ")
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if query.strip().lower() in ("q", "exit", "quit"):
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
                ctx = {"workspace": str(WORKDIR), "memories": "",
                       "mcp_servers": ", ".join(_mcp.mcp_clients.keys())}
                ctx = inject_memories(ctx)
                tools, handlers = assemble_tool_pool()
                ctx["enabled_tools"] = [t["name"] for t in tools]
                agent_loop_full(history, ctx, tools, handlers)
                _agent_idle = True
                last = history[-1]["content"] if history else ""
                if isinstance(last, list):
                    for block in last:
                        if getattr(block, "type", None) == "text":
                            render_markdown(block.text)
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

        history.append({"role": "user", "content": query})

        ctx = {"workspace": str(WORKDIR), "memories": "",
               "mcp_servers": ", ".join(_mcp.mcp_clients.keys())}
        ctx = inject_memories(ctx)
        tools, handlers = assemble_tool_pool()
        ctx["enabled_tools"] = [t["name"] for t in tools]

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

        last = history[-1]["content"] if history else ""
        if isinstance(last, list):
            for block in last:
                if getattr(block, "type", None) == "text":
                    render_markdown(block.text)
        print()
