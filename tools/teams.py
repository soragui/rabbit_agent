"""s15/s16/s17: Agent Teams — MessageBus, protocols, spawn, idle loop."""
import json, time, threading, random, copy
from pathlib import Path
from dataclasses import dataclass

from config import MODEL, WORKDIR, MAILBOX_DIR, WORKTREES_DIR, client
from tools import task_system
from tools.cron import _lock  # noqa: F401 — keep for future use
from harness.render import render_error, render_info, spinner


# -- MessageBus ------------------------------------------------------------
class MessageBus:
    def send(self, from_agent: str, to_agent: str, content: str,
             msg_type: str = "message", metadata: dict = None):
        msg = {"from": from_agent, "to": to_agent, "content": content,
               "type": msg_type, "ts": time.time()}
        if metadata:
            msg["metadata"] = metadata
        inbox = MAILBOX_DIR / f"{to_agent}.jsonl"
        with open(inbox, "a") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")

    def read_inbox(self, agent: str) -> list[dict]:
        inbox = MAILBOX_DIR / f"{agent}.jsonl"
        if not inbox.exists():
            return []
        msgs = [json.loads(line) for line in inbox.read_text().splitlines() if line.strip()]
        inbox.unlink()
        return msgs


BUS = MessageBus()

# -- Protocol state --------------------------------------------------------
@dataclass
class ProtocolState:
    request_id: str
    type: str
    sender: str
    target: str
    status: str     # pending | approved | rejected
    payload: str
    created_at: float


pending_requests: dict[str, ProtocolState] = {}
active_teammates: dict[str, dict] = {}
IDLE_POLL_INTERVAL = 5
IDLE_TIMEOUT = 60


def _new_request_id() -> str:
    return f"req_{int(time.time())}_{random.randint(0, 999999):06d}"


# -- Inbox / protocol helpers ----------------------------------------------
def _match_response(response_type: str, request_id: str, approve: bool):
    state = pending_requests.get(request_id)
    if not state:
        return
    if state.type == "shutdown" and response_type != "shutdown_response":
        return
    if state.type == "plan_approval" and response_type != "plan_approval_response":
        return
    if state.status != "pending":
        return
    state.status = "approved" if approve else "rejected"


def consume_lead_inbox(route_protocol: bool = True) -> list[dict]:
    msgs = BUS.read_inbox("lead")
    if route_protocol:
        for msg in msgs:
            meta = msg.get("metadata", {})
            rid = meta.get("request_id", "")
            mtype = msg.get("type", "")
            if rid and mtype.endswith("_response"):
                _match_response(mtype, rid, meta.get("approve", False))
    return msgs


def _handle_inbox_message(agent_name: str, msg: dict, messages: list) -> bool:
    """Returns True if agent should exit."""
    mtype = msg.get("type", "message")
    meta = msg.get("metadata", {})
    rid = meta.get("request_id", "")

    if mtype == "shutdown_request":
        BUS.send(agent_name, "lead", "Shutting down.", "shutdown_response",
                 {"request_id": rid, "approve": True})
        return True
    if mtype == "plan_approval_response":
        approve = meta.get("approve", False)
        prefix = "[Plan approved]" if approve else "[Plan rejected]"
        messages.append({"role": "user", "content": f"{prefix}: {msg.get('content', '')}"})
    else:
        messages.append({"role": "user",
                        "content": f"[Inbox from {msg['from']}]: {msg.get('content', '')[:500]}"})
    return False


