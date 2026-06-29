# -*- coding: utf-8 -*-
"""Vizir Phase 4 — stub JarvisDriver: same core, Telegram-style rendering +
approval buttons. STUB only (proves the seam); no live bot. TDD ($0)."""
import asyncio

from app.services.vizir.models import Task, Step, Plan, Policy, StepStatus
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.drivers.jarvis_driver import JarvisDriver


def _run(coro):
    return asyncio.run(coro)


def test_jarvis_driver_runs_core_and_renders_telegram_style():
    reg = HandlerRegistry()

    async def echo(step, ctx):
        return HandlerResult(result="ok")

    reg.register("a", echo)
    d = JarvisDriver(reg)
    report = _run(d.run(Task("t", "g"), Plan(steps=[Step(kind="a")])))

    assert report.status == "completed"
    text = "\n".join(d.messages)
    assert "a" in text
    assert "*OK*" in text or "finished" in text          # Telegram-flavored tokens


def test_jarvis_driver_renders_approval_buttons():
    reg = HandlerRegistry()

    async def sensitive(step, ctx):
        return HandlerResult(result="ran")

    reg.register("sensitive", sensitive)
    d = JarvisDriver(reg)
    report = _run(d.run(
        Task("ap", "g"),
        Plan(steps=[Step(kind="sensitive", policy=Policy.REQUIRES_APPROVAL)]),
    ))

    assert report.steps[0].status is StepStatus.NEEDS_APPROVAL
    text = "\n".join(d.messages)
    assert "approval needed" in text
    assert "[Approve]" in text and "[Reject]" in text     # button stubs for the bot
