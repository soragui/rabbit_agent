# TUI Redesign — Full-Screen Terminal Interface for 51agent

- **Date:** 2026-08-13
- **Status:** Approved for planning (design review passed 2026-08-13)
- **Scope:** `agent.py`, `loop.py`, `harness/` (render, permissions), `tools/__init__.py` (bash abort), plus new modules under `harness/`

## Problem

The agent currently runs as an ordinary scrollback CLI: prompt_toolkit input at the bottom of terminal scrollback, Rich-markdown output printed above it, and raw `input()` prompts for permissions and session resume. The screen has no persistent structure — no header, no status information, no visible state while the agent works. Tool activity, responses, and prompts all mix in one stream.

## Goals

1. Full-screen TUI on startup (TTY mode): header on top, split middle (chat pane + activity pane), footer input, status bar at the bottom.
2. Status bar shows: context usage (tokens vs limit), current working directory, git branch + dirty state, python version, node version, queued cron count, agent state.
3. All interactive prompts (permissions, session resume, plan approval) answered through the footer input — no raw `input()` while the TUI is up.
4. Ctrl+C aborts the current turn (stream and running tool) without killing the app.
5. Non-TTY / piped execution keeps today's plain output with zero behavior change.

## Non-Goals

- No markdown rendering in the chat pane (v1 renders plain text with role-based colors).
- No mouse support, no themes/customization UI.
- The educational stage scripts (`s01_loop.py`, `s02_tools.py`, `s03_permission.py`) are untouched.
- No changes to the agent loop's semantics (tools, compaction, cron, teams, plan mode all behave as before).

## Locked Decisions

- **Framework:** prompt_toolkit full-screen `Application` (already a dependency; ptpython-style layout). No new dependencies.
- **Layout:** split middle — chat pane (major height) over activity pane (minor height), labeled "Activity".
- **Threading:** agent turns run in a worker thread; the UI thread only consumes render events.

## Architecture

```
┌─ UI thread (prompt_toolkit Application) ─────────────────────┐
│  footer input → submit handler                                │
│      ├─ pending question?  → answer it                        │
│      └─ otherwise           → spawn worker thread → turn      │
│                                                               │
│  UI event queue ◄── call_from_executor ◄── render_*() facade  │
│  status timer (1s) → status.py collectors → invalidate        │
└───────────────────────────────────────────────────────────────┘
   worker thread: handle_input() → agent_loop_full() → render_*
```

The seam between the agent and the screen is one thread-safe event queue. Every existing `render_*()` call site stays untouched — `harness/render.py` becomes a facade that emits events when the TUI is active and prints (current Rich behavior) otherwise.

## Modules

### New: `harness/ui_bridge.py`

Thread-safe bridge between worker thread and UI thread.

- `Event` dataclass: `kind` (one of `chat`, `activity`, `stream`, `question`, `clear_stream`), `payload: str`, optional `style` hint.
- `emit(event)` — append to `queue.Queue`, thread-safe.
- `drain() -> list[Event]` — called by the UI thread.
- `ask_question(text: str, default: bool = False, timeout: float | None = None) -> bool` — blocks the calling (worker) thread on a `threading.Event` until the UI thread supplies an answer; empty line = `default`; `timeout` returns `default`. Exactly one question may be pending at a time.
- `abort` flag (`threading.Event`) — set by Ctrl+C in the UI thread; checked by the stream loop and the bash handler.
- `TokenTracker` is **not** here — it lives in `status.py`.

### New: `harness/tui.py`

The prompt_toolkit `Application` and its layout:

- Layout (top → bottom), heights as weights:
  1. **Header** (1 row): `51agent · {model}` + agent state (`idle` / `thinking` / `running {tool}`) + plan-mode phase when active.
  2. **Chat pane** (weight ~7): conversation — user prompts, assistant stream, info/error lines. Read-only buffer.
  3. **Activity pane** (weight ~3, titled `Activity`): tool calls with ✓/✗/⏳, permission questions, background-task and cron notifications, teammate inbox. Read-only buffer.
  4. **Footer input** (dynamic): the existing prompt buffer — FileHistory at `.agent_history`, merged completers (agent commands + tool names + `PathCompleter`), `51agent >> ` message, Esc+Enter inserts newline, Enter submits.
  5. **Status bar** (1 row): see Status Bar spec below.
