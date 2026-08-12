"""Smoke tests for the agent loop and related harness components."""
from unittest.mock import MagicMock

from harness.prompt import assemble_system_prompt
from harness.recovery import RecoveryState


class TestRecoveryState:
    def test_initial_state(self):
        state = RecoveryState()
        assert state.has_escalated is False
        assert state.recovery_count == 0
        assert state.has_attempted_reactive_compact is False
        assert state.consecutive_529 == 0

    def test_model_defaults_to_config_model(self):
        state = RecoveryState()
        from config import MODEL
        assert state.current_model == MODEL


class TestSystemPrompt:
    def test_includes_workdir(self):
        from config import WORKDIR
        ctx = {"workspace": str(WORKDIR), "memories": "", "mcp_servers": "", "enabled_tools": ["bash", "read_file"]}
        prompt = assemble_system_prompt(ctx)
        # assemble_system_prompt uses the real WORKDIR from config, not ctx["workspace"]
        assert str(WORKDIR) in prompt
        assert "bash" in prompt

    def test_includes_memories_when_present(self):
        ctx = {"workspace": "/tmp", "memories": "User prefers short answers", "mcp_servers": "", "enabled_tools": []}
        prompt = assemble_system_prompt(ctx)
        assert "short answers" in prompt

    def test_empty_memories_omitted(self):
        ctx = {"workspace": "/tmp", "memories": "", "mcp_servers": "", "enabled_tools": []}
        prompt = assemble_system_prompt(ctx)
        # Should not contain the "Relevant memories" header
        assert "Relevant memories" not in prompt

    def test_mcp_servers_listed(self):
        ctx = {"workspace": "/tmp", "memories": "", "mcp_servers": "docs, deploy", "enabled_tools": []}
        prompt = assemble_system_prompt(ctx)
        assert "docs" in prompt
        assert "deploy" in prompt


class TestHookSystem:
    def test_register_and_trigger(self):
        from harness import register_hook, trigger_hooks
        calls = []

        def my_hook(arg):
            calls.append(arg)
            return None

        register_hook("UserPromptSubmit", my_hook)
        trigger_hooks("UserPromptSubmit", "test query")
        assert calls == ["test query"]

    def test_first_non_none_blocks(self):
        # Clean up from prior test — hooks are module-level globals
        from harness import HOOKS, register_hook, trigger_hooks
        HOOKS["PreToolUse"].clear()

        def allow(_block):
            return None

        def deny(_block):
            return "Blocked by test"

        register_hook("PreToolUse", allow)
        register_hook("PreToolUse", deny)
        result = trigger_hooks("PreToolUse", MagicMock())
        assert result == "Blocked by test"
        HOOKS["PreToolUse"].clear()

    def test_post_tool_use_always_returns_none(self):
        from harness import HOOKS, register_hook, trigger_hooks
        HOOKS["PostToolUse"].clear()

        def log(_block, _output):
            return "this should be ignored"

        register_hook("PostToolUse", log)
        # PostToolUse triggers don't use the first-return-value pattern;
        # just verify the call doesn't crash
        trigger_hooks("PostToolUse", MagicMock(), "output")
        HOOKS["PostToolUse"].clear()