# -- Teammate tools --------------------------------------------------------
def _tm_tools() -> tuple[list[dict], dict]:
    tools = [
        {"name": "bash",        "description": "Run a shell command.", "input_schema": {
            "type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
        {"name": "read_file",   "description": "Read a file.", "input_schema": {
            "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
        {"name": "write_file",  "description": "Write content to a file.", "input_schema": {
            "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]}},
        {"name": "send_message","description": "Send message to lead.", "input_schema": {
            "type": "object", "properties": {"to": {"type": "string"}, "content": {"type": "string"}},
            "required": ["to", "content"]}},
        {"name": "list_tasks",  "description": "List all tasks.", "input_schema": {
            "type": "object", "properties": {"filter_status": {"type": "string"}}}},
        {"name": "claim_task",  "description": "Claim a task.", "input_schema": {
            "type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
        {"name": "complete_task","description": "Mark task as completed.", "input_schema": {
            "type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}},
    ]

    from tools import run_bash, run_read, run_write
    handlers = {
        "bash":         lambda **kw: run_bash(kw.get("command", "")),
        "read_file":    lambda **kw: run_read(kw["path"]),
        "write_file":   lambda **kw: run_write(kw["path"], kw["content"]),
        "send_message": lambda **kw: (BUS.send("teammate", kw.get("to", "lead"), kw.get("content", "")) or "Message sent."),
        "list_tasks":   lambda **kw: task_system.list_tasks(kw.get("filter_status")),
        "claim_task":   lambda **kw: task_system.claim_task(kw["task_id"]),
        "complete_task":lambda **kw: task_system.complete_task(kw["task_id"]),
    }
    return tools, handlers


def _tm_execute(agent_name, block, handler, wt_ctx):
    from tools import run_bash
    if block.name == "bash":
        return run_bash(block.input.get("command", ""), cwd=wt_ctx.get("path"))
    if block.name in ("read_file", "write_file") and wt_ctx.get("path"):
        p = block.input.get("path", "")
        if not p.startswith("/"):
            new_input = copy.deepcopy(dict(block.input))
            new_input["path"] = str(Path(wt_ctx["path"]) / p)
            return handler(**new_input)
    return handler(**block.input)


# -- s17: idle poll --------------------------------------------------------
def idle_poll(agent_name: str, messages: list, role: str) -> str:
    """Returns 'work', 'shutdown', or 'timeout'."""
    for _ in range(IDLE_TIMEOUT // IDLE_POLL_INTERVAL):
        time.sleep(IDLE_POLL_INTERVAL)
        inbox = BUS.read_inbox(agent_name)
        if inbox:
            should_exit = False
            for msg in inbox:
                if msg.get("type") == "shutdown_request":
                    rid = msg.get("metadata", {}).get("request_id", "")
                    BUS.send(agent_name, "lead", "Shutting down.", "shutdown_response",
                             {"request_id": rid, "approve": True})
                    should_exit = True
                else:
                    messages.append({"role": "user", "content": f"[Inbox]: {msg.get('content', '')[:500]}"})
            return "shutdown" if should_exit else "work"

        unclaimed = task_system.scan_unclaimed()
        if unclaimed:
            task = unclaimed[0]
            result = task_system.claim_task(task["id"], agent_name)
            if "Claimed" in result:
                task_data = task_system._load(task["id"])
                wt_info = ""
                if task_data.worktree:
                    wt_info = f"\nWorktree: {WORKTREES_DIR / task_data.worktree}"
                messages.append({"role": "user",
                                "content": f"Claimed task: '{task['subject']}'. "
                                           f"Complete it and mark as completed.{wt_info}"})
                return "work"
    return "timeout"


# -- spawn_teammate --------------------------------------------------------
SPAWN_COUNTER = [0]


def spawn_teammate_thread(name: str, role: str, prompt: str) -> str:
    name_safe = name.lower().replace(" ", "_")
    SPAWN_COUNTER[0] += 1
    system = (f"You are '{name}', a {role}. "
              f"Communicate via send_message when needed. "
              f"Complete your task, then list_tasks to find more work.")

    def run():
        messages = [{"role": "user", "content": prompt}]
        wt_ctx = {"path": None}
        all_tools, all_handlers = _tm_tools()

        while True:
            for _ in range(10):
                inbox = BUS.read_inbox(name_safe)
                should_exit = False
                for msg in inbox:
                    should_exit = _handle_inbox_message(name_safe, msg, messages)
                if should_exit:
                    BUS.send(name_safe, "lead",
                             f"Shutdown complete. Summary: I was working on: {prompt[:200]}", "result")
                    return

                try:
                    with spinner(f"Teammate {name} thinking..."):
                        response = client.messages.create(
                            model=MODEL, system=system, messages=messages[-30:],
                            tools=all_tools, max_tokens=8000)
                except Exception as e:
                    render_error(f"Teammate {name}: {e}")
                    break

                messages.append({"role": "assistant", "content": response.content})
                if response.stop_reason != "tool_use":
                    break

                results = []
                for block in response.content:
                    if block.type == "tool_use":
                        handler = all_handlers.get(block.name)
                        if handler:
                            try:
                                output = _tm_execute(name_safe, block, handler, wt_ctx)
                            except Exception as e:
                                output = f"Error: {e}"
                        else:
                            output = f"Unknown tool: {block.name}"
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})
                messages.append({"role": "user", "content": results})

                # switch cwd when claiming worktree-bound task
                for block in response.content:
                    if block.type == "tool_use" and block.name == "claim_task":
                        tid = block.input.get("task_id", "")
                        if tid and task_system._path(tid).exists():
                            t = task_system._load(tid)
                            if t.worktree:
                                wt_ctx["path"] = str(WORKTREES_DIR / t.worktree)

            idle_result = idle_poll(name_safe, messages, role)
            if idle_result in ("shutdown", "timeout"):
                break

        summary = f"Completed work. Last task was: {prompt[:200]}"
        BUS.send(name_safe, "lead", summary, "result", {"agent": name})

    t = threading.Thread(target=run, daemon=True)
    t.start()
    active_teammates[name_safe] = {"name": name, "role": role, "thread": t}
    return f"Spawned teammate '{name}' ({role}). They will communicate via inbox."


# -- Lead-facing tools -----------------------------------------------------
def run_send_message(to: str, content: str) -> str:
    BUS.send("lead", to, content)
    return f"Message sent to {to}"


def run_check_inbox() -> str:
    msgs = consume_lead_inbox(route_protocol=False)
    if not msgs:
        return "(inbox empty)"
    return "\n".join(f"From {m['from']} ({m.get('type', 'message')}): {m['content'][:200]}" for m in msgs)


def run_request_shutdown(target: str) -> str:
    rid = _new_request_id()
    pending_requests[rid] = ProtocolState(
        request_id=rid, type="shutdown", sender="lead", target=target,
        status="pending", payload="Shutdown requested", created_at=time.time())
    BUS.send("lead", target, "Please finish your work and shutdown.", "shutdown_request", {"request_id": rid})
    return f"Shutdown requested for {target} (request_id: {rid})"


def run_request_plan(target: str) -> str:
    rid = _new_request_id()
    pending_requests[rid] = ProtocolState(
        request_id=rid, type="plan_approval", sender="lead", target=target,
        status="pending", payload="Plan approval requested", created_at=time.time())
    BUS.send("lead", target, "Please submit your plan for approval.", "plan_approval_request", {"request_id": rid})
    return f"Plan requested from {target} (request_id: {rid})"


def run_review_plan(target: str, approve: bool, feedback: str = "") -> str:
    for rid, state in pending_requests.items():
        if state.type == "plan_approval" and state.target == target and state.status == "pending":
            BUS.send("lead", target, feedback or ("Approved" if approve else "Rejected"),
                     "plan_approval_response", {"request_id": rid, "approve": approve})
            state.status = "approved" if approve else "rejected"
            return f"Plan {'approved' if approve else 'rejected'} for {target}"
    return f"No pending plan request from {target}"
