# «Доступ для друга» (мульти-юзер) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать другу доступ на моём же боте (мульти-юзер) под дневным $-лимитом, с
железной изоляцией от админ-команд/ключей, флоу-запросом доступа через inline-кнопки мне,
и учётом всех платных трат друга в per-user леджере — не сломав одно-пользовательское поведение.

**Architecture:** Новый чистый модуль `app/services/auth/users_store.py` хранит роли/лимиты/статусы/pending
в `state/users.json` (атомарная запись, Kyiv TZ, env-first резолюция admin). Существующий
`whitelist.is_allowed` расширяется членством из users.json. Разбросанные по боту гейты
`chat_id == ALLOWED_CHAT_ID` заменяются на role-проверки (`_is_member`/`_is_admin`); текстовый
диспетчер `handle()` получает **allowlist команд для friend** (default-deny). Лимит-гейт
срабатывает ДО платного движкового вызова, читая потраченное из `audit/cost_tracker`. Траты
всех платных путей друга сводятся в тот же per-user леджер (аудит покрытия). Activity-лог —
поверх существующего `audit_logger`.

**Tech Stack:** Python 3.11, stdlib (json/os/threading/datetime), pytest, Telegram Bot API
(inline keyboards, callback_query). Без новых зависимостей.

**Критичные страховки (на каждой фазе проверяемы):**
- **БЕЗОПАСНОСТЬ / default-deny:** friend НЕ достаёт admin/restart/keys/чужую статистику —
  `handle()` allowlist + admin-гейт на привилегированных callback/командах.
- **Лимит ДО траты:** гейт перед движковым вызовом (animate-батч, одиночный /animate, свап).
- **Аудит покрытия трат:** все платные пути friend зовут `audit.cost_tracker.record_cost`.
- **Bootstrap-админ env-first:** `JARVIS_ADMIN_USER_ID` → роль admin даже без записи в users.json;
  users.json не может понизить/удалить bootstrap-админа.
- **Фаза 7 (живой smoke) = ⛔ СТОП, зову пользователя.**

**Ключевые факты кодовой базы (проверено 2026-06-23):**
- Whitelist: `app/services/auth/whitelist.py` — `is_allowed(user_id)`, `load_admin_user_id()`
  (env `JARVIS_ADMIN_USER_ID`), `load_allowed_user_ids()` (env `JARVIS_ALLOWED_USER_IDS` CSV),
  `REJECT_MESSAGE`. Lazy env read. `auth/__init__.py` пуст → прямой импорт модулей.
- Per-user леджер: `app/services/audit/cost_tracker.py` — `record_cost(user_id, username, amount)`,
  `get_user_stats(user_id) -> {today, month, all_time, ...}` (Kyiv TZ, `state/cost_tracking.json`,
  override файла env `JARVIS_COST_FILE`). **Без капа — только видимость.**
- Audit-лог: `app/services/audit/audit_logger.py` — `audit_event(user_id, username, chat_id, event, details)`;
  дневные файлы `state/audit/YYYY-MM-DD.jsonl` (env `JARVIS_AUDIT_DIR`); `_today_file`, `_audit_dir`.
- Бот: `tools/jarvis_smart_telegram_control.py`. Импорты: `_whitelist` (стр. 33), `_audit` (стр. 34),
  `_cost` (стр. 35). `ALLOWED_CHAT_ID` (стр. 38). `send(chat_id, text, reply_markup=None)` (стр. 143).
  `answer_callback_query(cq_id, text)` (стр. 225). `send_with_keyboard(chat_id, text, inline_keyboard)` (стр. 256).
- Гейт двери: `_whitelist_gate(upd)` (стр. 5700), зовётся в `process_update` (стр. 6293).
  Reject-ветка шлёт `REJECT_MESSAGE` + audit `whitelist_rejected`.
- Текстовый диспетчер: `handle(chat_id, text)` (стр. 5541) — жёсткий гейт `!= ALLOWED_CHAT_ID`
  → "Access denied". Зовёт `handle_command()` (стр. 4521), где живут `/animate` (4972),
  `/swapbatch_*` (4975-…), `/persona_*` (4962-4969), а также admin-команды (restart/design/cowork/logs).
- Callback-диспетчер: `handle_callback_query(cq, state)` (стр. 2755) — ветки `sbeng:` (2773),
  `sbq:` (2793), `anim:` (2806), `mesh:` (2818+), task/feedback/confirmation. Гейт вызова в
  `process_update` (стр. 6306): `cq_chat_id == ALLOWED_CHAT_ID or msg_chat_id == ALLOWED_CHAT_ID`.
- Message-интерсепторы в `process_update` гейтятся на `chat_id == ALLOWED_CHAT_ID`:
  persona_video (6326), video_face_swap (6330), animate-photo (6335), swapbatch-photo (6344),
  swapbatch-text (6354), files (6364). Плюс media-group flush `_flush_media_group` (1351).
- Свап+animate-батч УЖЕ пишут в леджер: `face_swap_handler.py:747` (animate), `:778`
  (`_bill_completed_videos`). Одиночный `/animate` (`_animate_run_single`, бот стр. 1137) —
  **НЕ пишет** record_cost (только текст стоимости). `_video_face_swap_run` (бот стр. 574) — проверить.
- Тест-паттерн интеграции бота: `tests/test_bot_whitelist_integration.py` — `_get_mod()` грузит
  модуль по пути, патчит `send`/`handle`/`handle_callback_query`, гоняет `process_update`.
- Запуск тестов: из `C:\jarvis`, `python -m pytest tests/<file>::<test> -v`.

---

## Структура файлов

| Файл | Ответственность | Действие |
|---|---|---|
| `app/services/auth/users_store.py` | Хранилище ролей/лимитов/статусов/pending в `state/users.json`; резолюция роли env-first; лимит-гейт-расчёт | Create |
| `tests/test_users_store.py` | Юниты на users_store (чистый модуль) | Create |
| `app/services/auth/whitelist.py` | `is_allowed` учитывает членство users.json | Modify |
| `tests/test_bot_whitelist_integration.py` | Существующий open-mode тест изолировать через `JARVIS_USERS_FILE` | Modify |
| `app/services/auth/access_control.py` | Чистый лимит-гейт: `check_limit(user_id, est_usd) -> (allowed, reason)` поверх cost_tracker + users_store | Create |
| `tests/test_access_control.py` | Юниты на лимит-гейт | Create |
| `app/services/audit/audit_logger.py` | + `read_user_activity(user_id, limit)` для `/admin_activity` | Modify |
| `tests/test_audit_activity.py` | Юнит на read_user_activity | Create |
| `tools/jarvis_smart_telegram_control.py` | Проводка: role-гейты, access-флоу с кнопками, лимит-гейт, админ-команды, activity | Modify |
| `tests/test_friend_access_integration.py` | Интеграция через `process_update` (изоляция, access-флоу, лимит) | Create |
| `app/handlers/face_swap_handler.py` | Лимит-гейт перед animate-батчем (опц., если не в бридже) | Modify |

---

## Фаза 1 — `users_store` (хранилище ролей/лимитов)

### Task 1: Модуль `users_store.py` — каркас, атомарная запись, роли env-first

