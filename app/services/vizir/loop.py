# -*- coding: utf-8 -*-
"""Vizir autonomy LOOP — a thin, acceptance-gated retry cycle OVER the Coordinator.

The Coordinator runs ONE plan under the money-gate. The LoopController runs the
Coordinator repeatedly on the SAME single-step plan (Hermes), re-checking Vizir
acceptance each attempt and injecting the failing reasons into the next attempt's
prompt, until acceptance passes or a deterministic tooth stops it. It is a
CONTROLLED EXECUTOR, never a parallel brain: it re-plans nothing and makes only
deterministic budget/stall decisions. Money teeth span the WHOLE cycle via one
shared Task.budget_usd + reserve-before-attempt. See docs/specs/vizir-loop.md.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .models import Task


@dataclass
class LoopConfig:
    max_attempts: int = 3          # hard ceiling on outer attempts
    min_attempt_usd: float = 0.0   # reserve-before-attempt floor (0 => only require remaining>0)
    loop_deadline_s: float = 0.0   # wall-clock cap over the whole loop (0 => off)


@dataclass
class LoopReport:
    task_id: str
    goal: str
    actor: str
    accepted: bool
    stopped_reason: str            # completed | stopped_max_attempts | stopped_budget
                                   # | stopped_cost_cap | stopped_stalled | stopped_timeout
    attempts: int
    loop_spent_usd: float
    last_result: Any
    reasons: list = field(default_factory=list)          # last acceptance reasons
    reasons_history: list = field(default_factory=list)  # reasons per rejected attempt
    needs_escalation: bool = False # True on any non-completed stop -> show Daniil


class LoopController:
    """Outer acceptance-gated retry loop over a Coordinator. Thin: it sequences
    attempts, injects acceptance feedback, and applies deterministic teeth. All
    money is metered by the injected Coordinator; the loop adds only the
    reserve-before-attempt tooth on the shared budget."""

    def __init__(
        self,
        coordinator,
        build_plan: Callable[[str], Any],   # (prompt) -> Plan (single Hermes step)
        accept_fn: Callable[[dict], Any],    # (result_dict) -> AcceptanceResult
        config: LoopConfig | None = None,
        on_event: Callable[[dict], None] | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._build_plan = build_plan
        self._accept_fn = accept_fn
        self._config = config or LoopConfig()
        self._on_event = on_event or (lambda e: None)
        self._now = now_fn or time.monotonic

    def _emit(self, type_: str, **fields) -> None:
        self._on_event({"type": type_, **fields})

    # ---- spy-able seams (permissive stubs; made real in later tasks) ----
    def _reserve_allows(self, remaining: float, cfg: LoopConfig) -> bool:
        # never start an attempt you cannot pay for within the shared budget
        if remaining <= 0:
            return False
        if cfg.min_attempt_usd and remaining < cfg.min_attempt_usd:
            return False
        return True

    def _is_hard_stop(self, status: str) -> bool:
        # Any non-"completed" Coordinator outcome is a money/approval event
        # (stopped_budget / stopped_cost_cap / stopped_for_approval) => stop the
        # whole loop conservatively; do NOT retry a run that burned toward a cap.
        return status != "completed"

    def _is_stalled(self, reasons: list, prev_reasons) -> bool:
        # identical failure signature two attempts running => not converging.
        return prev_reasons is not None and reasons == prev_reasons

    def _deadline_exceeded(self, start: float, cfg: LoopConfig) -> bool:
        return bool(cfg.loop_deadline_s) and (self._now() - start) >= cfg.loop_deadline_s

    def _compose_prompt(self, base_prompt: str, reasons: list) -> str:
        # IMMUTABLE base + APPENDED feedback (never overwrite the goal): bounds
        # goal-drift and gives directed convergence across attempts.
        if not reasons:
            return base_prompt
        joined = "; ".join(reasons)
        return (base_prompt
                + "\n\n[FEEDBACK] Предыдущая попытка провалила проверки: "
                + joined + ". Исправь их, остальное сохрани.")

    # ---- helpers ----
    def _stopped(self, task, reason, attempt, loop_spent, last_result,
                 reasons, reasons_history) -> LoopReport:
        self._emit("loop_stopped", reason=reason, spent=loop_spent, attempts=attempt)
        return LoopReport(
            task_id=task.task_id, goal=task.goal, actor=task.actor,
            accepted=False, stopped_reason=reason, attempts=attempt,
            loop_spent_usd=loop_spent, last_result=last_result,
            reasons=list(reasons), reasons_history=reasons_history,
            needs_escalation=True,
        )

    async def run(self, task, base_prompt: str) -> LoopReport:
        cfg = self._config
        loop_spent = 0.0
        reasons_history: list = []
        prev_reasons = None
        last_result = None
        reasons: list = []
        start = self._now()
        attempt = 0

        while True:
            # STOP 1: hard ceiling on attempts
            if attempt >= cfg.max_attempts:
                return self._stopped(task, "stopped_max_attempts", attempt,
                                     loop_spent, last_result, reasons, reasons_history)
            # STOP 6: wall-clock loop deadline (backstop)
            if self._deadline_exceeded(start, cfg):
                return self._stopped(task, "stopped_timeout", attempt,
                                     loop_spent, last_result, reasons, reasons_history)
            # STOP 3: reserve-before-attempt on the SHARED budget (never start
            # an attempt you cannot afford within the whole-loop budget)
            remaining = task.budget_usd - loop_spent
            if not self._reserve_allows(remaining, cfg):
                return self._stopped(task, "stopped_budget", attempt,
                                     loop_spent, last_result, reasons, reasons_history)

            attempt += 1
            prompt = self._compose_prompt(base_prompt, reasons)
            self._emit("loop_attempt_started", attempt=attempt)
            plan = self._build_plan(prompt)
            # per-attempt Task carries the REMAINING budget down => the
            # Coordinator's own pre-check is a double-insurance backstop.
            task_k = Task(
                task_id="%s-a%d" % (task.task_id, attempt), goal=task.goal,
                budget_usd=remaining, actor=task.actor,
                constraints=dict(task.constraints),
            )
            report = await self._coordinator.run(task_k, plan)
            loop_spent += report.total_cost_usd

            # STOP 2/4: the Coordinator did not complete the plan (money/approval
            # event: stopped_budget / stopped_cost_cap / stopped_for_approval)
            # => conservative stop of the whole loop, no retry.
            if self._is_hard_stop(report.status):
                return self._stopped(task, report.status, attempt,
                                     loop_spent, last_result, reasons, reasons_history)

            last_step = report.steps[-1] if report.steps else None
            result_dict = (last_step.result
                           if (last_step is not None and isinstance(last_step.result, dict))
                           else {})
            last_result = result_dict

            acc = self._accept_fn(result_dict)
            if acc.accepted:
                self._emit("loop_stopped", reason="completed",
                           spent=loop_spent, attempts=attempt)
                return LoopReport(
                    task_id=task.task_id, goal=task.goal, actor=task.actor,
                    accepted=True, stopped_reason="completed", attempts=attempt,
                    loop_spent_usd=loop_spent, last_result=last_result,
                    reasons=[], reasons_history=reasons_history,
                    needs_escalation=False,
                )

            reasons = list(acc.reasons)
            reasons_history.append(list(reasons))
            self._emit("loop_attempt_rejected", attempt=attempt, reasons=reasons)

            # STOP 5: not converging (identical failure signature two attempts running)
            if self._is_stalled(reasons, prev_reasons):
                return self._stopped(task, "stopped_stalled", attempt,
                                     loop_spent, last_result, reasons, reasons_history)
            prev_reasons = reasons
