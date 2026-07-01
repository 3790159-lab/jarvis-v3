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
