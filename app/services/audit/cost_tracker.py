# -*- coding: utf-8 -*-
"""Per-user cost tracking — daily / monthly / all-time spend, pure visibility.

State lives in a single JSON file at ``state/cost_tracking.json`` (override
with ``JARVIS_COST_FILE`` for tests). Every completed batch calls
:func:`record_cost`; users see their own spend via ``/my_stats`` and the admin
sees everyone via ``/admin_costs``. There is **no** hard cap — this module only
reports, it never blocks a run.

State shape::

    {
      "users": {
        "237616472": {
          "username": "daniil",
          "daily":   {"2026-05-27": 4.20, "2026-05-28": 1.85},
          "monthly": {"2026-05": 6.05},
          "all_time": 6.05,
          "last_seen": "2026-05-28T15:30:00+03:00"
        }
      }
    }

All timestamps are in Kyiv time (EEST = UTC+3 in summer), matching the bot's
operating timezone, so day/month rollovers happen at local midnight.

Writes are atomic (write to ``.tmp`` then :func:`os.replace`) so a crash mid
write can never corrupt the file. An in-process lock serialises concurrent
:func:`record_cost` calls so threaded batch completions don't lose updates;
across *processes* the semantics are last-write-wins, which is acceptable for a
visibility-only ledger.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Kyiv summer time. The bot runs on UTC+3; rollovers use local midnight.
KYIV_TZ = timezone(timedelta(hours=3))

_DEFAULT_STATE_FILE = Path("state") / "cost_tracking.json"

# Serialises record_cost across threads so read-modify-write doesn't drop
# concurrent updates within a single process.
_LOCK = threading.RLock()


def _state_file() -> Path:
    raw = os.getenv("JARVIS_COST_FILE", "").strip()
    return Path(raw) if raw else _DEFAULT_STATE_FILE


def _now() -> datetime:
    return datetime.now(KYIV_TZ)


def _load_state() -> Dict[str, Any]:
    """Read the full state file, returning an empty skeleton if absent/corrupt."""
    f = _state_file()
    if not f.exists():
        return {"users": {}}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("cost_tracker: state unreadable (%s); starting fresh", exc)
        return {"users": {}}
    if not isinstance(data, dict) or not isinstance(data.get("users"), dict):
        return {"users": {}}
    return data


def _save_state_atomic(state: Dict[str, Any]) -> None:
    """Write ``state`` to a ``.tmp`` sibling then atomically rename into place."""
    f = _state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, f)


# ── update API ───────────────────────────────────────────────────────────────


def record_cost(
    user_id: Any,
    username: Optional[str],
    amount_usd: float,
    timestamp: Optional[datetime] = None,
) -> None:
    """Add ``amount_usd`` to the user's daily / monthly / all-time totals.

    Creates the user record on first sight, refreshes ``username`` and
    ``last_seen``. ``timestamp`` defaults to *now* in Kyiv time; if supplied it
    determines which day/month bucket the cost lands in.

    Concurrency: serialised in-process via :data:`_LOCK`; last-write-wins
    across processes (acceptable for a visibility-only ledger).
    """
    when = timestamp or _now()
    amount = float(amount_usd)
    key = str(user_id)
    day = when.strftime("%Y-%m-%d")
    month = when.strftime("%Y-%m")

    with _LOCK:
        state = _load_state()
        users = state.setdefault("users", {})
        user = users.get(key)
        if user is None:
            user = {
                "username": username,
                "daily": {},
                "monthly": {},
                "all_time": 0.0,
                "last_seen": when.isoformat(),
            }
            users[key] = user
        if username:
            user["username"] = username
        daily = user.setdefault("daily", {})
        monthly = user.setdefault("monthly", {})
        daily[day] = round(float(daily.get(day, 0.0)) + amount, 2)
        monthly[month] = round(float(monthly.get(month, 0.0)) + amount, 2)
        user["all_time"] = round(float(user.get("all_time", 0.0)) + amount, 2)
        user["last_seen"] = when.isoformat()
        _save_state_atomic(state)


# ── read API ─────────────────────────────────────────────────────────────────


def _first_seen(user: Dict[str, Any]) -> Optional[str]:
    """Earliest dated daily bucket → 'YYYY-MM-DD', or None if no usage."""
    daily = user.get("daily") or {}
    if not daily:
        return None
    return min(daily.keys())


def _fmt_last_seen(iso: Optional[str]) -> Optional[str]:
    """'YYYY-MM-DDTHH:MM:SS+03:00' → 'YYYY-MM-DD HH:MM', best-effort."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return dt.strftime("%Y-%m-%d %H:%M")


