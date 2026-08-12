"""Tests for structured output tool."""
import json

from harness.tool_pool import _handle_structured_output


class TestStructuredOutputHandler:
    def test_formats_valid_json(self):
        result = _handle_structured_output("test schema", {"name": "foo", "count": 42})
        assert "```json" in result
        assert '"name": "foo"' in result
        assert '"count": 42' in result

    def test_no_format_description(self):
        result = _handle_structured_output("", {"key": "value"})
        assert "```json" in result
        assert '"key": "value"' in result
        assert "Structured output" not in result

    def test_nested_data(self):
        data = {"files": [{"name": "a.py", "lines": 100}, {"name": "b.py", "lines": 200}]}
        result = _handle_structured_output("file list", data)
        parsed = json.loads(result.split("```json\n")[1].split("\n```")[0])
        assert parsed == data

    def test_empty_dict(self):
        result = _handle_structured_output("empty", {})
        assert "{}" in result

    def test_invalid_data_returns_error(self):
        result = _handle_structured_output("bad", object())  # type: ignore[arg-type]
        assert "error" in result.lower() or "invalid" in result.lower()


class TestStructuredOutputInToolPool:
    def test_tool_registered(self):
        from harness.tool_pool import assemble_tool_pool
        tools, handlers = assemble_tool_pool()
        tool_names = [t["name"] for t in tools]
        assert "structured_output" in tool_names
        assert "structured_output" in handlers

    def test_handler_returns_valid_markdown(self):
        from harness.tool_pool import assemble_tool_pool
        _, handlers = assemble_tool_pool()
        result = handlers["structured_output"](format_description="user list", data={"users": ["alice", "bob"]})
        assert "alice" in result
        assert "```json" in result