**Files:**
- Create: `app/services/auth/users_store.py`
- Test: `tests/test_users_store.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_users_store.py
# -*- coding: utf-8 -*-
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from app.services.auth import users_store as us

KYIV = timezone(timedelta(hours=3))


@pytest.fixture
def store_env(tmp_path, monkeypatch):
    """Isolate users.json and the admin env for each test."""
    f = tmp_path / "users.json"
    monkeypatch.setenv("JARVIS_USERS_FILE", str(f))
    monkeypatch.delenv("JARVIS_ADMIN_USER_ID", raising=False)
    return f


def test_unknown_user_has_no_role(store_env):
    assert us.get_role(999) is None
    assert us.is_member(999) is False


def test_env_admin_is_admin_without_file(store_env, monkeypatch):
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    assert us.get_role(111) == "admin"
    assert us.is_member(111) is True
    # bootstrap-admin needs NO users.json entry
    assert not store_env.exists() or "111" not in store_env.read_text(encoding="utf-8")


def test_add_friend_persists_with_default_limit(store_env):
    us.add_friend(555, "petya", added_by="111")
    assert us.get_role(555) == "friend"
    assert us.get_limit(555) == us.DEFAULT_FRIEND_LIMIT_USD == 5.0
    data = json.loads(store_env.read_text(encoding="utf-8"))
    assert data["users"]["555"]["status"] == "active"
    assert data["users"]["555"]["added_by"] == "111"


def test_blocked_friend_has_no_role(store_env):
    us.add_friend(555, "petya", added_by="111")
    us.set_status(555, "blocked")
    assert us.get_role(555) is None
    assert us.is_member(555) is False


def test_set_limit_updates(store_env):
    us.add_friend(555, "petya", added_by="111")
    us.set_limit(555, 12.5)
    assert us.get_limit(555) == 12.5


def test_admin_limit_is_none_unlimited(store_env, monkeypatch):
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    assert us.get_limit(111) is None


def test_pending_dedup(store_env):
    assert us.add_pending(777, "stranger") is True   # new
    assert us.add_pending(777, "stranger") is False  # dup, no re-notify
    data = json.loads(store_env.read_text(encoding="utf-8"))
    assert data["pending"]["777"]["request_count"] == 2


def test_pop_pending_returns_and_removes(store_env):
    us.add_pending(777, "stranger")
    rec = us.pop_pending(777)
    assert rec is not None and rec["username"] == "stranger"
    assert us.pop_pending(777) is None


def test_has_members_and_list_users(store_env):
    assert us.has_members() is False
    us.add_friend(555, "petya", added_by="111")
    assert us.has_members() is True
    rows = us.list_users()
    assert any(r["user_id"] == "555" and r["role"] == "friend" for r in rows)


def test_reset_today_credit_only_applies_same_day(store_env):
    us.add_friend(555, "petya", added_by="111")
    today = datetime(2026, 6, 23, 12, 0, tzinfo=KYIV)
    us.record_reset(555, spent_today=3.40, when=today)
    # same day → credit forgives 3.40
    assert us.effective_spent(555, spent_today=4.0, when=today) == pytest.approx(0.60)
    # next day → credit expired
    nextday = datetime(2026, 6, 24, 12, 0, tzinfo=KYIV)
    assert us.effective_spent(555, spent_today=4.0, when=nextday) == pytest.approx(4.0)


def test_atomic_write_uses_replace(store_env, monkeypatch):
    called = {"n": 0}
    real = us.os.replace
    def spy(a, b):
        called["n"] += 1
        return real(a, b)
    monkeypatch.setattr(us.os, "replace", spy)
    us.add_friend(555, "petya", added_by="111")
    assert called["n"] >= 1
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `python -m pytest tests/test_users_store.py -v`
Expected: FAIL (`ModuleNotFoundError: ...users_store`).

- [ ] **Step 3: Реализовать модуль**

```python
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
```

- [ ] **Step 4: Запустить — проходит**

Run: `python -m pytest tests/test_users_store.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/auth/users_store.py tests/test_users_store.py
git commit -m "feat(auth): users_store — roles/limits/status/pending (env-first admin, atomic)"
```

---

## Фаза 2 — Разблокировка friend (реворк `ALLOWED_CHAT_ID`)

### Task 2: `whitelist.is_allowed` учитывает членство users.json

**Files:**
- Modify: `app/services/auth/whitelist.py:72-88`
- Test: `tests/test_users_store.py` (добавить интеграционный блок), `tests/test_bot_whitelist_integration.py:130-143`

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_users_store.py`:
```python
def test_whitelist_allows_active_member(store_env):
    from app.services.auth import whitelist
    us.add_friend(555, "petya", added_by="111")
    assert whitelist.is_allowed(555) is True
    us.set_status(555, "blocked")
    assert whitelist.is_allowed(555) is False


def test_whitelist_open_mode_only_when_nothing_configured(store_env, monkeypatch):
    from app.services.auth import whitelist
    monkeypatch.delenv("JARVIS_ALLOWED_USER_IDS", raising=False)
    # nothing configured + empty users.json → open mode preserved
    assert whitelist.is_allowed(12345) is True
    # once a member exists, open mode is OFF (strangers rejected)
    us.add_friend(555, "petya", added_by="111")
    assert whitelist.is_allowed(12345) is False
```

- [ ] **Step 2: Запустить — падает**

Run: `python -m pytest tests/test_users_store.py -k whitelist -v`
Expected: FAIL (member rejected; open mode still allows stranger after member added).

- [ ] **Step 3: Расширить `is_allowed`**

Заменить тело `is_allowed` в `whitelist.py` на:
```python
def is_allowed(user_id: int) -> bool:
    """Return ``True`` if ``user_id`` should reach the bot's handlers.

    Decision order:

    1. Active member of ``state/users.json`` (incl. env bootstrap-admin) → allow.
    2. ``user_id`` equals env admin → allow (bypass).
    3. ``user_id`` is in env allowed-list → allow.
    4. Nothing configured anywhere (no env, no members) → open mode → allow.
    5. Otherwise → reject.
    """
    # Lazy import avoids a module-load cycle (users_store imports this module).
    try:
        from app.services.auth import users_store
        if users_store.is_member(user_id):
            return True
        has_members = users_store.has_members()
    except Exception:  # noqa: BLE001 - users_store must never harden us into a lockout
        has_members = False

    admin = load_admin_user_id()
    allowed = load_allowed_user_ids()
    if admin is not None and user_id == admin:
        return True
    if user_id in allowed:
        return True
    if admin is None and not allowed and not has_members:
        return True  # open mode (dev): nothing configured at all
    return False
```

- [ ] **Step 4: Изолировать существующий open-mode тест**

В `tests/test_bot_whitelist_integration.py`, в `test_bot_open_mode_allows_anyone` добавить
изоляцию users.json (иначе тест читает реальный `state/users.json`). После строки
`monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "555")` добавить:
```python
    monkeypatch.setenv("JARVIS_USERS_FILE", str(tmp_path / "users.json"))
```
и добавить `tmp_path` в сигнатуру: `def test_bot_open_mode_allows_anyone(monkeypatch, tmp_path):`.

- [ ] **Step 5: Запустить — проходит**

Run: `python -m pytest tests/test_users_store.py tests/test_bot_whitelist_integration.py -v`
Expected: PASS (все).

- [ ] **Step 6: Commit**

```bash
git add app/services/auth/whitelist.py tests/test_users_store.py tests/test_bot_whitelist_integration.py
git commit -m "feat(auth): whitelist honors users.json membership (open mode only when unconfigured)"
```

