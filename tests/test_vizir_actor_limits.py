# -*- coding: utf-8 -*-
"""Vizir Phase 1 — actor-parametrized per-user limit, additional to the
universal per-task budget. admin = unlimited per-user; friend = capped.
Designed so Phase 3 can wire it to the proven access_control.check_limit. TDD."""
import asyncio

from app.services.vizir.models import Task, Step, Plan, StepStatus
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.coordinator import Coordinator


def _run(coro):
    return asyncio.run(coro)


def _paid_reg(executed):
    reg = HandlerRegistry()

    async def paid(step, ctx):
        executed.append(step.kind)
        return HandlerResult(result="ok", cost_usd=0.01)

    reg.register("paid", paid)
    return reg


def test_friend_actor_blocked_by_per_user_limit_below_task_budget():
    executed = []
    coord = Coordinator(_paid_reg(executed), actor_limits={"friend": 0.02})

    task = Task(task_id="f", goal="g", budget_usd=0.10, actor="friend")
    plan = Plan(steps=[Step(kind="paid", estimated_usd=0.01) for _ in range(6)])
    report = _run(coord.run(task, plan))

    assert len(executed) == 2                       # friend cap $0.02 hit at 3rd
    assert sum(1 for s in report.steps if s.status is StepStatus.DONE) == 2
    assert sum(1 for s in report.steps if s.status is StepStatus.BLOCKED) == 1
    assert report.status == "stopped_budget"


def test_admin_actor_unlimited_per_user_only_task_budget_applies():
    executed = []
    coord = Coordinator(_paid_reg(executed), actor_limits={"friend": 0.02})

    task = Task(task_id="a", goal="g", budget_usd=0.10, actor="admin")
    plan = Plan(steps=[Step(kind="paid", estimated_usd=0.01) for _ in range(6)])
    report = _run(coord.run(task, plan))

    assert len(executed) == 6                        # admin not capped per-user
    assert report.status == "completed"
    assert abs(report.total_cost_usd - 0.06) < 1e-9
