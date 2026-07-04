#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Crash-loop alert for the dev-task merge→restart flow (plan Допущение 11).

STANDALONE and stdlib-ONLY on purpose: it must survive even a merge that breaks
the bot's own imports. The guardian invokes it once per DOWN cycle:

    python scripts/boot_watch_check.py

If a merge wrote ``state/dev_tasks/boot_watch.json`` and the bot has NOT come up
healthy by the deadline (heartbeat still stale), it Telegram-alerts the admin
with ready-made rollback commands, then marks the marker ``alerted`` (single
shot). A healthy boot clears the marker first (bot-side reconcile), so this
never fires when the bot is alive.
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCH_PATH = ROOT / "state" / "dev_tasks" / "boot_watch.json"
HEARTBEAT_PATH = ROOT / "state" / "bot_heartbeat.txt"
ENV_PATH = ROOT / ".env"
ADMIN_CHAT_ID = "237616472"
HEARTBEAT_MAX_AGE_S = 90


# ── pure decision functions (unit-tested) ──────────────────────────────────
def is_overdue(watch: dict, now: float) -> bool:
    return now > float(watch.get("deadline_epoch", 0))


def should_alert(watch: dict, now: float, hb_fresh: bool) -> bool:
    """Alert only if past deadline AND bot still not healthy AND not yet alerted."""
    return is_overdue(watch, now) and not hb_fresh and not watch.get("alerted", False)


def alert_text(watch: dict) -> str:
    return (
        "🚨 Dev-мердж %s сломал загрузку бота (crash-loop, здоровый boot не "
        "случился за отведённое время).\n\nОТКАТ:\n%s"
        % (watch.get("task_id", "?"), watch.get("rollback_cmds", "нет команд"))
    )


# ── io helpers (not unit-tested; stdlib only) ──────────────────────────────
def _heartbeat_fresh(now: float) -> bool:
    try:
        last = int(HEARTBEAT_PATH.read_text(encoding="utf-8").strip())
        return (now - last) <= HEARTBEAT_MAX_AGE_S
    except Exception:
        return False


def _bot_token() -> str:
    try:
        env = ENV_PATH.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'^\s*(?:TELEGRAM_BOT_TOKEN|BOT_TOKEN)\s*=\s*"?([^"\r\n]+)"?', env, re.M)
        return m.group(1).strip() if m else ""
    except Exception:
        return ""


def _send_tg(text: str) -> bool:
    token = _bot_token()
    if not token:
        return False
    payload = json.dumps({"chat_id": ADMIN_CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendMessage" % token,
        data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception:
        return False


def main() -> int:
    if not WATCH_PATH.exists():
        return 0
    try:
        watch = json.loads(WATCH_PATH.read_text(encoding="utf-8"))
    except Exception:
        return 0
    now = time.time()
    if not should_alert(watch, now, _heartbeat_fresh(now)):
        return 0
    _send_tg(alert_text(watch))
    watch["alerted"] = True                       # single-shot
    try:
        WATCH_PATH.rename(WATCH_PATH.with_name("boot_watch.alerted.json"))
        WATCH_PATH.with_name("boot_watch.alerted.json").write_text(
            json.dumps(watch, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
