# -*- coding: utf-8 -*-
"""Daily state/ backup to R2 (JarvisStateBackup scheduled task, DEV-16).

Uploads the DEV-16 critical-file allowlist (users.json, money-ledgers,
brain-state, dev_tasks, verdicts, persona-centroids — see
``app.services.state_backup.CRITICAL_PATTERNS``; never .env or credential
files) to a private R2 bucket, writes a per-run manifest (sha256 per file),
rotates EVERY declared prefix by ITS OWN retention
(``state_backup.RETENTION``: ``backups/state`` 14 days, ``backups/client``
365 days — one threshold over both would silently delete the year-long client
set on day fifteen), and Telegram-notifies the admin with a one-line summary
naming what was deleted PER PREFIX. Standalone (as ``morning_digest.py``):
does not depend on the live bot process, sends directly via the Bot API.

DEV-46: после набора ``state`` пробуется КЛИЕНТСКИЙ набор
(``state_backup.run_client_backup``: снимок базы + реквизиты, зашифрованные
публичным ключом владельца). Нет ключа — задача НЕ падает и rc не портит
(это состояние настройки, а не авария), но отказ называется отдельной
строкой сводки. Ключ есть, а заливка упала — это ошибка: и в сводку, и в rc.

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


def format_rotation(deleted_by_prefix: dict, retention) -> str:
    """«backups/state — удалено 3 (>14д); backups/client — удалено 0 (>365д)».

    Удалённое называется ПО ПРЕФИКСАМ И СО СРОКОМ, а не одним числом: у
    префиксов разные сроки, и общее число не говорит, чей набор поехал. Префикс,
    у которого удалять было нечего, всё равно НАЗЫВАЕТСЯ — иначе «ротация не
    гонялась» и «нечего удалять» неотличимы.
    """
    order = [prefix for prefix, _ in retention]
    days = dict(retention)

    def _rank(prefix: str) -> int:
        return order.index(prefix) if prefix in order else len(order)

    parts = []
    for prefix in sorted(deleted_by_prefix, key=lambda x: (_rank(x), x)):
        keep = days.get(prefix)
        srok = f" (>{keep}д)" if keep is not None else ""
        parts.append(f"{prefix} — удалено {len(deleted_by_prefix[prefix])}{srok}")
    return "; ".join(parts)


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

    # Клиентский набор (DEV-46). Два РАЗНЫХ исхода, и склеивать их нельзя:
    #
    # * ключа нет — это осознанное состояние НАСТРОЙКИ, а не авария. Задача
    #   не падает и rc не портит: ронять её каждую ночь, пока владелец не
    #   завёл пару, значит приучить не смотреть на её алерты. Но МОЛЧАТЬ
    #   нельзя — отдельная строка в сводке;
    # * ключ есть, а заливка упала — это уже ошибка: и в сводку, и в rc.
    client_result = None
    client_refused: str | None = None
    client_error: Exception | None = None
    try:
        client_result = sb.run_client_backup(_ROOT)
    except sb.ClientBackupRefused as exc:
        client_refused = str(exc)
        logger.warning("state_backup: клиентский набор НЕ отправлен: %s", exc)
    except Exception as exc:
        client_error = exc
        logger.error("state_backup: клиентский набор упал: %s", exc)

    rotation_error: Exception | None = None
    deleted_by_prefix: dict = {}
    try:
        deleted_by_prefix = sb.rotate_all_backups()
    except Exception as exc:
        logger.warning("state_backup: rotation failed: %s", exc)
        rotation_error = exc

    text = sb.format_backup_result(result)
    if client_refused is not None:
        text += f"\n⚠️ клиентский набор НЕ отправлен: {client_refused}"
    elif client_error is not None:
        text += f"\n\U0001f6a8 Клиентский набор УПАЛ: {client_error}"
    elif client_result is not None:
        text += "\n" + sb.format_client_backup_result(client_result)
    if rotation_error is not None:
        # Сбой ротации сам бэкап не проваливает, но и молчать о нём нельзя.
        text += f"\n⚠️ Ротация НЕ выполнена: {rotation_error}"
    elif deleted_by_prefix:
        text += "\nРотация: " + format_rotation(deleted_by_prefix, sb.RETENTION)
    send_telegram(text)
    deleted_total = sum(len(keys) for keys in deleted_by_prefix.values())
    logger.info(
        "state_backup: run complete uploaded=%d failed=%d deleted=%d client=%s (%s)",
        len(result.uploaded), len(result.failed), deleted_total,
        "refused" if client_refused is not None
        else "error" if client_error is not None
        else f"uploaded={len(client_result.uploaded)} failed={len(client_result.failed)}"
        if client_result is not None else "none",
        ", ".join(f"{prefix}={len(keys)}" for prefix, keys in deleted_by_prefix.items())
        or "ротация не гонялась",
    )
    client_ok = client_error is None and (client_result is None or client_result.ok)
    return 0 if (result.ok and client_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
