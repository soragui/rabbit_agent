#!/usr/bin/env python3
"""
s02: Tool Use — 从 1 个工具到 5 个工具

新机制：TOOL_HANDLERS 字典实现工具分发。
加一个工具 = TOOLS 数组加一条 + TOOL_HANDLERS 字典加一行。循环不动。
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

SYSTEM = f"You are a coding agent working in {WORKDIR}. Use tools to solve tasks. Act, don't explain."

# ── 安全路径校验 ──
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
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"

def run_read(path: str, limit: int = None) -> str:
    lines = safe_path(path).read_text().splitlines()
    if limit and limit > 0:
        lines = lines[:limit]
    return "\n".join(lines)

def run_write(path: str, content: str) -> str:
    safe_path(path).write_text(content)
    return f"Wrote {len(content)} bytes to {path}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    text = safe_path(path).read_text()
    if old_text not in text:
        return f"Error: text not found in {path}"
    safe_path(path).write_text(text.replace(old_text, new_text, 1))
    return f"Edited {path}"

def run_glob(pattern: str) -> str:
    import glob as g
    matches = g.glob(pattern, root_dir=str(WORKDIR))
    return "\n".join(sorted(matches)) if matches else "(no matches)"

# ── 工具定义 ──
TOOLS = [
    {"name": "bash", "description": "Run a shell command.", "input_schema": {
        "type": "object", "properties": {"command": {"type": "string"}},
        "required": ["command"]}},
    {"name": "read_file", "description": "Read a file from the working directory.", "input_schema": {
        "type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
        "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.", "input_schema": {
        "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace text in a file (first occurrence only).", "input_schema": {
        "type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
        "required": ["path", "old_text", "new_text"]}},
    {"name": "glob", "description": "Find files matching a glob pattern.", "input_schema": {
        "type": "object", "properties": {"pattern": {"type": "string"}},
        "required": ["pattern"]}},
]

# ── 工具分发表：这就是 s02 的核心 ──
TOOL_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw.get("command", "")),
    "read_file":  lambda **kw: run_read(kw["path"], kw.get("limit")),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    "glob":       lambda **kw: run_glob(kw["pattern"]),
}

# ── 核心循环：只改了一行 ──
def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return

        results = []
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name)    # ← 查表分发
                if handler:
                    try:
                        output = handler(**block.input)
                    except Exception as e:
                        output = f"Error: {e}"
                else:
                    output = f"Unknown tool: {block.name}"
                print(f"\033[33m[{block.name}]\033[0m {str(block.input)[:100]}")
                print(str(output)[:200])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })
        messages.append({"role": "user", "content": results})

# ── CLI ──
if __name__ == "__main__":
    print("=" * 55)
    print("s02: Tool Use — 5 个工具 + dispatch map")
    print(f"Model: {MODEL}  |  Workdir: {WORKDIR}")
    print("工具: bash, read_file, write_file, edit_file, glob")
    print("输入问题，回车发送。输入 q 退出。")
    print("=" * 55)

    history = []
    while True:
        try:
            query = input("\n\033[36ms02 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if query.strip().lower() in ("q", "exit"):
            break
        if not query.strip():
            continue

        history.append({"role": "user", "content": query})
        agent_loop(history)

        last = history[-1]["content"]
        if isinstance(last, list):
            for block in last:
                if getattr(block, "type", None) == "text":
                    print(f"\n{block.text}")
        print()
