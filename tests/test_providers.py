"""Tests for the provider abstraction — AnthropicProvider integration."""
from unittest.mock import MagicMock

import pytest

from harness.providers import AnthropicProvider, Provider


class TestProviderABC:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            Provider()  # type: ignore[abstract]

    def test_anthropic_is_a_provider(self):
        p = AnthropicProvider(api_key="sk-test")
        assert isinstance(p, Provider)


class TestAnthropicProviderInit:
    def test_creates_with_api_key_only(self):
        p = AnthropicProvider(api_key="sk-test")
        assert p._client is not None
        assert p._client.api_key == "sk-test"

    def test_creates_with_base_url(self):
        p = AnthropicProvider(api_key="sk-test", base_url="https://api.example.com")
        assert p._client.base_url == "https://api.example.com"

    def test_creates_without_base_url(self):
        """No base_url means default Anthropic endpoint."""
        p = AnthropicProvider(api_key="sk-test")
        # The Anthropic client with no base_url uses the default production endpoint
        assert p._client.base_url is not None


class TestCreateMessage:
    def test_delegates_to_anthropic_client(self):
        p = AnthropicProvider(api_key="sk-test")
        mock_response = MagicMock()
        mock_response.stop_reason = "end_turn"
        mock_response.content = [MagicMock(type="text", text="hello")]
        p._client.messages.create = MagicMock(return_value=mock_response)

        result = p.create_message(
            model="claude-test", system="You are helpful.",
            messages=[{"role": "user", "content": "hi"}],
            tools=[], max_tokens=1000,
        )

        p._client.messages.create.assert_called_once_with(
            model="claude-test", system="You are helpful.",
            messages=[{"role": "user", "content": "hi"}],
            tools=[], max_tokens=1000,
        )
        assert result.stop_reason == "end_turn"
        assert result.content[0].text == "hello"

    def test_propagates_api_errors(self):
        p = AnthropicProvider(api_key="sk-test")
        from anthropic import APIStatusError
        p._client.messages.create = MagicMock(side_effect=APIStatusError(
            message="Overloaded", response=MagicMock(status_code=529), body={}))

        with pytest.raises(APIStatusError):
            p.create_message(
                model="claude-test", system="", messages=[{"role": "user", "content": "hi"}],
                tools=[], max_tokens=100,
            )

    def test_passes_tools_to_client(self):
        p = AnthropicProvider(api_key="sk-test")
        mock_response = MagicMock()
        mock_response.stop_reason = "tool_use"
        p._client.messages.create = MagicMock(return_value=mock_response)

        tools = [{"name": "bash", "description": "Run a command", "input_schema": {"type": "object"}}]
        result = p.create_message(
            model="claude-test", system="", messages=[{"role": "user", "content": "run ls"}],
            tools=tools, max_tokens=500,
        )

        assert p._client.messages.create.call_args[1]["tools"] == tools
        assert result.stop_reason == "tool_use"


class TestCreateMessageStream:
    def test_delegates_to_anthropic_stream(self):
        p = AnthropicProvider(api_key="sk-test")
        mock_stream = MagicMock()
        p._client.messages.stream = MagicMock(return_value=mock_stream)

        result = p.create_message_stream(
            model="claude-test", system="Be concise.",
            messages=[{"role": "user", "content": "hi"}],
            tools=[], max_tokens=1000,
        )

        p._client.messages.stream.assert_called_once_with(
            model="claude-test", system="Be concise.",
            messages=[{"role": "user", "content": "hi"}],
            tools=[], max_tokens=1000,
        )
        assert result is mock_stream

    def test_stream_passes_tools(self):
        p = AnthropicProvider(api_key="sk-test")
        mock_stream = MagicMock()
        p._client.messages.stream = MagicMock(return_value=mock_stream)

        tools = [{"name": "read_file", "description": "Read a file", "input_schema": {"type": "object"}}]
        p.create_message_stream(
            model="claude-test", system="", messages=[{"role": "user", "content": "read x.txt"}],
            tools=tools, max_tokens=500,
        )

        assert p._client.messages.stream.call_args[1]["tools"] == tools
