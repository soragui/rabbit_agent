"""Plan-mode state machine — read-only exploration → approval → execution."""
from dataclasses import dataclass

READONLY_TOOLS = {
    "read_file", "glob", "grep", "list_tasks", "get_task",
    "load_skill", "check_inbox", "list_crons", "compact", "todo_write",
}


@dataclass
class PlanState:
    """Tracks plan-mode lifecycle for one turn sequence."""

    active: bool = False
    phase: str = "idle"  # idle → planning → awaiting_approval → executing → idle
    plan_text: str = ""
    original_query: str = ""
    feedback: str = ""

    def start_planning(self, query: str) -> list[str]:
        """Enter planning phase. Returns the allowed-tool list for this phase."""
        self.active = True
        self.phase = "planning"
        self.original_query = query
        self.plan_text = ""
        self.feedback = ""
        return list(READONLY_TOOLS)

    def submit_plan(self, text: str) -> None:
        """Called when the agent produces a plan (stop_reason != tool_use)."""
        self.plan_text = text
        self.phase = "awaiting_approval"

    def approve(self) -> None:
        """User accepted the plan. Enter execution with full tools."""
        self.phase = "executing"

    def reject(self) -> None:
        """User rejected. Reset to idle."""
        self.reset()

    def revise(self, feedback: str) -> list[str]:
        """User wants changes. Re-enter planning with feedback."""
        self.feedback = feedback
        self.phase = "planning"
        return list(READONLY_TOOLS)

    def reset(self) -> None:
        self.active = False
        self.phase = "idle"
        self.plan_text = ""
        self.original_query = ""
        self.feedback = ""

    @property
    def is_readonly(self) -> bool:
        return self.phase in ("planning", "awaiting_approval")

    @property
    def plan_context(self) -> str:
        """The plan text to inject as context during execution."""
        if self.plan_text and self.phase == "executing":
            return f"[Approved plan]\n\n{self.plan_text}\n\nExecute the plan above."
        return ""


# -- singleton ------------------------------------------------------------
_plan_state = PlanState()


def get_plan_state() -> PlanState:
    return _plan_state
