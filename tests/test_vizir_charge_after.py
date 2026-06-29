# -*- coding: utf-8 -*-
"""Vizir Phase 3 — charge-after wiring: the Coordinator records cost ONLY after a
successful paid step (refusal/blocked never logged), via an injected charge_logger
(wired to an isolated CostTracker in prod). TDD on mocks/temp ledger ($0)."""
import asyncio

from app.services.vizir.models import Task, Step, Plan
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.coordinator import Coordinator


def _run(coro):
    return asyncio.run(coro)


def test_charge_logger_called_only_for_successful_paid_steps():
    charges = []

    async def logger(actor, operation, amount):
        charges.append((actor, operation, amount))

    reg = HandlerRegistry()

    async def ok_paid(step, ctx):
        return HandlerResult(ok=True, result="p", cost_usd=0.01)

    async def refuse(step, ctx):
        return HandlerResult(ok=False, error="r", cost_usd=0.0)

    reg.register("ok_paid", ok_paid)
    reg.register("refuse", refuse)
    coord = Coordinator(reg, charge_logger=logger)

    task = Task("t", "g", budget_usd=1.0, actor="friend")
    plan = Plan(steps=[
        Step(kind="ok_paid", estimated_usd=0.01),
        Step(kind="refuse", estimated_usd=0.01),
    ])
    report = _run(coord.run(task, plan))

    assert charges == [("friend", "ok_paid", 0.01)]    # refusal NOT logged
    assert abs(report.total_cost_usd - 0.01) < 1e-9


def test_charge_wired_to_isolated_cost_tracker_not_prod(tmp_path):
    from app.services.block_m_common.cost_tracker import CostTracker

    ledger = tmp_path / "iso.jsonl"                    # ISOLATED, not prod
    tracker = CostTracker(expenses_file=ledger, daily_limit=1.0)

    async def logger(actor, operation, amount):
        await tracker.log_expense(operation, amount, actor)

    reg = HandlerRegistry()

    async def paid(step, ctx):
        return HandlerResult(ok=True, result="p", cost_usd=0.01)

    reg.register("grok_motion", paid)
    coord = Coordinator(reg, charge_logger=logger)
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    plan = Plan(steps=[Step(kind="grok_motion", estimated_usd=0.01)])
    _run(coord.run(task, plan))

    assert ledger.exists()
    assert "grok_motion" in ledger.read_text(encoding="utf-8")
