"""Tests for 4-tier permission system."""
import threading
import time as _time
from unittest.mock import MagicMock, patch

from harness import render
from harness.permissions import (
    DENY_LIST,
    NEVER,
    _approved_once,
    _get_tier,
    _prompt_user,
)
from harness.ui_bridge import bridge


class TestTierClassification:
    def test_safe_tools(self):
        for tool in ["read_file", "glob", "grep", "list_tasks", "get_task",
                      "load_skill", "check_inbox", "list_crons", "compact",
                      "todo_write", "keep_worktree"]:
            assert _get_tier(tool) == "safe", f"{tool} should be safe"

    def test_ask_once_tools(self):
        for tool in ["bash", "write_file", "edit_file", "task", "create_task",
                      "claim_task", "complete_task", "send_message"]:
            assert _get_tier(tool) == "ask-once", f"{tool} should be ask-once"

    def test_always_ask_tools(self):
        for tool in ["schedule_cron", "cancel_cron", "connect_mcp",
                      "spawn_teammate", "create_worktree", "remove_worktree",
                      "request_shutdown", "request_plan", "review_plan"]:
            assert _get_tier(tool) == "always-ask", f"{tool} should be always-ask"

    def test_never_tier_is_empty(self):
        assert len(NEVER) == 0

    def test_mcp_tools_are_ask_once(self):
        assert _get_tier("mcp__docs__search") == "ask-once"
        assert _get_tier("mcp__deploy__trigger") == "ask-once"

    def test_unknown_tools_are_always_ask(self):
        assert _get_tier("some_future_tool") == "always-ask"


class TestDenyList:
    def test_rm_rf_slash_denied(self):
        assert any("rm -rf /" in p for p in DENY_LIST)

    def test_fork_bomb_denied(self):
        assert any(":(){ :|:& };:" in p for p in DENY_LIST)

    def test_dd_if_denied(self):
        assert any("dd if=" in p for p in DENY_LIST)


class TestPermissionHook:
    def test_safe_tool_allowed(self):
        from harness.permissions import _permission_hook
        block = MagicMock()
        block.name = "read_file"
        block.input = {"path": "test.txt"}
        assert _permission_hook(block) is None

    def test_workspace_boundary_blocked(self):
        from harness.permissions import _permission_hook
        block = MagicMock()
        block.name = "write_file"
        block.input = {"path": "../../../etc/passwd"}
        result = _permission_hook(block)
        assert result is not None
        assert "outside workspace" in str(result)

    def test_deny_list_pattern_blocked(self):
        from harness.permissions import _permission_hook
        block = MagicMock()
        block.name = "bash"
        block.input = {"command": "rm -rf /etc/config"}
        result = _permission_hook(block)
        assert result is not None
        assert "deny list" in str(result).lower() or "permission denied" in str(result).lower()

    def test_ask_once_approved_with_prompt(self):
        from harness.permissions import _permission_hook

        # Clear session state
        _approved_once.clear()

        block = MagicMock()
        block.name = "write_file"
        block.input = {"path": "test.txt", "content": "hello"}

        with patch("harness.permissions.input", return_value="y"):
            # First call: prompts
            result1 = _permission_hook(block)
            assert result1 is None
            assert "write_file" in _approved_once

            # Second call: no prompt needed
            result2 = _permission_hook(block)
            assert result2 is None

        _approved_once.clear()

    def test_ask_once_denied_stops(self):
        from harness.permissions import _permission_hook

        _approved_once.clear()
        block = MagicMock()
        block.name = "write_file"
        block.input = {"path": "test.txt", "content": "hello"}

        with patch("harness.permissions.input", return_value="n"):
            result = _permission_hook(block)
            assert result is not None
            assert "write_file" not in _approved_once

        _approved_once.clear()

    def test_dangerous_bash_always_prompts(self):
        from harness.permissions import _permission_hook

        _approved_once.clear()
        block = MagicMock()
        block.name = "bash"
        block.input = {"command": "rm temp.txt"}

        with patch("harness.permissions.input", return_value="y"):
            result = _permission_hook(block)
            assert result is None

        _approved_once.clear()

    def test_safe_bash_ask_once(self):
        from harness.permissions import _permission_hook

        _approved_once.clear()
        block = MagicMock()
        block.name = "bash"
        block.input = {"command": "echo hello"}

        with patch("harness.permissions.input", return_value="y"):
            result = _permission_hook(block)
            assert result is None
            assert "bash" in _approved_once

        _approved_once.clear()


class TestPromptUserTui:
    def test_tui_prompt_answered_yes(self, monkeypatch):
        monkeypatch.setattr(render, "_TUI_ACTIVE", True)

        def ui():
            while not bridge.has_pending_question():
                _time.sleep(0.01)
            bridge.answer_question("y")

        t = threading.Thread(target=ui)
        t.start()
        assert _prompt_user("bash") is True
        t.join()
        bridge.drain()

    def test_tui_prompt_empty_denies(self, monkeypatch):
        monkeypatch.setattr(render, "_TUI_ACTIVE", True)

        def ui():
            while not bridge.has_pending_question():
                _time.sleep(0.01)
            bridge.answer_question("")

        t = threading.Thread(target=ui)
        t.start()
        assert _prompt_user("bash") is False
        t.join()
        bridge.drain()
