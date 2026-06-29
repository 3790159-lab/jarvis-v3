# -*- coding: utf-8 -*-
"""Vizir mid-flight T-D (Feature 4) — quote phase for variable-cost agents.

A CHEAP quote (local heuristic, not a paid call) runs BEFORE the pre-check and
sets step.estimated_usd, so the existing gate pre-check is meaningful for an
agent whose price isn't known upfront. Three layers: quote (estimate before) ->
cost cap (mid-flight ceiling, T-B) -> charge (actual). Under-quote is backstopped
by the cap; over-quote blocks conservatively at the pre-check. TDD ($0)."""
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


def test_quote_sets_estimated_usd_and_pre_check_uses_it():
    reg = HandlerRegistry()

    async def handler(step, ctx):
        return HandlerResult(ok=True, result="ok", cost_usd=0.02)

    async def quote(step, ctx):          # cheap local estimate, no paid call
        return 0.02

    reg.register("agent", handler, quote=quote)
    events = []
    coord = Coordinator(reg, on_event=events.append)
    step = Step(kind="agent", needs_quote=True)        # estimated_usd starts 0
    report = _run(coord.run(Task("t", "g", budget_usd=1.0), Plan(steps=[step])))

    assert abs(step.estimated_usd - 0.02) < 1e-9        # quote filled it in
    gate = [e for e in events if e["type"] == "gate_checked"]
    assert gate and abs(gate[0]["estimated"] - 0.02) < 1e-9
    assert report.steps[0].status is StepStatus.DONE
    assert any(e["type"] == "quoted" for e in events)


def test_underquote_is_caught_by_cap_midflight_three_layers():
    reg = HandlerRegistry()

    async def streamer(step, ctx):
        for c in [0.01] * 6:             # really wants $0.06
            ctx["report_cost"](c)
        return HandlerResult(ok=True, cost_usd=0.06)

    async def quote_low(step, ctx):
        return 0.01                      # WAY under the real $0.06

    reg.register("streamer", streamer, quote=quote_low)
    charges = []
    coord = Coordinator(reg, charge_logger=_charge_sink(charges))
    step = Step(kind="streamer", needs_quote=True, max_usd=0.03)
    report = _run(coord.run(Task("t", "g", budget_usd=1.0, actor="admin"), Plan(steps=[step])))

    assert abs(step.estimated_usd - 0.01) < 1e-9        # quote passed the pre-check
    assert report.steps[0].status is StepStatus.BLOCKED  # ...but cap caught it
    assert abs(report.total_cost_usd - 0.03) < 1e-9      # charged up to the cap
    assert report.status == "stopped_cost_cap"
    assert charges == [("admin", "streamer", 0.03)]


def test_overquote_blocks_at_precheck_predictably():
    reg = HandlerRegistry()
    ran = []

    async def handler(step, ctx):
        ran.append(1)
        return HandlerResult(ok=True, cost_usd=0.0)

    async def quote_high(step, ctx):
        return 0.10                      # exceeds the $0.05 budget

    reg.register("agent", handler, quote=quote_high)
    coord = Coordinator(reg)
    step = Step(kind="agent", needs_quote=True)
    report = _run(coord.run(Task("t", "g", budget_usd=0.05), Plan(steps=[step])))

    assert ran == []                                     # never ran
    assert report.steps[0].status is StepStatus.BLOCKED
    assert report.status == "stopped_budget"             # conservative pre-check block


def test_backward_compat_explicit_estimated_without_quote():
    reg = HandlerRegistry()

    async def handler(step, ctx):
        return HandlerResult(ok=True, cost_usd=0.01)

    reg.register("agent", handler)                       # no quote registered
    coord = Coordinator(reg)
    step = Step(kind="agent", estimated_usd=0.01)        # explicit, needs_quote=False
    report = _run(coord.run(Task("t", "g", budget_usd=1.0), Plan(steps=[step])))

    assert abs(step.estimated_usd - 0.01) < 1e-9         # unchanged (no quote)
    assert report.steps[0].status is StepStatus.DONE


def test_spy_ignored_quote_lets_unaffordable_step_run_proving_teeth():
    reg = HandlerRegistry()
    ran = []

    async def handler(step, ctx):
        ran.append(1)
        return HandlerResult(ok=True, cost_usd=0.0)

    async def quote_high(step, ctx):
        return 0.10                      # would block: $0.10 > $0.05 budget

    reg.register("agent", handler, quote=quote_high)
    coord = Coordinator(reg)

    async def noop(step, ctx):           # SABOTAGE: quote -> estimated wiring broken
        return

    coord._apply_quote = noop
    step = Step(kind="agent", needs_quote=True)
    report = _run(coord.run(Task("t", "g", budget_usd=0.05), Plan(steps=[step])))

    # quote ignored -> estimated stays 0 -> the unaffordable step RUNS
    assert ran == [1]
    assert report.steps[0].status is StepStatus.DONE
    # the real test (over-quote -> BLOCKED) would FAIL here -> it has teeth
    assert report.status != "stopped_budget"
