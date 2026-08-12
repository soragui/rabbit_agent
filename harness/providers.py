"""Provider abstraction — decouples the agent loop from a specific LLM backend.

Wave 1: AnthropicProvider. Wave 3: OpenAIProvider (Gemini via OpenAI-compat endpoint).
"""
import json as _json
from abc import ABC, abstractmethod  # noqa: F401 — used by Provider subclasses
from dataclasses import dataclass, field
from typing import Any  # noqa: F401 — used in abstract method signatures

from anthropic import Anthropic
from openai import OpenAI

# ── Anthropic-compatible response shapes ─────────────────────────────────

@dataclass
class _TextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class _ToolUseBlock:
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class _Response:
    """Anthropic-compatible response shape, built from any provider."""
    content: list = field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: _Usage = field(default_factory=_Usage)


# ── Provider ABC ────────────────────────────────────────────────────────

class Provider(ABC):
    """Minimal LLM provider interface.

    Two methods only: blocking create_message and streaming create_message_stream.
    New backends implement both.
    """

    @abstractmethod
    def create_message(
        self, model: str, system: str, messages: list[dict],
        tools: list[dict], max_tokens: int,
    ) -> Any:
        """Send a non-streaming request. Returns a response with .content, .stop_reason, .usage."""
        ...

    @abstractmethod
    def create_message_stream(
        self, model: str, system: str, messages: list[dict],
        tools: list[dict], max_tokens: int,
    ) -> Any:
        """Return a stream context manager yielding SSE events.

        Caller iterates over events, then calls stream.get_final_message()
        for the full response (with .content, .stop_reason, .usage).
        """
        ...


# ── Message format conversion (Anthropic ↔ OpenAI) ──────────────────────

def _to_openai_messages(anthropic_msgs: list[dict], system: str) -> list[dict]:
    """Convert Anthropic-format messages to OpenAI chat format."""
    openai_msgs = []
    if system:
        openai_msgs.append({"role": "system", "content": system})
    for msg in anthropic_msgs:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            # Anthropic content blocks → OpenAI format
            text_parts = []
            tool_calls = []
            for block in content:
                btype = getattr(block, "type", None) or block.get("type", "")
                if btype == "text":
                    text_parts.append(getattr(block, "text", "") or block.get("text", ""))
                elif btype == "tool_use":
                    tool_id = getattr(block, "id", "") or block.get("id", "")
                    tool_name = getattr(block, "name", "") or block.get("name", "")
                    tool_input = getattr(block, "input", {}) or block.get("input", {})
                    tool_calls.append({
                        "id": tool_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": _json.dumps(tool_input),
                        },
                    })
                elif btype == "tool_result":
                    tool_id = getattr(block, "tool_use_id", "") or block.get("tool_use_id", "")
                    result_content = getattr(block, "content", "") or block.get("content", "")
                    openai_msgs.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": str(result_content)[:2000],
                    })
            if tool_calls and role == "assistant":
                openai_msgs.append({"role": "assistant", "content": "\n".join(text_parts), "tool_calls": tool_calls} if text_parts else {"role": "assistant", "tool_calls": tool_calls})
            elif text_parts and role == "assistant":
                openai_msgs.append({"role": "assistant", "content": "\n".join(text_parts)})
            elif text_parts and role == "user":
                openai_msgs.append({"role": "user", "content": "\n".join(text_parts)})
            # tool_result messages are handled inline above
        elif isinstance(content, str):
            openai_msgs.append({"role": role, "content": content})
        else:
            openai_msgs.append({"role": role, "content": str(content)})
    return openai_msgs


