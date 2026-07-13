# -*- coding: utf-8 -*-
"""Append-only cost tracker with configurable daily spending limit."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DAILY_LIMIT_USD: float = 10.0

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_EXPENSES_FILE = _ROOT / "state" / "personas" / "expenses.jsonl"


class DailyLimitExceeded(Exception):
    """Raised when an operation would exceed the daily spending cap."""


class CostTracker:
    """Append-only expense log with daily budget enforcement.

    All entries are written to a .jsonl file (one JSON object per line).
    Reading always scans the full file, which is intentional for audit
    correctness over performance.

    Args:
        expenses_file: Path to the .jsonl log file.
                       Defaults to state/personas/expenses.jsonl.
        daily_limit: Maximum USD to spend per calendar day (UTC).
                     Defaults to DAILY_LIMIT_USD (10.0).
    """

    def __init__(
        self,
        expenses_file: Path | None = None,
        daily_limit: float = DAILY_LIMIT_USD,
    ) -> None:
        self._file = expenses_file or _EXPENSES_FILE
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._daily_limit = daily_limit

    async def log_expense(
        self,
        operation: str,
        cost_usd: float,
        persona_id: str | None = None,
    ) -> None:
        """Record an expense entry.

        Args:
            operation: Name of the billed operation (e.g. "kling_video").
            cost_usd: Cost in US dollars.
            persona_id: Associated persona, or None for global operations.

        Raises:
            DailyLimitExceeded: If today's total already meets or exceeds the limit.
        """
        can_proceed, remaining = await self.check_limit()
        if not can_proceed:
            raise DailyLimitExceeded(
                f"Daily limit of ${self._daily_limit:.2f} exceeded. "
                f"Remaining: ${remaining:.2f}. Blocked operation: {operation}"
            )

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "operation": operation,
            "cost_usd": cost_usd,
            "persona_id": persona_id,
        }
        with self._file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        logger.info(
            "Expense logged: %s $%.4f persona=%s (daily total ~$%.4f)",
            operation,
            cost_usd,
            persona_id,
            await self.get_today_total(),
        )

    async def get_today_total(self) -> float:
        """Return total USD spent today (UTC calendar day)."""
        today = datetime.now(timezone.utc).date()
        return sum(
            e.get("cost_usd", 0.0)
            for e in self._read_all()
            if self._entry_date(e) == today
        )

    async def get_total_for_persona(self, persona_id: str) -> float:
        """Return cumulative USD spent for a specific persona across all time.

        Args:
            persona_id: Persona to aggregate costs for.
        """
        return sum(
            e.get("cost_usd", 0.0)
            for e in self._read_all()
            if e.get("persona_id") == persona_id
        )

    async def check_limit(self) -> tuple[bool, float]:
        """Whether more spending is allowed today.

        RETIRED (money-consolidation, hole b): this global $10/day budget read
        the stale expenses.jsonl, never fired in practice, and duplicated the
        real per-user cap (``app/services/auth/access_control.check_limit`` on
        the live audit ledger). It now ALWAYS allows — the per-user audit gate
        (pre-gates added for every paid persona command) is the real protection.
        ``remaining`` is kept informational for callers/logging.

        Returns:
            (can_proceed=True, remaining_usd)
        """
        today_total = await self.get_today_total()
        remaining = max(0.0, self._daily_limit - today_total)
        return True, remaining

    async def get_costs_by_day(self, target_date) -> dict:
        """Cost breakdown by operation for a single calendar day (UTC).

        Used by the morning digest to report "yesterday's spend by category".
        """
        result: dict = {}
        for entry in self._read_all():
            if self._entry_date(entry) != target_date:
                continue
            op = entry.get("operation", "unknown")
            result[op] = result.get(op, 0.0) + entry.get("cost_usd", 0.0)
        return result

    async def get_stats(self) -> dict:
        """Return cost breakdown by period and by operation type.

        Returns:
            dict with keys: daily, weekly, monthly, total, by_operation.
        """
        now = datetime.now(timezone.utc)
        today = now.date()
        stats: dict = {
            "daily": 0.0,
            "weekly": 0.0,
            "monthly": 0.0,
            "total": 0.0,
            "by_operation": {},
        }

        for entry in self._read_all():
            cost = entry.get("cost_usd", 0.0)
            op = entry.get("operation", "unknown")
            entry_date = self._entry_date(entry)
            if entry_date is None:
                continue

            stats["total"] += cost
            stats["by_operation"][op] = stats["by_operation"].get(op, 0.0) + cost

            if entry_date == today:
                stats["daily"] += cost
            if (today - entry_date).days < 7:
                stats["weekly"] += cost
            if entry_date.year == today.year and entry_date.month == today.month:
                stats["monthly"] += cost

        return stats

    # ── private helpers ────────────────────────────────────────────────────────

    def _read_all(self) -> list[dict]:
        if not self._file.exists():
            return []
        entries = []
        for line in self._file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return entries

    @staticmethod
    def _entry_date(entry: dict):
        ts = entry.get("ts", "")
        try:
            return datetime.fromisoformat(ts).date()
        except (ValueError, TypeError):
            return None
