# Rabbit Agent

A coding agent built from first principles in Python — from a bare `while` loop to a multi-agent system with 29 tools. Uses the Anthropic Messages API, works with Anthropic, DeepSeek, or any compatible endpoint.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/soragui/rabbit_agent/main/install.sh | bash
```

Then edit `~/.rabbit-agent/settings.json` with your API key:

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
rabbit-agent
```

## Dev mode

```bash
git clone git@github.com:soragui/rabbit_agent.git && cd rabbit_agent
uv python install 3.13
uv sync
cp .env.example .env   # edit with your API key
uv run python agent.py
```

Type `?` for help. `q` or Ctrl+C twice to exit.

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
- **Render** — Rich-based terminal UI with markdown rendering, syntax highlighting, spinners
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
| Installed | `~/.rabbit-agent/settings.json` |
| Dev | `.env` in the repo root |

| Key | Description |
|---|---|
| `api_key` / `ANTHROPIC_API_KEY` | API key |
| `api_base_url` / `ANTHROPIC_BASE_URL` | API endpoint (omit for Anthropic, set for DeepSeek) |
| `model` / `MODEL_ID` | Model ID (default: `claude-sonnet-4-6`) |
| `fallback_model` / `FALLBACK_MODEL_ID` | Fallback after repeated 529 errors |

Runtime directories (`.memory`, `.tasks`, `.mailboxes`, `.worktrees`, `.transcripts`, `.task_outputs`) live under `RABBIT_HOME` and are auto-created on import.
