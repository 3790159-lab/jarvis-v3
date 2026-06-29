# -*- coding: utf-8 -*-
"""Vizir Phase 1 — SPY / negative control (mock, $0 real).

Proves the in-orchestrator budget-cap test has TEETH: with the gate sabotaged
(simulating a bug), the 6th paid step runs, so the protective assertion used by
the real cap test (exactly 5 done / 1 blocked) FAILS — i.e. it would catch a
broken gate. Mirrors the live money-gate spy from run #2. No real money."""
import asyncio

from app.services.vizir.models import Task, Step, Plan, StepStatus
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.coordinator import Coordinator


def _run(coro):
    return asyncio.run(coro)


def test_spy_broken_gate_lets_sixth_run_so_cap_assertion_has_teeth():
    executed = []
    reg = HandlerRegistry()

    async def paid(step, ctx):
        executed.append(step.kind)
        return HandlerResult(result="ok", cost_usd=0.01)

    reg.register("paid", paid)
    coord = Coordinator(reg)

    # SABOTAGE the money gate (simulate a regression): always allow.
    coord._money_gate_allows = lambda task, spent, step: (True, "")

    task = Task(task_id="spy", goal="g", budget_usd=0.05)
    plan = Plan(steps=[Step(kind="paid", estimated_usd=0.01) for _ in range(6)])
    report = _run(coord.run(task, plan))

    done = sum(1 for s in report.steps if s.status is StepStatus.DONE)
    blocked = sum(1 for s in report.steps if s.status is StepStatus.BLOCKED)

    # Broken-gate signature: all 6 ran, nothing blocked.
    assert len(executed) == 6
    assert done == 6 and blocked == 0

    # The real cap test asserts (5 done, 1 blocked); a broken gate violates it,
    # so that assertion genuinely distinguishes a working gate from a broken one.
    cap_assertion_holds = (done == 5 and blocked == 1)
    assert not cap_assertion_holds
