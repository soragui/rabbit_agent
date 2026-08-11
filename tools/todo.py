"""s05: TodoWrite — session-level planning tool."""

CURRENT_TODOS: list[dict] = []
_todo_round_count = 0


def run_todo_write(todos: list) -> str:
    global CURRENT_TODOS, _todo_round_count
    CURRENT_TODOS = todos
    _todo_round_count = 0
    lines = ["\n## Current Tasks"]
    for t in CURRENT_TODOS:
        icon = {"pending": " ", "in_progress": "▸", "completed": "✓"}[t.get("status", "pending")]
        lines.append(f"  [{icon}] {t['content']}")
    print("\n".join(lines))
    return f"Updated {len(CURRENT_TODOS)} tasks"


def get_todo_round() -> int:
    return _todo_round_count


def increment_todo_round():
    global _todo_round_count
    _todo_round_count += 1


def reset_todo_round():
    global _todo_round_count
    _todo_round_count = 0
