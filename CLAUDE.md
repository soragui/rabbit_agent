# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is an educational project that builds a coding agent from scratch, implemented in Python. It uses the Anthropic Messages API (via the `anthropic` Python SDK) proxied through DeepSeek's compatible endpoint. The project traces a progressive learning path — `s01_loop.py` → `s02_tools.py` → `s03_permission.py` — then combines all mechanisms into the full agent at `agent.py`.

## Commands

```bash
# Install Python 3.13 (if not already available)
uv python install 3.13

# Sync dependencies (auto-creates .venv)
uv sync

# Run the full agent (interactive CLI) — dev mode
uv run python agent.py

# Install system-wide (to ~/.rabbit-agent)
bash install.sh
# Then run from any directory:
rabbit-agent

# Run progressive learning stages individually
uv run python s01_loop.py     # bare agent loop + bash tool
uv run python s02_tools.py    # 5 tools + handler dispatch map
uv run python s03_permission.py  # 3-gate permission pipeline

# Add/remove dependencies
uv add <package>
uv remove <package>
```

No test suite, linter, or build step exists.

## Architecture

### Progressive stages (standalone scripts)

`s01_loop.py`, `s02_tools.py`, `s03_permission.py` are self-contained educational snapshots. Each duplicates its own config and tool implementations — they are not imported by the main agent. `s01` introduces the core `while stop_reason == "tool_use"` loop. `s02` adds the tool handler dispatch pattern (a dict mapping tool names to lambdas). `s03` layers a 3-gate permission pipeline (hard deny-list → rule match → interactive user prompt).

### Full agent (`agent.py` + modules)

The main agent is `agent.py`, which imports from two packages:

#### `harness/` — core agent infrastructure

| Module | Role |
|---|---|
| `__init__.py` | Hook system with 4 extension points: `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`. First non-None return from `PreToolUse` blocks execution. |
| `permissions.py` | Installs `PreToolUse` hooks: deny-list of dangerous commands (e.g. `rm -rf /`), workspace-boundary enforcement for file tools, interactive user prompt for risky operations like `rm`, `curl`, `wget`. |
| `memory.py` | File-backed persistent memory from `.memory/*.md` files with YAML frontmatter. Keyword-matches recent conversation content against memory names/descriptions to select relevant memories for injection into the system context. |
| `compact.py` | 4-level context compaction pipeline (budget → snip → micro → reactive). `tool_result_budget` persists oversized outputs to disk. `snip_compact` drops middle messages while preserving tool_use/tool_result pair integrity. `micro_compact` collapses old tool results. `reactive_compact` is triggered on `prompt_too_long` errors — summarizes early conversation and keeps the tail. |
| `recovery.py` | `with_retry()` wraps LLM calls with exponential backoff (500ms base, max 32s with jitter). On 3 consecutive 529s, switches to `FALLBACK_MODEL_ID`. Tracks `RecoveryState` (has_escalated, consecutive_529, current_model). |
| `background.py` | Detects slow bash commands (`install`, `build`, `test`, `deploy`, etc.) and dispatches them as daemon threads. Results are collected next turn and injected as `<task_notification>` blocks. Also honors explicit `run_in_background: true`. |
| `prompt.py` | Assembles the system prompt at runtime from context: enabled tools list, skill catalog, injected memories, MCP server info. |
| `tool_pool.py` | Single `assemble_tool_pool()` function that returns the complete `(tools, handlers)` tuple — built-in tools plus dynamically-prefixed MCP tools (`mcp__{server}__{tool}`). |

#### `tools/` — tool implementations

