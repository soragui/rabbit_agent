"""Unit tests for the UI bridge — event queue, pending question, abort flag."""
import threading
import time

import pytest

from harness.ui_bridge import Bridge, Event, TurnAborted


def test_emit_and_drain_roundtrip():
    b = Bridge()
    b.emit("chat", "hello", style="agent")
    b.emit("activity", "tool ran", style="ok")
    assert b.drain() == [
        Event("chat", "hello", "agent"),
        Event("activity", "tool ran", "ok"),
    ]
    assert b.drain() == []


def test_abort_flag():
    b = Bridge()
    assert b.is_abort_requested() is False
    b.request_abort()
    assert b.is_abort_requested() is True
    b.clear_abort()
    assert b.is_abort_requested() is False


def test_turn_aborted_is_an_exception():
    assert issubclass(TurnAborted, Exception)


def test_ask_question_answered_yes():
    b = Bridge()

    def ui():
        while not b.has_pending_question():
            time.sleep(0.01)
        assert b.drain()[-1].kind == "question"
        b.answer_question("y")

    t = threading.Thread(target=ui)
    t.start()
    assert b.ask_question("Allow bash?") is True
    t.join()


def test_ask_question_empty_answer_uses_default():
    b = Bridge()

    def ui():
        while not b.has_pending_question():
            time.sleep(0.01)
        b.answer_question("")

    t = threading.Thread(target=ui)
    t.start()
    assert b.ask_question("Allow bash?", default=False) is False
    t.join()


def test_ask_question_timeout_returns_default_and_notes_it():
    b = Bridge()
    assert b.ask_question("Allow bash?", default=False, timeout=0.05) is False
    events = b.drain()
    assert events[0].kind == "question"
    assert events[-1].kind == "activity"
    assert "auto-denied" in events[-1].payload


def test_ask_question_rejects_nested():
    b = Bridge()

    def inner():
        b.ask_question("second?", timeout=2)

    t = threading.Thread(target=inner)
    t.start()
    while not b.has_pending_question():
        time.sleep(0.01)
    with pytest.raises(RuntimeError):
        b.ask_question("first?", timeout=0.2)
    b.answer_question("y")
    t.join()
