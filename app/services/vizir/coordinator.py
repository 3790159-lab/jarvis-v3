# -*- coding: utf-8 -*-
"""Vizir Coordinator — the core loop: decompose is the caller's job; here we
execute a Plan's Steps under the money-gate and emit structured events.

Transport-agnostic: the only outward surface is ``on_event`` (a callback the
driver renders) and the returned ``Report``. Knows nothing about CC/Telegram.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Callable

from .handlers import HandlerRegistry, HandlerResult
from .models import Plan, Policy, Report, Step, StepStatus, Task

logger = logging.getLogger(__name__)


class StepBudgetExceeded(Exception):
    """Raised by ctx['report_cost'] when a chunk would exceed the step's max_usd
    or the task budget / actor limit. Reserve-before-spend: the chunk is NOT
    spent. Agents may catch it to clean up, then it propagates to the Coordinator
    which blocks the step and charges only what was already approved."""


class Coordinator:
    def __init__(
        self,
        registry: HandlerRegistry,
        on_event: Callable[[dict], None] | None = None,
        actor_limits: dict[str, float] | None = None,
        state_dir: Path | None = None,
        charge_logger: Callable[[str, str, float], object] | None = None,
        check_limit_fn: Callable[[str, float], tuple[bool, str]] | None = None,
    ) -> None:
        self._registry = registry
        self._on_event = on_event or (lambda e: None)
        self._state_dir = Path(state_dir) if state_dir is not None else None
        # Optional async charge sink, called (actor, operation, amount) AFTER a
        # successful paid step. Wired to an isolated CostTracker in Phase 3 live.
        self._charge_logger = charge_logger
        # Optional per-user pre-check (actor, estimated)->(allowed, reason). In
        # prod this is access_control.check_limit (admin unlimited / friend capped).
        self._check_limit_fn = check_limit_fn
        # Per-user cap, additional to the universal per-task budget. An actor
        # not present here (e.g. "admin") is unlimited per-user. Phase 3 wires
        # this to the proven access_control.check_limit.
        self._actor_limits = actor_limits or {}

    def _emit(self, type_: str, **fields) -> None:
        self._on_event({"type": type_, **fields})

    def _money_gate_allows(self, task: Task, spent: float, step: Step) -> tuple[bool, str]:
        """Strict pre-check: would this paid step exceed the per-task budget or the
        actor's per-user limit? Extracted so it is a single, testable seam (Phase 3
        swaps in access_control.check_limit here)."""
        prospective = spent + step.estimated_usd
        # 1) Per-task budget — universal hard cap (applies even to admin).
        if prospective > task.budget_usd:
            return False, "per-task budget would be exceeded"
        # 2) Production per-user gate (access_control.check_limit) if injected.
        if self._check_limit_fn is not None:
            allowed, reason = self._check_limit_fn(task.actor, step.estimated_usd)
            if not allowed:
                return False, reason or f"actor '{task.actor}' per-user limit reached"
        # 3) In-memory per-actor cap (test/standalone fallback).
        actor_limit = self._actor_limits.get(task.actor)   # None => unlimited
        if actor_limit is not None and prospective > actor_limit:
            return False, f"actor '{task.actor}' per-user limit would be exceeded"
        return True, ""

    def _chunk_allowed(self, task: Task, total_cost: float, step_spent: float,
                       amount: float, step: Step) -> tuple[bool, str]:
        """Mid-flight reserve-before-spend check for ONE chunk. Reuses the same
        budget/actor numbers as the pre-check (no duplication), plus the per-step
        cap. Returns (allowed, reason); report_cost raises when not allowed."""
        prospective_step = step_spent + amount
        prospective_total = total_cost + prospective_step
        if step.max_usd and prospective_step > step.max_usd:
            return False, f"step cap ${step.max_usd:.4f} would be exceeded"
        if prospective_total > task.budget_usd:
            return False, "per-task budget would be exceeded"
        actor_limit = self._actor_limits.get(task.actor)
        if actor_limit is not None and prospective_total > actor_limit:
            return False, f"actor '{task.actor}' per-user limit would be exceeded"
        return True, ""

    async def _run_handler(self, handler, step: Step, ctx: dict) -> HandlerResult:
        """Run the handler, wrapped in a wall-clock timeout when step.timeout_s>0.
        A hung agent is cancelled (CancelledError reaches it for cleanup) and
        asyncio.TimeoutError propagates to the run loop. Single seam (spy-able)."""
        if step.timeout_s:
            return await asyncio.wait_for(handler(step, ctx), step.timeout_s)
        return await handler(step, ctx)

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
        results: dict = {}        # kind -> result, so later steps build on earlier ones

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
                allowed, reason = self._money_gate_allows(task, total_cost, step)
                if not allowed:
                    step.status = StepStatus.BLOCKED
                    blocked.append(step)
                    stopped = True
                    status = "stopped_budget"
                    self._emit("step_blocked", kind=step.kind, reason=reason)
                    self._emit("run_stopped", status=status)
                    continue

            # Mid-flight cost meter: the handler streams its spend via
            # ctx["report_cost"] (reserve-before-spend) and notes via
            # ctx["report_progress"]. T-B: report_cost ENFORCES the per-step cap /
            # budget, raising StepBudgetExceeded so a breaching chunk is never spent.
            meter = {"spent": 0.0}

            def report_cost(amount, _kind=step.kind, _step=step):
                allowed, reason = self._chunk_allowed(
                    task, total_cost, meter["spent"], amount, _step)
                if not allowed:
                    raise StepBudgetExceeded(reason)     # chunk NOT spent
                meter["spent"] += amount
                self._emit("cost_progress", kind=_kind,
                           spent=meter["spent"], delta=amount)

            def report_progress(note, _kind=step.kind):
                self._emit("progress", kind=_kind, note=note)

            handler = self._registry.get(step.kind)
            ctx = {
                "task": task, "results": results,
                "report_cost": report_cost, "report_progress": report_progress,
            }
            try:
                result: HandlerResult = await self._run_handler(handler, step, ctx)
            except StepBudgetExceeded as exc:
                # Mid-flight cap breach: charge ONLY the already-approved spend
                # (partial, real money already burned), block, and STOP the run
                # (money breach = conservative stop).
                spent = meter["spent"]
                step.status = StepStatus.BLOCKED
                step.cost_usd = spent
                step.error = str(exc)
                total_cost += spent
                if spent > 0:
                    if self._charge_logger is not None:
                        await self._charge_logger(task.actor, step.kind, spent)
                    self._emit("charged", kind=step.kind, cost_usd=spent)
                blocked.append(step)
                stopped = True
                status = "stopped_cost_cap"
                self._emit("step_blocked", kind=step.kind, reason=str(exc))
                self._emit("run_stopped", status=status)
                continue
            except asyncio.TimeoutError:
                # Hung agent: charge what it spent before hanging (partial), mark
                # the step FAILED, but CONTINUE — a hang is a per-step failure, not
                # a money breach (asymmetry with the cost-cap stop above).
                spent = meter["spent"]
                step.status = StepStatus.FAILED
                step.cost_usd = spent
                step.error = f"timeout after {step.timeout_s}s"
                total_cost += spent
                if spent > 0:
                    if self._charge_logger is not None:
                        await self._charge_logger(task.actor, step.kind, spent)
                    self._emit("charged", kind=step.kind, cost_usd=spent)
                self._emit("step_timeout", kind=step.kind, timeout_s=step.timeout_s)
                self._emit("step_failed", kind=step.kind, error=step.error)
                continue

            if not result.ok:
                # Refusal / failure -> NOT charged (proven rule 'refusal не списан').
                step.status = StepStatus.FAILED
                step.error = result.error
                self._emit("step_failed", kind=step.kind, error=result.error)
                continue

            step.status = StepStatus.DONE
            step.result = result.result
            step.cost_usd = result.cost_usd
            results[step.kind] = result.result
            total_cost += result.cost_usd
            if paid:
                # CHARGE *AFTER* success: record to the isolated ledger, then emit.
                if self._charge_logger is not None:
                    await self._charge_logger(task.actor, step.kind, result.cost_usd)
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
