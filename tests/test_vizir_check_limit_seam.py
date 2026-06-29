# -*- coding: utf-8 -*-
"""Vizir Phase 3 — the money-gate seam consults an injected check_limit_fn
(in prod: access_control.check_limit, admin unlimited / friend capped). Proven
on mocks ($0) so the live run can wire the real function. TDD."""
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
        return HandlerResult(ok=True, result="p", cost_usd=0.01)

    reg.register("paid", paid)
    return reg


def test_gate_blocks_when_injected_check_limit_denies():
    executed = []
    calls = []

    def deny(actor, estimated_usd):
        calls.append((actor, estimated_usd))
        return (False, "daily limit reached")

    coord = Coordinator(_paid_reg(executed), check_limit_fn=deny)
    task = Task("t", "g", budget_usd=1.0, actor="friend")     # budget is generous
    plan = Plan(steps=[Step(kind="paid", estimated_usd=0.01)])
    report = _run(coord.run(task, plan))

    assert executed == []                                     # never ran
    assert report.steps[0].status is StepStatus.BLOCKED
    assert report.status == "stopped_budget"
    assert calls == [("friend", 0.01)]                        # seam consulted check_limit


def test_gate_allows_when_injected_check_limit_allows():
    executed = []

    def allow(actor, estimated_usd):
        return (True, "")                                     # admin -> unlimited

    coord = Coordinator(_paid_reg(executed), check_limit_fn=allow)
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    plan = Plan(steps=[Step(kind="paid", estimated_usd=0.01)])
    report = _run(coord.run(task, plan))

    assert executed == ["paid"]
    assert report.status == "completed"
