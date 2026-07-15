# -*- coding: utf-8 -*-
"""Daily state/ backup to R2 (JarvisStateBackup scheduled task, DEV-16).

Uploads the DEV-16 critical-file allowlist (users.json, money-ledgers,
brain-state, dev_tasks, verdicts, persona-centroids — see
``app.services.state_backup.CRITICAL_PATTERNS``; never .env or credential
files) to a private R2 bucket, writes a per-run manifest (sha256 per file),
rotates backups older than ``KEEP_DAYS`` (14), and Telegram-notifies the
admin with a one-line summary. Standalone (as ``morning_digest.py``): does
not depend on the live bot process, sends directly via the Bot API.

    python scripts/state_backup.py
"""
from __future__ import annotations

import json
import logging
import sys
import urllib.parse
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

logger = logging.getLogger("jarvis.state_backup")

_TG_TIMEOUT = 20


def send_telegram(text: str) -> bool:
    """Best-effort admin notification via the Bot API. Never raises.

    Mirrors ``morning_digest.send_telegram`` — same test-isolation guard.
    """
    import os
    from app.core.notify_isolation import telegram_send_blocked
    if telegram_send_blocked():
        logger.info("state_backup: telegram send suppressed under test isolation")
        return False
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.warning("state_backup: no TELEGRAM_BOT_TOKEN/CHAT_ID — skipping notify")
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
        with urllib.request.urlopen(req, timeout=_TG_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8")).get("ok", False)
    except Exception as exc:
        logger.warning("state_backup: telegram notify failed: %s", exc)
        return False


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    try:
        import app.env_bootstrap  # noqa: F401  side-effect: loads .env
    except Exception as exc:
        logger.error("state_backup: env bootstrap failed: %s", exc)

    from app.services import state_backup as sb

    state_root = _ROOT / "state"
    try:
        result = sb.run_backup(state_root)
    except Exception as exc:
        logger.error("state_backup: run_backup failed: %s", exc)
        send_telegram(f"\U0001f6a8 Бэкап state/ упал: {exc}")
        return 1

    try:
        deleted = sb.rotate_old_backups()
    except Exception as exc:
        logger.warning("state_backup: rotation failed: %s", exc)
        deleted = []

    text = sb.format_backup_result(result)
    if deleted:
        text += f"\nРотация: удалено {len(deleted)} старых объектов (>{sb.KEEP_DAYS}д)."
    send_telegram(text)
    logger.info(
        "state_backup: run complete uploaded=%d failed=%d deleted=%d",
        len(result.uploaded), len(result.failed), len(deleted),
    )
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
