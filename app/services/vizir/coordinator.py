# -*- coding: utf-8 -*-
"""Vizir Coordinator — the core loop: decompose is the caller's job; here we
execute a Plan's Steps under the money-gate and emit structured events.

Transport-agnostic: the only outward surface is ``on_event`` (a callback the
driver renders) and the returned ``Report``. Knows nothing about CC/Telegram.
"""
from __future__ import annotations

import logging
from typing import Callable

from .handlers import HandlerRegistry, HandlerResult
from .models import Plan, Report, Step, StepStatus, Task

logger = logging.getLogger(__name__)


class Coordinator:
    def __init__(
        self,
        registry: HandlerRegistry,
        on_event: Callable[[dict], None] | None = None,
    ) -> None:
        self._registry = registry
        self._on_event = on_event or (lambda e: None)

    def _emit(self, type_: str, **fields) -> None:
        self._on_event({"type": type_, **fields})

    async def run(self, task: Task, plan: Plan) -> Report:
        total_cost = 0.0          # also the running "spent" for the budget gate
        stopped = False
        status = "completed"
        blocked: list[Step] = []

        for step in plan.steps:
            if stopped:
                step.status = StepStatus.SKIPPED
                continue

            paid = step.estimated_usd > 0
            self._emit("step_started", kind=step.kind)

            if paid:
                # GATE *BEFORE* spend: strict per-task budget pre-check (universal,
                # applies even to admin — it is the task's own cap, not a user limit).
                self._emit("gate_checked", kind=step.kind,
                           spent=total_cost, estimated=step.estimated_usd,
                           budget=task.budget_usd)
                if total_cost + step.estimated_usd > task.budget_usd:
                    step.status = StepStatus.BLOCKED
                    blocked.append(step)
                    stopped = True
                    status = "stopped_budget"
                    self._emit("step_blocked", kind=step.kind,
                               reason="per-task budget would be exceeded")
                    self._emit("run_stopped", status=status)
                    continue

            handler = self._registry.get(step.kind)
            result: HandlerResult = await handler(step, {"task": task})

            step.status = StepStatus.DONE
            step.result = result.result
            step.cost_usd = result.cost_usd
            total_cost += result.cost_usd
            if paid:
                # CHARGE *AFTER* success.
                self._emit("charged", kind=step.kind, cost_usd=result.cost_usd)
            self._emit("step_done", kind=step.kind, cost_usd=step.cost_usd)

        report = Report(
            task_id=task.task_id, goal=task.goal, actor=task.actor,
            steps=plan.steps, total_cost_usd=total_cost, status=status,
            blocked=blocked,
        )
        if not stopped:
            self._emit("run_completed", total_cost_usd=total_cost)
        return report
