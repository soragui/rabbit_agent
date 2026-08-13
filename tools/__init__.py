"""s01/s02: File-system tools — bash, read_file, write_file, edit_file, glob.

Also re-exports from the submodules so other code can ``from tools import ...``.
"""
import contextlib
import os as _os
import signal as _signal
import subprocess as _subprocess
import time as _time

from config import WORKDIR, safe_path
from harness.ui_bridge import bridge


def _kill_tree(proc: _subprocess.Popen) -> None:
    """Kill the process group (shell + children), falling back to the process."""
    try:
        _os.killpg(_os.getpgid(proc.pid), _signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


def run_bash(command: str, cwd: str = None) -> str:
    try:
        proc = _subprocess.Popen(
            command, shell=True, cwd=cwd or str(WORKDIR),
            stdout=_subprocess.PIPE, stderr=_subprocess.PIPE, text=True,
            start_new_session=True)
    except Exception as e:
        return f"Error: {e}"

    deadline = _time.monotonic() + 300
    while True:
        if bridge.is_abort_requested():
            _kill_tree(proc)
            out, err = proc.communicate()
            partial = (out + err).strip()[:2000]
            return f"[aborted]\n{partial}" if partial else "[aborted]"
        try:
            out, err = proc.communicate(timeout=0.1)
            break
        except _subprocess.TimeoutExpired:
            if _time.monotonic() > deadline:
                _kill_tree(proc)
                proc.communicate()
                return "Error: Timeout (300s)"

    combined = (out + err).strip()
    return combined[:50000] if combined else "(no output)"


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


def run_web_fetch(url: str) -> str:
    """Fetch a URL and return its content as markdown text."""
    import html2text as _html2text
    import httpx
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=30)
        resp.raise_for_status()
        ct = resp.headers.get("content-type", "")
        if "text/html" in ct:
            h = _html2text.HTML2Text()
            h.ignore_links = False
            h.ignore_images = True
            markdown = h.handle(resp.text)
            return markdown[:50000] if len(markdown) > 50000 else markdown
        return resp.text[:50000]
    except Exception as e:
        return f"Web fetch error: {e}"


def run_web_search(query: str, max_results: int = 8) -> str:
    """Search the web using DuckDuckGo (no API key required)."""
    import httpx
    try:
        resp = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": "51agent/0.1"},
            timeout=15,
        )
        resp.raise_for_status()
        from html.parser import HTMLParser as _HTMLParser

        results = []
        current = {}
        in_link = False

        class _Parser(_HTMLParser):
            def handle_starttag(self, tag, attrs):
                nonlocal current, in_link
                d = dict(attrs)
                if tag == "a" and "result__a" in d.get("class", ""):
                    current = {"title": "", "url": d.get("href", "").replace("//duckduckgo.com/l/?uddg=", "")}
                    in_link = True
                elif tag == "a" and "result__snippet" in d.get("class", ""):
                    current["snippet"] = ""

            def handle_data(self, data):
                nonlocal current, in_link
                if in_link and "title" in current:
                    current["title"] += data
                elif "snippet" in current:
                    current["snippet"] += data

            def handle_endtag(self, tag):
                nonlocal current, in_link, results
                if tag == "a" and in_link:
                    in_link = False
                elif tag == "a" and "snippet" in current:
                    if current.get("title"):
                        results.append(dict(current))
                    current = {}
                if len(results) >= max_results:
                    pass

        _Parser().feed(resp.text)

        if not results:
            # Fallback: return raw text snippet
            return f"No structured results found for '{query}'. Try a different query."

        lines = [f"Search results for '{query}':"]
        for i, r in enumerate(results[:max_results], 1):
            title = r.get("title", "").strip()
            snippet = r.get("snippet", "").strip()
            lines.append(f"\n{i}. {title}")
            if snippet:
                lines.append(f"   {snippet[:200]}")
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Web search error: {e}"


def run_git(args: list[str], cwd: str = None) -> tuple[bool, str]:
    """Run a git command, return (success, output)."""
    try:
        r = _subprocess.run(
            ["git"] + args, cwd=cwd or str(WORKDIR),
            capture_output=True, text=True, timeout=30)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:
        return False, str(e)
