# -*- coding: utf-8 -*-
"""Vizir Phase 1 — core models (Task/Step/Plan/Report). TDD."""
from app.services.vizir.models import (
    Task, Step, Plan, Report, Policy, StepStatus,
)


def test_step_defaults_to_auto_policy_free_and_pending():
    step = Step(kind="noop")
    assert step.policy is Policy.AUTO          # default: Jarvis/CC does it alone
    assert step.estimated_usd == 0.0           # default: free
    assert step.status is StepStatus.PENDING
    assert step.cost_usd == 0.0


def test_step_can_be_marked_requires_approval_and_paid():
    step = Step(kind="grok_motion", estimated_usd=0.01, policy=Policy.REQUIRES_APPROVAL)
    assert step.policy is Policy.REQUIRES_APPROVAL
    assert step.estimated_usd == 0.01


def test_task_carries_budget_and_actor_product_safeguards():
    task = Task(task_id="t1", goal="do thing", budget_usd=0.05, actor="friend")
    assert task.budget_usd == 0.05             # product-level per-task cap
    assert task.actor == "friend"              # actor parametrization


def test_task_defaults_actor_admin_and_zero_budget():
    task = Task(task_id="t2", goal="g")
    assert task.actor == "admin"
    assert task.budget_usd == 0.0


def test_plan_holds_ordered_steps():
    plan = Plan(steps=[Step(kind="a"), Step(kind="b")])
    assert [s.kind for s in plan.steps] == ["a", "b"]


def test_report_summarizes_task_outcome():
    report = Report(
        task_id="t1", goal="g", actor="admin",
        steps=[Step(kind="a", status=StepStatus.DONE)],
        total_cost_usd=0.0, status="completed",
    )
    assert report.status == "completed"
    assert report.total_cost_usd == 0.0
    assert report.steps[0].status is StepStatus.DONE
