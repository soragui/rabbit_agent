"""s12: Task System — file-persisted task graph with dependency tracking."""
import json, time, random
from dataclasses import dataclass, asdict

from config import TASKS_DIR


@dataclass
class Task:
    id: str
    subject: str
    description: str
    status: str          # pending | in_progress | completed
    owner: str | None
    blockedBy: list[str]
    worktree: str | None = None


def _path(task_id: str):
    return TASKS_DIR / f"{task_id}.json"


def _save(task: Task):
    _path(task.id).write_text(json.dumps(asdict(task), indent=2, ensure_ascii=False))


def _load(task_id: str) -> Task:
    return Task(**json.loads(_path(task_id).read_text()))


def create_task(subject: str, description: str = "", blockedBy: list = None) -> Task:
    task = Task(
        id=f"task_{int(time.time())}_{random.randint(0, 9999):04d}",
        subject=subject, description=description,
        status="pending", owner=None,
        blockedBy=blockedBy or [], worktree=None)
    _save(task)
    return task


def can_start(task_id: str) -> bool:
    task = _load(task_id)
    for dep_id in task.blockedBy:
        if not _path(dep_id).exists():
            return False
        if _load(dep_id).status != "completed":
            return False
    return True


def claim_task(task_id: str, owner: str = "agent") -> str:
    task = _load(task_id)
    if task.status != "pending":
        return f"Task {task_id} is {task.status}, cannot claim"
    if task.owner:
        return f"Task {task_id} already owned by {task.owner}"
    if not can_start(task_id):
        unfinished = [d for d in task.blockedBy
                      if _path(d).exists() and _load(d).status != "completed"]
        return f"Blocked by: {unfinished or task.blockedBy}"
    task.owner = owner
    task.status = "in_progress"
    _save(task)
    return f"Claimed {task.id} ({task.subject})"


def complete_task(task_id: str) -> str:
    task = _load(task_id)
    task.status = "completed"
    _save(task)
    unblocked = []
    for f in sorted(TASKS_DIR.glob("task_*.json")):
        t = json.loads(f.read_text())
        if t.get("status") == "pending" and t.get("blockedBy"):
            if all(_path(d).exists() and _load(d).status == "completed"
                   for d in t["blockedBy"]):
                unblocked.append(t["subject"])
    msg = f"Completed {task_id} ({task.subject})"
    if unblocked:
        msg += f"\nUnblocked: {', '.join(unblocked)}"
    return msg


def list_tasks(filter_status: str = None) -> str:
    tasks = []
    for f in sorted(TASKS_DIR.glob("task_*.json")):
        t = json.loads(f.read_text())
        if filter_status and t.get("status") != filter_status:
            continue
        owner = t.get("owner", "")
        blocked = ",".join(t.get("blockedBy", [])[:2])
        tasks.append(f"  [{t['status']}] {t['id']} - {t['subject']}"
                     f"{' (owner:' + owner + ')' if owner else ''}"
                     f"{' blockedBy:' + blocked if blocked else ''}")
    return "\n".join(tasks) if tasks else "(no tasks)"


def get_task(task_id: str) -> str:
    return json.dumps(asdict(_load(task_id)), indent=2, ensure_ascii=False)


def scan_unclaimed() -> list[dict]:
    """s17: Find pending tasks with no owner and all deps completed."""
    unclaimed = []
    for f in sorted(TASKS_DIR.glob("task_*.json")):
        task = json.loads(f.read_text())
        if (task.get("status") == "pending" and not task.get("owner")
                and can_start(task["id"])):
            unclaimed.append(task)
    return unclaimed
