"""Tests for plan-mode state machine."""
from harness.plan import READONLY_TOOLS, PlanState, get_plan_state


class TestPlanStateLifecycle:
    def test_initial_state_idle(self):
        ps = PlanState()
        assert ps.active is False
        assert ps.phase == "idle"
        assert ps.is_readonly is False

    def test_start_planning(self):
        ps = PlanState()
        allowed = ps.start_planning("add logout button")
        assert ps.phase == "planning"
        assert ps.active is True
        assert ps.is_readonly is True
        assert "read_file" in allowed
        assert "write_file" not in allowed
        assert "bash" not in allowed

    def test_submit_plan(self):
        ps = PlanState()
        ps.start_planning("test")
        ps.submit_plan("Step 1: do X. Step 2: do Y.")
        assert ps.phase == "awaiting_approval"
        assert ps.plan_text == "Step 1: do X. Step 2: do Y."

    def test_approve_flow(self):
        ps = PlanState()
        ps.start_planning("test")
        ps.submit_plan("the plan")
        ps.approve()
        assert ps.phase == "executing"
        assert ps.plan_context != ""
        assert "the plan" in ps.plan_context

    def test_reject_flow(self):
        ps = PlanState()
        ps.start_planning("test")
        ps.submit_plan("the plan")
        ps.reject()
        assert ps.phase == "idle"
        assert ps.active is False
        assert ps.plan_text == ""

    def test_revise_flow(self):
        ps = PlanState()
        ps.start_planning("test")
        ps.submit_plan("the plan")
        allowed = ps.revise("also update mobile nav")
        assert ps.phase == "planning"
        assert ps.feedback == "also update mobile nav"
        assert "read_file" in allowed

    def test_reset_clears_all(self):
        ps = PlanState()
        ps.start_planning("test")
        ps.submit_plan("plan")
        ps.reset()
        assert ps.phase == "idle"
        assert ps.plan_text == ""
        assert ps.original_query == ""


class TestReadonlyTools:
    def test_read_tools_included(self):
        for tool in ["read_file", "glob", "grep", "list_tasks", "get_task",
                      "load_skill", "check_inbox", "list_crons", "compact", "todo_write"]:
            assert tool in READONLY_TOOLS, f"{tool} should be in READONLY_TOOLS"

    def test_write_tools_excluded(self):
        for tool in ["write_file", "edit_file", "bash", "spawn_teammate",
                      "create_worktree", "remove_worktree", "connect_mcp"]:
            assert tool not in READONLY_TOOLS, f"{tool} should not be in READONLY_TOOLS"


class TestPlanContext:
    def test_empty_when_idle(self):
        ps = PlanState()
        assert ps.plan_context == ""

    def test_empty_when_planning(self):
        ps = PlanState()
        ps.start_planning("test")
        assert ps.plan_context == ""

    def test_populated_when_executing(self):
        ps = PlanState()
        ps.start_planning("test")
        ps.submit_plan("Step 1: modify navbar\nStep 2: add route")
        ps.approve()
        assert "modify navbar" in ps.plan_context
        assert "Execute the plan above" in ps.plan_context


class TestSingleton:
    def test_get_plan_state_returns_same_instance(self):
        a = get_plan_state()
        b = get_plan_state()
        assert a is b
