"""Provider abstraction — decouples the agent loop from a specific LLM backend.

Wave 1: AnthropicProvider only. Wave 3: OpenAIProvider, GeminiProvider.
"""
from abc import ABC, abstractmethod
from typing import Any

from anthropic import Anthropic


class Provider(ABC):
    """Minimal LLM provider interface.

    Two methods only: blocking create_message and streaming create_message_stream.
    New backends implement both.
    """

    @abstractmethod
    def create_message(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
    ) -> Any:
        """Send a non-streaming request. Returns a response with .content, .stop_reason, .usage."""
        ...

    @abstractmethod
    def create_message_stream(
        self,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int,
    ) -> Any:
        """Return a stream context manager yielding SSE events.

        Caller iterates over events, then calls stream.get_final_message()
        for the full response (with .content, .stop_reason, .usage).
        """
        ...


class AnthropicProvider(Provider):
    """Thin wrapper around the Anthropic SDK — same shapes, no translation."""

    def __init__(self, api_key: str, base_url: str | None = None):
        if base_url:
            self._client = Anthropic(api_key=api_key, base_url=base_url)
        else:
            self._client = Anthropic(api_key=api_key)

    def create_message(self, model, system, messages, tools, max_tokens):
        return self._client.messages.create(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
        )

    def create_message_stream(self, model, system, messages, tools, max_tokens):
        return self._client.messages.stream(
            model=model,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
        )
