"""s01/s02: File-system tools — bash, read_file, write_file, edit_file, glob.

Also re-exports from the submodules so other code can ``from tools import ...``.
"""
import subprocess as _subprocess

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
        before = safe_path(path).read_text()
        if old_text not in before:
            return f"Error: text not found in {path}"
        after = before.replace(old_text, new_text, 1)
        safe_path(path).write_text(after)

        import difflib
        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
        )
        diff_text = "".join(diff)
        return f"Edited {path}\n\n```diff\n{diff_text}```"
    except Exception as e:
        return f"Error: {e}"


def run_glob(pattern: str) -> str:
    import glob as g
    matches = g.glob(pattern, root_dir=str(WORKDIR))
    return "\n".join(sorted(matches)) if matches else "(no matches)"


def run_grep(pattern: str, path: str = ".", max_results: int = 50) -> str:
    """Search file contents with ripgrep. Falls back to grep -r if rg is missing."""
    try:
        r = _subprocess.run(
            ["rg", "--line-number", "--no-heading", "--color", "never",
             "--max-count", str(max_results), pattern, path],
            cwd=str(WORKDIR),
            capture_output=True, text=True, timeout=30,
        )
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no matches)"
    except FileNotFoundError:
        # ripgrep not installed — fall back to grep -r
        try:
            r = _subprocess.run(
                ["grep", "-rn", "--color=never", "-m", str(max_results), pattern, path],
                cwd=str(WORKDIR),
                capture_output=True, text=True, timeout=30,
            )
            out = (r.stdout + r.stderr).strip()
            return out[:50000] if out else "(no matches)"
        except Exception as e:
            return f"Grep error: {e}"
    except _subprocess.TimeoutExpired:
        return "Error: Grep timed out (30s)"
    except Exception as e:
        return f"Grep error: {e}"


def run_git(args: list[str], cwd: str = None) -> tuple[bool, str]:
    """Run a git command, return (success, output)."""
    try:
        r = _subprocess.run(
            ["git"] + args, cwd=cwd or str(WORKDIR),
            capture_output=True, text=True, timeout=30)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:
        return False, str(e)
