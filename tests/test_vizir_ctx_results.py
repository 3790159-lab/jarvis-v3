# -*- coding: utf-8 -*-
"""Vizir Phase 2 — a step can read prior steps' results via ctx, enabling real
multi-step coordination (not independent steps). TDD."""
import asyncio

from app.services.vizir.models import Task, Step, Plan
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.coordinator import Coordinator


def _run(coro):
    return asyncio.run(coro)


def test_step_reads_prior_step_result_via_ctx_results():
    reg = HandlerRegistry()

    async def produce(step, ctx):
        return HandlerResult(result=21)

    async def consume(step, ctx):
        prior = ctx["results"]["produce"]      # coordination: build on earlier output
        return HandlerResult(result=prior * 2)

    reg.register("produce", produce)
    reg.register("consume", consume)
    coord = Coordinator(reg)

    task = Task(task_id="t", goal="g")
    plan = Plan(steps=[Step(kind="produce"), Step(kind="consume")])
    report = _run(coord.run(task, plan))

    assert report.steps[1].result == 42
