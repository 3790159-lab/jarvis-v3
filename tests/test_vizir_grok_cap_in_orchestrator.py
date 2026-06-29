# -*- coding: utf-8 -*-
"""Vizir Phase 3 — the full run-#2 money-gate test mirrored INSIDE Vizir, on the
REAL grok_motion handler (Grok mocked, $0): micro-cap $0.05, 6 paid steps -> 5
run, 6th blocked before spend, isolated ledger holds 5 entries, prod untouched,
plus spy. All mock, $0 real. TDD."""
import asyncio
from types import SimpleNamespace

from app.services.vizir.models import Task, Step, Plan, StepStatus
from app.services.vizir.handlers import HandlerRegistry
from app.services.vizir.handlers_grok import make_grok_motion_handler
from app.services.vizir.coordinator import Coordinator
from app.services.block_m_common.cost_tracker import CostTracker


def _run(coro):
    return asyncio.run(coro)


def _grok_plan_and_reg(call_counter):
    def fake_motion(frames):
        call_counter.append(1)                      # one real (mock) Grok call
        return SimpleNamespace(prompt="slow head turn, locked static camera", cost_usd=0.01)

    reg = HandlerRegistry()
    reg.register("grok_motion", make_grok_motion_handler(motion_fn=fake_motion))
    plan = Plan(steps=[
        Step(kind="grok_motion", params={"image_path": f"{i}.jpg"}, estimated_usd=0.01)
        for i in range(6)
    ])
    return reg, plan


def test_micro_cap_stops_sixth_grok_step_with_isolated_ledger(tmp_path):
    calls = []
    reg, plan = _grok_plan_and_reg(calls)
    ledger = tmp_path / "iso.jsonl"
    tracker = CostTracker(expenses_file=ledger, daily_limit=10.0)

    async def charge(actor, op, amount):
        await tracker.log_expense(op, amount, actor)

    coord = Coordinator(reg, charge_logger=charge)
    task = Task("grok-cap", "g", budget_usd=0.05, actor="admin")
    report = _run(coord.run(task, plan))

    assert len(calls) == 5                            # 6th never called Grok
    assert sum(1 for s in report.steps if s.status is StepStatus.DONE) == 5
    assert sum(1 for s in report.steps if s.status is StepStatus.BLOCKED) == 1
    assert abs(report.total_cost_usd - 0.05) < 1e-9
    assert report.status == "stopped_budget"
    # isolated ledger: exactly 5 entries
    assert ledger.read_text(encoding="utf-8").count("grok_motion") == 5


def test_spy_broken_gate_runs_sixth_grok_step(tmp_path):
    calls = []
    reg, plan = _grok_plan_and_reg(calls)
    coord = Coordinator(reg)
    coord._money_gate_allows = lambda task, spent, step: (True, "")   # sabotage

    task = Task("grok-spy", "g", budget_usd=0.05, actor="admin")
    report = _run(coord.run(task, plan))

    done = sum(1 for s in report.steps if s.status is StepStatus.DONE)
    assert len(calls) == 6 and done == 6             # broken gate -> all 6 ran
    assert not (done == 5)                            # cap assertion would catch it
