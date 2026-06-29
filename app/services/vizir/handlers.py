# -*- coding: utf-8 -*-
"""Pluggable StepHandler registry.

Mirrors the proven ``SwapEngine`` factory pattern: a handler is registered by
``Step.kind`` and dispatched by the Coordinator. Handlers are async callables
``(step, ctx) -> HandlerResult``. Paid handlers report ``cost_usd``; the
Coordinator — not the handler — owns the money-gate (check before / charge
after), so a handler can never bypass the budget.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .models import Step


@dataclass
class HandlerResult:
    """What a StepHandler returns. ``ok=False`` => not charged (proven pattern)."""

    ok: bool = True
    result: Any = None
    cost_usd: float = 0.0
    error: str | None = None


# A StepHandler is an async callable taking the Step and a context dict.
StepHandler = Callable[[Step, dict], Awaitable[HandlerResult]]


class HandlerRegistry:
    """Maps ``Step.kind`` -> StepHandler."""

    def __init__(self) -> None:
        self._handlers: dict[str, StepHandler] = {}

    def register(self, kind: str, handler: StepHandler) -> None:
        self._handlers[kind] = handler

    def get(self, kind: str) -> StepHandler | None:
        return self._handlers.get(kind)

    def has(self, kind: str) -> bool:
        return kind in self._handlers
