# -*- coding: utf-8 -*-
"""``/costs`` report — pure aggregation + Russian Telegram formatting.

Two data sources, deliberately kept separate:

* **Totals / by-day / period comparison** come from the *live* per-user audit
  ledger (:func:`app.services.audit.cost_tracker.get_costs_range`) — the same
  store every real ``guard_spend``/``record_cost`` call writes to. These
  numbers are authoritative.
* **Category / top-operations breakdown** comes from
  :mod:`app.services.block_m_common.cost_tracker` — the only ledger in this
  codebase that records an *operation name* per expense. That ledger has been
  frozen since 2026-05 (see ``docs/superpowers/plans/2026-07-04-money-consolidation.md``)
  — most live spend (LLM router, face-swap, most persona ops) never reaches
  it — so this breakdown is **best-effort and may undercount** the
  authoritative total above. It is rendered as a clearly-labelled secondary
  section rather than folded into the total, so the report never claims more
  precision than the data supports.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

__all__ = ["categorize_operation", "build_summary", "format_costs_message"]

CATEGORIES: List[str] = ["LLM", "Фото", "Видео", "Тренинг", "Другое"]

_CATEGORY_LABELS = {
    "LLM": "🧠 LLM",
    "Фото": "🖼 Фото",
    "Видео": "🎬 Видео",
    "Тренинг": "🎓 Тренинг",
    "Другое": "📦 Другое",
}

_LLM_HINTS = (
    "llm", "chat", "router", "claude", "haiku", "sonnet", "opus",
    "vision", "grok", "transcri",
)


def categorize_operation(operation: Optional[str]) -> str:
    """Map a raw ``operation`` string (e.g. ``"kling_video"``) to a report category."""
    op = (operation or "").lower()
    if "train" in op:
        return "Тренинг"
    if "video" in op:
        return "Видео"
    if "photo" in op or "flux" in op or "swap" in op:
        return "Фото"
    if any(hint in op for hint in _LLM_HINTS):
        return "LLM"
    return "Другое"


def _entry_date(entry: Dict[str, Any]) -> Optional[date]:
    ts = entry.get("ts", "")
    try:
        return datetime.fromisoformat(ts).date()
    except (ValueError, TypeError):
        return None


def build_summary(
    audit_daily_totals: Dict[str, float],
    operation_entries: List[Dict[str, Any]],
    days: int,
    today: date,
) -> Dict[str, Any]:
    """Build an N-day report ending on ``today``.

    Args:
        audit_daily_totals: ISO-date -> total USD (all users summed), covering
            at least both the current and the equal-length preceding period
            (i.e. ``[today - 2*days + 1, today]``). Missing dates are treated
            as ``0.0``. Authoritative — see module docstring.
        operation_entries: Raw ``block_m_common`` expense entries
            (``{"ts", "operation", "cost_usd"}``). Best-effort — see module
            docstring. Only entries within the *current* period are used;
            there is no previous-period comparison for this breakdown.
        days: Report window length. Clamped to >= 1.
        today: The report's "as of" date.
    """
    days = max(1, int(days))
    period_end = today
    period_start = today - timedelta(days=days - 1)
    prev_end = period_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)

    day_list = []
    period_total = 0.0
    d = period_start
    while d <= period_end:
        iso = d.isoformat()
        cost = round(float(audit_daily_totals.get(iso, 0.0) or 0.0), 2)
        day_list.append({"date": iso, "cost_usd": cost})
        period_total += cost
        d += timedelta(days=1)

    prev_total = 0.0
    d = prev_start
    while d <= prev_end:
        prev_total += float(audit_daily_totals.get(d.isoformat(), 0.0) or 0.0)
        d += timedelta(days=1)

    by_category: Dict[str, float] = {c: 0.0 for c in CATEGORIES}
    by_operation: Dict[str, float] = {}
    for entry in operation_entries:
        d = _entry_date(entry)
        if d is None or not (period_start <= d <= period_end):
            continue
        cost = float(entry.get("cost_usd", 0.0) or 0.0)
        op = entry.get("operation") or "unknown"
        by_category[categorize_operation(op)] += cost
        by_operation[op] = by_operation.get(op, 0.0) + cost

    top_operations = [
        {"operation": op, "cost_usd": round(cost, 2)}
        for op, cost in sorted(by_operation.items(), key=lambda kv: -kv[1])[:5]
    ]

    delta = round(period_total, 2) - round(prev_total, 2)
    delta_pct = (delta / prev_total * 100.0) if prev_total > 0 else None

    return {
        "days": days,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "period_total": round(period_total, 2),
        "prev_total": round(prev_total, 2),
        "delta": round(delta, 2),
        "delta_pct": delta_pct,
        "by_day": day_list,
        "by_category": {k: round(v, 2) for k, v in by_category.items()},
        "top_operations": top_operations,
    }


def format_costs_message(summary: Dict[str, Any]) -> str:
    """Render :func:`build_summary`'s output as a compact Russian Telegram message."""
    lines = [
        f"💰 Траты за {summary['days']}д "
        f"({summary['period_start']} — {summary['period_end']}):",
        "",
        "По дням:",
    ]
    for row in summary["by_day"]:
        lines.append(f"  {row['date']}: ${row['cost_usd']:.2f}")
    lines.append("")

    prev = summary["prev_total"]
    delta = summary["delta"]
    sign = "+" if delta >= 0 else ""
    if summary["delta_pct"] is not None:
        cmp_line = (
            f"пред. период: ${prev:.2f} ({sign}{delta:.2f} / {sign}{summary['delta_pct']:.0f}%)"
        )
    else:
        cmp_line = f"пред. период: ${prev:.2f} ({sign}{delta:.2f})"
    lines.append(f"Итого: ${summary['period_total']:.2f} — {cmp_line}")
    lines.append("")

    any_cat = any(v > 0.0 for v in summary["by_category"].values())
    lines.append("По категориям (частичные данные*):")
    if any_cat:
        for cat in CATEGORIES:
            amount = summary["by_category"].get(cat, 0.0)
            if amount <= 0.0:
                continue
            lines.append(f"  {_CATEGORY_LABELS[cat]}: ${amount:.2f}")
    else:
        lines.append("  нет данных")
    lines.append("")

    if summary["top_operations"]:
        lines.append("🏆 Топ-5 операций (частичные данные*):")
        for i, row in enumerate(summary["top_operations"], start=1):
            lines.append(f"  {i}. {row['operation']} — ${row['cost_usd']:.2f}")
        lines.append("")

    lines.append(
        "* категории/топ-операций покрывают только детально залогированные "
        "операции — могут не сходиться с «Итого»."
    )
    return "\n".join(lines)
