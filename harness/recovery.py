"""s11: Error Recovery — retry with exponential backoff."""
import time
import random

from anthropic import APIStatusError
from config import BASE_DELAY_MS, ESCALATED_MAX_TOKENS, DEFAULT_MAX_TOKENS
from config import MAX_RETRIES, MAX_RECOVERY_RETRIES, FALLBACK_MODEL, MODEL
from harness.render import render_info, render_error


class RecoveryState:
    def __init__(self):
        self.has_escalated = False
        self.recovery_count = 0
        self.has_attempted_reactive_compact = False
        self.consecutive_529 = 0
        self.current_model = MODEL


def _retry_delay(attempt: int, retry_after: float = None) -> float:
    if retry_after:
        return retry_after
    base = min(BASE_DELAY_MS * (2 ** attempt), 32000) / 1000
    return base + random.uniform(0, base * 0.25)


def with_retry(fn, state: RecoveryState, max_retries: int = MAX_RETRIES):
    for attempt in range(max_retries):
        try:
            return fn()
        except APIStatusError as e:
            if e.status_code in (429, 529):
                delay = _retry_delay(attempt)
                render_info(f"Retry {e.status_code} — waiting {delay:.1f}s (attempt {attempt+1}/{max_retries})")
                time.sleep(delay)
                if e.status_code == 529:
                    state.consecutive_529 += 1
                    if state.consecutive_529 >= 3 and FALLBACK_MODEL:
                        render_info(f"Switching to fallback model: {FALLBACK_MODEL}")
                        state.current_model = FALLBACK_MODEL
                continue
            raise
    raise Exception(f"Max retries ({max_retries}) exceeded")
