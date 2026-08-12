"""s19: Tool Pool Assembly — build complete tool list + handler map."""
import json as _json

from config import normalize_name
from tools import mcp, run_bash, run_edit, run_glob, run_grep, run_read, run_write, todo
from tools.cron import cancel_job, list_crons, schedule_job
from tools.mcp import run_connect_mcp
from tools.skills import run_load_skill
from tools.subagent import spawn_subagent
from tools.task_system import claim_task, complete_task, create_task, get_task, list_tasks
from tools.teams import (
    run_check_inbox,
    run_request_plan,
    run_request_shutdown,
    run_review_plan,
    run_send_message,
    spawn_teammate_thread,
)
from tools.worktree import create_worktree, keep_worktree, remove_worktree


def _handle_structured_output(format_description: str, data: dict) -> str:
    """Validate and format structured JSON output from the model."""
    try:
        formatted = _json.dumps(data, indent=2, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        return f"Structured output error: invalid JSON data — {e}"
    if format_description:
        return f"Structured output ({format_description}):\n```json\n{formatted}\n```"
    return f"```json\n{formatted}\n```"


def assemble_tool_pool(allowed: set[str] | None = None) -> tuple[list[dict], dict]:
    tools = [
        # s01/s02: file tools
        {"name": "bash", "description": "Run a shell command.", "input_schema": {
            "type": "object", "properties": {"command": {"type": "string"},
            "run_in_background": {"type": "boolean"}}, "required": ["command"]}},
        {"name": "read_file", "description": "Read a file.", "input_schema": {
            "type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["path"]}},
        {"name": "write_file", "description": "Write content to a file.", "input_schema": {
            "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]}},
        {"name": "edit_file", "description": "Replace text in a file once.", "input_schema": {
            "type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"},
            "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
        {"name": "glob", "description": "Find files by pattern.", "input_schema": {
            "type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},
        {"name": "grep", "description": "Search file contents with ripgrep.", "input_schema": {
            "type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"},
            "max_results": {"type": "integer"}}, "required": ["pattern"]}},
        # s05
        {"name": "todo_write", "description": "Manage session task list.", "input_schema": {
            "type": "object", "properties": {"todos": {"type": "array", "items": {
                "type": "object", "properties": {"content": {"type": "string"},
                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}},
                "required": ["content", "status"]}}}, "required": ["todos"]}},
        {"name": "structured_output", "description": "Produce structured JSON output. Call this instead of writing JSON in a text response when the user asks for structured data.", "input_schema": {
            "type": "object", "properties": {
                "format_description": {"type": "string", "description": "Describe the JSON shape you are producing"},
                "data": {"type": "object", "description": "The structured data"},
            }, "required": ["data"]}},
        # s06
        {"name": "task", "description": "Launch a subagent. Returns final conclusion.", "input_schema": {
            "type": "object", "properties": {"description": {"type": "string"}}, "required": ["description"]}},
        # s07
        {"name": "load_skill", "description": "Load full content of a skill.", "input_schema": {
            "type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
        # s08
        {"name": "compact", "description": "Compact conversation history.", "input_schema": {
            "type": "object", "properties": {}}},
        # s12
        {"name": "create_task", "description": "Create a persistent task with dependencies.", "input_schema": {
            "type": "object", "properties": {"subject": {"type": "string"}, "description": {"type": "string"},
            "blockedBy": {"type": "array", "items": {"type": "string"}}}, "required": ["subject"]}},
        {"name": "list_tasks", "description": "List tasks.", "input_schema": {
            "type": "object", "properties": {"filter_status": {"type": "string"}}}},
        {"name": "get_task", "description": "Get full task details.", "input_schema": {
            "type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
        {"name": "claim_task", "description": "Claim a task.", "input_schema": {
            "type": "object", "properties": {"task_id": {"type": "string"}, "owner": {"type": "string"}},
            "required": ["task_id"]}},
        {"name": "complete_task", "description": "Mark task completed (unblocks downstream).", "input_schema": {
            "type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
        # s14
        {"name": "schedule_cron", "description": "Schedule a cron job.", "input_schema": {
            "type": "object", "properties": {"cron": {"type": "string"}, "prompt": {"type": "string"},
            "recurring": {"type": "boolean"}, "durable": {"type": "boolean"}},
            "required": ["cron", "prompt"]}},
        {"name": "list_crons", "description": "List cron jobs.", "input_schema": {"type": "object", "properties": {}}},
        {"name": "cancel_cron", "description": "Cancel a cron job.", "input_schema": {
            "type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]}},
        # s15/s16
        {"name": "spawn_teammate", "description": "Spawn a teammate agent.", "input_schema": {
            "type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"},
            "prompt": {"type": "string"}}, "required": ["name", "role", "prompt"]}},
        {"name": "send_message", "description": "Send a message to a teammate.", "input_schema": {
            "type": "object", "properties": {"to": {"type": "string"}, "content": {"type": "string"}},
            "required": ["to", "content"]}},
        {"name": "check_inbox", "description": "Check lead's inbox.", "input_schema": {"type": "object", "properties": {}}},
        {"name": "request_shutdown", "description": "Request teammate shutdown.", "input_schema": {
            "type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}},
        {"name": "request_plan", "description": "Request teammate plan for approval.", "input_schema": {
            "type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}},
        {"name": "review_plan", "description": "Approve/reject teammate plan.", "input_schema": {
            "type": "object", "properties": {"target": {"type": "string"}, "approve": {"type": "boolean"},
            "feedback": {"type": "string"}}, "required": ["target", "approve"]}},
        # s18
        {"name": "create_worktree", "description": "Create a git worktree for task isolation.", "input_schema": {
            "type": "object", "properties": {"name": {"type": "string"}, "task_id": {"type": "string"}},
            "required": ["name"]}},
        {"name": "remove_worktree", "description": "Remove a git worktree.", "input_schema": {
            "type": "object", "properties": {"name": {"type": "string"}, "discard_changes": {"type": "boolean"}},
            "required": ["name"]}},
        {"name": "keep_worktree", "description": "Keep a worktree for review.", "input_schema": {
            "type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
        # s19
        {"name": "connect_mcp", "description": "Connect to an MCP server.", "input_schema": {
            "type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    ]

    handlers = {
        "bash":            lambda **kw: run_bash(kw.get("command", "")),
        "read_file":       lambda **kw: run_read(kw["path"], kw.get("limit")),
        "write_file":      lambda **kw: run_write(kw["path"], kw["content"]),
        "edit_file":       lambda **kw: run_edit(kw["path"], kw.get("old_text", ""), kw.get("new_text", "")),
        "glob":            lambda **kw: run_glob(kw["pattern"]),
        "grep":            lambda **kw: run_grep(kw["pattern"], kw.get("path", "."), kw.get("max_results", 50)),
        "todo_write":         lambda **kw: todo.run_todo_write(kw["todos"]),
        "structured_output":  lambda **kw: _handle_structured_output(kw.get("format_description", ""), kw.get("data", {})),
        "task":            lambda **kw: spawn_subagent(kw["description"]),
        "load_skill":      lambda **kw: run_load_skill(kw["name"]),
        "compact":         lambda **kw: "[Compacted.]",
        "create_task":     lambda **kw: str(create_task(kw["subject"], kw.get("description", ""), kw.get("blockedBy"))),
        "list_tasks":      lambda **kw: list_tasks(kw.get("filter_status")),
        "get_task":        lambda **kw: get_task(kw["task_id"]),
        "claim_task":      lambda **kw: claim_task(kw["task_id"], kw.get("owner", "agent")),
        "complete_task":   lambda **kw: complete_task(kw["task_id"]),
        "schedule_cron":   lambda **kw: schedule_job(kw["cron"], kw["prompt"], kw.get("recurring", True), kw.get("durable", True)),
        "list_crons":      lambda **kw: list_crons(),
        "cancel_cron":     lambda **kw: cancel_job(kw["job_id"]),
        "spawn_teammate":  lambda **kw: spawn_teammate_thread(kw["name"], kw["role"], kw["prompt"]),
        "send_message":    lambda **kw: run_send_message(kw["to"], kw["content"]),
        "check_inbox":     lambda **kw: run_check_inbox(),
        "request_shutdown":lambda **kw: run_request_shutdown(kw["target"]),
        "request_plan":    lambda **kw: run_request_plan(kw["target"]),
        "review_plan":     lambda **kw: run_review_plan(kw["target"], kw["approve"], kw.get("feedback", "")),
        "create_worktree": lambda **kw: create_worktree(kw["name"], kw.get("task_id", "")),
        "remove_worktree": lambda **kw: remove_worktree(kw["name"], kw.get("discard_changes", False)),
        "keep_worktree":   lambda **kw: keep_worktree(kw["name"]),
        "connect_mcp":     lambda **kw: run_connect_mcp(kw["name"]),
    }

    # -- MCP tools --------------------------------------------------------
    for server_name, mcp_client in mcp.mcp_clients.items():
        safe_server = normalize_name(server_name)
        for td in mcp_client.tools:
            safe_tool = normalize_name(td["name"])
            prefixed = f"mcp__{safe_server}__{safe_tool}"
            tools.append({
                "name": prefixed,
                "description": f"{td['description']} [MCP:{safe_server}]",
                "input_schema": td["input_schema"],
            })

            def _make_mcp_handler(_client, _tool_name):
                def _handler(**kwargs):
                    return _client.call_tool(_tool_name, kwargs)
                return _handler
            handlers[prefixed] = _make_mcp_handler(mcp_client, td["name"])

    if allowed is not None:
        tools = [t for t in tools if t["name"] in allowed]

    return tools, handlers
