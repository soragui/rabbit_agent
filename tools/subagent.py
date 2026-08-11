"""s06: Subagent — isolated-context task delegation."""
from tools import run_bash, run_read, run_write, run_edit, run_glob
from config import MODEL, client
from harness.render import render_info, render_tool_use, spinner

SUB_TOOLS = [
    {"name": "bash", "description": "Run a shell command.", "input_schema": {
        "type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read a file.", "input_schema": {
        "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.", "input_schema": {
        "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace text in a file.", "input_schema": {
        "type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
        "required": ["path", "old_text", "new_text"]}},
    {"name": "glob", "description": "Find files by pattern.", "input_schema": {
        "type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},
]

SUB_HANDLERS = {
    "bash":       lambda **kw: run_bash(kw.get("command", "")),
    "read_file":  lambda **kw: run_read(kw["path"]),
    "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
    "edit_file":  lambda **kw: run_edit(kw["path"], kw.get("old_text", ""), kw.get("new_text", "")),
    "glob":       lambda **kw: run_glob(kw["pattern"]),
}

SUB_SYSTEM = "You are a subagent. Complete the task directly. Do not delegate."


def spawn_subagent(description: str) -> str:
    messages = [{"role": "user", "content": description}]
    render_info("Subagent spawned")

    for _ in range(30):
        try:
            with spinner("Subagent thinking..."):
                response = client.messages.create(
                    model=MODEL, system=SUB_SYSTEM, messages=messages,
                    tools=SUB_TOOLS, max_tokens=8000)
        except Exception as e:
            return f"Subagent error: {e}"

        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            break

        results = []
        for block in response.content:
            if block.type == "tool_use":
                handler = SUB_HANDLERS.get(block.name)
                try:
                    output = handler(**block.input) if handler else f"Unknown: {block.name}"
                except Exception as e:
                    output = f"Error: {e}"
                render_tool_use(f"[sub] {block.name}", str(block.input)[:120])
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})
        messages.append({"role": "user", "content": results})

    last = messages[-1]["content"]
    if isinstance(last, list):
        for block in last:
            if getattr(block, "type", None) == "text":
                render_info("Subagent done")
                return block.text
    render_info("Subagent done")
    return str(last)[:2000]
