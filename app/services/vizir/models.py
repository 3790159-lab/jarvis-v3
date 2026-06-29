# -*- coding: utf-8 -*-
"""Vizir core data models. Pure dataclasses, no behavior, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Policy(str, Enum):
    """Who may run a step. Designed into the core so the same boundary applies
    whether CC or (later) Jarvis drives Vizir for an admin or a friend."""

    AUTO = "auto"                          # Jarvis/CC runs it alone
    REQUIRES_APPROVAL = "requires_approval"  # must ask the human (Daniil)


class StepStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"
    BLOCKED = "blocked"            # money-gate / per-task budget blocked it
    NEEDS_APPROVAL = "needs_approval"
    FAILED = "failed"
    SKIPPED = "skipped"            # not reached (a prior step stopped the run)


@dataclass
class Step:
    """One unit of work dispatched to a registered StepHandler by ``kind``."""

    kind: str
    params: dict = field(default_factory=dict)
    policy: Policy = Policy.AUTO
    estimated_usd: float = 0.0
    max_usd: float = 0.0          # mid-flight per-step cost cap (0 = no cap)
    status: StepStatus = StepStatus.PENDING
    result: Any = None
    cost_usd: float = 0.0
    error: str | None = None


@dataclass
class Task:
    """A coordination request. ``budget_usd`` and ``actor`` are product-level
    money-safety, baked into the core (not bolted on per-driver)."""

    task_id: str
    goal: str
    budget_usd: float = 0.0       # per-task hard cap (universal, even for admin)
    actor: str = "admin"          # who requested it (admin = unlimited per-user)
    constraints: dict = field(default_factory=dict)


@dataclass
class Plan:
    """Ordered decomposition of a Task into Steps."""

    steps: list[Step]


@dataclass
class Report:
    """Outcome of a coordination run, rendered by the driver."""

    task_id: str
    goal: str
    actor: str
    steps: list[Step]
    total_cost_usd: float
    status: str                   # completed | stopped_budget | stopped_for_approval
    approvals_needed: list[Step] = field(default_factory=list)
    blocked: list[Step] = field(default_factory=list)
