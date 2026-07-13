# -*- coding: utf-8 -*-
"""Вечерний error-дайджест Jarvis (JarvisErrorDigest scheduled task, daily 21:00).

Greps fresh ERROR/CRITICAL lines out of the bot log (``logs/jarvis_bot.log``)
and the backend log (``logs/jarvis.log``) over the last 24h, dedupes exact
repeats into "line + counter" (see ``app.services.error_digest``), and sends
the top-5 to Telegram. **Empty digest -> sends NOTHING** (тишина = хорошо,
per spec) — unlike ``morning_digest.py``, which always sends one message.

Standalone (same pattern as ``ig_token_refresh.py`` / ``morning_digest.py``):
does not depend on the live bot process, sends directly via raw Bot API.

    python scripts/error_digest.py
"""
from __future__ import annotations

import json
import logging
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import List

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

logger = logging.getLogger("jarvis.error_digest")

BOT_LOG_PATH = _ROOT / "logs" / "jarvis_bot.log"
BACKEND_LOG_PATH = _ROOT / "logs" / "jarvis.log"
_TG_TIMEOUT = 20


def send_telegram(text: str) -> bool:
    """Best-effort admin notification via the Bot API. Never raises.

    Standalone job (see module docstring) — consults the shared isolation
    guard itself, same as ``morning_digest.send_telegram``.
    """
    import os
    from app.core.notify_isolation import telegram_send_blocked
    if telegram_send_blocked():
        logger.info("error_digest: telegram send suppressed under test isolation")
        return False
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.warning("error_digest: no TELEGRAM_BOT_TOKEN/CHAT_ID — skipping notify")
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
        with urllib.request.urlopen(req, timeout=_TG_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8")).get("ok", False)
    except Exception as exc:
        logger.warning("error_digest: telegram notify failed: %s", exc)
        return False


def _read_lines(path: Path) -> List[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    try:
        import app.env_bootstrap  # noqa: F401  side-effect: loads .env
    except Exception as exc:
        logger.error("error_digest: env bootstrap failed: %s", exc)

    from app.services import error_digest as ed

    top = ed.build_digest_report(
        bot_lines=_read_lines(BOT_LOG_PATH),
        backend_lines=_read_lines(BACKEND_LOG_PATH),
        now=datetime.now(),
    )

    if not top:
        logger.info("error_digest: no fresh ERROR/CRITICAL in the last 24h — staying silent")
        return 0

    text = ed.format_digest_message(top)
    sent = send_telegram(text)
    if not sent:
        logger.warning("error_digest: telegram send failed/suppressed")
    logger.info("error_digest: run complete (%d entries, sent=%s)", len(top), sent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
