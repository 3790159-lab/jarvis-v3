# -*- coding: utf-8 -*-
"""Vizir mid-flight T-A — drivers render cost_progress/progress (real-time spend
visibility). Thin: rendering only. TDD ($0)."""
import asyncio

from app.services.vizir.models import Task, Step, Plan
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.drivers.cc_driver import CCDriver
from app.services.vizir.drivers.jarvis_driver import JarvisDriver


def _run(coro):
    return asyncio.run(coro)


def _streaming_reg():
    reg = HandlerRegistry()

    async def streaming(step, ctx):
        ctx["report_progress"]("starting")
        ctx["report_cost"](0.02)
        return HandlerResult(ok=True, result="ok", cost_usd=0.02)

    reg.register("streaming", streaming)
    return reg


def test_cc_driver_renders_cost_progress_and_progress():
    d = CCDriver(_streaming_reg())
    _run(d.run(Task("t", "g", budget_usd=1.0), Plan(steps=[Step(kind="streaming", estimated_usd=0.05)])))
    text = "\n".join(d.journal)
    assert "starting" in text                      # progress note rendered
    assert "0.0200" in text                        # running spend rendered
    assert "event: cost_progress" not in text      # not the raw fallback


def test_jarvis_driver_renders_cost_progress_and_progress():
    d = JarvisDriver(_streaming_reg())
    _run(d.run(Task("t", "g", budget_usd=1.0), Plan(steps=[Step(kind="streaming", estimated_usd=0.05)])))
    text = "\n".join(d.messages)
    assert "starting" in text
    assert "0.0200" in text
    assert "_event_ cost_progress" not in text
