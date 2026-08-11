#!/usr/bin/env python3
"""
s01: Agent Loop — 一个循环 + Bash = 一个 Agent

核心理念：
  while stop_reason == "tool_use":
      response = LLM(messages, tools)
      执行工具()
      结果喂回 messages
      继续循环

这是整个 agent 的基础。后面所有机制都挂在这个循环上。
"""
import os, subprocess, sys

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
WORKDIR = os.getcwd()

SYSTEM = f"You are a coding agent working in {WORKDIR}. Use bash to solve tasks. Act, don't explain."

# ── 工具定义：只有一个 bash ──
TOOLS = [{
    "name": "bash",
    "description": "Run a shell command in the working directory.",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "The shell command to run"}},
        "required": ["command"],
    },
}]

# ── 工具执行 ──
def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/sda"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"

# ── 核心循环 ──
def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )

        messages.append({"role": "assistant", "content": response.content})

        # 模型不调工具 → 结束
        if response.stop_reason != "tool_use":
            return

        # 执行每个工具调用，收集结果
        results = []
        for block in response.content:
            if block.type == "tool_use":
                cmd = block.input.get("command", "")
                print(f"\033[33m$ {cmd}\033[0m")
                output = run_bash(cmd)
                print(output[:200])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                })

        # 结果喂回去，循环继续
        messages.append({"role": "user", "content": results})

# ── CLI 入口 ──
if __name__ == "__main__":
    print("=" * 55)
    print("s01: Agent Loop — 一个循环 + Bash")
    print(f"Model: {MODEL}  |  Workdir: {WORKDIR}")
    print("输入问题，回车发送。输入 q 退出。")
    print("=" * 55)

    history = []
    while True:
        try:
            query = input("\n\033[36m>> \033[0m")
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if query.strip().lower() in ("q", "exit"):
            break
        if not query.strip():
            continue

        history.append({"role": "user", "content": query})
        agent_loop(history)

        # 打印模型最终文本回复
        last = history[-1]["content"]
        if isinstance(last, list):
            for block in last:
                if getattr(block, "type", None) == "text":
                    print(f"\n{block.text}")
        print()
