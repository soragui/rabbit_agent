"""Permission hooks — 4-tier tool gating + workspace boundary enforcement.

Tiers (per tool name):
  safe       — always allow, no prompt
  ask-once   — prompt once per session, remember answer
  always-ask — prompt every invocation
  never      — always deny (also: deny-list patterns within bash)

Gate order: deny-list → never tier → workspace boundary → tier decision
"""
from config import WORKDIR
from harness import register_hook
from harness.render import render_info, render_tool_use, tui_active

# -- tier definitions ------------------------------------------------------
SAFE = {
    "read_file", "glob", "grep", "list_tasks", "get_task", "load_skill",
    "check_inbox", "list_crons", "compact", "todo_write", "keep_worktree",
}

ASK_ONCE = {
    "bash", "write_file", "edit_file", "task", "create_task", "claim_task",
    "complete_task", "send_message", "web_fetch",
}

ALWAYS_ASK = {
    "schedule_cron", "cancel_cron", "connect_mcp", "spawn_teammate",
    "create_worktree", "remove_worktree",
    "request_shutdown", "request_plan", "review_plan",
}

NEVER = set()  # no tool is unconditionally denied — deny-list handles that

# -- bash-level deny patterns (hard gate, overrides all tiers) -------------
DENY_LIST = [
    "rm -rf /", "sudo rm -rf", "shutdown now", "reboot", "mkfs",
    "dd if=", "> /dev/sda", ":(){ :|:& };:",
]

DANGEROUS_BASH_KW = ["rm ", "> /etc/", "chmod 777", "curl", "wget"]

# -- session state ---------------------------------------------------------
_approved_once: set[str] = set()  # tool names approved for this session


def _get_tier(tool_name: str) -> str:
    if tool_name in SAFE:
        return "safe"
    if tool_name in ASK_ONCE:
        return "ask-once"
    if tool_name in ALWAYS_ASK:
        return "always-ask"
    if tool_name in NEVER:
        return "never"
    # Unknown tools (MCP, future additions): always-ask
    if tool_name.startswith("mcp__"):
        return "ask-once"
    return "always-ask"


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


def _permission_hook(block):
    tool_name = block.name
    tier = _get_tier(tool_name)

    # Gate 0: deny-list (bash only, hard gate)
    if tool_name == "bash":
        cmd = block.input.get("command", "")
        for pattern in DENY_LIST:
            if pattern in cmd:
                return f"Permission denied: '{pattern}' is on the deny list"

    # Gate 1: never tier
    if tier == "never":
        return f"Permission denied: '{tool_name}' is blocked"

    # Gate 2: workspace boundary (file tools)
    if tool_name in ("read_file", "write_file", "edit_file"):
        try:
            path = block.input.get("path", "")
            p = (WORKDIR / path).resolve()
            if not str(p).startswith(str(WORKDIR.resolve())):
                return f"Permission denied: path '{path}' outside workspace"
        except Exception:
            return "Permission denied: invalid path"

    # Gate 3: safe — always allow
    if tier == "safe":
        return None

    # Gate 4: ask-once — prompt first time, then remember
    if tier == "ask-once":
        if tool_name in _approved_once:
            return None
        # For bash, also check dangerous keywords
        if tool_name == "bash":
            cmd = block.input.get("command", "")
            for kw in DANGEROUS_BASH_KW:
                if kw in cmd:
                    # Dangerous bash commands: escalate to always-ask behavior
                    if not _prompt_user(tool_name, kw):
                        return f"Permission denied by user for: {kw}"
                    _approved_once.add(tool_name)
                    return None
        # Normal ask-once
        if _prompt_user(tool_name):
            _approved_once.add(tool_name)
            return None
        return f"Permission denied by user for: {tool_name}"

    # Gate 5: always-ask
    if tier == "always-ask":
        if _prompt_user(tool_name):
            return None
        return f"Permission denied by user for: {tool_name}"

    return None


def _log_hook(block):
    render_tool_use(block.name, str(block.input)[:200])
    return None


def _large_output_hook(block, output):
    if len(str(output)) > 100_000:
        render_info(f"Large output from {block.name}: {len(str(output))} chars")
    return None


def install():
    register_hook("PreToolUse", _permission_hook)
    register_hook("PreToolUse", _log_hook)
    register_hook("PostToolUse", _large_output_hook)
