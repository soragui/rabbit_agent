#!/usr/bin/env python3
"""
s03: Permission — 执行前做权限判断
在 s02 的基础上，工具执行前加了三道闸门：拒绝列表 → 规则匹配 → 用户审批
"""
import os, subprocess, sys
from pathlib import Path

try:
    import readline
    readline.parse_and_bind('set bind-tty-special-chars off')
    readline.parse_and_bind('set input-meta on')
    readline.parse_and_bind('set output-meta on')
    readline.parse_and_bind('set convert-meta off')
except ImportError:
    pass

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]
WORKDIR = Path.cwd()

SYSTEM = f"You are a coding agent at {WORKDIR}. Use tools to solve tasks. Act, don't explain."

# ── 安全路径 ──
def safe_path(path: str) -> Path:
    p = (WORKDIR / path).resolve()
    if not str(p).startswith(str(WORKDIR.resolve())):
        raise ValueError(f"Path outside workspace: {path}")
    return p

# ── 工具实现 ──
def run_bash(command: str) -> str:
    try:
        r = subprocess.run(command, shell=True, cwd=str(WORKDIR),
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired: return "Error: Timeout (120s)"

def run_read(path: str, limit: int = None) -> str:
    lines = safe_path(path).read_text().splitlines()
    if limit and limit > 0: lines = lines[:limit]
    return "\n".join(lines)

def run_write(path: str, content: str) -> str:
    safe_path(path).write_text(content)
    return f"Wrote {len(content)} bytes to {path}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    text = safe_path(path).read_text()
    if old_text not in text: return f"Error: text not found in {path}"
    safe_path(path).write_text(text.replace(old_text, new_text, 1))
    return f"Edited {path}"

def run_glob(pattern: str) -> str:
    import glob as g
    matches = g.glob(pattern, root_dir=str(WORKDIR))
    return "\n".join(sorted(matches)) if matches else "(no matches)"

# ── 工具定义 ──
TOOLS = [
    {"name": "bash", "description": "Run a shell command.", "input_schema": {
        "type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read a file.", "input_schema": {
        "type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.", "input_schema": {
        "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace text in a file.", "input_schema": {
        "type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "glob", "description": "Find files by pattern.", "input_schema": {
        "type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},
]

TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw.get("command", "")),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file": lambda **kw: run_edit(kw["path"], kw.get("old_text", ""), kw.get("new_text", "")),
    "glob": lambda **kw: run_glob(kw["pattern"]),
}

# ═══════════════════════════════════════════════
# s03 核心：三道闸门权限管线
# ═══════════════════════════════════════════════

# 闸门 1: 硬拒绝列表
DENY_LIST = ["rm -rf /", "sudo rm -rf", "shutdown now", "reboot", "mkfs",
             "dd if=", "> /dev/sda", ":(){ :|:& };:"]

def check_deny_list(command: str) -> str | None:
    for pattern in DENY_LIST:
        if pattern in command:
            return f"⛔ Blocked: '{pattern}' is on the deny list"
    return None

# 闸门 2: 规则匹配
PERMISSION_RULES = [
    {"tools": ["read_file", "write_file", "edit_file"],
     "check": lambda args: not (WORKDIR / args.get("path", "")).resolve().is_relative_to(WORKDIR.resolve()),
     "message": "Access outside workspace"},
    {"tools": ["bash"],
     "check": lambda args: any(kw in args.get("command", "") for kw in ["rm ", "> /etc/", "chmod 777"]),
     "message": "Potentially destructive command"},
]

def check_rules(tool_name: str, args: dict) -> str | None:
    for rule in PERMISSION_RULES:
        if tool_name in rule["tools"]:
            try:
                if rule["check"](args):
                    return rule["message"]
            except Exception:
                return "Invalid arguments"
    return None

# 闸门 3: 用户审批
def ask_user(tool_name: str, args: dict, reason: str) -> str:
    print(f"\n  ⚠  {reason}")
    print(f"     Tool: {tool_name}({str(args)[:100]})")
    choice = input("     Allow? [y/N] ").strip().lower()
    return "allow" if choice in ("y", "yes") else "deny"

# 三道闸门合一
def check_permission(block) -> bool:
    # 闸门 1: 硬拒绝
    if block.name == "bash":
        reason = check_deny_list(block.input.get("command", ""))
        if reason:
            print(f"\n{reason}")
            return False
    # 闸门 2 + 3: 规则匹配 → 用户审批
    reason = check_rules(block.name, block.input)
    if reason:
        decision = ask_user(block.name, block.input, reason)
        if decision == "deny":
            return False
    return True

# ═══════════════════════════════════════════════
# 核心循环：s02 基础上只加了一行 check_permission
# ═══════════════════════════════════════════════
def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000)
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return

        results = []
        for block in response.content:
            if block.type == "tool_use":
                # ── s03 核心：权限检查 ──
                if not check_permission(block):
                    results.append({
                        "type": "tool_result", "tool_use_id": block.id,
                        "content": "Permission denied."})
                    continue
                # ── s02 原有 ──
                handler = TOOL_HANDLERS.get(block.name)
                try:
                    output = handler(**block.input) if handler else f"Unknown: {block.name}"
                except Exception as e:
                    output = f"Error: {e}"
                print(f"  [{block.name}] {str(block.input)[:100]}")
                print(f"  {str(output)[:200]}")
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})

if __name__ == "__main__":
    print("=" * 55)
    print("s03: Permission — 三道闸门权限管线")
    print(f"Model: {MODEL}  |  Workdir: {WORKDIR}")
    print("输入 q 退出。")
    print("=" * 55)
    history = []
    while True:
        try: query = input("\n\033[36ms03 >> \033[0m")
        except (EOFError, KeyboardInterrupt): print("\nBye."); break
        if query.strip().lower() in ("q", "exit"): break
        if not query.strip(): continue
        history.append({"role": "user", "content": query})
        agent_loop(history)
        last = history[-1]["content"]
        if isinstance(last, list):
            for block in last:
                if getattr(block, "type", None) == "text": print(f"\n{block.text}")
        print()
