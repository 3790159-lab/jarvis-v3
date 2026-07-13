# -*- coding: utf-8 -*-
"""``/devtask_stats`` report — pure aggregation over dev-task pipeline history.

Two inputs, both already loaded from disk by the caller (impure I/O stays in
:class:`app.services.devtask.queue.DevTaskQueue`'s ``list_all``/``log_entries``,
mirroring the split in :mod:`app.services.costs_summary`):

* ``cards`` — one dict per ``state/dev_tasks/<id>.json`` (``list_all()``).
* ``log_entries`` — raw ``log.jsonl`` lines (``log_entries()``), used only to
  derive each task's wall-clock duration (its ``added`` write to its LAST
  status write) — terminal cards carry no ``finished_at`` field of their own
  (only ``merged`` does, via ``merged_at``), but every ``set_status`` call is
  logged with a timestamp, so the log is the one source with a duration for
  every terminal status, not just merges.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.services.devtask.queue import (
    STATUS_MERGED,
    STATUS_ROLLED_BACK,
    STATUS_FAILED,
)

__all__ = ["build_summary", "format_stats_message"]

REASON_NO_REPORT = "no_report"
_TERMINAL = (STATUS_MERGED, STATUS_ROLLED_BACK, STATUS_FAILED)


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _task_durations(log_entries: List[Dict[str, Any]]) -> Dict[str, float]:
    """task_id -> seconds between its earliest and latest log.jsonl entry."""
    first: Dict[str, datetime] = {}
    last: Dict[str, datetime] = {}
    for entry in log_entries:
        tid = entry.get("id")
        ts = _parse_ts(entry.get("ts"))
        if not tid or ts is None:
            continue
        if tid not in first or ts < first[tid]:
            first[tid] = ts
        if tid not in last or ts > last[tid]:
            last[tid] = ts
    return {
        tid: (last[tid] - first[tid]).total_seconds()
        for tid in first
        if last[tid] > first[tid]
    }


def build_summary(cards: List[Dict[str, Any]], log_entries: List[Dict[str, Any]],
                   days: int, now: datetime) -> Dict[str, Any]:
    """Build a ``days``-day report ending on ``now``.

    Args:
        cards: task cards (``DevTaskQueue.list_all()``); a card counts iff its
            ``created_at`` parses to within ``[now - days, now]``.
        log_entries: raw ``log.jsonl`` lines (``DevTaskQueue.log_entries()``),
            used to compute per-task duration for terminal tasks.
        days: report window length. Clamped to >= 1.
        now: the report's "as of" time.
    """
    days = max(1, int(days))
    period_start = now - timedelta(days=days)

    in_period = []
    for card in cards:
        created = _parse_ts(card.get("created_at"))
        if created is not None and period_start <= created <= now:
            in_period.append(card)

    total = len(in_period)
    merged = sum(1 for c in in_period if c.get("status") == STATUS_MERGED)
    rolled_back = sum(1 for c in in_period if c.get("status") == STATUS_ROLLED_BACK)
    no_report = sum(
        1 for c in in_period
        if c.get("status") == STATUS_FAILED and c.get("error") == REASON_NO_REPORT
    )
    other_failed = sum(
        1 for c in in_period
        if c.get("status") == STATUS_FAILED and c.get("error") != REASON_NO_REPORT
    )
    in_flight = total - merged - rolled_back - no_report - other_failed

    costs = [float(c["cost"]) for c in in_period if c.get("cost") is not None]
    total_cost = round(sum(costs), 2)
    avg_cost = round(total_cost / len(costs), 2) if costs else 0.0

    durations = _task_durations(log_entries)
    terminal_ids = {c["id"] for c in in_period
                    if c.get("id") and c.get("status") in _TERMINAL}
    dur_values = [durations[tid] for tid in terminal_ids if tid in durations]
    avg_duration_s = round(sum(dur_values) / len(dur_values), 1) if dur_values else 0.0

    costed = [c for c in in_period if c.get("cost") is not None]
    top_costly = sorted(costed, key=lambda c: -float(c["cost"]))[:3]
    top = [
        {
            "id": c.get("id"),
            "desc": c.get("desc", ""),
            "cost_usd": round(float(c["cost"]), 2),
            "status": c.get("status"),
        }
        for c in top_costly
    ]

    return {
        "days": days,
        "total": total,
        "merged": merged,
        "rolled_back": rolled_back,
        "no_report": no_report,
        "other_failed": other_failed,
        "in_flight": in_flight,
        "total_cost_usd": total_cost,
        "avg_cost_usd": avg_cost,
        "avg_duration_s": avg_duration_s,
        "top_costly": top,
    }


def _format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "0м"
    total_min = int(round(seconds / 60))
    h, m = divmod(total_min, 60)
    if h:
        return f"{h}ч {m}м"
    return f"{m}м"


def format_stats_message(summary: Dict[str, Any]) -> str:
    """Render :func:`build_summary`'s output as a compact Russian Telegram message."""
    lines = [
        f"📈 Статистика конвейера за {summary['days']}д:",
        "",
        f"Всего задач: {summary['total']}",
        f"  ✅ Смерджено: {summary['merged']}",
        f"  ↩️ Откат: {summary['rolled_back']}",
        f"  📄 No report: {summary['no_report']}",
    ]
    if summary.get("other_failed"):
        lines.append(f"  ❌ Прочие failed: {summary['other_failed']}")
    if summary.get("in_flight"):
        lines.append(f"  ⏳ В работе: {summary['in_flight']}")
    lines.append("")
    lines.append(f"💰 Средняя стоимость: ${summary['avg_cost_usd']:.2f}")
    lines.append(f"⏱ Средняя длительность: {_format_duration(summary['avg_duration_s'])}")
    lines.append(f"💵 Суммарные траты: ${summary['total_cost_usd']:.2f}")
    lines.append("")

    if summary["top_costly"]:
        lines.append("🏆 Топ-3 самых дорогих задач:")
        for i, t in enumerate(summary["top_costly"], start=1):
            desc = (t["desc"] or "").strip()
            if len(desc) > 60:
                desc = desc[:57] + "..."
            lines.append(f"  {i}. {desc} — ${t['cost_usd']:.2f} ({t['status']})")
    else:
        lines.append("🏆 Топ-3 самых дорогих задач: нет данных о стоимости")

    return "\n".join(lines)
