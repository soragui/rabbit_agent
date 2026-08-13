"""Facade tests — TUI mode must emit events, plain mode must print."""
import pytest

from harness import render
from harness.ui_bridge import bridge


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    render.set_tui_active(False)
    bridge.drain()
    bridge.clear_abort()
    yield
    render.set_tui_active(False)
    bridge.drain()


def test_tui_render_markdown_emits_chat():
    render.set_tui_active(True)
    render.render_markdown("hello **world**")
    events = bridge.drain()
    assert len(events) == 1
    assert events[0].kind == "chat"
    assert events[0].payload == "hello **world**"
    assert events[0].style == "agent"


def test_tui_render_tool_use_emits_activity_and_state():
    render.set_tui_active(True)
    render.render_tool_use("bash", "ls -la")
    events = bridge.drain()
    kinds = [(e.kind, e.style) for e in events]
    assert ("activity", "tool") in kinds
    assert ("state", "") in kinds
    state_ev = [e for e in events if e.kind == "state"][0]
    assert state_ev.payload == "running bash"


def test_tui_render_error_and_info_go_to_chat():
    render.set_tui_active(True)
    render.render_error("boom")
    render.render_info("note")
    events = bridge.drain()
    assert all(e.kind == "chat" for e in events)
    assert events[0].style == "error"
    assert events[1].style == "info"


def test_tui_render_inbox_goes_to_activity():
    render.set_tui_active(True)
    render.render_inbox([{"from": "teammate", "type": "message", "content": "hi"}])
    events = bridge.drain()
    assert events[0].kind == "activity"
    assert events[0].style == "inbox"
    assert "teammate" in events[0].payload


def test_tui_render_tool_result_ok_and_fail():
    render.set_tui_active(True)
    render.render_tool_result("bash", "output", ok=True)
    render.render_tool_result("bash", "Error: boom", ok=False)
    events = bridge.drain()
    assert any(e.style == "ok" and "✓ bash" in e.payload for e in events)
    assert any(e.style == "fail" and "✗ bash" in e.payload for e in events)


def test_tui_render_activity():
    render.set_tui_active(True)
    render.render_activity("⏳ background", style="running")
    events = bridge.drain()
    assert events[0].kind == "activity"
    assert events[0].style == "running"


def test_plain_render_markdown_prints(capsys):
    render.set_tui_active(False)
    render.render_markdown("plain text")
    assert "plain text" in capsys.readouterr().out


def test_plain_render_tool_result_and_activity_are_silent(capsys):
    render.set_tui_active(False)
    render.render_tool_result("bash", "x", ok=True)
    render.render_activity("hello")
    assert capsys.readouterr().out == ""


def test_tui_spinner_emits_state_enter_and_exit():
    render.set_tui_active(True)
    with render.spinner("Thinking..."):
        pass
    events = bridge.drain()
    states = [e.payload for e in events if e.kind == "state"]
    assert states == ["Thinking...", "idle"]


def test_tui_streaming_renderer_emits_incremental():
    render.set_tui_active(True)
    with render.streaming_renderer() as r:
        r("Hel")
        r("Hello")
    events = bridge.drain()
    streams = [e for e in events if e.kind == "stream"]
    assert streams[0].payload == ""          # block opens empty
    assert streams[-1].payload == "Hello"    # accumulated text
    assert events[-1].kind == "clear_stream"