def get_user_stats(user_id: Any, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Return a snapshot for ``user_id``; all-zero fields for an unknown user."""
    when = now or _now()
    day = when.strftime("%Y-%m-%d")
    month = when.strftime("%Y-%m")
    key = str(user_id)

    with _LOCK:
        state = _load_state()
        user = state.get("users", {}).get(key)

    if user is None:
        return {
            "user_id": key,
            "username": None,
            "today": 0.0,
            "month": 0.0,
            "all_time": 0.0,
            "first_seen": None,
            "last_seen": None,
        }

    daily = user.get("daily") or {}
    monthly = user.get("monthly") or {}
    return {
        "user_id": key,
        "username": user.get("username"),
        "today": float(daily.get(day, 0.0)),
        "month": float(monthly.get(month, 0.0)),
        "all_time": float(user.get("all_time", 0.0)),
        "first_seen": _first_seen(user),
        "last_seen": _fmt_last_seen(user.get("last_seen")),
    }


def get_admin_overview(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Aggregate today / month / all-time across every user, plus per-user rows."""
    when = now or _now()
    day = when.strftime("%Y-%m-%d")
    month = when.strftime("%Y-%m")

    with _LOCK:
        state = _load_state()
        users = dict(state.get("users", {}))

    rows = []
    today_total = month_total = all_time_total = 0.0
    for key, user in users.items():
        daily = user.get("daily") or {}
        monthly = user.get("monthly") or {}
        today = float(daily.get(day, 0.0))
        this_month = float(monthly.get(month, 0.0))
        all_time = float(user.get("all_time", 0.0))
        rows.append({
            "user_id": key,
            "username": user.get("username"),
            "today": today,
            "month": this_month,
            "all_time": all_time,
        })
        today_total += today
        month_total += this_month
        all_time_total += all_time

    # Biggest spenders first — most useful ordering for the operator.
    rows.sort(key=lambda r: r["all_time"], reverse=True)
    return {
        "users": rows,
        "today_total": round(today_total, 2),
        "month_total": round(month_total, 2),
        "all_time_total": round(all_time_total, 2),
        "active_users": len(rows),
    }


def get_costs_range(start: date, end: date) -> Dict[str, float]:
    """Per-day totals (every user summed) for the ``[start, end]`` window, inclusive.

    Days with no recorded spend are present with ``0.0`` so callers get a
    complete calendar, not a sparse dict. Used by ``/costs`` to build an
    arbitrary N-day report from the live per-user ledger.
    """
    with _LOCK:
        state = _load_state()
        users = state.get("users", {})

    totals: Dict[str, float] = {}
    d = start
    while d <= end:
        totals[d.isoformat()] = 0.0
        d += timedelta(days=1)

    for user in users.values():
        daily = user.get("daily") or {}
        for day_str, amount in daily.items():
            if day_str in totals:
                totals[day_str] += float(amount)

    return {k: round(v, 2) for k, v in totals.items()}


# ── message formatting (Russian) ─────────────────────────────────────────────


def format_my_stats_message(
    user_id: Any, username: Optional[str], now: Optional[datetime] = None
) -> str:
    """Render the ``/my_stats`` reply for ``user_id`` in Russian."""
    s = get_user_stats(user_id, now=now)
    if s["all_time"] <= 0.0:
        return (
            "📊 Твоя статистика: пока пусто. "
            "Запусти /swapbatch_source чтобы начать."
        )
    return "\n".join([
        "📊 Твоя статистика:",
        f"💰 Сегодня:     ${s['today']:.2f}",
        f"💰 Этот месяц:  ${s['month']:.2f}",
        f"💰 Всего:       ${s['all_time']:.2f}",
        f"📅 Первое использование: {s['first_seen']}",
        f"📅 Последнее:   {s['last_seen']}",
    ])


def format_admin_costs_message(now: Optional[datetime] = None) -> str:
    """Render the ``/admin_costs`` per-user table + totals in Russian."""
    o = get_admin_overview(now=now)
    lines = ["📊 Статистика по пользователям (сегодня / месяц / всего):", ""]
    for u in o["users"]:
        name = u["username"] or "(без username)"
        lines.append(f"👤 {name} ({u['user_id']})")
        lines.append(
            f"   ${u['today']:.2f} / ${u['month']:.2f} / ${u['all_time']:.2f}"
        )
        lines.append("")
    lines.extend([
        f"💰 Сегодня всего:    ${o['today_total']:.2f}",
        f"💰 Этот месяц:       ${o['month_total']:.2f}",
        f"💰 За всё время:     ${o['all_time_total']:.2f}",
        f"👥 Активных users:   {o['active_users']}",
    ])
    return "\n".join(lines)
