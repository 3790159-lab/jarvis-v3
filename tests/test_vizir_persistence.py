# -*- coding: utf-8 -*-
"""Vizir Phase 1 — state persisted per-step so an interrupted run can be
reported (mirror batch_orchestrator, never auto-resume). TDD."""
import asyncio
import json

from app.services.vizir.models import Task, Step, Plan
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.coordinator import Coordinator


def _run(coro):
    return asyncio.run(coro)


def _reg(executed):
    reg = HandlerRegistry()

    async def paid(step, ctx):
        executed.append(step.kind)
        return HandlerResult(result="ok", cost_usd=0.01)

    reg.register("paid", paid)
    return reg


def test_completed_run_persists_final_state(tmp_path):
    coord = Coordinator(_reg([]), state_dir=tmp_path)
    task = Task(task_id="done1", goal="g", budget_usd=1.0)
    plan = Plan(steps=[Step(kind="paid", estimated_usd=0.01) for _ in range(2)])
    _run(coord.run(task, plan))

    state_file = tmp_path / "done1" / "state.json"
    assert state_file.exists()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["task_id"] == "done1"
    assert data["status"] == "completed"
    assert [s["status"] for s in data["steps"]] == ["done", "done"]
    assert abs(data["total_cost_usd"] - 0.02) < 1e-9


def test_budget_stopped_run_persists_interrupted_state(tmp_path):
    coord = Coordinator(_reg([]), state_dir=tmp_path)
    task = Task(task_id="cap1", goal="g", budget_usd=0.02)
    plan = Plan(steps=[Step(kind="paid", estimated_usd=0.01) for _ in range(4)])
    _run(coord.run(task, plan))

    data = json.loads((tmp_path / "cap1" / "state.json").read_text(encoding="utf-8"))
    assert data["status"] == "stopped_budget"
    statuses = [s["status"] for s in data["steps"]]
    assert statuses == ["done", "done", "blocked", "skipped"]