- Key bindings: Enter submit, Esc+Enter newline, Tab cycles focus (input ↔ panes) for PgUp/PgDn scrolling, Ctrl+C sets the abort flag.
- The submit handler routes by state: pending question → answer it; plan `awaiting_approval` → the line is a plan decision; else → new prompt → worker thread runs `handle_input`.
- UI queue consumption: events arrive via `call_from_executor`; `stream` events append/replace the in-flight chat block so streaming is incremental; panes auto-scroll to bottom when at bottom.
- `run(startup: Callable) -> None` — entry point. On non-TTY stdin/stdout, refuses to start (caller falls back to plain mode).
- Status refresh: 1s timer thread collecting via `status.py` and invalidating the status bar.

### New: `harness/status.py`

Pure, unit-testable collectors (subprocess-based ones take a `timeout` and never raise):

- `git_summary(workdir) -> str | None` — `"{branch} ✓"` / `"{branch} ✗{n}"` via `git rev-parse --abbrev-ref HEAD` + `git status --porcelain` count; `None` if not a repo or git missing. Results cached (2s TTL) at the call site, not inside the function.
- `python_version() -> str` — from `sys.version_info`, no subprocess.
- `node_version() -> str | None` — `node --version` if `shutil.which("node")`; `None` otherwise; cached.
- `cron_count() -> int` — pending jobs in the cron queue (import from `tools.cron`).
- `TokenTracker` — `update(input_tokens, output_tokens)` from the stream's final-message `usage`; renders `ctx {:.0f}k/{}` against `CONTEXT_LIMIT` (50k). Kept across turns (running session total of the last call's usage — v1 does not sum history).
- `collect_status(ctx) -> list[str]` — ordered segments: `ctx`, `agent state`, workdir, git, python, node, cron (omits `None` segments).

### Modified: `harness/render.py`

Facade. `_TUI_ACTIVE` flag set by `tui.py` at startup. Every render function branches:

- `render_markdown` / `render_tool_use` / `render_error` / `render_info` / `render_inbox` / `render_banner` / `render_help` → emit `chat`/`activity` events (tool use, inbox, permission questions → activity; everything else → chat) when TUI-active, else current behavior.
- `streaming_renderer()` — TUI branch yields a callable that emits `stream` events per chunk; plain branch keeps Rich `Live`.
- `spinner()` — TUI branch: emit a state event (`thinking`/`running tool`), no visual spinner (the status bar + ⏳ markers show activity).

### Modified: `agent.py`

- Extract the while-loop body into `handle_input(query: str, history: list) -> bool` (returns whether the session should continue). Branches preserved: `q`/`exit`/`quit`, `?` help, empty-line inbox check, plan approval/revision, `/plan`, normal execution, MCP rebinding. No UI code remains here.
- Main: TTY → `tui.run()` with startup callback (banner into chat pane, then pending question "Resume session from {time}? (y/N)"). Non-TTY → the current plain loop, unchanged.

### Modified: `loop.py`

- `_stream_llm`: checks the abort flag between stream chunks (raises a `TurnAborted` exception, caught at the turn boundary); records stream usage into `TokenTracker`.
- Exception handling at turn boundary: catch `TurnAborted` → render "Interrupted." and return.

### Modified: `tools/__init__.py` (bash handler)

- The bash handler gains abort support: poll the abort flag while the subprocess runs (e.g. every 100ms) and terminate the process group on abort; return `"[aborted]"` as tool output.

### Modified: `harness/permissions.py`

- `_prompt_user` → TUI-active branch calls `ask_question(f"Allow {tool}? [detail] (y/N)")`; non-TUI branch keeps `input()`.

### Plan-mode approval

Answered through the footer via existing input routing (no blocking event needed — no turn is running while awaiting approval). UX-identical to a pending question: `render_info("Approve? (y/Enter = yes, n = no, r: feedback)")` lands in the chat pane, the next line is the decision. This is a deliberate refinement of the original "all three become pending questions" framing: pending-question is reserved for prompts raised while the worker thread is mid-turn.

## Pending-Question Semantics

- Raised by: permission hook (mid-turn), startup resume (pre-loop).
- Rendered in the activity pane (permissions) or chat pane (resume) with the `[y/N]` default shown.
- The footer input is the only answer surface. Empty line = default (deny for permissions, no for resume). Non-empty line = answer (`y`/`yes` true; anything else false).
- While a question is pending, the submit handler never starts a new turn — the line always answers the question.
- Timeout (permissions only, 300s): returns default, renders "auto-denied".
- If the worker thread dies with a question pending, the UI clears it and re-enables input.

