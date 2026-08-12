"""s07: Skill Loading — on-demand injection from skills/ directory."""
from config import SKILLS_DIR

try:
    import yaml
except ImportError:
    yaml = None

SKILL_REGISTRY: dict[str, dict] = {}


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    if yaml is None:
        return {}, raw
    raw = raw.strip()
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
            except Exception:
                meta = {}
            return meta, parts[2].strip()
    return {}, raw


def load_skills():
    """Scan SKILLS_DIR and populate SKILL_REGISTRY."""
    if not SKILLS_DIR.exists():
        return
    for d in sorted(SKILLS_DIR.iterdir()):
        if not d.is_dir():
            continue
        manifest = d / "SKILL.md"
        if manifest.exists():
            raw = manifest.read_text()
            meta, _ = _parse_frontmatter(raw)
            name = meta.get("name", d.name)
            desc = meta.get("description", raw.split("\n")[0].lstrip("#").strip())
            SKILL_REGISTRY[name] = {"name": name, "description": desc, "content": raw}


def run_load_skill(name: str) -> str:
    skill = SKILL_REGISTRY.get(name)
    if not skill:
        available = ", ".join(SKILL_REGISTRY.keys())
        return f"Skill not found: {name}. Available: {available or '(none)'}"
    return skill["content"]


# auto-scan on import
load_skills()