### Task 3: Role-хелперы в боте + реворк feature-гейтов

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py` (импорт; хелперы; гейты в `process_update`, `handle`, `_flush_media_group`)
- Test: `tests/test_friend_access_integration.py` (новый)

Решение: `handle()` (текстовые команды) — **allowlist для friend** (default-deny); генеративные
интерсепторы/коллбэки — `_is_member`; admin-команды/коллбэки — `_is_admin`.

- [ ] **Step 1: Написать падающий тест (изоляция)**

```python
# tests/test_friend_access_integration.py
# -*- coding: utf-8 -*-
"""Friend-access: process_update enforces roles (isolation, generative allow)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _get_mod():
    name = f"_test_fa_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _text_update(user_id: int, chat_id: int, text: str) -> dict:
    return {"update_id": 1, "message": {
        "from": {"id": user_id, "username": "petya"},
        "chat": {"id": chat_id}, "text": text}}


@pytest.fixture
def friend_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "111")
    monkeypatch.delenv("JARVIS_ALLOWED_USER_IDS", raising=False)
    from app.services.auth import users_store
    users_store.add_friend(555, "petya", added_by="111")
    return tmp_path


def test_friend_blocked_from_admin_command(friend_env):
    mod = _get_mod()
    sent, handled = [], []
    upd = _text_update(555, 555, "/restart_bot")
    with patch.object(mod, "send", lambda c, t, **k: sent.append(t)), \
         patch.object(mod, "handle_command", lambda *a, **k: handled.append(a)):
        mod.handle("555", "/restart_bot")
    assert handled == []  # admin command never dispatched for friend
    assert any("админ" in t.lower() or "access" in t.lower() for t in sent)


def test_friend_allowed_generative_command(friend_env):
    mod = _get_mod()
    handled = []
    with patch.object(mod, "send", lambda c, t, **k: None), \
         patch.object(mod, "handle_command",
                      lambda cid, cmd, q, st: handled.append(cmd)), \
         patch.object(mod, "classify_message",
                      lambda text, st: {"intent": "command",
                                        "command": {"cmd": "/swapbatch_source", "query": ""}}):
        mod.handle("555", "/swapbatch_source")
    assert handled == ["/swapbatch_source"]


def test_admin_runs_everything(friend_env):
    mod = _get_mod()
    handled = []
    with patch.object(mod, "send", lambda c, t, **k: None), \
         patch.object(mod, "handle_command",
                      lambda cid, cmd, q, st: handled.append(cmd)), \
         patch.object(mod, "classify_message",
                      lambda text, st: {"intent": "command",
                                        "command": {"cmd": "/restart_bot", "query": ""}}):
        mod.handle("111", "/restart_bot")
    assert handled == ["/restart_bot"]
```

> ⚠️ Перед реализацией Step 3 — прочитать `handle()` (5541-5621) и узнать ТОЧНУЮ форму,
> которой `classify_message` отдаёт команду (ключ `command`/`cmd`), чтобы извлечь `cmd` для
> allowlist-проверки. Тест выше мокает `classify_message`; реализация должна читать ту же форму.
> Если формат иной — поправить и тест, и извлечение согласованно.

- [ ] **Step 2: Запустить — падает**

Run: `python -m pytest tests/test_friend_access_integration.py -v`
Expected: FAIL (friend получает "Access denied" на всё; нет role-логики).

- [ ] **Step 3: Добавить импорт и хелперы**

В `tools/jarvis_smart_telegram_control.py` после строки 33 (`from app.services.auth import whitelist as _whitelist`) добавить:
```python
from app.services.auth import users_store as _users_store
```

Рядом с `_extract_user_id` (стр. 5673) добавить хелперы и allowlist:
```python
# Команды, доступные роли friend (всё прочее в handle() — только admin).
# Генеративный набор: лицевой своп + анимация + persona (+ video_face_swap идёт
# через interceptor, не команду). Личная статистика. Default-deny.
FRIEND_ALLOWED_COMMANDS: frozenset[str] = frozenset({
    "/animate", "/swapbatch", "/swapbatch_source", "/swapbatch_batch",
    "/swapbatch_go", "/swapbatch_set_quality", "/swapbatch_set_prompt",
    "/swapbatch_set_wardrobe", "/swapbatch_set_engine",
    "/swapbatch_animate_yes", "/swapbatch_animate_go", "/swapbatch_animate_no",
    "/swapbatch_animate_custom", "/swapbatch_status", "/swapbatch_cancel",
    # persona / me-persona (решение пользователя 2026-06-23: friend получает persona)
    "/persona_photo", "/persona_video", "/persona_video_redo", "/persona_redo",
    "/persona_engine", "/persona_batch", "/me_swap_photo", "/me_swap_video",
    "/my_stats", "/start", "/help",
})
# Примечание: video_face_swap (фото+видео пара) идёт через _video_face_swap_intercept,
# уже member-gated в process_update (Task 3 Step 5) — отдельная команда не нужна.

# Префиксы callback_data, разрешённые friend (генеративные кнопки). Остальное — admin.
FRIEND_ALLOWED_CALLBACK_PREFIXES: tuple[str, ...] = ("sbeng:", "sbq:", "anim:")


def _role_for_chat(chat_id) -> Optional[str]:
    """Роль по chat_id (в личке chat_id == user_id; бот одно-чат-на-юзера)."""
    try:
        return _users_store.get_role(int(chat_id))
    except (TypeError, ValueError):
        return None


def _is_member_id(user_id) -> bool:
    try:
        return _users_store.is_member(int(user_id))
    except (TypeError, ValueError):
        return False


def _is_admin_id(user_id) -> bool:
    try:
        return _users_store.get_role(int(user_id)) == "admin"
    except (TypeError, ValueError):
        return False
```

- [ ] **Step 4: Реворк гейта в `handle()`**

В `handle()` (стр. 5542-5544) заменить:
```python
def handle(chat_id: str, text: str) -> None:
    if str(chat_id) != ALLOWED_CHAT_ID:
        send(chat_id, "Access denied.")
        return
```
на:
```python
def handle(chat_id: str, text: str) -> None:
    role = _role_for_chat(chat_id)
    # Backward-compat: legacy single-chat admin still works even без users.json.
    if role is None and str(chat_id) == ALLOWED_CHAT_ID:
        role = "admin"
    if role is None:
        send(chat_id, "Access denied.")
        return
    if role != "admin":
        # friend: только генеративный allowlist (default-deny на всё прочее).
        _cmd = (text or "").strip().split(maxsplit=1)[0].split("@", 1)[0].lower()
        if _cmd not in FRIEND_ALLOWED_COMMANDS:
            send(chat_id, "🚫 Эта команда доступна только администратору.")
            return
```

- [ ] **Step 5: Реворк гейтов в `process_update`**

В `process_update` заменить шесть `chat_id == ALLOWED_CHAT_ID` (стр. 6326, 6330, 6335, 6344, 6354, 6364) и callback-гейт (6306). Для message-веток ввести один раз `_member = _is_member_id(chat_id)` сразу после `chat_id = str(chat.get("id", ""))` (стр. 6319):
```python
    chat_id = str(chat.get("id", ""))
    _member = _is_member_id(chat_id) or chat_id == ALLOWED_CHAT_ID  # legacy admin
```
Затем заменить каждое `chat_id == ALLOWED_CHAT_ID` в этих ветках на `_member`.

Для callback-гейта (стр. 6304-6306) заменить:
```python
        cq_chat_id = str((cq.get("from") or {}).get("id", ""))
        msg_chat_id = str((cq.get("message", {}).get("chat") or {}).get("id", ""))
        if cq_chat_id == ALLOWED_CHAT_ID or msg_chat_id == ALLOWED_CHAT_ID:
```
на:
```python
        cq_uid = (cq.get("from") or {}).get("id")
        cq_chat_id = str(cq_uid or "")
        msg_chat_id = str((cq.get("message", {}).get("chat") or {}).get("id", ""))
        if _is_member_id(cq_uid) or cq_chat_id == ALLOWED_CHAT_ID or msg_chat_id == ALLOWED_CHAT_ID:
```

- [ ] **Step 6: Реворк гейта media-group flush**

В `_flush_media_group` (стр. 1350-1352) заменить:
```python
    chat_id = str(msgs[0].get("chat", {}).get("id", ""))
    if chat_id != ALLOWED_CHAT_ID:
        return
```
на:
```python
    chat_id = str(msgs[0].get("chat", {}).get("id", ""))
    _uid = (msgs[0].get("from") or {}).get("id")
    if not (_is_member_id(_uid) or chat_id == ALLOWED_CHAT_ID):
        return
```

- [ ] **Step 7: Admin-гейт на привилегированных callback в `handle_callback_query`**

В начале `handle_callback_query` (после извлечения `data`, `chat_id`, стр. 2770) добавить
role-проверку: friend допускается только к генеративным префиксам.
```python
    parts = data.split(":")

    # Role-гейт: friend → только генеративные кнопки; админ → всё.
    _cq_uid = (callback_query.get("from") or {}).get("id")
    if not _is_admin_id(_cq_uid) and str(_cq_uid) != ALLOWED_CHAT_ID:
        if not data.startswith(FRIEND_ALLOWED_CALLBACK_PREFIXES):
            answer_callback_query(cq_id, "🚫 Только для администратора")
            return
```

- [ ] **Step 8: Smoke-импорт + тесты**

Run: `python -c "import ast; ast.parse(open(r'tools/jarvis_smart_telegram_control.py', encoding='utf-8').read()); print('ok')"`
Run: `python -m pytest tests/test_friend_access_integration.py tests/test_bot_whitelist_integration.py -v`
Expected: `ok` + PASS.

- [ ] **Step 9: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py tests/test_friend_access_integration.py
git commit -m "feat(access): role-based gates — friend command allowlist + member interceptors (default-deny)"
```

---

## Фаза 3 — Access-флоу с кнопками (запрос доступа)

### Task 4: Reject-ветка → pending + уведомление админу с кнопками

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py:5700-5742` (`_whitelist_gate`)
- Test: `tests/test_friend_access_integration.py`

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_friend_access_integration.py`:
```python
def test_stranger_creates_pending_and_pings_admin(friend_env):
    mod = _get_mod()
    sent = []  # (chat_id, text, reply_markup)
    upd = _text_update(999, 999, "привет")
    with patch.object(mod, "send",
                      lambda c, t, reply_markup=None: sent.append((str(c), t, reply_markup))):
        ok = mod.process_update(upd)
    from app.services.auth.whitelist import REJECT_MESSAGE
    from app.services.auth import users_store
    # вежливый отказ юзеру
    assert any(str(c) == "999" and REJECT_MESSAGE in t for c, t, _ in sent)
    # уведомление АДМИНУ (111) с inline-кнопками approve/reject
    admin_msgs = [(c, t, kb) for c, t, kb in sent if c == "111" and kb]
    assert admin_msgs, "admin not pinged with buttons"
    flat = [b["callback_data"] for row in admin_msgs[0][2]["inline_keyboard"] for b in row]
    assert "access:approve:999" in flat and "access:reject:999" in flat
    # pending записан
    assert any(p["user_id"] == "999" for p in users_store.list_pending())


def test_stranger_repeat_does_not_respam_admin(friend_env):
    mod = _get_mod()
    sent = []
    with patch.object(mod, "send",
                      lambda c, t, reply_markup=None: sent.append((str(c), t, reply_markup))):
        mod.process_update(_text_update(999, 999, "1"))
        mod.process_update(_text_update(999, 999, "2"))
    admin_button_pings = [1 for c, t, kb in sent if c == "111" and kb]
    assert len(admin_button_pings) == 1  # только первый запрос пингует админа
```

- [ ] **Step 2: Запустить — падает**

Run: `python -m pytest tests/test_friend_access_integration.py -k stranger -v`
Expected: FAIL (нет pending/кнопок; админ не пингуется).

- [ ] **Step 3: Доработать reject-ветку `_whitelist_gate`**

В `_whitelist_gate` (стр. 5726-5742), после `chat_id = _extract_reply_chat_id(upd)` и отправки
`REJECT_MESSAGE`, ВСТАВИТЬ перед `try: _audit.audit_event(...)`:
```python
    # Access-flow: register pending; ping admin with buttons only on FIRST request.
    try:
        is_new = _users_store.add_pending(user_id, username)
        if is_new:
            admin_id = _whitelist.load_admin_user_id()
            admin_to = str(admin_id) if admin_id is not None else ALLOWED_CHAT_ID
            if admin_to:
                kb = {"inline_keyboard": [[
                    {"text": "✅ Добавить", "callback_data": f"access:approve:{user_id}"},
                    {"text": "❌ Отклонить", "callback_data": f"access:reject:{user_id}"},
                ]]}
                send(
                    admin_to,
                    f"🔔 Запрос доступа к боту:\n"
                    f"👤 @{username or '—'} (id={user_id})\n"
                    f"Добавить как друга (лимит ${_users_store.DEFAULT_FRIEND_LIMIT_USD:.0f}/день)?",
                    reply_markup=kb,
                )
    except Exception as _e:  # noqa: BLE001 - access-flow must not break the gate
        print(f"[access] pending/notify failed: {_e}", flush=True)
```

- [ ] **Step 4: Запустить — проходит**

Run: `python -m pytest tests/test_friend_access_integration.py -k stranger -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py tests/test_friend_access_integration.py
git commit -m "feat(access): pending + admin approve/reject buttons on first request (dedup)"
```

### Task 5: Callback `access:approve` / `access:reject` (admin-only)

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py` (`handle_callback_query`, рядом с `sbeng:`)
- Test: `tests/test_friend_access_integration.py`

- [ ] **Step 1: Написать падающий тест**

Добавить:
```python
def _cb(uid, chat, data):
    return {"id": "cq1", "data": data,
            "from": {"id": uid, "username": "admin"},
            "message": {"message_id": 7, "chat": {"id": chat}}}


def test_admin_approve_adds_friend(friend_env):
    mod = _get_mod()
    from app.services.auth import users_store
    users_store.add_pending(999, "newguy")
    sent = []
    with patch.object(mod, "send", lambda c, t, **k: sent.append((str(c), t))), \
         patch.object(mod, "answer_callback_query", lambda *a, **k: None):
        mod.handle_callback_query(_cb(111, 111, "access:approve:999"), {})
    assert users_store.get_role(999) == "friend"
    assert users_store.get_limit(999) == 5.0
    assert any(str(c) == "999" for c, _ in sent)  # друг уведомлён


def test_admin_reject_blocks(friend_env):
    mod = _get_mod()
    from app.services.auth import users_store
    users_store.add_pending(999, "newguy")
    with patch.object(mod, "send", lambda c, t, **k: None), \
         patch.object(mod, "answer_callback_query", lambda *a, **k: None):
        mod.handle_callback_query(_cb(111, 111, "access:reject:999"), {})
    assert users_store.get_role(999) is None
    assert users_store.pop_pending(999) is None  # вынут из pending


def test_friend_cannot_use_access_callback(friend_env):
    mod = _get_mod()
    from app.services.auth import users_store
    users_store.add_pending(999, "newguy")
    answered = []
    with patch.object(mod, "send", lambda c, t, **k: None), \
         patch.object(mod, "answer_callback_query",
                      lambda cid, txt="": answered.append(txt)):
        # friend (555) пытается сам себя одобрить
        mod.handle_callback_query(_cb(555, 555, "access:approve:999"), {})
    assert users_store.get_role(999) is None  # не сработало
    assert any("админ" in a.lower() for a in answered)
```

- [ ] **Step 2: Запустить — падает**

Run: `python -m pytest tests/test_friend_access_integration.py -k "approve or reject or access_callback" -v`
Expected: FAIL (нет ветки `access:`).

- [ ] **Step 3: Добавить ветку в `handle_callback_query`**

В `handle_callback_query`, сразу ПОСЛЕ role-гейта (Task 3 Step 7) и перед веткой `sbeng:`
(стр. 2773) добавить:
```python
    # ── Access requests: admin approve/reject (Фаза 3) ────────────────────────
    if data.startswith("access:"):
        # Role-гейт выше уже отсёк friend от admin-callback; это страховка.
        if not (_is_admin_id(_cq_uid) or str(_cq_uid) == ALLOWED_CHAT_ID):
            answer_callback_query(cq_id, "🚫 Только для администратора")
            return
        _, action, target = data.split(":", 2)
        target_id = int(target)
        pend = _users_store.pop_pending(target_id)
        uname = (pend or {}).get("username")
        if action == "approve":
            _users_store.add_friend(
                target_id, uname, added_by=str(_cq_uid),
                limit_usd=_users_store.DEFAULT_FRIEND_LIMIT_USD,
            )
            answer_callback_query(cq_id, "Добавлен")
            send(chat_id, f"✅ @{uname or target_id} добавлен (лимит "
                          f"${_users_store.DEFAULT_FRIEND_LIMIT_USD:.0f}/день).")
            try:
                send(str(target_id),
                     "✅ Тебе открыли доступ к боту! Команды: /swapbatch_source, /animate. "
                     "Статистика: /my_stats.")
            except Exception as _e:  # noqa: BLE001
                print(f"[access] notify approved user failed: {_e}", flush=True)
        else:  # reject → blocked (тихо игнорить дальше)
            _users_store.add_friend(target_id, uname, added_by=str(_cq_uid))
            _users_store.set_status(target_id, "blocked")
            _users_store.pop_pending(target_id)
            answer_callback_query(cq_id, "Отклонён")
            send(chat_id, f"❌ Запрос @{uname or target_id} отклонён.")
        return
```

> Примечание по reject: чтобы «тихо игнорить» (страховка-решение #2 = blocked), создаём запись
> со `status=blocked`. `is_allowed` вернёт False (не open mode, т.к. есть members), а
> `_whitelist_gate` повторно НЕ запишет pending (юзер не member, но `add_pending` дедупит —
> однако blocked-юзер всё равно создаст pending). **Уточнение:** в `_whitelist_gate` перед
> `add_pending` добавить ранний выход для blocked, чтобы не пинговать админа повторно — см. Step 4.

- [ ] **Step 4: Не пинговать админа по blocked-юзерам**

В `_whitelist_gate`, перед блоком access-flow (Task 4 Step 3), добавить ранний skip:
```python
    # Blocked users: молча отклонены, без pending/пинга админу.
    try:
        _rec = next((u for u in _users_store.list_users()
                     if u["user_id"] == str(user_id)), None)
        _is_blocked = bool(_rec and _rec.get("status") == "blocked")
    except Exception:  # noqa: BLE001
        _is_blocked = False
    if _is_blocked:
        return False
```
(Разместить после отправки `REJECT_MESSAGE`? Нет — blocked не должен получать даже REJECT повторно.
Поставить СРАЗУ после `if _whitelist.is_allowed(user_id): return True` и извлечения username,
до отправки REJECT_MESSAGE.)

- [ ] **Step 5: Запустить — проходит**

Run: `python -m pytest tests/test_friend_access_integration.py -v`
Expected: PASS (все).

- [ ] **Step 6: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py tests/test_friend_access_integration.py
git commit -m "feat(access): admin approve/reject callbacks (approve→friend, reject→blocked)"
```

---

## Фаза 4 — Лимит-гейт ДО траты + аудит покрытия трат

### Task 6: Чистый лимит-гейт `access_control.check_limit`

**Files:**
- Create: `app/services/auth/access_control.py`
- Test: `tests/test_access_control.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_access_control.py
# -*- coding: utf-8 -*-
from datetime import datetime, timezone, timedelta

import pytest

from app.services.auth import access_control as ac
from app.services.auth import users_store as us

KYIV = timezone(timedelta(hours=3))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    return tmp_path


def test_admin_unlimited(env):
    allowed, reason = ac.check_limit(111, estimated_usd=999.0)
    assert allowed is True and reason == ""


def test_unknown_user_blocked(env):
    allowed, _ = ac.check_limit(999, estimated_usd=0.1)
    assert allowed is False


def test_friend_under_limit_allowed(env):
    us.add_friend(555, "p", added_by="111", limit_usd=5.0)
    allowed, _ = ac.check_limit(555, estimated_usd=1.0)
    assert allowed is True


def test_friend_over_limit_blocked_before_spend(env):
    from app.services.audit import cost_tracker as ct
    us.add_friend(555, "p", added_by="111", limit_usd=5.0)
    ct.record_cost(555, "p", 4.50)  # spent today
    allowed, reason = ac.check_limit(555, estimated_usd=1.0)  # 4.5 + 1.0 > 5.0
    assert allowed is False
    assert "лимит" in reason.lower()


def test_reset_override_forgives_today(env):
    from app.services.audit import cost_tracker as ct
    us.add_friend(555, "p", added_by="111", limit_usd=5.0)
    ct.record_cost(555, "p", 4.50)
    us.record_reset(555, spent_today=4.50)  # forgive
    allowed, _ = ac.check_limit(555, estimated_usd=1.0)  # effective 0 + 1 <= 5
    assert allowed is True
```

- [ ] **Step 2: Запустить — падает**

Run: `python -m pytest tests/test_access_control.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Реализовать**

```python
# app/services/auth/access_control.py
# -*- coding: utf-8 -*-
"""Лимит-гейт ДО траты. Чистая склейка users_store (лимит/override) и
audit.cost_tracker (потрачено сегодня). admin → безлимит; неизвестный → блок.

