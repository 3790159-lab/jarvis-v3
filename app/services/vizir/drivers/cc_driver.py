# -*- coding: utf-8 -*-
"""CC-driver — renders Vizir core events to a chat journal and runs a task.

THIN by design: it formats events and wires a Coordinator. It holds NO
coordination logic (budget, policy, persistence all live in the core), so a
JarvisDriver can later replace it by swapping render() for Telegram output.
ASCII tokens keep journal lines Windows-console safe.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..coordinator import Coordinator
from ..handlers import HandlerRegistry
from ..models import Plan, Report, Task


class CCDriver:
    def __init__(
        self,
        registry: HandlerRegistry,
        sink: Callable[[str], None] | None = None,
        state_dir: Path | None = None,
        actor_limits: dict[str, float] | None = None,
    ) -> None:
        self._registry = registry
        self._journal: list[str] = []
        self._sink = sink or self._journal.append
        self._state_dir = state_dir
        self._actor_limits = actor_limits

    @property
    def journal(self) -> list[str]:
        return self._journal

    @staticmethod
    def _format(event: dict) -> str:
        t = event.get("type", "?")
        k = event.get("kind", "")
        if t == "step_started":
            return f"> step: {k}"
        if t == "gate_checked":
            return (f"  gate: {k} spent=${event['spent']:.4f} "
                    f"+${event['estimated']:.4f} / budget=${event['budget']:.2f}")
        if t == "charged":
            return f"  charged: {k} ${event['cost_usd']:.4f}"
        if t == "step_done":
            return f"  done: {k}"
        if t == "step_failed":
            return f"  failed: {k} ({event.get('error')})"
        if t == "step_blocked":
            return f"  blocked: {k} - {event.get('reason')}"
        if t == "step_needs_approval":
            return f"  needs-approval: {k}"
        if t == "run_completed":
            return f"= run completed, total ${event['total_cost_usd']:.4f}"
        if t == "run_stopped":
            return f"= run stopped: {event.get('status')}"
        return f"  event: {t}"

    def render(self, event: dict) -> None:
        """Driver's only inbound surface — render a core event. No logic here."""
        self._sink(self._format(event))

    def _summary(self, report: Report) -> str:
        return (f"REPORT task={report.task_id} status={report.status} "
                f"cost=${report.total_cost_usd:.4f} "
                f"steps={[s.status.value for s in report.steps]}")

    async def run(self, task: Task, plan: Plan) -> Report:
        """Wire a Coordinator with render() as its event sink, run, summarize."""
        coord = Coordinator(
            self._registry, on_event=self.render,
            actor_limits=self._actor_limits, state_dir=self._state_dir,
        )
        report = await coord.run(task, plan)
        self._sink(self._summary(report))
        return report
