# -*- coding: utf-8 -*-
"""Vizir Coordinator — the core loop: decompose is the caller's job; here we
execute a Plan's Steps under the money-gate and emit structured events.

Transport-agnostic: the only outward surface is ``on_event`` (a callback the
driver renders) and the returned ``Report``. Knows nothing about CC/Telegram.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from .handlers import HandlerRegistry, HandlerResult
from .models import Plan, Policy, Report, Step, StepStatus, Task

logger = logging.getLogger(__name__)


class Coordinator:
    def __init__(
        self,
        registry: HandlerRegistry,
        on_event: Callable[[dict], None] | None = None,
        actor_limits: dict[str, float] | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self._registry = registry
        self._on_event = on_event or (lambda e: None)
        self._state_dir = Path(state_dir) if state_dir is not None else None
        # Per-user cap, additional to the universal per-task budget. An actor
        # not present here (e.g. "admin") is unlimited per-user. Phase 3 wires
        # this to the proven access_control.check_limit.
        self._actor_limits = actor_limits or {}

    def _emit(self, type_: str, **fields) -> None:
        self._on_event({"type": type_, **fields})

    def _persist(self, task: Task, plan: Plan, status: str, total_cost: float) -> None:
        """Durable snapshot (mirror batch_orchestrator). Written before each step
        and at the end, so a crash mid-step leaves a reportable record. We never
        auto-resume an in-flight step — too dangerous; this is for reporting."""
        if self._state_dir is None:
            return
        d = self._state_dir / task.task_id
        d.mkdir(parents=True, exist_ok=True)
        snap = {
            "task_id": task.task_id, "goal": task.goal, "actor": task.actor,
            "status": status, "total_cost_usd": total_cost,
            "steps": [
                {"kind": s.kind, "status": s.status.value,
                 "cost_usd": s.cost_usd, "error": s.error}
                for s in plan.steps
            ],
        }
        (d / "state.json").write_text(
            json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def run(self, task: Task, plan: Plan) -> Report:
        total_cost = 0.0          # also the running "spent" for the budget gate
        stopped = False
        status = "completed"
        blocked: list[Step] = []
        approvals: list[Step] = []

        for step in plan.steps:
            self._persist(task, plan, "running", total_cost)  # durable before each step
            if stopped:
                step.status = StepStatus.SKIPPED
                continue

            paid = step.estimated_usd > 0
            self._emit("step_started", kind=step.kind)

            # Policy gate BEFORE the money gate: a requires_approval step is never
            # auto-run — Jarvis/CC must surface it to Daniil (product boundary).
            if step.policy is Policy.REQUIRES_APPROVAL:
                step.status = StepStatus.NEEDS_APPROVAL
                approvals.append(step)
                stopped = True
                status = "stopped_for_approval"
                self._emit("step_needs_approval", kind=step.kind)
                self._emit("run_stopped", status=status)
                continue

            if paid:
                # GATE *BEFORE* spend: strict per-task budget pre-check (universal,
                # applies even to admin — it is the task's own cap, not a user limit).
                self._emit("gate_checked", kind=step.kind,
                           spent=total_cost, estimated=step.estimated_usd,
                           budget=task.budget_usd)
                actor_limit = self._actor_limits.get(task.actor)  # None => unlimited
                over_budget = total_cost + step.estimated_usd > task.budget_usd
                over_actor = (
                    actor_limit is not None
                    and total_cost + step.estimated_usd > actor_limit
                )
                if over_budget or over_actor:
                    step.status = StepStatus.BLOCKED
                    blocked.append(step)
                    stopped = True
                    status = "stopped_budget"
                    reason = ("per-task budget would be exceeded" if over_budget
                              else f"actor '{task.actor}' per-user limit would be exceeded")
                    self._emit("step_blocked", kind=step.kind, reason=reason)
                    self._emit("run_stopped", status=status)
                    continue

            handler = self._registry.get(step.kind)
            result: HandlerResult = await handler(step, {"task": task})

            if not result.ok:
                # Refusal / failure -> NOT charged (proven rule 'refusal не списан').
                step.status = StepStatus.FAILED
                step.error = result.error
                self._emit("step_failed", kind=step.kind, error=result.error)
                continue

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
            blocked=blocked, approvals_needed=approvals,
        )
        self._persist(task, plan, status, total_cost)        # final terminal snapshot
        if not stopped:
            self._emit("run_completed", total_cost_usd=total_cost)
        return report
