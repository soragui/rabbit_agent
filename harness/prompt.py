"""s10: System Prompt — runtime assembly from sections."""
from config import WORKDIR
from tools.skills import SKILL_REGISTRY


def assemble_system_prompt(context: dict) -> str:
    sections = [
        f"You are 51agent, a coding assistant working in {WORKDIR}. Act, don't explain. "
        "Use tools to complete tasks. Before complex tasks, use todo_write to plan.",
    ]

    tools_list = context.get("enabled_tools", [])
    if tools_list:
        sections.append(f"Available tools: {', '.join(tools_list)}")
    sections.append(f"Working directory: {WORKDIR}")

    if SKILL_REGISTRY:
        catalog = "\n".join(f"- **{s['name']}**: {s['description']}"
                          for s in SKILL_REGISTRY.values())
        sections.append(f"Skills available (use load_skill for details):\n{catalog}")

    memories = context.get("memories", "")
    if memories:
        sections.append(f"Relevant memories:\n{memories}")

    mcp_info = context.get("mcp_servers", "")
    if mcp_info:
        sections.append(f"Connected MCP servers: {mcp_info}")

    sections.append(
        "Use task tools (create_task, list_tasks, claim_task, complete_task) "
        "to manage work. Use todo_write for session-level planning. "
        "Use spawn_teammate for parallel work. Use worktrees for isolation.")

    return "\n\n".join(sections)
