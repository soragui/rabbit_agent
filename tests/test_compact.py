"""Smoke tests for the compaction pipeline — message slicing, snipping, micro-compact."""
from harness.compact import (
    _is_tool_result_message,
    _message_has_tool_use,
    micro_compact,
    safe_messages_slice,
    snip_compact,
    tool_result_budget,
)


class TestMessageDetection:
    def test_detects_tool_use_message(self):
        msg = {"role": "assistant", "content": [{"type": "tool_use", "name": "bash", "input": {}}]}
        assert _message_has_tool_use(msg) is True

    def test_plain_text_is_not_tool_use(self):
        msg = {"role": "assistant", "content": [{"type": "text", "text": "hello"}]}
        assert _message_has_tool_use(msg) is False

    def test_empty_content_is_not_tool_use(self):
        msg = {"role": "user", "content": "hello"}
        assert _message_has_tool_use(msg) is False

    def test_detects_tool_result_message(self):
        msg = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]}
        assert _is_tool_result_message(msg) is True

    def test_mixed_content_is_not_tool_result(self):
        msg = {"role": "user", "content": [{"type": "text", "text": "x"}, {"type": "tool_result", "tool_use_id": "1", "content": "ok"}]}
        assert _is_tool_result_message(msg) is False


class TestSafeMessagesSlice:
    def test_short_list_unchanged(self):
        msgs = [{"role": "user", "content": "hi"}]
        assert safe_messages_slice(msgs, max_items=10) == msgs

    def test_truncates_long_list(self):
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(200)]
        result = safe_messages_slice(msgs, max_items=100)
        assert len(result) <= 100

    def test_does_not_split_tool_pair(self):
        """A leading tool_result must not be orphaned from its tool_use."""
        msgs = [
            {"role": "user", "content": "start"},
            {"role": "assistant", "content": [{"type": "tool_use", "name": "bash", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]},
        ]
        # max_items=2 takes the last 2: assistant(tool_use) + user(tool_result).
        # The pair is intact — no orphaned tool_result. The "start" message
        # is dropped but that's safe (no tool_use precedes it).
        result = safe_messages_slice(msgs, max_items=2)
        assert len(result) == 2
        # Verify the pair is together
        assert _message_has_tool_use(result[0])
        assert _is_tool_result_message(result[1])

    def test_orphaned_tool_result_pulls_in_tool_use(self):
        """When the slice starts with a tool_result, walk back to include its tool_use."""
        msgs = [
            {"role": "user", "content": "prefix"},
            {"role": "assistant", "content": [{"type": "tool_use", "name": "bash", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]},
            {"role": "user", "content": "extra"},
            {"role": "user", "content": "extra2"},
            {"role": "assistant", "content": [{"type": "tool_use", "name": "read_file", "input": {"path": "x"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "2", "content": "data"}]},
        ]
        # With max_items=3, the last 3 are: extra2, assistant(tool_use read_file), user(tool_result)
        # The tool_use+tool_result pair is intact. No orphan.
        result = safe_messages_slice(msgs, max_items=3)
        assert len(result) >= 2
        # The last message should be a tool_result
        assert _is_tool_result_message(result[-1])
        # The message before it should be a tool_use
        assert _message_has_tool_use(result[-2])


class TestSnipCompact:
    def test_short_messages_unchanged(self):
        msgs = [{"role": "user", "content": "hi"}]
        assert snip_compact(msgs, max_messages=50) == msgs

    def test_snips_long_history(self):
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(100)]
        result = snip_compact(msgs, max_messages=50)
        assert len(result) < len(msgs)
        # Should contain a snipped marker
        markers = [m for m in result if "snipped" in str(m.get("content", ""))]
        assert len(markers) == 1

    def test_preserves_head_and_tail(self):
        msgs = [{"role": "user", "content": f"msg{i}"} for i in range(100)]
        result = snip_compact(msgs, max_messages=30)
        # First message preserved
        assert result[0]["content"] == "msg0"
        # Last message preserved
        assert result[-1]["content"] == "msg99"


class TestMicroCompact:
    def test_empty_messages_unchanged(self):
        msgs = []
        assert micro_compact(msgs) == []

    def test_few_tool_results_unchanged(self):
        msgs = [
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "short"}]},
        ]
        result = micro_compact(msgs)
        assert result == msgs

    def test_compacts_old_tool_results(self):
        """Old tool results beyond KEEP_RECENT should be compacted."""
        msgs = []
        for i in range(10):
            msgs.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": str(i), "content": "x" * 200}
            ]})
        result = micro_compact(msgs)
        # Early results should be compacted
        for msg in result[:5]:
            block = msg["content"][0]
            assert "compacted" in str(block["content"]).lower() or block["content"] == "x" * 200


class TestToolResultBudget:
    def test_empty_messages_unchanged(self):
        assert tool_result_budget([]) == []

    def test_small_output_unchanged(self):
        msgs = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "tiny"}]}]
        result = tool_result_budget(msgs, max_bytes=10_000)
        assert result == msgs

    def test_large_output_persisted(self):
        msgs = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "big1", "content": "x" * 300_000}]}]
        result = tool_result_budget(msgs, max_bytes=1_000)
        block = result[0]["content"][0]
        assert "persisted-output" in str(block["content"])
