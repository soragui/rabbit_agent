#!/usr/bin/env python3
"""
agent.py — 从 0 到 1 实现一个完整的 Coding Agent

用法:
    pip install anthropic python-dotenv pyyaml
    编辑 .env 填入 API key
    python agent.py
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

# -- ensure we can import from agent_main ---------------------------------
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
from harness.render import (render_markdown, render_banner, render_help,
                              render_inbox, render_error, render_info, prompt)
from loop import agent_loop_full

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


# -- banner / help ---------------------------------------------------------
# (moved to harness/render.py — kept here as thin wrappers)
def print_banner():
    render_banner(MODEL, str(WORKDIR))


# -- prompt ---------------------------------------------------------------
def _prompt(text: str) -> str:
    return prompt(text)


# -- double Ctrl+C safety --------------------------------------------------
_last_interrupt = 0.0
_INTERRUPT_WINDOW = 2.0  # seconds


def _handle_interrupt() -> bool:
    """Return True if the agent should exit (two Ctrl+C within window)."""
    global _last_interrupt
    now = time.time()
    if now - _last_interrupt < _INTERRUPT_WINDOW:
        return True
    _last_interrupt = now
    print(f"\n  ⚠ Press Ctrl+C again within {_INTERRUPT_WINDOW:.0f}s to exit (or type 'q')")
    return False


def print_help():
    render_help()


# -- main ------------------------------------------------------------------
if __name__ == "__main__":
    print_banner()
    print_help()

    import atexit
    atexit.register(lambda: print("\nAgent shut down. Goodbye!"))

    history: list = []
    _history_ref = history

    # start background threads
    threading.Thread(target=_cron.scheduler_loop, daemon=True, name="cron").start()
    threading.Thread(target=queue_processor_loop, daemon=True, name="cron-queue").start()

    while True:
        try:
            query = _prompt("agent >> ")
        except EOFError:
            print("\nBye.")
            break
        except KeyboardInterrupt:
            print()
            if _handle_interrupt():
                print("Bye.")
                break
            continue

        if query.strip().lower() in ("q", "exit", "quit"):
            break
        if query.strip().lower() == "?":
            print_help()
            continue

        # empty line: check inbox
        if not query.strip():
            inbox = _teams.consume_lead_inbox()
            if inbox:
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

        # cron
        for job in _cron.consume_queue():
            history.append({"role": "user", "content": f"[Scheduled] {job.prompt}"})

        # inbox
        inbox = _teams.consume_lead_inbox()
        if inbox:
            inbox_text = "\n".join(
                f"From {m['from']} ({m.get('type', 'message')}): {m['content'][:300]}"
                for m in inbox)
            history.append({"role": "user", "content": f"[Inbox]\n{inbox_text}"})

        history.append({"role": "user", "content": query})

        ctx = {
            "workspace": str(WORKDIR),
            "memories": "",
            "mcp_servers": ", ".join(_mcp.mcp_clients.keys()),
        }
        ctx = inject_memories(ctx)
        tools, handlers = assemble_tool_pool()
        ctx["enabled_tools"] = [t["name"] for t in tools]

        agent_loop_full(history, ctx, tools, handlers)

        # rebuild tool pool if MCP was connected
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
