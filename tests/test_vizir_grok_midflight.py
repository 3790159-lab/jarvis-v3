# -*- coding: utf-8 -*-
"""Vizir mid-flight T-E — grok_motion uses the new ctx (report_progress/report_cost)
under a coordinator, demonstrating the contract on a real handler, while a direct
call (no ctx callbacks) stays unchanged. TDD ($0)."""
import asyncio
from types import SimpleNamespace

from app.services.vizir.models import Task, Step, Plan, StepStatus
from app.services.vizir.handlers import HandlerRegistry
from app.services.vizir.handlers_grok import make_grok_motion_handler
from app.services.vizir.coordinator import Coordinator


def _run(coro):
    return asyncio.run(coro)


def test_grok_uses_midflight_ctx_under_coordinator():
    def fake_analyze(paths, question):
        return SimpleNamespace(
            text="slow head turn, soft blinking, locked static camera, photorealistic",
            cost_usd=0.02,
        )

    reg = HandlerRegistry()
    reg.register("grok_motion", make_grok_motion_handler(analyze_fn=fake_analyze))
    events = []
    coord = Coordinator(reg, on_event=events.append)
    step = Step(kind="grok_motion", params={"image_path": "x.jpg"}, estimated_usd=0.05)
    report = _run(coord.run(Task("t", "g", budget_usd=1.0, actor="admin"), Plan(steps=[step])))

    assert report.steps[0].status is StepStatus.DONE
    types = [e["type"] for e in events]
    assert "progress" in types                       # report_progress note
    assert "cost_progress" in types                  # report_cost recorded the spend
    assert abs(report.total_cost_usd - 0.02) < 1e-9  # charged via charge-after


def test_grok_direct_call_without_ctx_callbacks_unchanged():
    def fake_analyze(paths, question):
        return SimpleNamespace(text="gentle sway, locked static camera", cost_usd=0.01)

    handler = make_grok_motion_handler(analyze_fn=fake_analyze)
    res = _run(handler(Step(kind="grok_motion", params={"image_path": "x.jpg"}),
                       {"task": None, "results": {}}))      # old ctx, no callbacks

    assert res.ok is True
    assert "locked static camera" in res.result
    assert abs(res.cost_usd - 0.01) < 1e-9
