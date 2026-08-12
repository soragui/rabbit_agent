"""Session persistence — save conversation on exit, offer resume on start."""
import json as _json
import time as _time
from pathlib import Path

from config import TRANSCRIPT_DIR


def save_session(history: list) -> Path | None:
    """Persist the conversation history to a JSONL transcript file.

    Returns the path to the saved file, or None if history is empty.
    """
    if not history:
        return None
    path = TRANSCRIPT_DIR / f"session_{int(_time.time())}.jsonl"
    lines = []
    for msg in history:
        # Convert content blocks to dicts for serialization
        content = msg.get("content", "")
        if isinstance(content, list):
            serializable = []
            for block in content:
                if hasattr(block, "__dict__"):
                    d = {k: v for k, v in block.__dict__.items() if not k.startswith("_")}
                    d["type"] = getattr(block, "type", "unknown")
                    serializable.append(d)
                elif isinstance(block, dict):
                    serializable.append(block)
                else:
                    serializable.append({"type": "unknown", "value": str(block)})
            msg_copy = {**msg, "content": serializable}
        else:
            msg_copy = msg
        lines.append(_json.dumps(msg_copy, default=str, ensure_ascii=False))
    path.write_text("\n".join(lines))
    return path


def find_latest_session() -> Path | None:
    """Return the most recent transcript file, if any."""
    files = sorted(TRANSCRIPT_DIR.glob("session_*.jsonl"), reverse=True)
    return files[0] if files else None


def load_session(path: Path) -> list[dict] | None:
    """Load a transcript file back into a message list."""
    try:
        messages = []
        for line in path.read_text().splitlines():
            if line.strip():
                messages.append(_json.loads(line))
        return messages
    except Exception:
        return None