def _to_anthropic_tools(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool schemas to OpenAI function format."""
    openai_tools = []
    for tool in tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return openai_tools


def _openai_response_to_anthropic(openai_resp) -> _Response:
    """Convert an OpenAI chat completion response to Anthropic-compatible shape."""
    choice = openai_resp.choices[0]
    msg = choice.message
    content_blocks = []
    if msg.content:
        content_blocks.append(_TextBlock(text=msg.content))
    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                args = _json.loads(tc.function.arguments)
            except (_json.JSONDecodeError, TypeError):
                args = {}
            content_blocks.append(_ToolUseBlock(
                id=tc.id, name=tc.function.name, input=args,
            ))
    stop_map = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}
    stop_reason = stop_map.get(openai_resp.choices[0].finish_reason, "end_turn")
    usage = _Usage(
        input_tokens=getattr(openai_resp.usage, "prompt_tokens", 0),
        output_tokens=getattr(openai_resp.usage, "completion_tokens", 0),
    )
    return _Response(content=content_blocks, stop_reason=stop_reason, usage=usage)


# ── OpenAI provider ─────────────────────────────────────────────────────

class OpenAIProvider(Provider):
    """OpenAI chat completions, adapted to Anthropic-compatible shapes.

    Also works with any OpenAI-compatible endpoint (Gemini, Groq, local LLMs)
    by setting base_url to the compat endpoint.
    """

    def __init__(self, api_key: str, base_url: str | None = None):
        if base_url:
            self._client = OpenAI(api_key=api_key, base_url=base_url)
        else:
            self._client = OpenAI(api_key=api_key)

    def create_message(self, model, system, messages, tools, max_tokens):
        openai_msgs = _to_openai_messages(messages, system)
        openai_tools = _to_anthropic_tools(tools)
        kwargs = dict(
            model=model, messages=openai_msgs, max_tokens=max_tokens,
            temperature=0.0,
        )
        if openai_tools:
            kwargs["tools"] = openai_tools
        resp = self._client.chat.completions.create(**kwargs)
        return _openai_response_to_anthropic(resp)

    def create_message_stream(self, model, system, messages, tools, max_tokens):
        openai_msgs = _to_openai_messages(messages, system)
        openai_tools = _to_anthropic_tools(tools)
        kwargs = dict(
            model=model, messages=openai_msgs, max_tokens=max_tokens,
            temperature=0.0, stream=True,
            stream_options={"include_usage": True},
        )
        if openai_tools:
            kwargs["tools"] = openai_tools
        raw_stream = self._client.chat.completions.create(**kwargs)
        return _OpenAIStreamWrapper(raw_stream)


# ── Streaming wrapper (OpenAI SSE → Anthropic SSE events) ───────────────

class _OpenAIStreamWrapper:
    """Wraps an OpenAI streaming response to look like Anthropic's MessageStream.

    Yields Anthropic-style SSE events with .type, .delta, .content_block.
    Provides .get_final_message() for the accumulated response.
    """

    def __init__(self, openai_stream):
        self._stream = openai_stream
        self._final: _Response | None = None
        self._text_buf = ""
        self._tool_calls: dict[int, dict] = {}  # index → {id, name, args_str}
        self._finish_reason = "end_turn"
        self._usage = _Usage()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._stream.close()

    def __iter__(self):
        for chunk in self._stream:
            if chunk.usage:
                self._usage = _Usage(
                    input_tokens=getattr(chunk.usage, "prompt_tokens", 0),
                    output_tokens=getattr(chunk.usage, "completion_tokens", 0),
                )
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                self._text_buf += delta.content
                yield _StreamEvent("content_block_delta", delta_type="text_delta", text=delta.content)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in self._tool_calls:
                        self._tool_calls[idx] = {"id": tc.id or "", "name": "", "args_str": ""}
                    if tc.id:
                        self._tool_calls[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            self._tool_calls[idx]["name"] = tc.function.name
                        if tc.function.arguments:
                            self._tool_calls[idx]["args_str"] += tc.function.arguments
                            yield _StreamEvent("content_block_delta", delta_type="input_json_delta",
                                              partial_json=tc.function.arguments)
            if chunk.choices[0].finish_reason:
                self._finish_reason = chunk.choices[0].finish_reason

    def get_final_message(self) -> _Response:
        """Build and return the accumulated Anthropic-compatible response."""
        content_blocks = []
        if self._text_buf:
            content_blocks.append(_TextBlock(text=self._text_buf))
        for tc in sorted(self._tool_calls.values(), key=lambda x: list(self._tool_calls.keys())[list(self._tool_calls.values()).index(x)] if x in self._tool_calls.values() else 0):
            try:
                args = _json.loads(tc["args_str"]) if tc["args_str"] else {}
            except _json.JSONDecodeError:
                args = {}
            content_blocks.append(_ToolUseBlock(id=tc["id"], name=tc["name"], input=args))
        stop_map = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}
        return _Response(
            content=content_blocks,
            stop_reason=stop_map.get(self._finish_reason, "end_turn"),
            usage=self._usage,
        )


class _StreamEvent:
    """Minimal Anthropic-style SSE event for the streaming loop."""
    def __init__(self, event_type: str, delta_type: str = "", text: str = "", partial_json: str = ""):
        self.type = event_type
        self.delta = _StreamDelta(delta_type, text, partial_json)


class _StreamDelta:
    def __init__(self, delta_type: str, text: str = "", partial_json: str = ""):
        self.type = delta_type
        self.text = text
        self.partial_json = partial_json


# ── Anthropic provider (unchanged from Wave 1) ──────────────────────────

class AnthropicProvider(Provider):
    """Thin wrapper around the Anthropic SDK — same shapes, no translation."""

    def __init__(self, api_key: str, base_url: str | None = None):
        if base_url:
            self._client = Anthropic(api_key=api_key, base_url=base_url)
        else:
            self._client = Anthropic(api_key=api_key)

    def create_message(self, model, system, messages, tools, max_tokens):
        return self._client.messages.create(
            model=model, system=system, messages=messages,
            tools=tools, max_tokens=max_tokens,
        )

    def create_message_stream(self, model, system, messages, tools, max_tokens):
        return self._client.messages.stream(
            model=model, system=system, messages=messages,
            tools=tools, max_tokens=max_tokens,
        )
