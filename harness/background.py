"""s13: Background Tasks — run slow operations in daemon threads."""
import threading
import time

background_tasks: dict[str, dict] = {}
background_results: dict[str, str] = {}
_lock = threading.Lock()
_counter = [0]


def is_slow_operation(tool_name: str, tool_input: dict) -> bool:
    if tool_name != "bash":
        return False
    cmd = tool_input.get("command", "").lower()
    slow_kw = ["install", "build", "test", "deploy", "compile",
               "docker build", "pip install", "npm install",
               "cargo build", "pytest", "make"]
    return any(kw in cmd for kw in slow_kw)


def should_run_background(tool_name: str, tool_input: dict) -> bool:
    if tool_input.get("run_in_background"):
        return True
    return is_slow_operation(tool_name, tool_input)


def start_background_task(block, handler) -> str:
    _counter[0] += 1
    bg_id = f"bg_{_counter[0]:04d}"

    def worker():
        try:
            result = handler(**block.input)
        except Exception as e:
            result = f"Error: {e}"
        with _lock:
            background_tasks[bg_id]["status"] = "completed"
            background_results[bg_id] = str(result)

    with _lock:
        background_tasks[bg_id] = {
            "tool_use_id": block.id,
            "command": str(block.input)[:100],
            "status": "running",
        }
    threading.Thread(target=worker, daemon=True).start()
    return bg_id


def collect_background_results() -> list[str]:
    with _lock:
        ready = [bid for bid, t in background_tasks.items() if t["status"] == "completed"]
    notifications = []
    for bg_id in ready:
        with _lock:
            task = background_tasks.pop(bg_id, None)
            output = background_results.pop(bg_id, "")
        if task:
            notifications.append(
                f"<task_notification>\n"
                f"  <task_id>{bg_id}</task_id>\n"
                f"  <status>completed</status>\n"
                f"  <command>{task['command']}</command>\n"
                f"  <summary>{output[:200]}</summary>\n"
                f"</task_notification>")
    return notifications
