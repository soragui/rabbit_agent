# Coding Agent — from scratch

A coding agent built from first principles in Python, tracing the full learning path from a bare `while` loop to a multi-agent system with 29 tools. Uses the Anthropic Messages API (via the `anthropic` Python SDK) — works with Anthropic, DeepSeek, or any compatible endpoint.

## Quick start

```bash
# Prerequisites: uv (https://docs.astral.sh/uv/)

# Install Python 3.13
uv python install 3.13

# Clone and sync
git clone <repo-url> && cd agent_main
uv sync

# Configure
cp .env.example .env
# Edit .env — fill in ANTHROPIC_API_KEY and adjust MODEL_ID / ANTHROPIC_BASE_URL

# Run
uv run python agent.py
```

Type `?` at the prompt for help. Type `q` or press Ctrl+C twice to exit.

## Progressive learning stages

Three standalone scripts trace how an agent is built, step by step:

| Script | What it teaches |
|---|---|
| `s01_loop.py` | Core agent loop — `while stop_reason == "tool_use"` with a single `bash` tool |
| `s02_tools.py` | Tool dispatch — a dict mapping tool names to lambdas, 5 tools |
| `s03_permission.py` | 3-gate permission pipeline — deny-list → rule match → user prompt |

Each script is fully self-contained (duplicates its own config and tools) so you can read them in any order.

## Architecture

The full agent (`agent.py`) imports from two packages:

### `harness/` — core infrastructure

- **Hook system** — 4 extension points: `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`. First non-None `PreToolUse` return blocks execution.
- **Permissions** — deny-list for dangerous commands, workspace-boundary enforcement, interactive approval for risky ops.
- **Memory** — file-backed persistent memory from `.memory/*.md` with YAML frontmatter, keyword-matched into context.
- **Compaction** — 4-level pipeline (budget → snip → micro → reactive) to keep context within limits while preserving tool-use pair integrity.
- **Recovery** — exponential backoff with jitter, automatic model fallback on repeated 529 errors.
- **Background** — auto-detects slow bash commands and dispatches them to daemon threads.
- **Tool pool** — assembles the complete `(tools, handlers)` tuple including dynamically-prefixed MCP tools.

### `tools/` — tool implementations

- **Filesystem** — `bash`, `read_file`, `write_file`, `edit_file`, `glob` (all workspace-bound)
- **Subagent** — isolated agent with its own message loop, 5 file tools, max 30 turns
- **Skills** — on-demand loading of `SKILL.md` files from `skills/` directory
- **Task system** — persistent task graph with dependency tracking in `.tasks/*.json`
- **Cron** — 5-field cron scheduler with recurring and durable job support
- **Teams** — multi-agent system with file-backed message bus, protocol state machine, idle-poll loop
- **Worktrees** — git worktree isolation branched as `wt/{name}`
- **MCP** — plugin protocol with mock `docs` and `deploy` servers demonstrating the pattern
- **Todo** — session-level todo list with reminder injection

## Configuration

All config lives in `config.py` and `.env`:

| Variable | Default | Description |
|---|---|---|
| `MODEL_ID` | `claude-sonnet-4-6` | Model to use |
| `FALLBACK_MODEL_ID` | (none) | Switched to after 3 consecutive 529s |
| `ANTHROPIC_BASE_URL` | — | API endpoint (set for DeepSeek, omit for Anthropic) |
| `ANTHROPIC_API_KEY` | — | API key |

Runtime directories (`.memory`, `.tasks`, `.mailboxes`, `.worktrees`, `.transcripts`, `.task_outputs`) are auto-created on import.
