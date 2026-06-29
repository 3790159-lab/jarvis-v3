# -*- coding: utf-8 -*-
"""Vizir mid-flight T-C (Feature 3) — timeout + cancellation.

A hung agent (just waits, never calls report_cost) is invisible to the cost cap
(no chunks) — timeout is the out-of-band stop for it. Step.timeout_s wraps the
handler in asyncio.wait_for; on TimeoutError the agent is cancelled, the step is
FAILED, step_spent (what it spent before hanging) is charged, and the coordinator
CONTINUES (asymmetry with the cost-cap STOP: a hang is a per-step failure, not a
money breach). TDD on mocks ($0)."""
import asyncio

from app.services.vizir.models import Task, Step, Plan, StepStatus
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.coordinator import Coordinator


def _run(coro):
    return asyncio.run(coro)


def _charge_sink(sink):
    async def charge(actor, op, amount):
        sink.append((actor, op, amount))
    return charge


def _reg(sleep_s, pre_chunk=0.0, with_after=False):
    reg = HandlerRegistry()

    async def slow(step, ctx):
        if pre_chunk:
            ctx["report_cost"](pre_chunk)        # spends before hanging
        await asyncio.sleep(sleep_s)             # the hang
        return HandlerResult(ok=True, result="done", cost_usd=pre_chunk or 0.0)

    reg.register("slow", slow)
    if with_after:
        async def after(step, ctx):
            return HandlerResult(ok=True, result="q")
        reg.register("after", after)
    return reg


def test_within_timeout_runs_normally():
    reg = _reg(sleep_s=0.0, pre_chunk=0.02)
    coord = Coordinator(reg)
    plan = Plan(steps=[Step(kind="slow", estimated_usd=0.05, timeout_s=1.0)])
    report = _run(coord.run(Task("t", "g", budget_usd=1.0), plan))

    assert report.steps[0].status is StepStatus.DONE
    assert abs(report.total_cost_usd - 0.02) < 1e-9


def test_hang_beyond_timeout_fails_charges_partial_and_continues():
    reg = _reg(sleep_s=0.3, pre_chunk=0.01, with_after=True)
    charges = []
    coord = Coordinator(reg, charge_logger=_charge_sink(charges))
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    plan = Plan(steps=[
        Step(kind="slow", estimated_usd=0.05, timeout_s=0.05),
        Step(kind="after"),
    ])
    report = _run(coord.run(task, plan))

    assert report.steps[0].status is StepStatus.FAILED
    assert "timeout" in (report.steps[0].error or "")
    assert abs(report.steps[0].cost_usd - 0.01) < 1e-9    # charged step_spent (partial)
    assert charges == [("admin", "slow", 0.01)]
    assert report.steps[1].status is StepStatus.DONE       # CONTINUED to next step
    assert report.status == "completed"                    # not stopped


def test_backward_compat_no_timeout_does_not_apply_wait_for():
    reg = _reg(sleep_s=0.05)                  # sleeps, but step has no timeout_s
    coord = Coordinator(reg)
    plan = Plan(steps=[Step(kind="slow")])    # timeout_s defaults to 0
    report = _run(coord.run(Task("t", "g"), plan))

    assert report.steps[0].status is StepStatus.DONE       # completes, never timed out


def test_spy_disabled_timeout_lets_hang_complete_proving_teeth():
    reg = _reg(sleep_s=0.3, pre_chunk=0.01)
    coord = Coordinator(reg)

    # SABOTAGE: ignore the timeout (no wait_for).
    async def no_timeout(handler, step, ctx):
        return await handler(step, ctx)

    coord._run_handler = no_timeout
    plan = Plan(steps=[Step(kind="slow", estimated_usd=0.05, timeout_s=0.05)])
    report = _run(coord.run(Task("t", "g", budget_usd=1.0), plan))

    # with timeout disabled, the hung agent COMPLETES instead of being cut off
    assert report.steps[0].status is StepStatus.DONE
    # therefore the real timeout test (FAILED by timeout) would FAIL -> it has teeth
    timeout_assertion_holds = (report.steps[0].status is StepStatus.FAILED)
    assert not timeout_assertion_holds
