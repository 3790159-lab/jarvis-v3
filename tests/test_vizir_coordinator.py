# -*- coding: utf-8 -*-
"""Vizir Phase 1 — Coordinator loop. TDD."""
import asyncio

from app.services.vizir.models import Task, Step, Plan, StepStatus
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.coordinator import Coordinator


def _run(coro):
    return asyncio.run(coro)


def test_runs_free_plan_all_steps_done_zero_cost_with_events():
    reg = HandlerRegistry()
    seen = []

    async def echo(step, ctx):
        seen.append(step.kind)
        return HandlerResult(result=f"did:{step.kind}")

    reg.register("a", echo)
    reg.register("b", echo)
    events = []
    coord = Coordinator(reg, on_event=events.append)

    task = Task(task_id="t1", goal="g")
    plan = Plan(steps=[Step(kind="a"), Step(kind="b")])
    report = _run(coord.run(task, plan))

    assert seen == ["a", "b"]                              # executed in order
    assert all(s.status is StepStatus.DONE for s in report.steps)
    assert report.steps[0].result == "did:a"
    assert report.total_cost_usd == 0.0
    assert report.status == "completed"

    types = [e["type"] for e in events]
    assert types.count("step_started") == 2
    assert types.count("step_done") == 2
    assert "run_completed" in types
