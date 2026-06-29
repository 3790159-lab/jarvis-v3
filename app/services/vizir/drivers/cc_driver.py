# -*- coding: utf-8 -*-
"""CC-driver — renders Vizir core events to a chat journal (ASCII, Windows-safe).

Thin: only ``_format`` differs from the shared BaseDriver; all wiring (build a
Coordinator, run, summarize, forward gate wiring) lives in the base. A
JarvisDriver is the same base with a Telegram ``_format`` — same core, same
Report, different rendering.
"""
from __future__ import annotations

from .base import BaseDriver


class CCDriver(BaseDriver):
    @property
    def journal(self) -> list[str]:
        """Back-compat alias for the rendered transcript."""
        return self._transcript

    def _format(self, event: dict) -> str:
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
        if t == "cost_progress":
            return f"  ..spend: {k} +${event['delta']:.4f} (so far ${event['spent']:.4f})"
        if t == "progress":
            return f"  ..{k}: {event.get('note')}"
        if t == "run_completed":
            return f"= run completed, total ${event['total_cost_usd']:.4f}"
        if t == "run_stopped":
            return f"= run stopped: {event.get('status')}"
        return f"  event: {t}"
