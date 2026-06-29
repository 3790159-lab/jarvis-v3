# -*- coding: utf-8 -*-
"""Vizir mid-flight T-A (Feature 2) — report_cost / report_progress in ctx.

FOUNDATION only: the coordinator accumulates per-step `step_spent` from
report_cost calls and emits cost_progress/progress events (real-time visibility).
NO enforcement yet (that is T-B). Charging stays unchanged (charge result.cost_usd
on ok=True) — backward-compatible with every existing handler. TDD ($0)."""
import asyncio

from app.services.vizir.models import Task, Step, Plan, StepStatus
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.coordinator import Coordinator


def _run(coro):
    return asyncio.run(coro)


def test_backward_compat_handler_without_report_cost_unchanged():
    """An old handler that never touches report_cost behaves EXACTLY as before:
    charged result.cost_usd on success, no cost_progress events."""
    charges = []

    async def charge(actor, op, amount):
        charges.append((actor, op, amount))

    reg = HandlerRegistry()

    async def old_paid(step, ctx):                      # ignores the new ctx keys
        return HandlerResult(ok=True, result="p", cost_usd=0.01)

    reg.register("old_paid", old_paid)
    events = []
    coord = Coordinator(reg, on_event=events.append, charge_logger=charge)
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    report = _run(coord.run(task, Plan(steps=[Step(kind="old_paid", estimated_usd=0.01)])))

    assert report.steps[0].status is StepStatus.DONE
    assert abs(report.total_cost_usd - 0.01) < 1e-9
    assert charges == [("admin", "old_paid", 0.01)]      # charged result.cost_usd
    assert "cost_progress" not in [e["type"] for e in events]   # no progress noise


def test_report_cost_accumulates_step_spent_and_emits_cost_progress():
    reg = HandlerRegistry()

    async def streaming(step, ctx):
        ctx["report_cost"](0.01)                        # reserve-before-spend chunks
        ctx["report_cost"](0.02)
        return HandlerResult(ok=True, result="done", cost_usd=0.03)

    reg.register("streaming", streaming)
    events = []
    coord = Coordinator(reg, on_event=events.append)
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    _run(coord.run(task, Plan(steps=[Step(kind="streaming", estimated_usd=0.05)])))

    progress = [e for e in events if e["type"] == "cost_progress"]
    assert len(progress) == 2
    assert abs(progress[0]["spent"] - 0.01) < 1e-9       # running step_spent
    assert abs(progress[1]["spent"] - 0.03) < 1e-9
    assert abs(progress[1]["delta"] - 0.02) < 1e-9


def test_report_progress_emits_progress_event():
    reg = HandlerRegistry()

    async def chatty(step, ctx):
        ctx["report_progress"]("halfway through")
        return HandlerResult(ok=True, result="ok")

    reg.register("chatty", chatty)
    events = []
    coord = Coordinator(reg, on_event=events.append)
    _run(coord.run(Task("t", "g"), Plan(steps=[Step(kind="chatty")])))

    notes = [e for e in events if e["type"] == "progress"]
    assert notes and notes[0]["note"] == "halfway through"
    assert notes[0]["kind"] == "chatty"