## Turn Lifecycle

1. **Startup (TTY):** app launches full-screen; header + banner render into chat; resume pending question shown. No text printed before the TUI exists.
2. **Submit:** input handler routes (question / plan decision / new prompt). New prompt spawns the worker thread; agent state → `thinking`.
3. **Streaming:** `stream` events update the in-flight block in the chat pane; abort flag checked between chunks.
4. **Tool execution:** activity pane ⏳ → permission question may block the worker → answer via footer → result ✓/✗.
5. **Stop:** usage → TokenTracker; state → `idle`; input re-enabled. Cron queue processor and background-task notifications arrive as activity events.

## Status Bar Spec

Ordered segments, separated by ` │ `:

| Segment | Source | Format |
|---|---|---|
| context | TokenTracker | `ctx 12.4k/50k` (or `ctx –/50k` before first call) |
| agent state | ui bridge | `idle` / `thinking` / `running bash` |
| workdir | `WORKDIR` | basename, `~`-abbreviated |
| git | `git_summary` | `main ✓` / `main ✗3` |
| python | `sys.version_info` | `py 3.13.2` |
| node | `node --version` | `node 22.11.0` |
| cron | cron queue | `3 cron` (omitted when 0) |

Refresh: 1s timer; git/node subprocess results cached 2s to avoid spawning processes every tick. All collectors are `None`-safe; `None` segments are omitted.

## Error Handling

- **API errors** → red `✗` line in chat pane (existing `render_error` re-targeted).
- **Ctrl+C mid-turn** → abort flag; stream raises between chunks; bash handler kills the process group; partial output stays; input returns. App never exits on Ctrl+C mid-turn.
- **Ctrl+C at the idle footer** → exits the app ("Bye."), matching current behavior.
- **Worker-thread exceptions** → caught at turn boundary, rendered, UI survives; UI thread never runs agent code.
- **Non-TTY / piped / broken pipe** → TUI never starts; plain fallback path unchanged.
- **Resize** → handled by prompt_toolkit layout weights; no custom code.
- **Unwritable history file** → in-memory history fallback.

## Testing Plan

Unit tests (pytest; no TTY required):

- `status.py`: git/python/node collectors with mocked subprocess; `TokenTracker` accounting and formatting; `collect_status` segment ordering and `None` omission.
- `ui_bridge.py`: queue round-trip; `ask_question` answer / default-on-empty / timeout; abort flag.
- render facade: TUI-active branch enqueues without printing; inactive branch prints (smoke via capsys).
- `handle_input` (agent.py): `q`, `?`, `/plan`, plan-approval branches with a stubbed loop.
- bash handler abort: subprocess started, abort flag set, assert termination (fast `sleep` command).

Manual smoke (real terminal):

- resize, pane scrolling (Tab/PgUp/PgDn), Ctrl+C during a long bash, permission y/n + auto-deny on empty, resume prompt, non-TTY piped run.

## Risks & Accepted Tradeoffs

- **Markdown rendered as plain text** in the chat pane (prompt_toolkit has no markdown widget). Readable; formatting lost. Revisit only if it hurts.
- **Activity pane truncation:** long tool outputs are truncated to ~200 chars there (full content already goes to the model and to `.task_outputs/` on persistence). Conversation stays clean.
- **Two-event-loop discipline:** all cross-thread traffic must go through `ui_bridge` (`emit` / `ask_question` / `call_from_executor`). Any direct UI mutation from the worker thread is a bug — the facade centralizes this.
- **Buffers vs formatters:** chat/activity use read-only `Buffer`s so scrolling, wrapping, and cursor navigation come for free.

## File-by-File Change List

| File | Change |
|---|---|
| `harness/ui_bridge.py` | **new** — event queue, pending question, abort flag |
| `harness/tui.py` | **new** — Application, layout, key bindings, status timer |
| `harness/status.py` | **new** — status collectors + TokenTracker |
| `harness/render.py` | modified — facade with TUI/plain branches |
| `agent.py` | modified — extract `handle_input`; TTY/plain dispatch in main |
| `loop.py` | modified — abort checks, usage tracking |
| `tools/__init__.py` | modified — bash abort support |
| `harness/permissions.py` | modified — `_prompt_user` TUI branch |
| `tests/` | new tests per Testing Plan |
