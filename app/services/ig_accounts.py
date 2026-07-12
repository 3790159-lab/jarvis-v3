# -*- coding: utf-8 -*-
"""Мульти-аккаунт хранилище IG-креденшелов (``state/ig_accounts.json``).

Один JSON, ключ — ``account_key`` (человекочитаемое имя аккаунта, напр.
``jtest_lab_``/``vera_ai_ua``), значение — ``{account_key, ig_user_id,
username, access_token, token_refreshed_at}``. Тот же контракт хранения, что
``app.services.auth.users_store``: относительный дефолт ``state/ig_accounts.json``
с override через env ``IG_ACCOUNTS_FILE`` (тесты изолируются тем же паттерном,
что ``JARVIS_USERS_FILE``), атомарная запись (``.tmp`` + ``os.replace``),
in-process ``RLock``. ``state/`` уже в ``.gitignore`` целиком — секреты не
попадают в git, отдельных правил не нужно.

Bc-совместимость (обязательна, jtest_lab_ не должен сломаться): пока файла
нет и credentials НЕ передан явный ``account_key`` вызывающим кодом —
:func:`resolve_credentials` читает старые ``IG_ACCESS_TOKEN``/``IG_USER_ID``
из env один-в-один, как раньше. Первое обращение к credentials, когда файла
ещё нет, но env-токен уже есть, — авто-мигрирует его в json под
``DEFAULT_ACCOUNT_KEY`` (см. :func:`ensure_migrated`); значения токена не
меняются, только место хранения — поведение для существующих вызывающих
кода идентично.

Секреты никогда не логируются (только account_key/путь к файлу).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_STATE_FILE = Path("state") / "ig_accounts.json"
_LOCK = threading.RLock()
_ACCOUNT_ARG_RE = re.compile(r"^@([\w.\-]+)$")

# Аккаунт, под которым легаси-токен переезжает в json при авто-миграции, и
# дефолт для резолва, если ни явный account_key, ни env IG_DEFAULT_ACCOUNT не
# заданы — совпадает с единственным аккаунтом, который существовал до
# мультиаккаунтности.
DEFAULT_ACCOUNT_KEY = "jtest_lab_"


class IGAccountError(RuntimeError):
    """Честная ошибка резолва credentials (неизвестный account_key). $0, ничего не публикует."""


def _state_file() -> Path:
    raw = os.getenv("IG_ACCOUNTS_FILE", "").strip()
    return Path(raw) if raw else _DEFAULT_STATE_FILE


def _load() -> Dict[str, Any]:
    f = _state_file()
    if not f.exists():
        return {"accounts": {}}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("ig_accounts: state unreadable (%s); treating as empty", exc)
        return {"accounts": {}}
    if not isinstance(data, dict):
        return {"accounts": {}}
    data.setdefault("accounts", {})
    return data


def _save_atomic(state: Dict[str, Any]) -> None:
    f = _state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)


def list_accounts() -> Dict[str, Dict[str, Any]]:
    """Все аккаунты из json, ``{}`` если файла нет/пуст."""
    with _LOCK:
        return dict(_load().get("accounts", {}))


def get_account(account_key: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _load().get("accounts", {}).get(account_key)


def save_account(account_key: str, *, ig_user_id: str = "", username: str = "",
                  access_token: str = "", token_refreshed_at: Optional[float] = None) -> None:
    """Создать/обновить один аккаунт (upsert). Секреты в лог не пишутся."""
    with _LOCK:
        state = _load()
        state["accounts"][account_key] = {
            "account_key": account_key,
            "ig_user_id": ig_user_id,
            "username": username,
            "access_token": access_token,
            "token_refreshed_at": token_refreshed_at,
        }
        _save_atomic(state)


def default_account_key() -> str:
    """``IG_DEFAULT_ACCOUNT`` env, иначе :data:`DEFAULT_ACCOUNT_KEY` (jtest_lab_)."""
    return os.getenv("IG_DEFAULT_ACCOUNT", "").strip() or DEFAULT_ACCOUNT_KEY


def ensure_migrated() -> bool:
    """Одноразово перенести легаси ``.env`` IG_ACCESS_TOKEN/IG_USER_ID в json.

    Срабатывает только когда json ещё нет И env-токен непустой — идемпотентно
    (после первого раза файл уже существует, ветка больше не заходит).
    Значения токена не меняются, только место хранения.
    """
    with _LOCK:
        f = _state_file()
        if f.exists():
            return False
        token = os.getenv("IG_ACCESS_TOKEN", "").strip()
        if not token:
            return False
        user_id = os.getenv("IG_USER_ID", "").strip()
        state = {"accounts": {
            DEFAULT_ACCOUNT_KEY: {
                "account_key": DEFAULT_ACCOUNT_KEY,
                "ig_user_id": user_id,
                "username": DEFAULT_ACCOUNT_KEY,
                "access_token": token,
                "token_refreshed_at": None,
            }
        }}
        _save_atomic(state)
        logger.info("ig_accounts: migrated legacy .env token into %s (account_key=%s)",
                    f, DEFAULT_ACCOUNT_KEY)
        return True


def resolve_credentials(account_key: Optional[str] = None) -> Tuple[str, str]:
    """``(access_token, ig_user_id)`` для ``account_key`` (или дефолт-аккаунта).

    Нет json и нет легаси env-токена для миграции -> старое поведение
    (``IG_ACCESS_TOKEN``/``IG_USER_ID`` из env, возможно пустые строки).
    Json есть (после миграции или создан вручную) -> резолв по ключу; если
    ключ не найден — :class:`IGAccountError` (fail-closed: не публикуем под
    чужим/несуществующим аккаунтом).
    """
    ensure_migrated()
    accounts = list_accounts()
    if not accounts:
        return (os.getenv("IG_ACCESS_TOKEN", "").strip(),
                os.getenv("IG_USER_ID", "").strip())
    key = account_key or default_account_key()
    acct = accounts.get(key)
    if not acct:
        raise IGAccountError(
            f"IG-аккаунт '{key}' не найден в {_state_file()} "
            f"(доступны: {', '.join(sorted(accounts)) or '—'})"
        )
    return (str(acct.get("access_token") or "").strip(),
            str(acct.get("ig_user_id") or "").strip())


def parse_account_arg(query: Optional[str]) -> Tuple[Optional[str], str]:
    """Вытащить необязательный префикс ``@<account_key>`` из текста команды.

    ``"@vera_ai_ua тема"`` -> ``("vera_ai_ua", "тема")``. Без префикса ->
    ``(None, query.strip())`` — как ``brand_config.parse_client_arg``.
    """
    parts = (query or "").strip().split(None, 1)
    if not parts:
        return None, ""
    m = _ACCOUNT_ARG_RE.match(parts[0])
    if not m:
        return None, (query or "").strip()
    rest = parts[1].strip() if len(parts) > 1 else ""
    return m.group(1), rest
