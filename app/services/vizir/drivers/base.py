# -*- coding: utf-8 -*-
"""BaseDriver — the shared, thin driver seam.

Holds ALL the driver wiring (build a Coordinator, run it, summarize) and the
event dispatch. The ONLY thing a concrete driver overrides is ``_format`` (and
optionally ``_summary``) — i.e. how core events are rendered for its transport.

This is what makes the core driver-agnostic: CCDriver (chat journal) and
JarvisDriver (Telegram) share this identical wiring and differ only in rendering,
so the SAME core produces the SAME Report under either driver.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..coordinator import Coordinator
from ..handlers import HandlerRegistry
from ..models import Plan, Report, Task


class BaseDriver:
    def __init__(
        self,
        registry: HandlerRegistry,
        sink: Callable[[str], None] | None = None,
        state_dir: Path | None = None,
        actor_limits: dict[str, float] | None = None,
        charge_logger: Callable[[str, str, float], object] | None = None,
        check_limit_fn: Callable[[str, float], tuple[bool, str]] | None = None,
    ) -> None:
        self._registry = registry
        self._transcript: list[str] = []
        self._sink = sink or self._transcript.append
        self._state_dir = state_dir
        self._actor_limits = actor_limits
        # Forwarded to the Coordinator — drivers hold NO gate logic, only pass through.
        self._charge_logger = charge_logger
        self._check_limit_fn = check_limit_fn

    @property
    def transcript(self) -> list[str]:
        return self._transcript

    # ── render seam (the only driver-specific part) ──────────────────────────
    def _format(self, event: dict) -> str:
        raise NotImplementedError

    def _summary(self, report: Report) -> str:
        return (f"REPORT task={report.task_id} status={report.status} "
                f"cost=${report.total_cost_usd:.4f} "
                f"steps={[s.status.value for s in report.steps]}")

    def render(self, event: dict) -> None:
        """Driver's only inbound surface — render a core event. No logic here."""
        self._sink(self._format(event))

    # ── shared wiring (identical for every driver) ───────────────────────────
    async def run(self, task: Task, plan: Plan) -> Report:
        coord = Coordinator(
            self._registry, on_event=self.render,
            actor_limits=self._actor_limits, state_dir=self._state_dir,
            charge_logger=self._charge_logger, check_limit_fn=self._check_limit_fn,
        )
        report = await coord.run(task, plan)
        self._sink(self._summary(report))
        return report
