# -*- coding: utf-8 -*-
"""JarvisDriver — STUB Telegram driver proving the core is driver-agnostic.

Same BaseDriver wiring as CCDriver; ONLY ``_format`` differs (Telegram-markdown
flavor) and ``step_needs_approval`` renders inline-button stubs ([Approve]/
[Reject]) — the seam the live bot will fill with real Telegram buttons.

STUB scope: it renders to an in-memory ``messages`` transcript, not a real chat.
Resolving an approval and RESUMING the run is a later arc (the core currently
stops at ``stopped_for_approval``); this stub only proves the same core runs
identically under a non-CC driver. The live bot integration is a separate arc.
"""
from __future__ import annotations

from .base import BaseDriver


class JarvisDriver(BaseDriver):
    @property
    def messages(self) -> list[str]:
        """Telegram-style rendered messages (in-memory stub for the real chat)."""
        return self._transcript

    def _format(self, event: dict) -> str:
        t = event.get("type", "?")
        k = event.get("kind", "")
        if t == "step_started":
            return f"_running_ {k}"
        if t == "gate_checked":
            return (f"_gate_ {k}: ${event['spent']:.4f}+${event['estimated']:.4f}"
                    f"/${event['budget']:.2f}")
        if t == "charged":
            return f"_charged_ {k} ${event['cost_usd']:.4f}"
        if t == "step_done":
            return f"*OK* {k}"
        if t == "step_failed":
            return f"*FAIL* {k}: {event.get('error')}"
        if t == "step_blocked":
            return f"*BLOCKED* {k} - {event.get('reason')}"
        if t == "step_needs_approval":
            # The seam the live bot fills with real inline buttons.
            return f"*approval needed*: {k}   [Approve] [Reject]"
        if t == "cost_progress":
            return f"_spend_ {k} +${event['delta']:.4f} (= ${event['spent']:.4f})"
        if t == "progress":
            return f"_note_ {k}: {event.get('note')}"
        if t == "run_completed":
            return f"*finished* total ${event['total_cost_usd']:.4f}"
        if t == "run_stopped":
            return f"*stopped*: {event.get('status')}"
        return f"_event_ {t}"
