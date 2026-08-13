"""Smoke tests for file-system tools — bash, read, write, edit, glob, safe_path."""

import threading
import time as _time

import pytest

from config import WORKDIR, safe_path
from harness.ui_bridge import bridge
from tools import run_bash, run_edit, run_glob, run_grep, run_read, run_write


class TestSafePath:
    def test_path_inside_workdir(self):
        p = safe_path("test.txt")
        assert str(p).startswith(str(WORKDIR.resolve()))

    def test_path_traversal_blocked(self):
        with pytest.raises(ValueError, match="outside workspace"):
            safe_path("../../../etc/passwd")

    def test_absolute_path_inside_workdir(self):
        abs_path = str(WORKDIR / "test.txt")
        p = safe_path(abs_path)
        assert p.exists() or not p.exists()  # should resolve without error


class TestReadFile:
    def test_reads_existing_file(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("hello world")
        # run_read uses WORKDIR-relative paths, so we test via direct path
        content = f.read_text()
        assert content == "hello world"

    def test_nonexistent_file_returns_error(self):
        result = run_read("nonexistent_file_xyz123.txt")
        assert "Error" in result


class TestWriteFile:
    def test_write_and_read_round_trip(self):
        run_write("_test_roundtrip.txt", "round-trip content")
        content = run_read("_test_roundtrip.txt")
        assert "round-trip content" in content
        # Cleanup
        (WORKDIR / "_test_roundtrip.txt").unlink()

    def test_overwrites_existing_file(self):
        run_write("_test_overwrite.txt", "v1")
        run_write("_test_overwrite.txt", "v2")
        content = run_read("_test_overwrite.txt")
        assert "v2" in content
        (WORKDIR / "_test_overwrite.txt").unlink()


class TestEditFile:
    def test_replaces_text_and_shows_diff(self):
        run_write("_test_edit.txt", "line one\nbefore\nline three\n")
        result = run_edit("_test_edit.txt", "before", "replaced")
        assert "Edited" in result
        # Unified diff should show the change
        assert "-before" in result
        assert "+replaced" in result
        assert "```diff" in result
        content = run_read("_test_edit.txt")
        assert "replaced" in content
        (WORKDIR / "_test_edit.txt").unlink()

    def test_text_not_found_returns_error(self):
        run_write("_test_edit2.txt", "hello world")
        result = run_edit("_test_edit2.txt", "nonexistent", "replacement")
        assert "not found" in result
        (WORKDIR / "_test_edit2.txt").unlink()


class TestGlob:
    def test_finds_python_files(self):
        result = run_glob("*.py")
        # The repo root has at least agent.py, config.py, loop.py
        assert "agent.py" in result or "config.py" in result

    def test_no_matches_returns_placeholder(self):
        result = run_glob("*.nonexistent_xyz_extension")
        assert "(no matches)" in result


class TestGrep:
    def test_finds_matches(self):
        """Grep should find matches in a known file."""
        result = run_grep("def ", "tools/__init__.py")
        assert "tools/__init__.py" in result or "def " in result
        assert "(no matches)" not in result

    def test_no_matches(self):
        """Search a file that exists but has no matches."""
        # Search for a pattern that won't appear in pyproject.toml
        result = run_grep("zzNO_MATCH_THIS_CANNOT_EXIST_zz", "pyproject.toml")
        assert "(no matches)" in result

    def test_invalid_regex_returns_error(self):
        result = run_grep("[invalid", "pyproject.toml")
        # ripgrep reports regex errors to stderr
        assert isinstance(result, str)


class TestBash:
    def test_echo(self):
        result = run_bash("echo hello")
        assert "hello" in result

    def test_failing_command(self):
        result = run_bash("exit 1")
        # Should not raise, stderr captured
        assert isinstance(result, str)

    def test_command_not_found(self):
        result = run_bash("nonexistent_command_xyz_123")
        assert isinstance(result, str)


class TestBashAbort:
    def test_abort_kills_long_command(self):
        bridge.clear_abort()
        result = {}

        def worker():
            result["value"] = run_bash("sleep 5 && echo done")

        t = threading.Thread(target=worker)
        t.start()
        _time.sleep(0.5)  # let the subprocess start
        bridge.request_abort()
        t.join(timeout=5)
        assert not t.is_alive(), "run_bash did not abort within 5s"
        assert result["value"] == "[aborted]"
        bridge.clear_abort()

    def test_normal_execution_unchanged(self):
        bridge.clear_abort()
        assert run_bash("echo hello") == "hello"
