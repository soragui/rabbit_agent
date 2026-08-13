"""TUI unit tests — headless; no terminal, no Application.run()."""
import pytest
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document

from harness import tui
from harness.ui_bridge import Event


def test_apply_events_chat_and_stream():
    tui._reset_for_tests()
    chat, activity = Buffer(read_only=True), Buffer(read_only=True)
    events = [
        Event("chat", "You: hi"),
        Event("stream", "Hel"),
        Event("stream", "Hello"),
        Event("clear_stream"),
        Event("activity", "✓ bash", "ok"),
        Event("state", "running bash"),
    ]
    state = tui.apply_events(events, chat, activity)
    assert "You: hi" in chat.text
    assert "Hello" in chat.text
    assert "✓ bash" in activity.text
    assert state == "running bash"


def test_stream_block_lands_on_one_line():
    tui._reset_for_tests()
    chat, activity = Buffer(read_only=True), Buffer(read_only=True)
    tui.apply_events(
        [Event("stream", "a"), Event("stream", "ab"), Event("clear_stream")],
        chat, activity)
    assert chat.text.splitlines() == ["ab"]
    tui.apply_events([Event("chat", "next")], chat, activity)
    assert chat.text.splitlines() == ["ab", "next"]


def test_autoscroll_follows_only_when_at_bottom():
    tui._reset_for_tests()
    chat = Buffer(read_only=True)
    activity = Buffer(read_only=True)
    # Read-only buffer: set initial text via set_document (bypass_readonly).
    chat.set_document(
        Document("line1\nline2\nline3", len("line1\nline2\nline3")),
        bypass_readonly=True,
    )
    chat.cursor_position = 0  # user scrolled up
    tui.apply_events([Event("chat", "line4")], chat, activity)
    assert chat.cursor_position == 0  # stay put
    chat.cursor_position = len(chat.text)  # user at bottom
    tui.apply_events([Event("chat", "line5")], chat, activity)
    assert chat.cursor_position == len(chat.text)


def test_question_events_prefixed_in_activity():
    tui._reset_for_tests()
    chat, activity = Buffer(read_only=True), Buffer(read_only=True)
    tui.apply_events([Event("question", "Allow bash? (y/N)")], chat, activity)
    assert "? Allow bash? (y/N)" in activity.text


def test_header_includes_model_and_state(monkeypatch):
    tui._reset_for_tests()
    tui._state = "thinking"
    text = tui._header_text()
    joined = " ".join(t for _, t in text)
    assert "51agent" in joined
    assert "thinking" in joined


def test_status_text_joins_segments():
    """_status_text renders the cached segments joined with ' │ '."""
    tui._status_segments = ["seg1", "seg2"]
    text = tui._status_text()
    assert "seg1 │ seg2" in text[0][1]


def test_status_text_empty_cache_shows_placeholder():
    tui._status_segments = []
    text = tui._status_text()
    assert text[0][1] == " "


def test_refresh_status_updates_cache(monkeypatch):
    monkeypatch.setattr(
        tui, "collect_status", lambda workdir, state: ["seg1", "seg2"])
    tui._refresh_status()
    assert tui._status_segments == ["seg1", "seg2"]


def test_build_app_returns_full_screen_app():
    tui._reset_for_tests()
    app = tui._build_app()
    assert app.full_screen is True
    assert app.layout is not None


def test_run_tui_requires_tty(monkeypatch):
    monkeypatch.setattr(tui.sys, "stdin", tui.sys.stdout)
    with pytest.raises(RuntimeError):
        tui.run_tui([], on_line=lambda q, h: True)
