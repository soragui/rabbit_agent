"""s03: Permission hooks — deny-list + rules + user prompt."""
from config import WORKDIR
from harness import register_hook
from harness.render import render_tool_use, render_info, render_error

DENY_LIST = [
    "rm -rf /", "sudo rm -rf", "shutdown now", "reboot", "mkfs",
    "dd if=", "> /dev/sda", ":(){ :|:& };:",
]

DANGEROUS_KEYWORDS = ["rm ", "> /etc/", "chmod 777", "curl", "wget"]


def _permission_hook(block):
    # Gate 1: deny list
    if block.name == "bash":
        cmd = block.input.get("command", "")
        for pattern in DENY_LIST:
            if pattern in cmd:
                return f"Permission denied: '{pattern}' is on the deny list"
        for kw in DANGEROUS_KEYWORDS:
            if kw in cmd:
                try:
                    decision = input(f"  Allow dangerous command? [{kw}] (y/N): ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print("  [auto-denied: non-interactive]")
                    return f"Permission denied for: {kw}"
                if decision not in ("y", "yes"):
                    return f"Permission denied by user for: {kw}"

    # Gate 2: workspace boundary
    if block.name in ("read_file", "write_file", "edit_file"):
        try:
            path = block.input.get("path", "")
            p = (WORKDIR / path).resolve()
            if not str(p).startswith(str(WORKDIR.resolve())):
                return f"Permission denied: path '{path}' outside workspace"
        except Exception:
            return f"Permission denied: invalid path"

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
