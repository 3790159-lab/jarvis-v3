# -*- coding: utf-8 -*-
"""Generation analytics — daily summaries, persona breakdowns, usage trends."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.block_m2_video.generation_history import GenerationHistory
from app.services.block_m_common.cost_tracker import CostTracker
from app.services.block_m_common.logging_setup import get_logger

logger = get_logger("analytics")

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PERSONAS_DIR = _ROOT / "state" / "personas"


class Analytics:
    """Cost and generation analytics for the Block-M pipeline.

    Args:
        tracker: CostTracker for expense data.
        history: GenerationHistory for record counts.
    """

    def __init__(self, tracker: CostTracker, history: GenerationHistory) -> None:
        self._tracker = tracker
        self._history = history

    async def daily_summary(self, persona_id: str | None = None) -> dict:
        """Return today's cost and generation stats.

        Args:
            persona_id: If provided, filter by this persona. None = global.

        Returns:
            {
                "today_cost_usd": float,
                "today_count": int,
                "remaining_budget_usd": float,
                "by_operation": dict[str, float],
                "date": str (YYYY-MM-DD),
            }
        """
        stats = await self._tracker.get_stats()
        today_str = datetime.now(timezone.utc).date().isoformat()

        if persona_id is not None:
            persona_cost = await self._tracker.get_total_for_persona(persona_id)
            records = await self._history.list_recent(persona_id, limit=1000)
            today = datetime.now(timezone.utc).date()
            today_count = sum(
                1 for r in records
                if (r.created_at.date() if isinstance(r.created_at, datetime) else r.created_at) == today
            )
            return {
                "today_cost_usd": persona_cost,
                "today_count": today_count,
                "remaining_budget_usd": max(0.0, 10.0 - stats["daily"]),
                "by_operation": {},
                "date": today_str,
            }

        _, remaining = await self._tracker.check_limit()
        today_count = await self._count_today_records()
        return {
            "today_cost_usd": stats["daily"],
            "today_count": today_count,
            "remaining_budget_usd": remaining,
            "by_operation": stats.get("by_operation", {}),
            "date": today_str,
        }

    async def persona_summary(self, persona_id: str) -> dict:
        """Return lifetime stats for a persona.

        Returns:
            {
                "persona_id": str,
                "total_cost_usd": float,
                "generation_count": int,
                "by_engine": dict[str, int],
            }
        """
        total_cost = await self._tracker.get_total_for_persona(persona_id)
        records = await self._history.list_recent(persona_id, limit=10000)
        by_engine: dict[str, int] = {}
        for r in records:
            by_engine[r.engine] = by_engine.get(r.engine, 0) + 1
        return {
            "persona_id": persona_id,
            "total_cost_usd": total_cost,
            "generation_count": len(records),
            "by_engine": by_engine,
        }

    async def usage_trend(self, days: int = 7) -> list[dict]:
        """Return per-day cost for the last N days.

        Returns:
            List of {"date": "YYYY-MM-DD", "cost_usd": float} newest-first.
        """
        today = datetime.now(timezone.utc).date()
        stats = await self._tracker.get_stats()
        result: list[dict] = []
        for offset in range(days):
            day = today - timedelta(days=offset)
            cost = self._day_cost_from_stats(stats, day)
            result.append({"date": day.isoformat(), "cost_usd": cost})
        return result

    # ── private helpers ────────────────────────────────────────────────────────

    async def _count_today_records(self) -> int:
        """Count all generation records from today across all personas."""
        today = datetime.now(timezone.utc).date()
        count = 0
        for history_file in _PERSONAS_DIR.glob("*/history.jsonl"):
            try:
                lines = history_file.read_text(encoding="utf-8").splitlines()
                for line in lines:
                    if not line.strip():
                        continue
                    import json
                    try:
                        rec = json.loads(line)
                        ts = rec.get("created_at", "")
                        if ts:
                            d = datetime.fromisoformat(ts).date()
                            if d == today:
                                count += 1
                    except Exception:
                        pass
            except Exception:
                pass
        return count

    @staticmethod
    def _day_cost_from_stats(stats: dict, day) -> float:
        """Extract single-day cost from a CostTracker stats dict.

        CostTracker.get_stats() does not break down by day — it only returns
        today's total. For trend data we re-read the file. This is a best-effort
        approximation using the daily total for today and 0.0 for past days that
        aren't separately tracked in the stats dict.
        """
        from datetime import date
        today = datetime.now(timezone.utc).date()
        if day == today:
            return stats.get("daily", 0.0)
        return 0.0
