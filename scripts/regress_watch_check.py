#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Standalone runaway-regress killer (Master-Plan Этап 1, хвост #6).

STANDALONE + stdlib-ONLY on purpose (mirrors ``boot_watch_check.py``): it must
run even if the bot's own imports are broken, because the very failure it
guards against is the bot dying and leaving an orphaned pytest grinding RAM.

The guardian invokes it each cycle when ``state/regress_watch.json`` exists:

    python scripts/regress_watch_check.py

If the recorded regress is past its wall-clock deadline OR its parent bot PID is
dead (orphan), it kills the whole PID-group (``taskkill /T /F``), TG-alerts the
admin («runaway-регресс убит»), and clears the marker. No-op when the marker is
absent or the regress is still legitimately running — so it is cheap to poll.

This is the SECOND, independent layer; the bot's periodic loop
(:func:`app.services.devtask.regress_watch.sweep`) is the first. Двойная
страховка: the loop catches an overdue regress while the bot is alive, this
catches an orphan after the bot has died.
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCH_PATH = ROOT / "state" / "regress_watch.json"
ENV_PATH = ROOT / ".env"
ADMIN_CHAT_ID = "237616472"


# ── pure decision functions (unit-tested) ──────────────────────────────────
def is_expired(watch: dict, now: float) -> bool:
    return float(now) > float(watch.get("deadline_epoch", 0))


def is_orphaned(watch: dict, parent_alive: bool) -> bool:
    return not bool(parent_alive)


def runaway_reason(watch: dict, now: float, parent_alive: bool):
    """Orphan (dead parent) takes precedence over an expired deadline."""
    if is_orphaned(watch, parent_alive):
        return "orphan"
    if is_expired(watch, now):
        return "deadline"
    return None


def alert_text(watch: dict, reason: str) -> str:
    reasons = {
        "deadline": "истёк дедлайн (wall-clock)",
        "orphan": "родитель-бот мёртв (сирота)",
    }
    return (
        "🛑 Runaway-регресс убит: PID-группа %s (%s), причина: %s.\n"
        "Оставшиеся pytest-процессы прибиты (taskkill /T /F), state очищен."
        % (watch.get("pid"), watch.get("label") or "regress",
           reasons.get(reason, reason))
    )


# ── io helpers (stdlib only; not unit-tested) ──────────────────────────────
def _pid_alive(pid) -> bool:
    """True iff the process still exists. On doubt (tasklist error) fail-SAFE to
    True — never declare an orphan we cannot prove, so a legit regress is not
    killed just because the query hiccupped (the deadline still bounds it)."""
    if pid is None:
        return False
    try:
        if sys.platform == "win32":
            r = subprocess.run(["tasklist", "/FI", "PID eq %d" % int(pid)],
                               capture_output=True, text=True, timeout=10,
                               encoding="utf-8", errors="replace")
            return str(int(pid)) in (r.stdout or "")
        return Path("/proc/%d" % int(pid)).exists()
    except Exception:
        return True


def _kill_group(pid) -> bool:
    if pid is None:
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(int(pid))],
                           capture_output=True, text=True, timeout=15,
                           encoding="utf-8", errors="replace")
        else:
            import os as _os
            import signal as _sig
            _os.kill(int(pid), _sig.SIGKILL)
        return True
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
    parent_alive = _pid_alive(watch.get("bot_pid"))
    reason = runaway_reason(watch, now, parent_alive)
    if reason is None:
        return 0
    _kill_group(watch.get("pid"))
    _send_tg(alert_text(watch, reason))
    try:
        WATCH_PATH.unlink()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
