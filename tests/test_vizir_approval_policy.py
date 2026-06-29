# -*- coding: utf-8 -*-
"""Vizir Phase 1 — requires_approval steps are NOT auto-run; they surface a
checkpoint. Product-level 'what Jarvis does alone vs needs Daniil'. TDD."""
import asyncio

from app.services.vizir.models import Task, Step, Plan, Policy, StepStatus
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.coordinator import Coordinator


def _run(coro):
    return asyncio.run(coro)


def test_requires_approval_step_is_not_executed_and_stops_run():
    executed = []
    reg = HandlerRegistry()

    async def sensitive(step, ctx):
        executed.append(step.kind)
        return HandlerResult(result="ran")

    async def after(step, ctx):
        executed.append(step.kind)
        return HandlerResult(result="ran")

    reg.register("sensitive", sensitive)
    reg.register("after", after)
    events = []
    coord = Coordinator(reg, on_event=events.append)

    task = Task(task_id="ap", goal="g")
    plan = Plan(steps=[
        Step(kind="sensitive", policy=Policy.REQUIRES_APPROVAL),
        Step(kind="after"),
    ])
    report = _run(coord.run(task, plan))

    assert executed == []                                   # neither ran
    assert report.steps[0].status is StepStatus.NEEDS_APPROVAL
    assert report.steps[1].status is StepStatus.SKIPPED     # blocked behind approval
    assert report.status == "stopped_for_approval"
    assert report.approvals_needed and report.approvals_needed[0].kind == "sensitive"

    types = [e["type"] for e in events]
    assert "step_needs_approval" in types
    assert "run_stopped" in types
