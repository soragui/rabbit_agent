"""Route-line branching — headless, using the real bridge singleton."""
import threading
import time as _time

from harness import tui
from harness.ui_bridge import bridge


def _wait_for_question():
    while not bridge.has_pending_question():
        _time.sleep(0.01)


def test_route_line_answers_pending_question():
    tui._reset_for_tests()
    got = {}

    def asker():
        got["answer"] = bridge.ask_question("Allow bash?", timeout=5)

    t = threading.Thread(target=asker)
    t.start()
    _wait_for_question()
    tui._route_line("y")
    t.join()
    assert got["answer"] is True
    bridge.drain()


def test_route_line_busy_ignores_input(monkeypatch):
    tui._reset_for_tests()
    tui._turn_running = True
    tui._route_line("hello")
    events = bridge.drain()
    assert any("busy" in e.payload for e in events)


def test_route_line_empty_line_answers_default():
    tui._reset_for_tests()
    got = {}

    def asker():
        got["answer"] = bridge.ask_question("Resume session? (y/N)", timeout=5)

    t = threading.Thread(target=asker)
    t.start()
    _wait_for_question()
    tui._route_line("")
    t.join()
    assert got["answer"] is False
    bridge.drain()
