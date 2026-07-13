# -*- coding: utf-8 -*-
"""Отложенная публикация IG (JarvisIgSchedulePublisher scheduled task, every 5 min).

Проверяет очередь ``/ig_schedule`` (``state/ig_scheduled_posts.json`` через
``app.services.ig_schedule``) и публикует все посты, чьё время настало — тем
же fail-closed путём (poll контейнера → publish → permalink), что и ручной
тап [📤 Опубликовать]/``_ig_post_publish`` в боте (``InstagramAPI.publish_photo``).
Квота 25/сутки на аккаунт учитывается ПРЕВЕНТИВНО внутри
``ig_schedule.process_due`` — не тратим API-вызов, если аккаунт уже исчерпал
лимит за последние 24ч, пост остаётся в очереди на следующий тик. Любой сбой
публикации (протухшее медиа, сеть, невалидный токен) — честный отказ,
пост НЕ считается опубликованным (квотная ошибка оставляет его в очереди на
повтор, любая другая переводит в ``failed`` — см. ``ig_schedule.process_due``
docstring).

Standalone (как ``morning_digest.py``/``ig_token_refresh.py``): не зависит от
живого процесса бота, шлёт результат напрямую через Bot API в чат, что
поставил пост в очередь (обычно админ — ``/ig_schedule`` admin-only).

    python scripts/ig_schedule_publisher.py
"""
from __future__ import annotations

import json
import logging
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

logger = logging.getLogger("jarvis.ig_schedule_publisher")

_TG_TIMEOUT = 20


def send_telegram(chat_id: str, text: str) -> bool:
    """Best-effort per-chat admin notification via the Bot API. Never raises.

    Standalone job (see module docstring) — consults the shared isolation
    guard itself, same as ``morning_digest.send_telegram``. Falls back to
    ``TELEGRAM_ALLOWED_CHAT_ID`` if ``chat_id`` is empty.
    """
    import os
    from app.core.notify_isolation import telegram_send_blocked
    if telegram_send_blocked():
        logger.info("ig_schedule_publisher: telegram send suppressed under test isolation")
        return False
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    target = (chat_id or "").strip() or os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    if not token or not target:
        logger.warning("ig_schedule_publisher: no TELEGRAM_BOT_TOKEN/chat_id — skipping notify")
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": target, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
        with urllib.request.urlopen(req, timeout=_TG_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8")).get("ok", False)
    except Exception as exc:
        logger.warning("ig_schedule_publisher: telegram notify failed: %s", exc)
        return False


def publish(post: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Real ``publish_fn`` for ``ig_schedule.process_due`` — same call as the
    bot's manual [📤 Опубликовать] tap (``_ig_post_publish``)."""
    from app.services.instagram_api import InstagramAPI
    api = InstagramAPI(account_key=post.get("account_key"))
    return api.publish_photo(post.get("photo_url", ""), post.get("caption", ""))


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    try:
        import app.env_bootstrap  # noqa: F401  side-effect: loads .env
    except Exception as exc:
        logger.error("ig_schedule_publisher: env bootstrap failed: %s", exc)

    from app.services import ig_schedule as _igsc

    now = datetime.now()
    result = _igsc.process_due(now, publish_fn=publish, notify_fn=send_telegram)
    logger.info(
        "ig_schedule_publisher: run complete (published=%d failed=%d skipped_quota=%d)",
        len(result["published"]), len(result["failed"]), len(result["skipped_quota"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
