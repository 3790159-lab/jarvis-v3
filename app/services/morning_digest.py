# -*- coding: utf-8 -*-
"""Утренний дайджест Jarvis — pure text-formatting logic, $0, no network.

Assembles the one-message Telegram summary from already-gathered facts
(see ``scripts/morning_digest.py`` for the network/IO orchestration). Every
section is fail-closed: a missing/errored input renders "⚠️ N/A" for that
line instead of crashing or fabricating a number — the digest as a whole
always sends, per source unreliability.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = [
    "format_ig_section",
    "format_health_section",
    "format_costs_section",
    "format_balance_section",
    "build_digest_text",
]


def _fmt_metric(value: Optional[int], delta: Optional[int]) -> str:
    if value is None:
        return "н/д"
    if delta is None:
        return str(value)
    sign = "+" if delta >= 0 else ""
    return f"{value} ({sign}{delta})"


def format_ig_section(accounts: List[Dict[str, Any]]) -> List[str]:
    """One line per IG account: followers/media + delta, or "⚠️ N/A" on error."""
    lines = ["📸 IG-аккаунты:"]
    if not accounts:
        lines.append("  нет аккаунтов")
        return lines
    for a in accounts:
        name = a.get("username") or a.get("account_key") or "?"
        if a.get("error"):
            lines.append(f"👤 @{name}: ⚠️ N/A ({a['error']})")
            continue
        followers_s = _fmt_metric(a.get("followers"), a.get("followers_delta"))
        media_s = _fmt_metric(a.get("media_count"), a.get("media_delta"))
        lines.append(f"👤 @{name}: 👥 {followers_s} 🖼 {media_s}")
    return lines


def format_health_section(
    bot_alive: Optional[bool],
    bot_heartbeat_age_sec: Optional[float],
    backend_ok: Optional[bool],
    token_ages: List[Dict[str, Any]],
) -> List[str]:
    """Bot heartbeat age, backend reachability, IG token days-to-expiry."""
    lines = ["🩺 Здоровье:"]

    if bot_heartbeat_age_sec is None:
        lines.append("🤖 Бот: ⚠️ N/A")
    elif bot_alive:
        lines.append(f"🤖 Бот: ✅ (heartbeat {int(bot_heartbeat_age_sec)}с назад)")
    else:
        lines.append(f"🤖 Бот: ⚠️ heartbeat протух ({int(bot_heartbeat_age_sec)}с назад)")

    if backend_ok is None:
        lines.append("🖥 Backend: ⚠️ N/A")
    elif backend_ok:
        lines.append("🖥 Backend: ✅")
    else:
        lines.append("🖥 Backend: ⚠️ не отвечает")

    for t in token_ages:
        name = t.get("account_key") or "?"
        days_left = t.get("days_left")
        if t.get("error") or days_left is None:
            lines.append(f"🔑 IG-токен {name}: ⚠️ N/A")
        else:
            lines.append(f"🔑 IG-токен {name}: {days_left:.0f}д до истечения")

    return lines


def format_costs_section(by_operation: Optional[Dict[str, float]]) -> List[str]:
    """Yesterday's spend grouped by operation/category, plus a total line."""
    lines = ["💰 Траты вчера:"]
    if by_operation is None:
        lines.append("  ⚠️ N/A")
        return lines
    if not by_operation:
        lines.append("  нет трат")
        return lines
    total = 0.0
    for op, cost in sorted(by_operation.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {op}: ${cost:.2f}")
        total += cost
    lines.append(f"  Итого: ${total:.2f}")
    return lines


def format_balance_section(balance_ok: Optional[bool]) -> str:
    """Anthropic credit-balance canary flag (see devtask.preflight)."""
    if balance_ok is None:
        return "💳 Anthropic баланс: ⚠️ N/A"
    if balance_ok:
        return "💳 Anthropic баланс: ✅ ok"
    return "💳 Anthropic баланс: ⚠️ НИЗКИЙ — разберись"


def build_digest_text(
    date_str: str,
    accounts: List[Dict[str, Any]],
    bot_alive: Optional[bool],
    bot_heartbeat_age_sec: Optional[float],
    backend_ok: Optional[bool],
    token_ages: List[Dict[str, Any]],
    costs_by_operation: Optional[Dict[str, float]],
    balance_ok: Optional[bool],
) -> str:
    """Assemble the full compact one-message digest."""
    parts = [f"🌅 Утренний дайджест Jarvis — {date_str}", ""]
    parts.extend(format_ig_section(accounts))
    parts.append("")
    parts.extend(format_health_section(bot_alive, bot_heartbeat_age_sec, backend_ok, token_ages))
    parts.append("")
    parts.extend(format_costs_section(costs_by_operation))
    parts.append("")
    parts.append(format_balance_section(balance_ok))
    return "\n".join(parts)
