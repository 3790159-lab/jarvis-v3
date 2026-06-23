# app/services/auth/users_store.py
# -*- coding: utf-8 -*-
"""Мульти-юзер хранилище: роли, дневные лимиты, статусы, pending-запросы доступа.

Состояние — единый JSON `state/users.json` (override env `JARVIS_USERS_FILE`).
Запись атомарная (`.tmp` + `os.replace`), in-process RLock сериализует обновления —
тот же контракт, что в `audit/cost_tracker`. Время — Kyiv TZ, чтобы дневной сброс
лимита совпадал с дневными бакетами cost_tracker.

Bootstrap-админ резолвится ENV-FIRST (`JARVIS_ADMIN_USER_ID`): он admin даже без
записи в файле, и файл не может его понизить/удалить — защита от самоблока.

Это хранилище НЕ дублирует траты — потраченное берётся из `audit/cost_tracker`.
Здесь только лимит и override-кредит «прощения» (`/admin_resetlimit`).

Форма состояния::

    {
      "users": {
        "555": {"role": "friend", "username": "petya", "status": "active",
                 "daily_limit_usd": 5.0, "added_by": "111",
                 "added_at": "2026-06-23T13:30:00+03:00",
                 "reset": {"date": "2026-06-23", "credit_usd": 3.40}}
      },
      "pending": {
        "777": {"username": "stranger",
                 "first_request_at": "2026-06-23T14:00:00+03:00",
                 "request_count": 2}
      }
    }
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.auth.whitelist import load_admin_user_id

logger = logging.getLogger(__name__)

KYIV_TZ = timezone(timedelta(hours=3))
DEFAULT_FRIEND_LIMIT_USD: float = 5.0

_DEFAULT_STATE_FILE = Path("state") / "users.json"
_LOCK = threading.RLock()


def _state_file() -> Path:
    raw = os.getenv("JARVIS_USERS_FILE", "").strip()
    return Path(raw) if raw else _DEFAULT_STATE_FILE


def _now() -> datetime:
    return datetime.now(KYIV_TZ)


def _load() -> Dict[str, Any]:
    f = _state_file()
    if not f.exists():
        return {"users": {}, "pending": {}}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("users_store: state unreadable (%s); starting fresh", exc)
        return {"users": {}, "pending": {}}
    if not isinstance(data, dict):
        return {"users": {}, "pending": {}}
    data.setdefault("users", {})
    data.setdefault("pending", {})
    return data


def _save_atomic(state: Dict[str, Any]) -> None:
    f = _state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)


# ── role / membership ────────────────────────────────────────────────────────


def get_role(user_id: int) -> Optional[str]:
    """Вернуть 'admin' | 'friend' | None. ENV-admin резолвится первым (bootstrap)."""
    admin_env = load_admin_user_id()
    if admin_env is not None and int(user_id) == admin_env:
        return "admin"
    with _LOCK:
        rec = _load()["users"].get(str(user_id))
    if rec and rec.get("status") == "active":
        return rec.get("role")
    return None


def is_member(user_id: int) -> bool:
    return get_role(user_id) is not None


def has_members() -> bool:
    with _LOCK:
        return bool(_load()["users"])


def get_limit(user_id: int) -> Optional[float]:
    """Дневной лимит в $; None = безлимит (admin). Friend без записи → None."""
    if get_role(user_id) == "admin":
        return None
    with _LOCK:
        rec = _load()["users"].get(str(user_id))
    if not rec:
        return None
    lim = rec.get("daily_limit_usd")
    return float(lim) if lim is not None else None


# ── mutations ────────────────────────────────────────────────────────────────


def add_friend(
    user_id: int, username: Optional[str], *, added_by: str,
    limit_usd: float = DEFAULT_FRIEND_LIMIT_USD,
) -> None:
    with _LOCK:
        state = _load()
        state["users"][str(user_id)] = {
            "role": "friend",
            "username": username,
            "status": "active",
            "daily_limit_usd": float(limit_usd),
            "added_by": str(added_by),
            "added_at": _now().isoformat(),
        }
        state["pending"].pop(str(user_id), None)
        _save_atomic(state)


def set_limit(user_id: int, limit_usd: float) -> bool:
    with _LOCK:
        state = _load()
        rec = state["users"].get(str(user_id))
        if rec is None:
            return False
        rec["daily_limit_usd"] = float(limit_usd)
        _save_atomic(state)
        return True


def set_status(user_id: int, status: str) -> bool:
    with _LOCK:
        state = _load()
        rec = state["users"].get(str(user_id))
        if rec is None:
            return False
        rec["status"] = status
        _save_atomic(state)
        return True


def record_reset(user_id: int, *, spent_today: float, when: Optional[datetime] = None) -> bool:
    """Override-«прощение»: запомнить кредит = текущие траты дня (история цела)."""
    w = when or _now()
    with _LOCK:
        state = _load()
        rec = state["users"].get(str(user_id))
        if rec is None:
            return False
        rec["reset"] = {"date": w.strftime("%Y-%m-%d"), "credit_usd": float(spent_today)}
        _save_atomic(state)
        return True


def effective_spent(user_id: int, *, spent_today: float, when: Optional[datetime] = None) -> float:
    """Траты за вычетом override-кредита, если он за сегодня."""
    w = when or _now()
    with _LOCK:
        rec = _load()["users"].get(str(user_id))
    if rec:
        reset = rec.get("reset") or {}
        if reset.get("date") == w.strftime("%Y-%m-%d"):
            return max(0.0, float(spent_today) - float(reset.get("credit_usd", 0.0)))
    return float(spent_today)


# ── pending access requests ──────────────────────────────────────────────────


def add_pending(user_id: int, username: Optional[str]) -> bool:
    """Зарегистрировать запрос доступа. True если НОВЫЙ (повод уведомить админа)."""
    with _LOCK:
        state = _load()
        key = str(user_id)
        existing = state["pending"].get(key)
        if existing is None:
            state["pending"][key] = {
                "username": username,
                "first_request_at": _now().isoformat(),
                "request_count": 1,
            }
            _save_atomic(state)
            return True
        existing["request_count"] = int(existing.get("request_count", 1)) + 1
        if username:
            existing["username"] = username
        _save_atomic(state)
        return False


def pop_pending(user_id: int) -> Optional[Dict[str, Any]]:
    with _LOCK:
        state = _load()
        rec = state["pending"].pop(str(user_id), None)
        if rec is not None:
            _save_atomic(state)
        return rec


# ── read ─────────────────────────────────────────────────────────────────────


def list_users() -> List[Dict[str, Any]]:
    with _LOCK:
        users = dict(_load()["users"])
    return [{"user_id": k, **v} for k, v in users.items()]


def list_pending() -> List[Dict[str, Any]]:
    with _LOCK:
        pend = dict(_load()["pending"])
    return [{"user_id": k, **v} for k, v in pend.items()]
