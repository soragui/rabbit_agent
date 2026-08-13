"""Unit tests for status bar collectors — no subprocesses are really spawned."""
from pathlib import Path
from types import SimpleNamespace

from harness import status


class FakeRunner:
    """Replaces subprocess.run for collector tests."""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        key = cmd[1]  # e.g. "rev-parse", "status", "--version"
        rc, out = self.results.get(key, (0, ""))
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")


class TestGitSummary:
    def test_clean_repo(self, monkeypatch):
        runner = FakeRunner({"rev-parse": (0, "main\n"), "status": (0, "")})
        monkeypatch.setattr(status.subprocess, "run", runner)
        monkeypatch.setattr(status.shutil, "which", lambda x: "/usr/bin/git")
        assert status.git_summary("/repo") == "main ✓"

    def test_dirty_repo_counts_changes(self, monkeypatch):
        runner = FakeRunner({"rev-parse": (0, "dev\n"), "status": (0, "M a.py\n?? b.py\n")})
        monkeypatch.setattr(status.subprocess, "run", runner)
        monkeypatch.setattr(status.shutil, "which", lambda x: "/usr/bin/git")
        assert status.git_summary("/repo") == "dev ✗2"

    def test_not_a_repo_returns_none(self, monkeypatch):
        runner = FakeRunner({"rev-parse": (128, "")})
        monkeypatch.setattr(status.subprocess, "run", runner)
        monkeypatch.setattr(status.shutil, "which", lambda x: "/usr/bin/git")
        assert status.git_summary("/repo") is None

    def test_no_git_returns_none(self, monkeypatch):
        monkeypatch.setattr(status.shutil, "which", lambda x: None)
        assert status.git_summary("/repo") is None


class TestNodeVersion:
    def test_strips_leading_v(self, monkeypatch):
        runner = FakeRunner({"--version": (0, "v22.11.0\n")})
        monkeypatch.setattr(status.subprocess, "run", runner)
        monkeypatch.setattr(status.shutil, "which", lambda x: "/usr/bin/node")
        assert status.node_version() == "node 22.11.0"

    def test_missing_node_returns_none(self, monkeypatch):
        monkeypatch.setattr(status.shutil, "which", lambda x: None)
        assert status.node_version() is None


class TestPythonVersion:
    def test_matches_sys_version(self):
        assert status.python_version().startswith("py 3.")


class TestTokenTracker:
    def test_display_before_first_call(self):
        t = status.TokenTracker()
        assert t.display.startswith("ctx –/")

    def test_update_and_display(self):
        t = status.TokenTracker()
        t.update(SimpleNamespace(input_tokens=9400, output_tokens=3000))
        assert t.display == "ctx 12.4k/50k"

    def test_update_with_none_is_noop(self):
        t = status.TokenTracker()
        t.update(None)
        assert t.display.startswith("ctx –/")


class TestTTLCache:
    def test_recomputes_after_ttl(self):
        calls = []

        def fn():
            calls.append(1)
            return len(calls)

        cache = status._TTLCache(2.0)
        assert cache.get(fn, now=0.0) == 1
        assert cache.get(fn, now=1.9) == 1  # cached
        assert cache.get(fn, now=2.1) == 2  # expired, recomputed


class TestCollectStatus:
    def test_segments_ordered_and_none_omitted(self, monkeypatch):
        monkeypatch.setattr(status, "tracker", status.TokenTracker())
        monkeypatch.setattr(status, "_git_cache", status._TTLCache(0.0))
        monkeypatch.setattr(status, "_node_cache", status._TTLCache(0.0))
        monkeypatch.setattr(status, "git_summary", lambda w, timeout=2.0: None)
        monkeypatch.setattr(status, "node_version", lambda timeout=2.0: "node 22.11.0")
        monkeypatch.setattr(status, "cron_count", lambda: 0)
        segments = status.collect_status(Path("/repo"), state="thinking")
        assert segments[0].startswith("ctx ")
        assert segments[1] == "thinking"
        assert segments[2] == "/repo"
        assert any(s.startswith("py 3.") for s in segments)
        assert any(s.startswith("node ") for s in segments)
        # git was None and cron was 0 -> both omitted
        assert not any("git" in s for s in segments)
        assert not any("cron" in s for s in segments)

    def test_cron_segment_when_jobs_queued(self, monkeypatch):
        monkeypatch.setattr(status, "tracker", status.TokenTracker())
        monkeypatch.setattr(status, "_git_cache", status._TTLCache(0.0))
        monkeypatch.setattr(status, "_node_cache", status._TTLCache(0.0))
        monkeypatch.setattr(status, "git_summary", lambda w, timeout=2.0: "main ✓")
        monkeypatch.setattr(status, "node_version", lambda timeout=2.0: None)
        monkeypatch.setattr(status, "cron_count", lambda: 3)
        segments = status.collect_status(Path("/repo"))
        assert segments[-1] == "3 cron"
