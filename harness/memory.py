"""s09: Memory — file-backed persistent memory storage."""
import json

from config import MEMORY_DIR
from tools.skills import _parse_frontmatter

MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"


def list_memory_files() -> list[dict]:
    files = []
    for f in sorted(MEMORY_DIR.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        try:
            content = f.read_text()
            meta, _ = _parse_frontmatter(content)
            files.append({
                "filename": f.name,
                "name": meta.get("name", f.stem),
                "description": meta.get("description", ""),
                "type": meta.get("type", "user"),
            })
        except Exception:
            pass
    return files


def select_relevant_memories(messages, max_items=5) -> list[str]:
    files = list_memory_files()
    if not files:
        return []
    recent = " ".join(str(m.get("content", "")) for m in messages[-3:])[:2000].lower()
    selected = []
    for f in files:
        if (f["name"].lower() in recent
                or f["description"].lower() in recent
                or any(kw in recent for kw in f["name"].lower().replace("-", " ").split())):
            selected.append(f["filename"])
    return selected[:max_items]


def inject_memories(context: dict) -> dict:
    selected = select_relevant_memories([])
    texts = []
    for fn in selected:
        path = MEMORY_DIR / fn
        if path.exists():
            texts.append(path.read_text()[:2000])
    context["memories"] = "\n---\n".join(texts)
    return context
