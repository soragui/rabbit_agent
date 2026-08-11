"""s04: Hooks system — extension points around the agent cycle."""
from harness.render import render_error

HOOKS = {
    "UserPromptSubmit": [],
    "PreToolUse": [],
    "PostToolUse": [],
    "Stop": [],
}


def register_hook(event: str, callback):
    if event in HOOKS:
        HOOKS[event].append(callback)


def trigger_hooks(event: str, *args):
    """Trigger all hooks for an event. First non-None return value blocks."""
    results = []
    for cb in HOOKS.get(event, []):
        try:
            r = cb(*args)
            if r is not None:
                results.append(r)
        except Exception as e:
            render_error(f"HOOK {event}: {e}")
    return results[0] if results else None
