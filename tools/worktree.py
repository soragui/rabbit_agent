"""s18: Worktree Isolation — git worktree per task."""
import re

from config import WORKDIR, WORKTREES_DIR
from tools import task_system, run_git


def validate_worktree_name(name: str) -> str | None:
    if not name or len(name) > 64:
        return "Name must be 1-64 characters"
    if not re.match(r'^[A-Za-z0-9._-]+$', name):
        return "Name may only contain [A-Za-z0-9._-]"
    if name in (".", ".."):
        return "Name cannot be . or .."
    return None


def create_worktree(name: str, task_id: str = "") -> str:
    err = validate_worktree_name(name)
    if err:
        return f"Error: {err}"
    path = WORKTREES_DIR / name
    if path.exists():
        return f"Error: Worktree '{name}' already exists"
    ok, result = run_git(["worktree", "add", str(path), "-b", f"wt/{name}", "HEAD"])
    if not ok:
        return f"Git error: {result}"
    if task_id and task_system._path(task_id).exists():
        t = task_system._load(task_id)
        t.worktree = name
        task_system._save(t)
    return f"Worktree '{name}' created at {path}"


def remove_worktree(name: str, discard_changes: bool = False) -> str:
    path = WORKTREES_DIR / name
    if not path.exists():
        return f"Error: Worktree '{name}' not found"
    if not discard_changes:
        ok, status = run_git(["status", "--porcelain"], cwd=str(path))
        if ok and status.strip():
            return ("Worktree has uncommitted changes. "
                    "Use discard_changes=true to force removal, or keep_worktree.")
    ok, result = run_git(["worktree", "remove", str(path), "--force"])
    if not ok:
        return f"Error removing worktree: {result}"
    run_git(["branch", "-D", f"wt/{name}"])
    return f"Worktree '{name}' removed"


def keep_worktree(name: str) -> str:
    return f"Worktree '{name}' kept for review (branch: wt/{name})"