| Module | Role |
|---|---|
| `__init__.py` | File-system tools: `bash` (subprocess, 300s timeout), `read_file`, `write_file`, `edit_file` (single-replace), `glob`. All paths validated by `safe_path()` to stay within `WORKDIR`. |
| `subagent.py` | `spawn_subagent()` runs an isolated agent with its own message loop (max 30 turns), only 5 file tools. Returns final text. No streaming. |
| `skills.py` | Scans `skills/` directory at import time for `SKILL.md` files with YAML frontmatter (`name`, `description`). `load_skill` tool returns full skill content. |
| `task_system.py` | Persistent task graph. Tasks are JSON files in `.tasks/` with `blockedBy` dependency lists. `claim_task` enforces dependency resolution. `complete_task` auto-detects unblocked downstream tasks. |
| `cron.py` | 5-field cron scheduler running in a daemon thread, checking every second. `schedule_cron` tool supports `recurring` and `durable` flags. Durable jobs persist to `.scheduled_tasks.json`. |
| `teams.py` | Multi-agent system with a `MessageBus` (file-backed via `.mailboxes/*.jsonl`), protocol state machine (shutdown, plan_approval), and idle-poll loop that auto-claims unblocked tasks. Teammates get a reduced tool set including `send_message` to the lead. |
| `worktree.py` | Git worktree isolation. Creates branches as `wt/{name}`, validates names, refuses removal of dirty worktrees unless `discard_changes=true`. |
| `mcp.py` | MCP client registry with mock servers (`docs`, `deploy`) demonstrating the pattern. Tools get `mcp__{server}__{tool}` prefixes in the main tool pool. Never connects to real MCP servers — mock only. |
| `todo.py` | Session-level todo list tracking. The loop injects a reminder after 3+ turns without a `todo_write` call. |

### Agent loop flow (`loop.py` → `agent_loop_full`)

1. **Compaction pipeline** runs on every turn (budget → snip → micro)
2. **LLM call** via `with_retry()`, with `safe_messages_slice` keeping tool pairs intact
3. **max_tokens handling**: escalate to 64K on first truncation; inject "Continue" user message on subsequent
4. **Todo reminder**: after 3 turns without `todo_write`, inject a reminder
5. **Tool execution**: PreToolUse hooks run first (permission check, logging); background-capable tools dispatched to daemon threads; PostToolUse hooks fire on completion
6. **MCP rebinding**: if `connect_mcp` was called, rebuild the tool pool so new tools are available immediately
7. **Cron queue processor**: a background thread waits for agent idle, acquires a lock, and delivers queued cron jobs through the same `agent_loop_full`

### Configuration (`config.py`)

- `WORKDIR`: `Path.cwd()` (the agent works in its launch directory)
- `MODEL`: `MODEL_ID` env var (defaults to `claude-sonnet-4-6`); currently `deepseek-v4-pro`
- API: uses `ANTHROPIC_BASE_URL` pointing to `https://api.deepseek.com/anthropic`
- All runtime directories (`.memory`, `.tasks`, `.mailboxes`, `.worktrees`, `.transcripts`, `.task_outputs/tool-results`) are auto-created on import
- `safe_path()` enforces all file operations stay within `WORKDIR`

### Configuration (`config.py`)

Two modes:
- **Installed**: loads from `$RABBIT_HOME/settings.json` (`~/.rabbit-agent/settings.json`)
- **Dev**: loads from `.env` in the current directory (via `python-dotenv`)

`RABBIT_HOME` is the agent's own directory (runtime data, skills, settings). `WORKDIR` is always `cwd()` — the directory the user invoked the agent from. All file tools are bound to `WORKDIR`; runtime directories live under `RABBIT_HOME`.

### Key design patterns

- **Tool dispatch**: every tool is a pair — a JSON Schema definition + a lambda handler — indexed by name. Adding a tool means adding entries to both `tools` list and `handlers` dict.
- **Hook pipeline**: extension points are pre-registered callbacks. `PreToolUse` blocks on first non-None return. Used by permissions (blocking) and logging (observability, always returns None).
- **File-based IPC**: teammates and the lead communicate through `.mailboxes/*.jsonl` files (write-append, read-and-delete). Task state persists as individual JSON files.
- **Context safety**: all compaction operations preserve tool_use/tool_result pair boundaries so the API never receives orphaned results.
