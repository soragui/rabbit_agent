"""handle_input routing — no terminal, no network."""
from unittest.mock import MagicMock

import pytest

import agent


@pytest.fixture(autouse=True)
def _mocks(monkeypatch):
    monkeypatch.setattr(agent, "agent_loop_full", MagicMock())
    monkeypatch.setattr(agent, "save_session", MagicMock())
    agent.get_plan_state().reset()
    agent._agent_idle = True
    yield


def test_q_returns_false_and_saves():
    assert agent.handle_input("q", []) is False
    agent.save_session.assert_called_once()


def test_help_returns_true_without_turn():
    assert agent.handle_input("?", []) is True
    agent.agent_loop_full.assert_not_called()


def test_plain_query_appends_and_runs_once():
    history = []
    assert agent.handle_input("hello world", history) is True
    assert history[-1] == {"role": "user", "content": "hello world"}
    agent.agent_loop_full.assert_called_once()


def test_plan_approval_routes_decision():
    plan = agent.get_plan_state()
    plan.start_planning("write tests")
    plan.submit_plan("the plan")
    assert plan.phase == "awaiting_approval"
    assert agent.handle_input("y", []) is True
    assert plan.phase == "idle"  # approved -> executed (mocked) -> reset


def test_plan_rejection_routes_decision():
    plan = agent.get_plan_state()
    plan.start_planning("write tests")
    plan.submit_plan("the plan")
    assert agent.handle_input("n", []) is True
    assert plan.phase == "idle"
    agent.agent_loop_full.assert_not_called()
