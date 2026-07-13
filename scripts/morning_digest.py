# -*- coding: utf-8 -*-
"""Утренний дайджест Jarvis (JarvisMorningDigest scheduled task, daily 09:00).

Собирает и шлёт в Telegram одно сообщение-сводку:
  1) IG-аккаунты (``state/ig_accounts.json``) — подписчики/посты через
     ``InstagramAPI.get_profile`` (read-only, $0) + дельта за сутки
     (``app.services.daily_metrics``, хранит вчерашний замер).
  2) Здоровье: бот жив (heartbeat age из ``state/bot_heartbeat.txt``),
     backend жив (``system_watchdog.check_backend``), возраст IG-токенов
     (дней до истечения, long-lived ~60д).
  3) Вчерашние траты по категориям (``block_m_common.CostTracker``,
     ``state/personas/expenses.jsonl``).
  4) Anthropic credit-balance canary (``devtask.preflight.preflight_credit_check``
     — тот же $0-механизм, что уже используется перед каждым спавном CC).

Fail-closed по каждому источнику независимо: недоступный источник -> строчка
"⚠️ N/A" в этой секции (см. ``app.services.morning_digest``), дайджест всё
равно уходит одним сообщением. Standalone (как ig_token_refresh.py): не
зависит от живого процесса бота, шлёт напрямую через Bot API.

    python scripts/morning_digest.py
"""
from __future__ import annotations

import json
import logging
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

logger = logging.getLogger("jarvis.morning_digest")

HEARTBEAT_FILE = _ROOT / "state" / "bot_heartbeat.txt"
TOKEN_LIFETIME_DAYS = 60.0
_TG_TIMEOUT = 20


def send_telegram(text: str) -> bool:
    """Best-effort admin notification via the Bot API. Never raises.

    Standalone job (see module docstring) — consults the shared isolation
    guard itself, same as ``ig_token_refresh.send_telegram``.
    """
    import os
    from app.core.notify_isolation import telegram_send_blocked
    if telegram_send_blocked():
        logger.info("morning_digest: telegram send suppressed under test isolation")
        return False
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.warning("morning_digest: no TELEGRAM_BOT_TOKEN/CHAT_ID — skipping notify")
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
        with urllib.request.urlopen(req, timeout=_TG_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8")).get("ok", False)
    except Exception as exc:
        logger.warning("morning_digest: telegram notify failed: %s", exc)
        return False


# ── section gatherers (each fail-closed independently) ──────────────────────


def gather_ig_accounts() -> List[Dict[str, Any]]:
    """Per-account followers/media_count + delta, or ``error`` on failure."""
    from app.services import ig_accounts as _iga
    from app.services import daily_metrics
    from app.services.ig_stats import PROFILE_FIELDS
    from app.services.instagram_api import InstagramAPI

    _iga.ensure_migrated()
    accounts = _iga.list_accounts()
    out: List[Dict[str, Any]] = []
    for key, acct in accounts.items():
        username = acct.get("username") or key
        try:
            api = InstagramAPI(account_key=key)
            profile = api.get_profile(fields=PROFILE_FIELDS)
            followers = profile.get("followers_count")
            media_count = profile.get("media_count")
            delta = daily_metrics.record_and_diff(key, followers, media_count)
            out.append({
                "account_key": key,
                "username": profile.get("username") or username,
                "followers": followers,
                "media_count": media_count,
                "followers_delta": delta["followers_delta"],
                "media_delta": delta["media_delta"],
            })
        except Exception as exc:
            logger.warning("morning_digest: IG profile fetch failed for %s: %s", key, exc)
            out.append({"account_key": key, "username": username, "error": str(exc)})
    return out


def gather_token_ages(raw_accounts: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Days-to-expiry per account from ``token_refreshed_at`` (~60d lifetime)."""
    out: List[Dict[str, Any]] = []
    now = time.time()
    for key, acct in raw_accounts.items():
        ts = acct.get("token_refreshed_at")
        if ts is None:
            out.append({"account_key": key, "days_left": None, "error": "no timestamp"})
            continue
        try:
            age_days = (now - float(ts)) / 86400.0
        except (TypeError, ValueError):
            out.append({"account_key": key, "days_left": None, "error": "bad timestamp"})
            continue
        out.append({"account_key": key, "days_left": TOKEN_LIFETIME_DAYS - age_days})
    return out


def gather_bot_health() -> Tuple[Optional[bool], Optional[float]]:
    """``(alive, heartbeat_age_sec)`` — ``(None, None)`` if unreadable."""
    try:
        from app.services import system_watchdog as wd
        if not HEARTBEAT_FILE.exists():
            return None, None
        last_beat = int(HEARTBEAT_FILE.read_text(encoding="utf-8").strip())
        age = time.time() - last_beat
        alive = age < wd.heartbeat_stale_sec()
        return alive, age
    except Exception as exc:
        logger.warning("morning_digest: bot heartbeat check failed: %s", exc)
        return None, None


def gather_backend_ok() -> Optional[bool]:
    try:
        from app.services.system_watchdog import check_backend
        return bool(check_backend().get("ok"))
    except Exception as exc:
        logger.warning("morning_digest: backend health check failed: %s", exc)
        return None


def gather_costs_yesterday() -> Optional[Dict[str, float]]:
    """Yesterday's (UTC calendar day) spend by category, or ``None`` on failure."""
    import asyncio
    try:
        from app.services.block_m_common.cost_tracker import CostTracker
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        tracker = CostTracker()
        return asyncio.run(tracker.get_costs_by_day(yesterday))
    except Exception as exc:
        logger.warning("morning_digest: cost tracker read failed: %s", exc)
        return None


def gather_balance_ok() -> Optional[bool]:
    """Anthropic credit-balance canary — same $0 mechanism as devtask preflight."""
    try:
        from app.services.devtask.preflight import preflight_credit_check
        return bool(preflight_credit_check().get("ok"))
    except Exception as exc:
        logger.warning("morning_digest: balance canary failed: %s", exc)
        return None


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    try:
        import app.env_bootstrap  # noqa: F401  side-effect: loads .env
    except Exception as exc:
        logger.error("morning_digest: env bootstrap failed: %s", exc)

    from app.services import ig_accounts as _iga
    from app.services.morning_digest import build_digest_text

    accounts = gather_ig_accounts()
    raw_accounts = _iga.list_accounts()
    token_ages = gather_token_ages(raw_accounts)
    bot_alive, bot_age = gather_bot_health()
    backend_ok = gather_backend_ok()
    costs = gather_costs_yesterday()
    balance_ok = gather_balance_ok()

    text = build_digest_text(
        date_str=datetime.now().strftime("%Y-%m-%d"),
        accounts=accounts,
        bot_alive=bot_alive,
        bot_heartbeat_age_sec=bot_age,
        backend_ok=backend_ok,
        token_ages=token_ages,
        costs_by_operation=costs,
        balance_ok=balance_ok,
    )

    sent = send_telegram(text)
    if not sent:
        logger.warning("morning_digest: telegram send failed/suppressed")
    logger.info("morning_digest: run complete (sent=%s)", sent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
