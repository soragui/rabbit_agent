# 51agent

A coding agent built from first principles in Python — from a bare `while` loop to a multi-agent system with 29 tools. Uses the Anthropic Messages API, works with Anthropic, DeepSeek, or any compatible endpoint. On a real terminal it runs in a full-screen TUI; piped or non-TTY runs fall back to plain scrollback output.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/soragui/rabbit_agent/main/install.sh | bash
```

Then edit `~/.51agent/settings.json` with your API key:

```json
{
    "api_key": "sk-your-api-key-here",
    "api_base_url": "https://api.anthropic.com",
    "model": "claude-sonnet-4-6",
    "fallback_model": null
}
```

Run from any directory:

```bash
51agent
```

## Dev mode

```bash
git clone git@github.com:soragui/rabbit_agent.git && cd rabbit_agent
uv python install 3.13
uv sync
cp .env.example .env   # edit with your API key
uv run python agent.py
```

## The full-screen TUI

On a terminal, `51agent` (or `uv run python agent.py`) starts a full-screen interface:

- **Header** — model, agent state, plan-mode phase
- **Chat pane** — your prompts and the assistant's streamed replies
- **Activity pane** — tool calls with ✓/✗/⏳ markers, permission questions, background-task and cron notifications
- **Status bar** — context usage, workdir, git branch + dirty count, python/node versions, queued cron jobs

| Key | Action |
|---|---|
| `Enter` | submit (`Esc`+`Enter` inserts a newline) |
| `Tab` | cycle focus between the input and the panes |
| `PgUp` / `PgDn` | scroll a focused pane |
| `Ctrl+C` mid-turn | abort the turn (kills running bash commands) |
| `Ctrl+C` at the prompt | exit |
| `q` | exit — saves the session; next start offers to resume |

Permission prompts (`Allow bash? (y/N)`) appear in the activity pane and are answered through the input line. Piped or non-TTY runs (`echo "…" | 51agent`) keep the plain scrollback output unchanged.

## Progressive learning stages

Three standalone scripts trace how an agent is built, step by step:

| Script | What it teaches |
|---|---|
| `s01_loop.py` | Core agent loop — `while stop_reason == "tool_use"` with a single `bash` tool |
| `s02_tools.py` | Tool dispatch — a dict mapping tool names to lambdas, 5 tools |
| `s03_permission.py` | 3-gate permission pipeline — deny-list → rule match → user prompt |

Each script is fully self-contained — read them in any order.

## Architecture

The full agent (`agent.py`) imports from two packages:

### `harness/` — core infrastructure

- **Hook system** — 4 extension points: `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`
- **Permissions** — deny-list, workspace-boundary enforcement, interactive approval for risky ops
- **Memory** — file-backed persistent memory from `.memory/*.md` with YAML frontmatter
- **Compaction** — 4-level pipeline (budget → snip → micro → reactive), preserves tool-use pair integrity
- **Recovery** — exponential backoff with jitter, automatic model fallback on repeated 529 errors
- **Background** — auto-detects slow bash commands and dispatches to daemon threads
- **Render facade** — routes all output to TUI events in full-screen mode, Rich/print otherwise — every call site is mode-agnostic
- **UI bridge** — thread-safe event queue, pending-question mechanism, and abort flag between the agent's worker threads and the UI
- **TUI** — prompt_toolkit full-screen app: layout, key bindings, event consumer, status refresh
- **Status** — status-bar collectors (git/python/node, token usage, cron count) with TTL caching
- **Tool pool** — assembles the complete `(tools, handlers)` tuple including dynamically-prefixed MCP tools

### `tools/` — tool implementations

- **Filesystem** — `bash`, `read_file`, `write_file`, `edit_file`, `glob`
- **Subagent** — isolated agent with its own message loop, max 30 turns
- **Skills** — on-demand loading of `SKILL.md` files from `skills/` directory
- **Task system** — persistent task graph with dependency tracking in `.tasks/*.json`
- **Cron** — 5-field cron scheduler with recurring and durable job support
- **Teams** — multi-agent system with file-backed message bus, protocol state machine, idle-poll loop
- **Worktrees** — git worktree isolation branched as `wt/{name}`
- **MCP** — plugin protocol with mock `docs` and `deploy` servers demonstrating the pattern
- **Todo** — session-level todo list with reminder injection

## Configuration

| Mode | Config location |
|---|---|
| Installed | `~/.51agent/settings.json` |
| Dev | `.env` in the repo root |

| Key | Description |
|---|---|
| `api_key` / `ANTHROPIC_API_KEY` | API key |
| `api_base_url` / `ANTHROPIC_BASE_URL` | API endpoint (omit for Anthropic, set for DeepSeek) |
| `model` / `MODEL_ID` | Model ID (default: `claude-sonnet-4-6`) |
| `fallback_model` / `FALLBACK_MODEL_ID` | Fallback after repeated 529 errors |

Runtime directories (`.memory`, `.tasks`, `.mailboxes`, `.worktrees`, `.transcripts`, `.task_outputs`) live under `AGENT_HOME` and are auto-created on import.
