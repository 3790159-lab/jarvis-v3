# -*- coding: utf-8 -*-
"""Отложенная публикация IG (Этап 3): очередь ``/ig_schedule``/``/ig_queue``.

Берёт уже готовую (оплаченную/подтверждённую) превью-карточку
(``app.services.ig_post.IG_POST_PENDING_KEY``) и кладёт её в очередь на диске
(``state/ig_scheduled_posts.json``, по образцу ``app.services.ig_accounts`` —
атомарная запись ``.tmp`` + ``os.replace``, ``RLock``, env-override
``IG_SCHEDULE_FILE`` для тестовой изоляции).

Чистая логика — ноль сети. Публикация инжектится в :func:`process_due` как
``publish_fn`` (реальный вызов — ``InstagramAPI.publish_photo`` в обвязке
бота/скрипте), поэтому весь модуль полностью тестируется на mock-часах без
единого реального HTTP-вызова. Fail-closed по образцу ``ig_post``: любая
ошибка публикации (протухшее медиа, сеть, невалидный токен) → пост НЕ
считается опубликованным; квотная ошибка (``ig_post.is_quota_error``)
оставляет пост в очереди на повтор, любая другая — переводит в ``failed``
(чтобы не долбить API по кругу вхолостую) с честным уведомлением админу.

Квота 25/сутки на аккаунт учитывается и ПРЕВЕНТИВНО (см.
:func:`count_published_since` в :func:`process_due`) — если аккаунт уже
исчерпал квоту за последние 24ч, due-пост не трогаем вовсе (не тратим API-вызов
впустую), он остаётся в очереди для следующего тика.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.ig_post import format_publish_error, is_quota_error

logger = logging.getLogger(__name__)

__all__ = [
    "IGScheduleError",
    "DATETIME_FMT",
    "DAILY_QUOTA",
    "parse_schedule_args",
    "parse_when",
    "enqueue",
    "list_posts",
    "get_post",
    "cancel",
    "due_posts",
    "mark_published",
    "mark_failed",
    "count_published_since",
    "process_due",
    "build_queue_text",
    "queue_keyboard",
]

_DEFAULT_STATE_FILE = Path("state") / "ig_scheduled_posts.json"
_LOCK = threading.RLock()

DATETIME_FMT = "%Y-%m-%d %H:%M"
DAILY_QUOTA = 25


class IGScheduleError(RuntimeError):
    """Честная ошибка постановки в очередь (плохие аргументы/время). $0."""


def _state_file() -> Path:
    raw = os.getenv("IG_SCHEDULE_FILE", "").strip()
    return Path(raw) if raw else _DEFAULT_STATE_FILE


def _load() -> Dict[str, Any]:
    f = _state_file()
    if not f.exists():
        return {"posts": []}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("ig_schedule: state unreadable (%s); treating as empty", exc)
        return {"posts": []}
    if not isinstance(data, dict):
        return {"posts": []}
    data.setdefault("posts", [])
    return data


def _save_atomic(state: Dict[str, Any]) -> None:
    f = _state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)


# ---- parsing ----------------------------------------------------------------


def parse_schedule_args(query: Optional[str]) -> Tuple[str, str]:
    """``<аккаунт> <YYYY-MM-DD HH:MM>`` → ``(account_key, when_text)``.

    ``account_key`` — первый токен, опциональный ведущий ``@`` срезается
    (тот же UX, что ``ig_accounts.parse_account_arg``). Остаток строки —
    сырой текст даты/времени, разбирается отдельно (:func:`parse_when`).
    """
    parts = (query or "").strip().split(None, 1)
    if not parts:
        return "", ""
    account = parts[0].lstrip("@")
    when_text = parts[1].strip() if len(parts) > 1 else ""
    return account, when_text


def parse_when(when_text: Optional[str], now: datetime) -> datetime:
    """Разобрать ``YYYY-MM-DD HH:MM`` (наивное локальное время). Только будущее."""
    text = (when_text or "").strip()
    if not text:
        raise IGScheduleError("укажи дату и время публикации: YYYY-MM-DD HH:MM")
    try:
        when = datetime.strptime(text, DATETIME_FMT)
    except ValueError:
        raise IGScheduleError(
            f"не удалось разобрать дату/время {text!r} (формат: YYYY-MM-DD HH:MM)"
        )
    if when <= now:
        raise IGScheduleError("время публикации должно быть в будущем")
    return when


# ---- queue CRUD ---------------------------------------------------------------


def enqueue(*, chat_id: Any, account_key: Optional[str], run_at: datetime,
            pending: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """Положить подтверждённую превью-карточку ``pending`` в очередь."""
    with _LOCK:
        state = _load()
        post_id = "sched_" + uuid.uuid4().hex[:12]
        record: Dict[str, Any] = {
            "id": post_id,
            "chat_id": str(chat_id),
            "account_key": account_key,
            "photo_url": pending.get("photo_url", ""),
            "caption": pending.get("caption", ""),
            "topic": pending.get("topic", ""),
            "source": pending.get("source", ""),
            "run_at": run_at.isoformat(),
            "status": "pending",
            "created_at": now.isoformat(),
            "published_at": None,
            "media_id": None,
            "permalink": None,
            "error": None,
        }
        state["posts"].append(record)
        _save_atomic(state)
        return dict(record)


def list_posts(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Все посты (опционально отфильтрованные по ``status``), сорт. по ``run_at``."""
    with _LOCK:
        posts = list(_load().get("posts", []))
    if status is not None:
        posts = [p for p in posts if p.get("status") == status]
    return sorted(posts, key=lambda p: p.get("run_at", ""))


