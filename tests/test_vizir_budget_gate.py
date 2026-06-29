# -*- coding: utf-8 -*-
"""Vizir Phase 1 — per-task budget cap enforced INSIDE the coordinator.

Mirrors the proven live money-gate test (run #2) but in-orchestrator and with
MOCK charges ($0 real): micro-budget $0.05, six $0.01 paid steps -> 5 run, 6th
is blocked BEFORE execution. check-before / charge-after. TDD.
"""
import asyncio

from app.services.vizir.models import Task, Step, Plan, StepStatus
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.coordinator import Coordinator


def _run(coro):
    return asyncio.run(coro)


def _paid_registry(executed_log):
    reg = HandlerRegistry()

    async def paid(step, ctx):
        executed_log.append(step.kind)            # only real executions land here
        return HandlerResult(result="ok", cost_usd=0.01)   # MOCK charge, $0 real

    reg.register("paid", paid)
    return reg


def test_budget_cap_stops_sixth_paid_step_before_spending():
    executed = []
    reg = _paid_registry(executed)
    events = []
    coord = Coordinator(reg, on_event=events.append)

    task = Task(task_id="cap", goal="g", budget_usd=0.05)   # micro-cap
    plan = Plan(steps=[Step(kind="paid", estimated_usd=0.01) for _ in range(6)])
    report = _run(coord.run(task, plan))

    # exactly 5 executed, 6th blocked BEFORE the handler ran
    assert len(executed) == 5
    done = [s for s in report.steps if s.status is StepStatus.DONE]
    blocked = [s for s in report.steps if s.status is StepStatus.BLOCKED]
    assert len(done) == 5
    assert len(blocked) == 1
    assert abs(report.total_cost_usd - 0.05) < 1e-9
    assert report.status == "stopped_budget"

    types = [e["type"] for e in events]
    assert "step_blocked" in types
    assert "run_stopped" in types


def test_each_paid_step_is_gate_checked_before_charge():
    executed = []
    reg = _paid_registry(executed)
    events = []
    coord = Coordinator(reg, on_event=events.append)

    task = Task(task_id="order", goal="g", budget_usd=0.05)
    plan = Plan(steps=[Step(kind="paid", estimated_usd=0.01) for _ in range(2)])
    _run(coord.run(task, plan))

    # for each executed step: gate_checked precedes charged
    seq = [e["type"] for e in events if e["type"] in ("gate_checked", "charged")]
    assert seq == ["gate_checked", "charged", "gate_checked", "charged"]
