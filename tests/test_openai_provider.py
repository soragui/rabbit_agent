"""Tests for OpenAIProvider and message format adapters."""
from unittest.mock import MagicMock

from harness.providers import (
    OpenAIProvider,
    Provider,
    _openai_response_to_anthropic,
    _TextBlock,
    _to_anthropic_tools,
    _to_openai_messages,
    _ToolUseBlock,
)


class TestOpenAIProviderInit:
    def test_is_a_provider(self):
        p = OpenAIProvider(api_key="sk-test")
        assert isinstance(p, Provider)

    def test_creates_client(self):
        p = OpenAIProvider(api_key="sk-test")
        assert p._client is not None


class TestMessageConversion:
    def test_simple_user_message(self):
        msgs = [{"role": "user", "content": "hello"}]
        result = _to_openai_messages(msgs, "")
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "hello"

    def test_system_prompt(self):
        msgs = [{"role": "user", "content": "hi"}]
        result = _to_openai_messages(msgs, "You are helpful.")
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful."

    def test_anthropic_text_block(self):
        block = _TextBlock(text="hello world")
        msgs = [{"role": "assistant", "content": [block]}]
        result = _to_openai_messages(msgs, "")
        assert result[0]["role"] == "assistant"
        assert "hello world" in result[0]["content"]

    def test_anthropic_tool_use_block(self):
        block = _ToolUseBlock(id="tool_1", name="bash", input={"command": "ls"})
        msgs = [{"role": "assistant", "content": [block]}]
        result = _to_openai_messages(msgs, "")
        assert result[0]["role"] == "assistant"
        assert result[0]["tool_calls"][0]["function"]["name"] == "bash"

    def test_tool_result_block(self):
        msgs = [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tool_1", "content": "file1.txt\nfile2.txt"}
        ]}]
        result = _to_openai_messages(msgs, "")
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "tool_1"

    def test_mixed_assistant_content(self):
        """Assistant message with text + tool_use becomes text + tool_calls."""
        blocks = [
            _TextBlock(text="Let me run that."),
            _ToolUseBlock(id="t1", name="bash", input={"command": "ls"}),
        ]
        msgs = [{"role": "assistant", "content": blocks}]
        result = _to_openai_messages(msgs, "")
        assert result[0]["role"] == "assistant"
        assert "Let me run that." in result[0]["content"]
        assert result[0]["tool_calls"][0]["function"]["name"] == "bash"


class TestToolConversion:
    def test_converts_anthropic_tool_to_openai_function(self):
        tools = [{"name": "bash", "description": "Run a command",
                   "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}}}]
        result = _to_anthropic_tools(tools)
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "bash"
        assert "command" in result[0]["function"]["parameters"]["properties"]

    def test_empty_tools(self):
        assert _to_anthropic_tools([]) == []


class TestResponseConversion:
    def test_text_response(self):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "Hello!"
        mock_resp.choices[0].message.tool_calls = None
        mock_resp.choices[0].finish_reason = "stop"
        mock_resp.usage.prompt_tokens = 10
        mock_resp.usage.completion_tokens = 5

        result = _openai_response_to_anthropic(mock_resp)
        assert result.stop_reason == "end_turn"
        assert result.content[0].text == "Hello!"
        assert result.usage.input_tokens == 10
        assert result.usage.output_tokens == 5

    def test_tool_call_response(self):
        mock_tc = MagicMock()
        mock_tc.id = "call_1"
        mock_tc.function.name = "bash"
        mock_tc.function.arguments = '{"command": "ls"}'

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = None
        mock_resp.choices[0].message.tool_calls = [mock_tc]
        mock_resp.choices[0].finish_reason = "tool_calls"
        mock_resp.usage.prompt_tokens = 5
        mock_resp.usage.completion_tokens = 15

        result = _openai_response_to_anthropic(mock_resp)
        assert result.stop_reason == "tool_use"
        assert result.content[0].name == "bash"
        assert result.content[0].input == {"command": "ls"}

    def test_max_tokens_stop_reason(self):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "truncated"
        mock_resp.choices[0].message.tool_calls = None
        mock_resp.choices[0].finish_reason = "length"
        mock_resp.usage.prompt_tokens = 1
        mock_resp.usage.completion_tokens = 1

        result = _openai_response_to_anthropic(mock_resp)
        assert result.stop_reason == "max_tokens"


class TestCreateMessage:
    def test_delegates_to_openai(self):
        p = OpenAIProvider(api_key="sk-test")
        mock_choice = MagicMock()
        mock_choice.message.content = "Hi"
        mock_choice.message.tool_calls = None
        mock_choice.finish_reason = "stop"
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        mock_resp.usage.prompt_tokens = 1
        mock_resp.usage.completion_tokens = 1
        p._client.chat.completions.create = MagicMock(return_value=mock_resp)

        result = p.create_message(
            model="gpt-4", system="Be nice.",
            messages=[{"role": "user", "content": "hi"}],
            tools=[], max_tokens=100,
        )
        assert result.stop_reason == "end_turn"
        assert result.content[0].text == "Hi"

    def test_passes_tools_to_openai(self):
        p = OpenAIProvider(api_key="sk-test")
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "ok"
        mock_resp.choices[0].message.tool_calls = None
        mock_resp.choices[0].finish_reason = "stop"
        mock_resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
        p._client.chat.completions.create = MagicMock(return_value=mock_resp)

        tools = [{"name": "bash", "description": "Run a command",
                   "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}}}]
        p.create_message(model="gpt-4", system="", messages=[{"role": "user", "content": "ls"}],
                         tools=tools, max_tokens=100)
        call_kwargs = p._client.chat.completions.create.call_args[1]
        assert "tools" in call_kwargs
        assert call_kwargs["tools"][0]["function"]["name"] == "bash"
