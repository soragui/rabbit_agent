"""s08: Context Compact — four-level compaction pipeline."""
import json
import time

from config import (
    KEEP_RECENT_TOOL_RESULTS,
    MODEL,
    PERSIST_THRESHOLD,
    TOOL_RESULTS_DIR,
    TRANSCRIPT_DIR,
    client,
)


def _message_has_tool_use(msg: dict) -> bool:
    content = msg.get("content", [])
    return any(isinstance(b, dict) and b.get("type") == "tool_use"
               for b in content) if isinstance(content, list) else False


def _is_tool_result_message(msg: dict) -> bool:
    content = msg.get("content", [])
    return (isinstance(content, list)
            and all(isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in content) if content else False)


# -- safe slice ------------------------------------------------------------
def safe_messages_slice(messages, max_items=100):
    """Return last max_items messages, keeping tool_use/tool_result pairs intact."""
    if len(messages) <= max_items:
        return messages
    start = len(messages) - max_items
    while start > 0 and _is_tool_result_message(messages[start]):
        start -= 1
        if _message_has_tool_use(messages[start]):
            break
    return messages[start:]


# -- snip ------------------------------------------------------------------
def _find_safe_head_end(messages, head_end):
    idx = head_end
    while idx < len(messages) and _is_tool_result_message(messages[idx]):
        idx += 1
    return idx


def _find_safe_tail_start(messages, tail_start):
    if tail_start >= len(messages):
        return tail_start
    idx = tail_start
    while idx > 0 and _is_tool_result_message(messages[idx]):
        idx -= 1
        if idx >= 0 and _message_has_tool_use(messages[idx]):
            return idx
    return tail_start


def snip_compact(messages, max_messages=50):
    if len(messages) <= max_messages:
        return messages
    head_end = min(3, len(messages) - 10)
    if head_end < 1:
        return messages
    tail_start = max(len(messages) - (max_messages - head_end), head_end + 1)

    head_end = _find_safe_head_end(messages, head_end)
    if head_end >= tail_start:
        return messages
    tail_start = _find_safe_tail_start(messages, tail_start)
    if tail_start <= head_end:
        return messages

    snipped = tail_start - head_end
    if snipped <= 0:
        return messages
    return (messages[:head_end]
            + [{"role": "user", "content": f"[snipped {snipped} messages from middle]"}]
            + messages[tail_start:])


# -- micro -----------------------------------------------------------------
def micro_compact(messages):
    tool_results = []
    for mi, msg in enumerate(messages):
        content = msg.get("content", [])
        if isinstance(content, list):
            for bi, block in enumerate(content):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_results.append((mi, bi, block))
    if len(tool_results) <= KEEP_RECENT_TOOL_RESULTS:
        return messages
    for _, _, block in tool_results[:-KEEP_RECENT_TOOL_RESULTS]:
        if len(str(block.get("content", ""))) > 120:
            block["content"] = "[Earlier tool result compacted. Re-run if needed.]"
    return messages


# -- budget ----------------------------------------------------------------
def _persist_large_output(tool_use_id: str, content: str) -> str:
    TOOL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = TOOL_RESULTS_DIR / f"{tool_use_id}.txt"
    path.write_text(content)
    return (f"<persisted-output path='{path}'>\n{content[:2000]}\n"
            f"... ({len(content)} chars total)\n</persisted-output>")


def tool_result_budget(messages, max_bytes=PERSIST_THRESHOLD):
    if not messages:
        return messages
    last = messages[-1]
    content = last.get("content", [])
    if not isinstance(content, list):
        return messages
    blocks = [(i, b) for i, b in enumerate(content)
              if isinstance(b, dict) and b.get("type") == "tool_result"]
    total = sum(len(str(b.get("content", ""))) for _, b in blocks)
    if total <= max_bytes:
        return messages
    ranked = sorted(blocks, key=lambda p: len(str(p[1].get("content", ""))), reverse=True)
    for _idx, block in ranked:
        if total <= max_bytes:
            break
        block["content"] = _persist_large_output(
            block.get("tool_use_id", "unknown"), str(block["content"]))
        total = sum(len(str(b.get("content", ""))) for _, b in blocks)
    return messages


# -- full compact / reactive -----------------------------------------------
def compact_history(messages) -> list:
    path = TRANSCRIPT_DIR / f"session_{int(time.time())}.jsonl"
    path.write_text("\n".join(json.dumps(m, default=str) for m in messages))
    try:
        resp = client.messages.create(
            model=MODEL, messages=messages[-20:] + [{"role": "user",
                "content": "Summarize: current goal, findings, files changed, remaining work, preferences."}],
            max_tokens=1000)
        summary = resp.content[0].text if resp.content else "Summary unavailable."
    except Exception:
        summary = "Conversation compacted."
    return [{"role": "user", "content": f"[Compacted]\n\n{summary}"}]


def reactive_compact(messages) -> list:
    tail_start = max(0, len(messages) - 5)
    if (tail_start > 0
            and _is_tool_result_message(messages[tail_start])
            and _message_has_tool_use(messages[tail_start - 1])):
        tail_start -= 1
    try:
        resp = client.messages.create(
            model=MODEL, messages=messages[:tail_start] + [{"role": "user",
                "content": "Summarize key facts, decisions, and pending work."}],
            max_tokens=800)
        summary = resp.content[0].text if resp.content else "Compacted."
    except Exception:
        summary = "Compacted."
    return [{"role": "user", "content": f"[Reactive compact]\n\n{summary}"}] + messages[tail_start:]


# -- pipeline --------------------------------------------------------------
def run_compaction_pipeline(messages) -> list:
    messages = tool_result_budget(messages)
    messages = snip_compact(messages)
    messages = micro_compact(messages)
    return messages
