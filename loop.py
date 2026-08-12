"""s01-s20: Agent Loop — all mechanisms on one while True."""
import time as _time

from anthropic import APIStatusError

from config import (
    DEFAULT_MAX_TOKENS,
    ESCALATED_MAX_TOKENS,
    FALLBACK_MODEL,
    MAX_RECOVERY_RETRIES,
    MAX_RETRIES,
    provider,
)
from harness import trigger_hooks
from harness.background import (
    collect_background_results,
    should_run_background,
    start_background_task,
)
from harness.compact import reactive_compact, run_compaction_pipeline, safe_messages_slice
from harness.prompt import assemble_system_prompt
from harness.recovery import RecoveryState, _retry_delay
from harness.render import render_error, render_info, streaming_renderer
from tools.todo import CURRENT_TODOS, get_todo_round, increment_todo_round, reset_todo_round


def _stream_llm(messages, tools, state, system, max_tokens):
    """Stream an LLM response with retry on 429/529, rendering text live.

    Returns the final Message (same shape as a blocking response).
    Raises on non-retryable errors.
    """
    for attempt in range(MAX_RETRIES):
        try:
            stream = provider.create_message_stream(
                model=state.current_model, system=system,
                messages=safe_messages_slice(messages, 100),
                tools=tools, max_tokens=max_tokens,
            )
            with streaming_renderer() as render:
                text_buf: list[str] = []
                with stream as event_stream:
                    for event in event_stream:
                        if event.type == "content_block_delta":
                            delta = event.delta
                            if delta.type == "text_delta":
                                text_buf.append(delta.text)
                                render("".join(text_buf))
                return stream.get_final_message()
        except APIStatusError as e:
            if e.status_code not in (429, 529):
                raise
            if attempt >= MAX_RETRIES - 1:
                break
            delay = _retry_delay(attempt)
            render_info(f"Retry {e.status_code} — waiting {delay:.1f}s (attempt {attempt + 1}/{MAX_RETRIES})")
            _time.sleep(delay)
            if e.status_code == 529:
                state.consecutive_529 += 1
                if state.consecutive_529 >= 3 and FALLBACK_MODEL:
                    render_info(f"Switching to fallback model: {FALLBACK_MODEL}")
                    state.current_model = FALLBACK_MODEL
    msg = f"Max retries ({MAX_RETRIES}) exceeded"
    raise Exception(msg)


def agent_loop_full(messages: list, context: dict, tools: list[dict], handlers: dict):
    state = RecoveryState()
    max_tokens = DEFAULT_MAX_TOKENS
    system = assemble_system_prompt(context)

    while True:
        # compaction pipeline
        messages[:] = run_compaction_pipeline(messages)

        # LLM call — streaming with live render
        try:
            response = _stream_llm(messages, tools, state, system, max_tokens)
        except KeyboardInterrupt:
            print()
            render_info("Interrupted.")
            return
        except APIStatusError as e:
            err_str = str(e).lower()
            if "prompt_too_long" in err_str or "413" in err_str:
                if not state.has_attempted_reactive_compact:
                    messages[:] = reactive_compact(messages)
                    state.has_attempted_reactive_compact = True
                    continue
                render_error("Context still too long after compact.")
                return
            render_error(str(e))
            return
        except Exception as e:
            render_error(str(e))
            return

        # handle max_tokens truncation
        if response.stop_reason == "max_tokens":
            if not state.has_escalated and max_tokens < ESCALATED_MAX_TOKENS:
                max_tokens = ESCALATED_MAX_TOKENS
                state.has_escalated = True
                continue
            messages.append({"role": "assistant", "content": response.content})
            if state.recovery_count < MAX_RECOVERY_RETRIES:
                messages.append({"role": "user",
                    "content": "Continue from the previous response. Do not repeat completed work."})
                state.recovery_count += 1
                continue
            return

        messages.append({"role": "assistant", "content": response.content})

        # s05: todo reminder
        increment_todo_round()
        if get_todo_round() > 3 and CURRENT_TODOS:
            messages.append({"role": "user", "content": "<reminder>Update your todos.</reminder>"})
            reset_todo_round()

        # stop if no tool calls
        if response.stop_reason != "tool_use":
            trigger_hooks("Stop", messages)
            return

        # s13: collect background notifications
        bg_notifications = collect_background_results()

        # execute tools
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            if block.name == "todo_write":
                reset_todo_round()

            # s03/s04: PreToolUse hooks + permission
            blocked = trigger_hooks("PreToolUse", block)
            if blocked:
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(blocked)})
                continue

            # s13: background dispatch
            if should_run_background(block.name, block.input):
                handler = handlers.get(block.name, lambda **kw: "Unknown tool")
                bg_id = start_background_task(block, handler)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": f"[Background task {bg_id} started] Will notify when complete."})
                continue

            # normal execution
            handler = handlers.get(block.name)
            try:
                output = handler(**block.input) if handler else f"Unknown tool: {block.name}"
            except Exception as e:
                output = f"Error: {e}"

            trigger_hooks("PostToolUse", block, output)
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(output)})

        # compose user message: bg notifications + tool results
        user_content = [{"type": "text", "text": n} for n in bg_notifications]
        user_content.extend(results)
        messages.append({"role": "user", "content": user_content})
