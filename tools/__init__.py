"""s01/s02: File-system tools — bash, read_file, write_file, edit_file, glob.

Also re-exports from the submodules so other code can ``from tools import ...``.
"""
import subprocess as _subprocess
from pathlib import Path as _Path

from config import WORKDIR, safe_path


def run_bash(command: str, cwd: str = None) -> str:
    try:
        r = _subprocess.run(
            command, shell=True, cwd=cwd or str(WORKDIR),
            capture_output=True, text=True, timeout=300)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except _subprocess.TimeoutExpired:
        return "Error: Timeout (300s)"


def run_read(path: str, limit: int = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit > 0:
            lines = lines[:limit]
        return "\n".join(lines)
    except Exception as e:
        return f"Error reading {path}: {e}"


def run_write(path: str, content: str) -> str:
    try:
        safe_path(path).write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        text = safe_path(path).read_text()
        if old_text not in text:
            return f"Error: text not found in {path}"
        safe_path(path).write_text(text.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


def run_glob(pattern: str) -> str:
    import glob as g
    matches = g.glob(pattern, root_dir=str(WORKDIR))
    return "\n".join(sorted(matches)) if matches else "(no matches)"


def run_git(args: list[str], cwd: str = None) -> tuple[bool, str]:
    """Run a git command, return (success, output)."""
    try:
        r = _subprocess.run(
            ["git"] + args, cwd=cwd or str(WORKDIR),
            capture_output=True, text=True, timeout=30)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:
        return False, str(e)
