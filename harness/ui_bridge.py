"""Thread-safe bridge between agent worker threads and the TUI thread.

All cross-thread traffic between the agent (worker threads) and the
full-screen UI goes through this module:
  - render events flow worker -> UI via emit()/drain()
  - permission questions block the worker until the UI answers
  - the abort flag lets Ctrl+C stop a running turn

Plain threading code — no prompt_toolkit, no terminal I/O.
"""
import queue
import threading
from dataclasses import dataclass


class TurnAborted(Exception):
    """Raised inside a turn when the user pressed Ctrl+C (abort flag set)."""


@dataclass
class Event:
    """One render event, consumed by the UI thread.

    kind    — where the payload renders:
              "chat"         append to the chat pane
              "activity"     append to the activity pane
              "stream"       replace the in-flight chat block with payload
              "clear_stream" finalize the in-flight chat block
              "question"     a question is waiting in the activity pane
              "state"        agent state text (header/status bar)
    style   — hint for the pane's style class ("user", "agent", "info",
              "error", "tool", "ok", "fail", "running", "inbox", ...)
    """

    kind: str
    payload: str = ""
    style: str = ""


class Bridge:
    """Queue + pending question + abort flag."""

    def __init__(self):
        self._queue: queue.Queue[Event] = queue.Queue()
        self._abort = threading.Event()
        self._lock = threading.Lock()
        self._pending = False
        self._question_text = ""
        self._answered = threading.Event()
        self._answer = ""
        self._default = False

    # -- event queue (worker -> UI) ----------------------------------------
    def emit(self, kind: str, payload: str = "", style: str = "") -> None:
        self._queue.put(Event(kind=kind, payload=payload, style=style))

    def drain(self) -> list[Event]:
        events: list[Event] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except queue.Empty:
                return events

    # -- abort flag (UI -> worker) -----------------------------------------
    def request_abort(self) -> None:
        self._abort.set()

    def clear_abort(self) -> None:
        self._abort.clear()

    def is_abort_requested(self) -> bool:
        return self._abort.is_set()

    # -- pending question (worker blocks, UI answers) -----------------------
    def ask_question(self, text: str, default: bool = False, timeout: float | None = None) -> bool:
        """Block the calling thread until the UI answers `text`.

        Empty answer or timeout -> `default` (timeout also emits an
        "auto-denied" activity note). Exactly one question may be
        pending at a time.
        """
        with self._lock:
            if self._pending:
                raise RuntimeError("a question is already pending")
            self._pending = True
            self._question_text = text
            self._answer = ""
            self._default = default
            self._answered.clear()
        self.emit("question", text)
        got = self._answered.wait(timeout)
        with self._lock:
            self._pending = False
            answer = self._answer
        if not got:
            self.emit("activity", f"⏱ auto-denied: {text}", style="fail")
            return default
        answer = answer.strip().lower()
        if not answer:
            return default
        return answer in ("y", "yes")

    def has_pending_question(self) -> bool:
        with self._lock:
            return self._pending

    def answer_question(self, line: str) -> bool:
        """UI thread: answer the pending question (empty line = default).

        Returns False when no question is pending.
        """
        with self._lock:
            if not self._pending:
                return False
            self._answer = line
        self._answered.set()
        return True


bridge = Bridge()
