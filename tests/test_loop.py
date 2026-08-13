"""Smoke tests for the agent loop and related harness components."""
import time as _time
import types
from unittest.mock import MagicMock

import pytest

from harness.prompt import assemble_system_prompt
from harness.recovery import RecoveryState
from harness.ui_bridge import TurnAborted, bridge


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


class TestSleepInterruptible:
    def test_raises_when_abort_requested(self):
        bridge.request_abort()
        start = _time.monotonic()
        with pytest.raises(TurnAborted):
            from loop import _sleep_interruptible
            _sleep_interruptible(5.0)
        assert _time.monotonic() - start < 2.0
        bridge.clear_abort()

    def test_sleeps_when_no_abort(self):
        bridge.clear_abort()
        from loop import _sleep_interruptible
        start = _time.monotonic()
        _sleep_interruptible(0.2)
        assert _time.monotonic() - start >= 0.2


class TestStreamAbort:
    def test_stream_raises_turn_aborted_mid_chunks(self, monkeypatch):
        import loop as loop_mod
        from loop import _stream_llm

        class Evt:
            def __init__(self):
                self.type = "content_block_delta"
                self.delta = types.SimpleNamespace(type="text_delta", text="x")

        class FakeStream:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def __iter__(self):
                for _ in range(50):
                    yield Evt()
                raise AssertionError("stream should have aborted")

            def get_final_message(self):
                return None

        monkeypatch.setattr(
            loop_mod.provider, "create_message_stream", lambda **kw: FakeStream())
        monkeypatch.setattr(
            loop_mod, "streaming_renderer", loop_mod.streaming_renderer)
        bridge.request_abort()
        with pytest.raises(TurnAborted):
            _stream_llm([], [], RecoveryState(), "sys", 100)
        bridge.clear_abort()


class TestToolResultHeuristic:
    def test_aborted_output_rendered_as_failure(self, monkeypatch):
        """[aborted] tool output must render as a failure, not a green check."""
        import loop as loop_mod

        render_calls: list[tuple[str, str, bool]] = []
        stream_calls: list[int] = []

        def fake_stream_llm(messages, tools, state, system, max_tokens):
            stream_calls.append(1)
            if len(stream_calls) == 1:
                return types.SimpleNamespace(
                    stop_reason="tool_use",
                    content=[types.SimpleNamespace(
                        type="tool_use", name="bash", id="toolu_1",
                        input={"command": "x"})])
            return types.SimpleNamespace(stop_reason="stop", content=[])

        monkeypatch.setattr(loop_mod, "_stream_llm", fake_stream_llm)
        monkeypatch.setattr(
            loop_mod, "render_tool_result",
            lambda name, output, ok: render_calls.append((name, output, ok)))

        loop_mod.agent_loop_full(
            [], {}, [], {"bash": lambda **kw: "[aborted]"})

        assert render_calls == [("bash", "[aborted]", False)]