def get_post(post_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        for p in _load().get("posts", []):
            if p.get("id") == post_id:
                return dict(p)
    return None


def cancel(post_id: str) -> bool:
    """Отменить ЕЩЁ НЕ опубликованный пост. ``False`` — не найден/уже не pending."""
    with _LOCK:
        state = _load()
        for p in state["posts"]:
            if p.get("id") == post_id and p.get("status") == "pending":
                p["status"] = "cancelled"
                _save_atomic(state)
                return True
        return False


def due_posts(now: datetime) -> List[Dict[str, Any]]:
    """Посты ``pending`` со временем публикации ``<= now``, сорт. по ``run_at``."""
    pending = list_posts(status="pending")
    return [p for p in pending if datetime.fromisoformat(p["run_at"]) <= now]


def mark_published(post_id: str, *, media_id: Optional[str],
                    permalink: Optional[str], now: datetime) -> None:
    with _LOCK:
        state = _load()
        for p in state["posts"]:
            if p.get("id") == post_id:
                p["status"] = "published"
                p["media_id"] = media_id
                p["permalink"] = permalink
                p["published_at"] = now.isoformat()
                p["error"] = None
                break
        _save_atomic(state)


def mark_failed(post_id: str, *, error: str, now: datetime, retry: bool) -> None:
    """``retry=True`` (квота — истечёт сама) оставляет пост ``pending`` на
    повтор; иначе (протухшее медиа/иная неисправимая ошибка) → ``failed``,
    чтобы не долбить API вхолостую на каждом тике."""
    with _LOCK:
        state = _load()
        for p in state["posts"]:
            if p.get("id") == post_id:
                p["status"] = "pending" if retry else "failed"
                p["error"] = error
                p["last_attempt_at"] = now.isoformat()
                break
        _save_atomic(state)


def count_published_since(account_key: Optional[str], since: datetime) -> int:
    n = 0
    for p in list_posts(status="published"):
        if p.get("account_key") != account_key:
            continue
        pub_at = p.get("published_at")
        if not pub_at:
            continue
        if datetime.fromisoformat(pub_at) >= since:
            n += 1
    return n


# ---- the periodic tick --------------------------------------------------------


def process_due(now: datetime, *, publish_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
                 notify_fn: Callable[[str, str], None],
                 quota: int = DAILY_QUOTA) -> Dict[str, List[str]]:
    """Один тик планировщика: публикует все due-посты через ``publish_fn``.

    ``publish_fn(post) -> {"id": media_id, "permalink": Optional[str]}``,
    поднимает любое исключение при сбое (как ``InstagramAPI.publish_photo`` —
    ``code``/``subcode`` атрибуты опциональны, используются для классификации
    квотной ошибки). ``notify_fn(chat_id, text)`` — доставка результата в чат,
    инжектируется, чтобы модуль оставался $0/без сети.
    """
    results: Dict[str, List[str]] = {"published": [], "failed": [], "skipped_quota": []}
    for post in due_posts(now):
        account_key = post.get("account_key")
        since = now - timedelta(hours=24)
        if count_published_since(account_key, since) >= quota:
            results["skipped_quota"].append(post["id"])
            logger.warning(
                "ig_schedule: daily quota reached for account=%s, post=%s stays queued",
                account_key, post["id"],
            )
            continue
        try:
            result = publish_fn(post)
        except Exception as exc:  # noqa: BLE001 — honest fail-closed classification
            code = getattr(exc, "code", None)
            subcode = getattr(exc, "subcode", None)
            retry = is_quota_error(code, subcode)
            mark_failed(post["id"], error=str(exc), now=now, retry=retry)
            notify_fn(post.get("chat_id", ""), _format_schedule_error(post, exc))
            results["failed"].append(post["id"])
            continue
        mark_published(post["id"], media_id=result.get("id"),
                        permalink=result.get("permalink"), now=now)
        notify_fn(post.get("chat_id", ""), _format_schedule_success(post, result))
        results["published"].append(post["id"])
    return results


def _format_schedule_error(post: Dict[str, Any], exc: Exception) -> str:
    topic = post.get("topic") or "?"
    return "⏰ Отложенный пост (%s) НЕ опубликован:\n%s" % (topic, format_publish_error(exc))


def _format_schedule_success(post: Dict[str, Any], result: Dict[str, Any]) -> str:
    topic = post.get("topic") or "?"
    permalink = result.get("permalink")
    if permalink:
        return "✅ Отложенный пост (%s) опубликован:\n%s" % (topic, permalink)
    return "✅ Отложенный пост (%s) опубликован (media_id=%s)." % (topic, result.get("id", ""))


# ---- /ig_queue rendering ------------------------------------------------------


def build_queue_text(posts: List[Dict[str, Any]]) -> str:
    if not posts:
        return "📭 Очередь отложенных постов пуста."
    lines = ["🕒 Очередь отложенных постов:"]
    for p in posts:
        acc = p.get("account_key") or "?"
        lines.append("· %s — @%s — %s" % (p.get("run_at", "?"), acc, p.get("topic") or "?"))
    return "\n".join(lines)


def queue_keyboard(posts: List[Dict[str, Any]]) -> list:
    rows = []
    for p in posts:
        label = ("❌ %s %s" % (p.get("run_at", "?"), p.get("topic") or "?"))[:40]
        rows.append([{"text": label, "callback_data": "igsched:cancel:%s" % p["id"]}])
    return rows
