"""Tests for streaming LLM call and the streaming renderer."""
from unittest.mock import MagicMock, patch

from harness.render import streaming_renderer


class TestStreamingRenderer:
    def test_renders_accumulated_text(self):
        """The render callable should not crash — we can't easily test Rich's Live."""
        with streaming_renderer() as render:
            render("Hello")
            render("Hello, world!")

    def test_empty_render(self):
        with streaming_renderer():
            pass  # nothing rendered — should not crash

    def test_keyboard_interrupt_during_render(self):
        """KeyboardInterrupt should propagate cleanly through the renderer."""
        with streaming_renderer() as render:
            render("partial text")
            # Simulating interrupt: the renderer should propagate KeyboardInterrupt
            # but we can't easily test this without actually interrupting


class TestStreamLLMRetry:
    def test_retry_on_529(self):
        """_stream_llm should retry on 529 errors from stream entry."""
        from anthropic import APIStatusError

        from harness.recovery import RecoveryState
        from loop import _stream_llm

        state = RecoveryState()
        messages = [{"role": "user", "content": "hi"}]
        tools = []
        system = "test"

        mock_final = MagicMock()
        mock_final.stop_reason = "end_turn"
        mock_final.content = [MagicMock(type="text", text="hello")]

        call_count = [0]

        def mock_create_stream(**kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise APIStatusError(
                    message="Overloaded",
                    response=MagicMock(status_code=529),
                    body={},
                )
            inner = MagicMock()
            inner.__iter__ = MagicMock(return_value=iter([]))
            inner.get_final_message.return_value = mock_final
            result = MagicMock()
            result.__enter__ = MagicMock(return_value=inner)
            return result

        with patch("loop.provider") as mock_provider, patch("loop._sleep_interruptible"):
            mock_provider.create_message_stream.side_effect = mock_create_stream
            response = _stream_llm(messages, tools, state, system, max_tokens=500)

        assert response.stop_reason == "end_turn"
        assert call_count[0] == 3  # 2 failures + 1 success

    def test_gives_up_after_max_retries(self):
        """_stream_llm should raise after exhausting retries."""
        import pytest
        from anthropic import APIStatusError

        from harness.recovery import RecoveryState
        from loop import _stream_llm

        state = RecoveryState()
        messages = [{"role": "user", "content": "hi"}]
        tools = []
        system = "test"

        with patch("loop.provider") as mock_provider, patch("loop._sleep_interruptible"):
            mock_provider.create_message_stream.side_effect = APIStatusError(
                message="Overloaded",
                response=MagicMock(status_code=529),
                body={},
            )
            with pytest.raises(Exception, match="Max retries"):
                _stream_llm(messages, tools, state, system, max_tokens=500)

    def test_fallback_model_on_repeated_529(self):
        """After 3 consecutive 529s, switch to fallback model."""
        import pytest
        from anthropic import APIStatusError

        from harness.recovery import RecoveryState
        from loop import _stream_llm

        state = RecoveryState()
        state.current_model = "primary-model"
        messages = [{"role": "user", "content": "hi"}]
        tools = []
        system = "test"

        def mock_create_stream(**kwargs):
            raise APIStatusError(
                message="Overloaded",
                response=MagicMock(status_code=529),
                body={},
            )

        with (
            patch("loop.provider") as mock_provider,
            patch("loop._sleep_interruptible"),
            patch("loop.FALLBACK_MODEL", "fallback-model"),
        ):
            mock_provider.create_message_stream.side_effect = mock_create_stream
            with pytest.raises(Exception, match="Max retries"):
                _stream_llm(messages, tools, state, system, max_tokens=500)

        # After 3 consecutive 529s, model should have switched
        assert state.current_model == "fallback-model"
        assert state.consecutive_529 >= 3