Возвращает (allowed, reason). reason пуст при allowed=True; иначе — мягкий
русский текст для пользователя.
"""
from __future__ import annotations

from typing import Tuple

from app.services.audit import cost_tracker as _ct
from app.services.auth import users_store as _us


def check_limit(user_id: int, *, estimated_usd: float) -> Tuple[bool, str]:
    role = _us.get_role(user_id)
    if role is None:
        return False, "Нет доступа."
    if role == "admin":
        return True, ""
    limit = _us.get_limit(user_id)
    if limit is None:
        return True, ""
    spent_today = float(_ct.get_user_stats(user_id).get("today", 0.0))
    effective = _us.effective_spent(user_id, spent_today=spent_today)
    if effective + float(estimated_usd) > limit:
        return False, (
            f"Дневной лимит ${limit:.2f} исчерпан "
            f"(потрачено ${effective:.2f}). Напиши Даниилу, если нужно больше."
        )
    return True, ""
```

- [ ] **Step 4: Запустить — проходит**

Run: `python -m pytest tests/test_access_control.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/auth/access_control.py tests/test_access_control.py
git commit -m "feat(access): pre-spend limit gate (cost_tracker spent + override vs users.json limit)"
```

### Task 7: Хук лимит-гейта перед платной генерацией (одиночный /animate)

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py:1137-1193` (`_animate_run_single`)
- Test: `tests/test_friend_access_integration.py`

Одиночный `/animate` — самый изолированный чокпоинт. Гейтим перед захватом lock/генерацией.

- [ ] **Step 1: Написать падающий тест**

Добавить в `tests/test_friend_access_integration.py`:
```python
def test_single_animate_blocked_over_limit_before_spend(friend_env, monkeypatch):
    mod = _get_mod()
    from app.services.audit import cost_tracker as ct
    from app.services.auth import users_store
    users_store.set_limit(555, 0.10)          # tiny limit
    ct.record_cost(555, "petya", 0.10)        # already at limit
    mod._ANIMATE_PENDING[555] = {"photo": "x.jpg"}
    sent, ran = [], []
    with patch.object(mod, "send", lambda c, t, **k: sent.append(t)), \
         patch.object(mod, "_get_video_lock",
                      lambda: (_ for _ in ()).throw(AssertionError("must not lock"))), \
         patch.object(mod, "build_single_animate_request", create=True,
                      lambda *a, **k: ran.append(1)):
        mod._animate_run_single("555", "spicy")
    assert any("лимит" in t.lower() for t in sent)
    assert ran == []  # генерация не началась → деньги не потрачены
```

- [ ] **Step 2: Запустить — падает**

Run: `python -m pytest tests/test_friend_access_integration.py -k single_animate_blocked -v`
Expected: FAIL (генерация стартует несмотря на лимит).

- [ ] **Step 3: Вставить гейт в `_animate_run_single`**

В `_animate_run_single`, после получения `caps`, `seconds`, `resolution` и ДО `lock = _get_video_lock()`
(стр. ~1164) вставить:
```python
    # Лимит-гейт ДО траты (friend под лимитом; admin безлимит).
    from app.services.auth.access_control import check_limit
    _est = caps.cost_for(seconds, resolution)
    _allowed, _reason = check_limit(chat_id_int, estimated_usd=_est)
    if not _allowed:
        send(chat_id, f"🚫 {_reason}")
        try:
            _admin = _whitelist.load_admin_user_id()
            if _admin is not None and _admin != chat_id_int:
                send(str(_admin), f"⚠️ Друг id={chat_id_int} уперся в лимит (/animate, ~${_est:.2f}).")
        except Exception:  # noqa: BLE001
            pass
        return
```

- [ ] **Step 4: Запустить — проходит**

Run: `python -m pytest tests/test_friend_access_integration.py -k single_animate_blocked -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py tests/test_friend_access_integration.py
git commit -m "feat(access): pre-spend limit gate on standalone /animate (+admin ping)"
```

### Task 8: Лимит-гейт перед swapbatch-animate-батчем

**Files:**
- Modify: `app/handlers/face_swap_handler.py` (`run_animate_batch_phase`, стр. 704)
- Test: `tests/test_swapbatch_handler.py`

- [ ] **Step 1: Прочитать `run_animate_batch_phase` (704-781)**

Run: `sed -n '704,781p' app/handlers/face_swap_handler.py` (или Read) — узнать, как доступны
`user_id`, число целей и estimate (`caps_for(engine).cost_for(seconds, resolution)`), и куда
вставить ранний возврат `HandlerReply`.

- [ ] **Step 2: Написать падающий тест**

Добавить в `tests/test_swapbatch_handler.py` (использует существующие фикстуры файла; если
нужен батч — собрать как в соседних тестах):
```python
def test_animate_batch_blocked_when_friend_over_limit(handler_with_batch, monkeypatch):
    handler, chat_id = handler_with_batch
    import app.handlers.face_swap_handler as fsh
    # friend с лимитом 0 → любой estimate блокирует
    monkeypatch.setattr(
        fsh, "check_limit", lambda uid, *, estimated_usd: (False, "Дневной лимит исчерпан"),
        raising=False,
    )
    called = {"gen": False}
    async def _fake_fn(photos, cancel_check):
        called["gen"] = True
        return []
    import asyncio
    reply = asyncio.run(handler.run_animate_batch_phase(
        chat_id, _fake_fn, user_id=555, username="petya",
    ))
    assert called["gen"] is False                # генерация не запущена
    assert "лимит" in reply.text.lower()
```

- [ ] **Step 3: Запустить — падает**

Run: `python -m pytest tests/test_swapbatch_handler.py -k animate_batch_blocked -v`
Expected: FAIL (генерация запускается; нет гейта).

- [ ] **Step 4: Вставить гейт в `run_animate_batch_phase`**

В начале `run_animate_batch_phase`, после получения сессии и расчёта estimate (число целей ×
`caps_for(engine).cost_for(...)`), но ДО вызова `animate_fn`/`confirm_animate_batch`, вставить:
```python
        from app.services.auth.access_control import check_limit
        if user_id is not None:
            sess = self.orchestrator.get(chat_id)
            n = len([t for t in sess.targets if t.swap_result_path]) if sess else 0
            from app.services.block_m2_video.engines.capabilities import caps_for
            engine_mode = getattr(sess, "video_engine", "spicy") if sess else "spicy"
            seconds = getattr(sess, "duration_sec", 5) if sess else 5
            resolution = getattr(sess, "resolution", "720p") if sess else "720p"
            est = n * caps_for(engine_mode).cost_for(seconds, resolution)
            allowed, reason = check_limit(user_id, estimated_usd=est)
            if not allowed:
                return HandlerReply(text=f"🚫 {reason}")
```
(`check_limit` импортируется внутри функции, чтобы тест мог его подменить на уровне модуля —
после первого реального импорта он становится атрибутом модуля `fsh.check_limit`. Если тест
требует `raising=False` до первого импорта — добавить в шапку файла
`from app.services.auth.access_control import check_limit` и убрать локальный импорт.)

> Для надёжности теста: добавить `from app.services.auth.access_control import check_limit`
> в импорты `face_swap_handler.py` (шапка), и в функции звать просто `check_limit(...)`.

- [ ] **Step 5: Запустить — проходит**

Run: `python -m pytest tests/test_swapbatch_handler.py -k animate_batch_blocked -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/handlers/face_swap_handler.py tests/test_swapbatch_handler.py
git commit -m "feat(access): pre-spend limit gate on swapbatch animate batch"
```

### Task 9: Аудит покрытия трат — ВСЕ платные пути friend пишут в per-user леджер

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py` (`_animate_run_single` ~1183; `_video_face_swap_run` ~574)
- Modify: `app/handlers/persona_handler.py` (рядом с каждым `log_expense`, стр. 844, 892, и др.)
- Test: `tests/test_friend_access_integration.py`

> **Контекст (проверено 2026-06-23):** persona/me-persona пишут траты ТОЛЬКО в глобальный
> `block_m_common.cost_tracker` (`log_expense`), НЕ в per-user `audit.cost_tracker`. Поэтому
> friend-лимит СЛЕП на persona → друг может обойти лимит через persona. Решение: **dual-write** —
> рядом с каждым `log_expense(op, cost, persona_id)` добавить `record_cost(chat_id, None, cost)`
> в per-user леджер (глобальный предохранитель оставить как есть). chat_id == user_id в личке.

- [ ] **Step 1: Перечислить ВСЕ платные пути friend и их покрытие**

Run:
```bash
grep -rn "record_cost\|log_expense" tools/jarvis_smart_telegram_control.py \
  app/handlers/ app/services/block_m2_video/ app/services/block_m22_fun/
```
Свести таблицу: путь → есть ли per-user `record_cost`. Известно:
- swapbatch свап+animate — ✅ есть (`face_swap_handler:747/778`).
- одиночный `/animate` (`_animate_run_single`) — ❌ нет (только текст).
- `_video_face_swap_run` (бот ~574) — проверить фактом.
- persona/me-persona (`persona_handler` log_expense сайты 844/892 + прочие) — ❌ только глобально.
Каждый ❌ закрывается в шагах ниже одинаковым паттерном.

- [ ] **Step 2: Написать падающий тест (одиночный /animate пишет в леджер)**

```python
def test_single_animate_records_cost(friend_env, monkeypatch):
    mod = _get_mod()
    from app.services.audit import cost_tracker as ct
    mod._ANIMATE_PENDING[555] = {"photo": "x.jpg"}
    # короткозамкнуть генерацию на успех в один кадр
    monkeypatch.setattr(mod, "_send_local_video", lambda *a, **k: None)
    import app.services.block_m2_video.batch_animate as ba
    async def _ok(engine, reqs, concurrency=1):
        return ["out.mp4"]
    monkeypatch.setattr(ba, "animate_batch", _ok)
    handler = type("H", (), {"build_single_animate_request":
                             staticmethod(lambda *a, **k: object())})()
    monkeypatch.setattr(mod, "_swapbatch_get_handler", lambda: (handler, None))
    class _Lock:
        def acquire(self, c): return "tok"
        def release(self, t): pass
    monkeypatch.setattr(mod, "_get_video_lock", lambda: _Lock())
    with patch.object(mod, "send", lambda c, t, **k: None):
        mod._animate_run_single("555", "spicy")
        import time; time.sleep(0.3)  # worker thread
    assert ct.get_user_stats(555)["today"] > 0.0
```

- [ ] **Step 3: Запустить — падает**

Run: `python -m pytest tests/test_friend_access_integration.py -k records_cost -v`
Expected: FAIL (today == 0.0 — трата не записана).

- [ ] **Step 4: Добавить `record_cost` в `_animate_run_single`**

В `_animate_run_single._run`, после успешной генерации (где сейчас
`send(chat_id, f"✅ Готово. Стоимость ~${cost:.2f}.")`, стр. ~1184) добавить:
```python
            try:
                _uname = _USERNAME_BY_CHAT.get(str(chat_id))
                _cost.record_cost(chat_id_int, _uname, cost)
            except Exception as _e:  # noqa: BLE001 - billing must not break send
                print(f"[cost] single /animate record failed: {_e}", flush=True)
```

- [ ] **Step 5: Покрыть `_video_face_swap_run` (если не пишет)**

Если Step 1 показал, что `_video_face_swap_run` НЕ зовёт `record_cost` — добавить запись после
успешного свопа (estimate из caps или фикс-ставки env `VIDEO_FACE_SWAP_USD`, дефолт 0.10),
вызвав `_cost.record_cost(int(chat_id), _USERNAME_BY_CHAT.get(str(chat_id)), amount)` в
best-effort try/except. Если уже пишет — пропустить, отметив факт в коммите.

- [ ] **Step 6: Dual-write per-user в persona_handler (закрыть дыру лимита)**

В `app/handlers/persona_handler.py` добавить импорт в шапку:
```python
from app.services.audit import cost_tracker as _user_cost
```
Рядом с КАЖДЫМ `await tracker.log_expense(op, result["cost_usd"], data.persona_id)` (стр. 844, 892
и аналогичные сайты, найденные в Step 1) добавить per-user запись (chat_id в области видимости):
```python
            try:
                _user_cost.record_cost(chat_id, None, result["cost_usd"])
            except Exception as _e:  # noqa: BLE001 - billing must not break generation
                logger.warning("cost: per-user record failed: %s", _e)
```
Глобальный `log_expense` НЕ удаляем — он остаётся общим предохранителем (страховка #3:
без третьего трекера; per-user видимость и глобальный кап сосуществуют).

- [ ] **Step 7: Тест на покрытие persona (моки)**

Добавить в `tests/test_friend_access_integration.py` (или отдельный `tests/test_persona_cost_coverage.py`):
```python
def test_persona_me_swap_photo_records_per_user_cost(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    import app.handlers.persona_handler as ph
    from app.services.audit import cost_tracker as ct
    # подменить тяжёлые зависимости на лёгкие заглушки
    class _Client:
        async def generate_flux_with_lora(self, **kw):
            return {"cost_usd": 0.30, "image_url": "http://x/y.png"}
    class _Data:
        is_trained = True; lora_weights_url = "u"; trigger_word = "t"; persona_id = "p1"
    class _Mgr:
        async def get(self, cid): return _Data()
    monkeypatch.setattr(ph, "ReplicateVideoClient", lambda: _Client())
    monkeypatch.setattr("app.services.block_m22_fun.me_persona.MePersonaManager", _Mgr)
    monkeypatch.setattr(ph, "_safe_send", lambda *a, **k: None)
    monkeypatch.setattr(ph, "_safe_send_photo", lambda *a, **k: None)
    # CostTracker.log_expense не должен ронять (нет дневного капа в тесте)
    ph.handle_me_swap_photo(555, "тест промт")
    import time; time.sleep(0.4)  # worker thread
    assert ct.get_user_stats(555)["today"] == pytest.approx(0.30)
```
(При необходимости — добавить `import pytest` и подстроить имена под фактические заглушки;
цель шага: доказать, что persona-трата видна per-user лимиту.)

- [ ] **Step 8: Запустить — проходит**

Run: `python -m pytest tests/test_friend_access_integration.py -k "records_cost or persona" tests/test_persona_cost_coverage.py -v`
Expected: PASS (today > 0 по обоим путям).

- [ ] **Step 9: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py app/handlers/persona_handler.py tests/
git commit -m "fix(cost): per-user record for /animate + video_face_swap + persona (close limit blind-spots)"
```

---

## Фаза 5 — Админ-команды управления

### Task 10: `/admin_users`, `/admin_setlimit`, `/admin_resetlimit`, `/admin_activity`

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py` (`_cost_command_intercept` → переименовать суть в `_admin_command_intercept` или расширить; зарегистрировать новые команды)
- Test: `tests/test_friend_access_integration.py`

- [ ] **Step 1: Написать падающий тест**

```python
def _admin_text(text):
    return {"update_id": 1, "message": {
        "from": {"id": 111, "username": "daniil"},
        "chat": {"id": 111}, "text": text}}


def test_admin_users_lists_members(friend_env):
    mod = _get_mod()
    sent = []
    with patch.object(mod, "send", lambda c, t, **k: sent.append(t)):
        consumed = mod._admin_command_intercept(_admin_text("/admin_users"))
    assert consumed is True
    assert any("555" in t for t in sent)  # друг petya в списке


def test_admin_setlimit_changes_limit(friend_env):
    mod = _get_mod()
    from app.services.auth import users_store
    with patch.object(mod, "send", lambda c, t, **k: None):
        mod._admin_command_intercept(_admin_text("/admin_setlimit 555 12"))
    assert users_store.get_limit(555) == 12.0


def test_admin_resetlimit_forgives_today(friend_env):
    mod = _get_mod()
    from app.services.audit import cost_tracker as ct
    from app.services.auth import users_store
    ct.record_cost(555, "petya", 4.0)
    with patch.object(mod, "send", lambda c, t, **k: None):
        mod._admin_command_intercept(_admin_text("/admin_resetlimit 555"))
    assert users_store.effective_spent(555, spent_today=4.0) == 0.0


def test_friend_cannot_run_admin_command(friend_env):
    mod = _get_mod()
    sent = []
    upd = {"update_id": 1, "message": {
        "from": {"id": 555, "username": "petya"},
        "chat": {"id": 555}, "text": "/admin_users"}}
    with patch.object(mod, "send", lambda c, t, **k: sent.append(t)):
        consumed = mod._admin_command_intercept(upd)
    assert consumed is True
    assert any("админ" in t.lower() for t in sent)  # отказ, не список
```

- [ ] **Step 2: Запустить — падает**

Run: `python -m pytest tests/test_friend_access_integration.py -k admin_ -v`
Expected: FAIL (`_admin_command_intercept` не существует).

- [ ] **Step 3: Реализовать интерсептор**

Добавить функцию рядом с `_cost_command_intercept` (стр. 5810) и зарегистрировать её вызов
в `process_update` сразу после `if _cost_command_intercept(upd): return` (стр. 6297):
```python
def _admin_command_intercept(upd: Dict[str, Any]) -> bool:
    """Обработать /admin_users|/admin_setlimit|/admin_resetlimit|/admin_activity.

    Все — только для роли admin (default-deny). Возвращает True, если апдейт был
    админ-командой и потреблён.
    """
    msg = upd.get("message") or upd.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    if not text.startswith("/admin_"):
        return False
    cmd = text.split(maxsplit=1)[0].split("@", 1)[0]
    if cmd not in ("/admin_users", "/admin_setlimit", "/admin_resetlimit", "/admin_activity"):
        return False
    uid, uname, cid = _extract_audit_ctx(upd)
    reply_to = cid or (str(uid) if uid is not None else "")
    if not reply_to:
        return True
    if not (_is_admin_id(uid) or str(uid) == ALLOWED_CHAT_ID):
        send(reply_to, "🚫 Команда доступна только администратору.")
        return True

    parts = text.split()
    if cmd == "/admin_users":
        rows = _users_store.list_users()
        if not rows:
            send(reply_to, "👥 Пользователей нет.")
            return True
        lines = ["👥 Пользователи:"]
        for r in rows:
            lim = r.get("daily_limit_usd")
            lim_s = "∞" if lim is None else f"${float(lim):.2f}"
            lines.append(
                f"• {r.get('role')} @{r.get('username') or '—'} (id={r['user_id']}) "
                f"[{r.get('status')}] лимит {lim_s}"
            )
        pend = _users_store.list_pending()
        if pend:
            lines.append("\n⏳ Ожидают:")
            for p in pend:
                lines.append(f"• @{p.get('username') or '—'} (id={p['user_id']})")
        send(reply_to, "\n".join(lines))
        return True

    if cmd == "/admin_setlimit":
        if len(parts) < 3:
            send(reply_to, "Использование: /admin_setlimit <user_id> <сумма$>")
            return True
        try:
            tgt, amount = int(parts[1]), float(parts[2])
        except ValueError:
            send(reply_to, "user_id и сумма должны быть числами.")
            return True
        ok = _users_store.set_limit(tgt, amount)
        send(reply_to, f"✅ Лимит id={tgt}: ${amount:.2f}" if ok
                       else f"⚠️ Нет такого пользователя id={tgt}.")
        return True

    if cmd == "/admin_resetlimit":
        if len(parts) < 2:
            send(reply_to, "Использование: /admin_resetlimit <user_id>")
            return True
        try:
            tgt = int(parts[1])
        except ValueError:
            send(reply_to, "user_id должен быть числом.")
            return True
        spent = float(_cost.get_user_stats(tgt).get("today", 0.0))
        ok = _users_store.record_reset(tgt, spent_today=spent)
        send(reply_to, f"✅ Лимит id={tgt} сброшен на сегодня (прощено ${spent:.2f})."
                       if ok else f"⚠️ Нет такого пользователя id={tgt}.")
        return True

    if cmd == "/admin_activity":
        if len(parts) < 2:
            send(reply_to, "Использование: /admin_activity <user_id>")
            return True
        try:
            tgt = int(parts[1])
        except ValueError:
            send(reply_to, "user_id должен быть числом.")
            return True
        events = _audit.read_user_activity(tgt, limit=20)
        if not events:
            send(reply_to, f"Активности по id={tgt} нет.")
            return True
        lines = [f"📜 Активность id={tgt} (последние {len(events)}):"]
        for e in events:
            d = e.get("details") or {}
            extra = d.get("command") or d.get("action") or e.get("event")
            lines.append(f"• {e.get('ts', '')[:16]} {extra}")
        send(reply_to, "\n".join(lines))
        return True

    return True
```
И в `process_update`:
```python
    if _cost_command_intercept(upd):
        return
    if _admin_command_intercept(upd):
        return
```

> `read_user_activity` появляется в Task 11 — если выполняем строго по порядку, временно
> в `/admin_activity` вернуть заглушку «команда появится позже» ИЛИ выполнить Task 11 перед
> Step 4 ниже. Рекомендуется: реализовать Task 11 (read API) ПЕРЕД запуском теста `admin_activity`.

- [ ] **Step 4: Запустить — проходит**

Run: `python -m pytest tests/test_friend_access_integration.py -k "admin_users or setlimit or resetlimit or friend_cannot_run" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py tests/test_friend_access_integration.py
git commit -m "feat(admin): /admin_users /admin_setlimit /admin_resetlimit /admin_activity (admin-only)"
```

---

## Фаза 6 — Activity-лог

### Task 11: `read_user_activity` + детальные события активности

**Files:**
- Modify: `app/services/audit/audit_logger.py` (+ `read_user_activity`)
- Test: `tests/test_audit_activity.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_audit_activity.py
# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

from app.services.audit import audit_logger as al


@pytest.fixture
def audit_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_AUDIT_DIR", str(tmp_path / "audit"))
    al._reset_seen_users_cache()
    return tmp_path


def test_read_user_activity_filters_and_limits(audit_env):
    al.audit_event(555, "petya", "555", "command", {"command": "/swapbatch_go"})
    al.audit_event(555, "petya", "555", "animate_started", {"mode": "yes"})
    al.audit_event(999, "other", "999", "command", {"command": "/start"})
    rows = al.read_user_activity(555, limit=10)
    assert all(r["user_id"] == 555 for r in rows)
    assert len(rows) == 2
    # newest first
    assert rows[0]["event"] in ("animate_started", "command")


def test_read_user_activity_respects_limit(audit_env):
    for i in range(5):
        al.audit_event(555, "p", "555", "command", {"command": f"/c{i}"})
    rows = al.read_user_activity(555, limit=3)
    assert len(rows) == 3
```

- [ ] **Step 2: Запустить — падает**

Run: `python -m pytest tests/test_audit_activity.py -v`
Expected: FAIL (`read_user_activity` не существует).

- [ ] **Step 3: Реализовать read-API**

Добавить в конец `audit_logger.py`:
```python
def read_user_activity(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """Вернуть последние ``limit`` событий ``user_id`` (новые первыми).

    Сканирует дневные .jsonl от свежих к старым, собирает совпадения, пока не
    наберёт ``limit``. Битые строки пропускаются.
    """
    out: List[Dict[str, Any]] = []
    d = _audit_dir()
    if not d.exists():
        return out
    for f in sorted(d.glob("*.jsonl"), reverse=True):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("user_id") == user_id:
                out.append(obj)
                if len(out) >= limit:
                    return out
    return out
```
Убедиться, что `List`/`Dict`/`Any` импортированы в шапке (`from typing import ...`); если нет —
добавить.

- [ ] **Step 4: Запустить — проходит**

Run: `python -m pytest tests/test_audit_activity.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Богатые события активности друга (опц., если время)**

Существующий `_audit_message` (стр. 5774) уже логирует команды с `details.command`/`args` —
этого достаточно для `/admin_activity` (действие + аргументы). Для детализации «движок/исход/$»
можно расширить точки биллинга, но это **вне MVP** — `_AUDIT_COMMAND_EVENT` уже маппит
`animate_started`/`swapbatch_go`. Зафиксировать: activity-лог опирается на существующий
`_audit_message`; дополнительных событий не добавляем (YAGNI).

- [ ] **Step 6: Commit**

```bash
git add app/services/audit/audit_logger.py tests/test_audit_activity.py
git commit -m "feat(audit): read_user_activity for /admin_activity (newest-first, capped)"
```

---

## Фаза 7 — Живой smoke ⛔ СТОП, зову пользователя

### Task 12: Полный прогон тестов + живой сценарий

**Files:** нет (env + бот).

- [ ] **Step 1: Полный прогон всех новых/затронутых тестов**

Run:
```bash
python -m pytest tests/test_users_store.py tests/test_access_control.py \
  tests/test_audit_activity.py tests/test_friend_access_integration.py \
  tests/test_bot_whitelist_integration.py tests/test_swapbatch_handler.py -v
```
Expected: PASS (все). Зафиксировать число зелёных.

- [ ] **Step 2: Выставить bootstrap-админа в env бота (страховка #5)**

Убедиться, что в окружении бота задан `JARVIS_ADMIN_USER_ID=<мой user_id>` (защита от самоблока).
Если не задан — задать и перезапустить бота. `state/users.json` создастся при первом approve.

- [ ] **Step 3: ⛔ СТОП — позвать пользователя.** Дальше живой мульти-юзер тест.

- [ ] **Step 4: Сценарий (вместе с пользователем)**

1. **Друг** (другой Telegram-аккаунт) пишет боту `привет` → получает вежливый отказ.
2. **Мне** приходит уведомление с кнопками `[✅ Добавить] [❌ Отклонить]`.
3. Друг пишет ещё раз → мне НЕ дублируется (дедуп).
4. Тап **✅ Добавить** → друг получает «доступ открыт»; `/admin_users` показывает его (friend, $5).
5. Друг гонит `/swapbatch_source → … → /swapbatch_animate_go` (дешёвый профиль 5с/720p) →
   видео приходит; `/my_stats` друга растёт; **мой** `/admin_costs` его видит.
6. Друг исчерпывает лимит (или `/admin_setlimit <id> 0.1`) → следующая генерация **блокируется
   ДО траты** мягким текстом; мне приходит пинг.
7. `/admin_resetlimit <id>` → друг снова может генерить (сегодня прощено).
8. **Изоляция:** друг шлёт `/restart_bot`, `/admin_users`, `/admin_costs` → «только для
   администратора»; ключи/мои сессии недоступны.

- [ ] **Step 5: Зафиксировать факт в память**

Обновить заметку проекта: фича «доступ для друга» живьём подтверждена; формат `users.json`;
env `JARVIS_ADMIN_USER_ID` обязателен. Связать с `[[jarvis-router-enabled]]` (мульти-юзер).

- [ ] **Step 6: Финал — finishing-a-development-branch**

Использовать `superpowers:finishing-a-development-branch` для слияния/PR.

---

## Self-review (сверка плана со спекой)

- **Страховка #1 (изоляция):** Task 3 (allowlist `handle()` + admin-callback гейт), Task 5
  (access-callback admin-only), Task 10 (admin-команды admin-only). ✓
- **Страховка #2 (лимит ДО траты):** Task 6 (чистый гейт), Task 7 (/animate), Task 8 (батч). ✓
- **Страховка #3 (аудит покрытия трат):** Task 9 — /animate + video_face_swap + **persona dual-write**
  (persona писала только в глобальный трекер → лимит был слеп; закрыто). ✓
- **Страховка #4 (унификация — без третьего трекера):** Task 6 читает `audit/cost_tracker`;
  users.json хранит только лимит/override. ✓
- **Страховка #5 (bootstrap env-first):** Task 1 (`get_role` env-first), Task 7 Step 2. ✓
- **Решения:** override-reset (Task 1 `record_reset`/`effective_spent`, Task 10 resetlimit);
  reject→blocked (Task 5 + Task 5 Step 4); дефолт $5 (Task 1 `DEFAULT_FRIEND_LIMIT_USD`);
  мягкий текст превышения (Task 6 reason). ✓
- **Все 6 секций дизайна → задачи:** хранилище (1), роли/разблокировка (2-3), access-флоу (4-5),
  лимит (6-8), админ-команды (10), activity (11). ✓
- **Риск-замечания для исполнителя:** (а) точная форма `classify_message` в Task 3 — прочитать
  перед кодом; (б) `_video_face_swap_run` покрытие в Task 9 — проверить фактом; (в) Task 11
  (read API) выполнить до теста `/admin_activity` в Task 10.

---

**Plan complete and saved to `docs/superpowers/plans/2026-06-23-friend-access.md`.**
