# -*- coding: utf-8 -*-
"""Vizir Phase 1 — a paid step that fails/refuses is NOT charged.
Mirrors the proven production rule 'refusal не списан'. TDD."""
import asyncio

from app.services.vizir.models import Task, Step, Plan, StepStatus
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.coordinator import Coordinator


def _run(coro):
    return asyncio.run(coro)


def test_failed_paid_step_is_not_charged():
    reg = HandlerRegistry()

    async def refuses(step, ctx):
        # estimated_usd was 0.01 but the call refused -> ok=False, no charge
        return HandlerResult(ok=False, error="refusal", cost_usd=0.0)

    reg.register("refuses", refuses)
    events = []
    coord = Coordinator(reg, on_event=events.append)

    task = Task(task_id="r", goal="g", budget_usd=0.05)
    plan = Plan(steps=[Step(kind="refuses", estimated_usd=0.01)])
    report = _run(coord.run(task, plan))

    assert report.steps[0].status is StepStatus.FAILED
    assert report.steps[0].error == "refusal"
    assert report.total_cost_usd == 0.0           # NOT charged
    assert "charged" not in [e["type"] for e in events]
