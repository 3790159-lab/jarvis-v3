#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DOWN alerter for the chatter Telethon runner (arc 2 liveness).

STANDALONE and stdlib-ONLY on purpose (like boot_watch_check.py / ops_watchdog.py):
it must run even if the chatter package or its deps are broken. JarvisChatterGuardian
invokes it once per DOWN cycle:

    python scripts/chatter_watch_check.py

It reads the runner's heartbeat (state/chatter_heartbeat.txt). If the heartbeat
is stale (runner dead or hung), it Telegram-alerts the ADMIN via the main Jarvis
bot token -- NOT via the chatter userbot itself (which is exactly what's down) --
with a cooldown so a prolonged outage doesn't spam. A fresh heartbeat is a no-op.

The alert reaches the operator (chat 237616472) so the first client is never left
without a bot after a crash / Windows Update reboot.
"""
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEARTBEAT_PATH = ROOT / "state" / "chatter_heartbeat.txt"
MARKER_PATH = ROOT / "state" / "chatter_watch_alert.json"
ENV_PATH = ROOT / ".env"
# P1P2 слой процесса: после cutover plaintext .env шредится (§6 п.9) — токен
# живёт в .env.enc (machine-scope DPAPI + entropy). Plaintext ниже — легаси
# до шреда.
ENV_ENC_PATH = ROOT / ".env.enc"
ADMIN_CHAT_ID = "237616472"
HEARTBEAT_MAX_AGE_S = 180      # matches the guardian's tolerance for a transient stall
ALERT_COOLDOWN_S = 3600        # at most one DOWN alert per hour


# ── pure decision functions (unit-tested) ──────────────────────────────────
def is_heartbeat_fresh(hb_text, *, now: float, max_age: float) -> bool:
    """True only if `hb_text` is a numeric unix-seconds stamp within max_age."""
    if not hb_text:
        return False
    try:
        last = int(str(hb_text).strip())
    except (ValueError, TypeError):
        return False
    return (now - last) <= max_age


def should_alert(*, is_down: bool, last_alert_ts, now: float, cooldown: float) -> bool:
    """Alert only if currently down AND we haven't alerted within the cooldown."""
    if not is_down:
        return False
    if last_alert_ts is None:
        return True
    return (now - float(last_alert_ts)) >= cooldown


def resolve_is_down(state_arg, hb_text, *, now: float, max_age: float) -> bool:
    """Decide DOWN from the guardian's verdict when it gave one, else from the
    heartbeat.

    The guardian and this script had DIFFERENT definitions of "down", and the
    weaker one won: the guardian calls DOWN when the PROCESS is gone (after ~90s
    of debounce), but this script only ever looked at heartbeat age (>180s). A
    killed runner leaves a heartbeat that stays "fresh" for another ~90s, so the
    alerter contradicted the guardian and said nothing — a real 85s outage went
    unannounced (drill, 13:06:25). Last night's 🔴 only fired because the relaunch
    was ALSO broken, which let the heartbeat rot past 180s; fixing cold-start
    would have silently taken the alert away with it.

    The guardian checks process existence + heartbeat + debounce, so it is the
    authority. `state_arg` is that verdict ("down"/"up"). Falling back to the
    heartbeat keeps this script standalone for an external/manual invocation."""
    if state_arg == "down":
        return True
    if state_arg == "up":
        return False
    return not is_heartbeat_fresh(hb_text, now=now, max_age=max_age)


def should_notify_recovery(*, is_down: bool, alerted: bool) -> bool:
    """Send the ✅ only to close a 🔴 we actually sent: up now AND we alerted
    before. A DOWN alert with no paired recovery leaves the operator unable to
    tell "fixed itself" from "still broken, alerter died" -- so the pair is the
    contract, and `alerted` (from the marker) is what makes it survive a
    guardian restart. No cooldown here: a recovery is a one-shot edge, deduped
    by clearing the flag."""
    return (not is_down) and bool(alerted)


def alert_text() -> str:
    return (
        "🔴 chatter-раннер (Telethon-юзербот) НЕ отвечает: heartbeat устарел "
        "(процесс мёртв или завис). JarvisChatterGuardian пытается перезапустить. "
        "Если не поднимется — проверь logs/chatter_telethon.log и "
        "`schtasks /Run /TN JarvisChatterGuardian`. Пока раннер лежит, входящие "
        "в личку копятся непрочитанными (catch-up подхватит их за 24ч после старта)."
    )


def recovery_text() -> str:
    return (
        "✅ chatter-раннер (Telethon-юзербот) снова живой: heartbeat свежий, "
        "MTProto подключён. Пропущенное за время простоя catch-up подхватил "
        "(лог: logs/chatter_telethon.log, строка `catch-up: N dialog(s)`). "
        "Можно расслабиться."
    )


# ── stdlib-only IO (exercised live, not unit-tested) ───────────────────────
def _read_marker() -> dict:
    try:
        return json.loads(MARKER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_marker(now: float, *, alerted: bool) -> None:
    try:
        MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        MARKER_PATH.write_text(
            json.dumps({"last_alert_ts": now, "alerted": alerted}), encoding="utf-8")
    except Exception:
        pass


def _token_from_enc() -> str:
    """Токен из .env.enc. Standalone-инвариант алертера НЕ нарушаем: сбой
    ЛЮБОГО рода (нет entropy, tamper, битый chatter-пакет) не делает алертер
    немым — печатаем в stdout (лог гардиана) и отдаём "" для легаси-ветки."""
    if not ENV_ENC_PATH.exists():
        return ""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from chatter.security.crypto import decrypt_from_file
        from chatter.security.secret_loader import parse_env_text
        entropy = os.environ.get("JARVIS_ENTROPY_FILE") \
            or str(ROOT / ".secrets" / "entropy.bin")
        values = parse_env_text(
            decrypt_from_file(ENV_ENC_PATH, entropy_path=entropy)
            .decode("utf-8-sig"))
        return (values.get("TELEGRAM_BOT_TOKEN")
                or values.get("BOT_TOKEN") or "")
    except Exception as exc:  # noqa: BLE001 - алертер обязан пережить всё
        print("[chatter_watch_check] .env.enc unreadable (%s: %s) - "
              "falling back to legacy plaintext .env" % (type(exc).__name__, exc))
        return ""


def _bot_token() -> str:
    token = _token_from_enc()
    if token:
        return token
    try:
        env = ENV_PATH.read_text(encoding="utf-8")
    except Exception:
        return ""
    m = re.search(r'^\s*(?:TELEGRAM_BOT_TOKEN|BOT_TOKEN)\s*=\s*"?([^"\r\n]+)"?', env, re.M)
    return m.group(1).strip() if m else ""


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


def main(argv=None) -> int:
    # --state is the GUARDIAN's verdict (it knows about the process; we only see
    # a file). Optional on purpose: without it this stays a standalone
    # heartbeat-only checker, like boot_watch_check.py.
    argv = sys.argv[1:] if argv is None else argv
    state_arg = None
    if "--state" in argv:
        i = argv.index("--state")
        if i + 1 < len(argv) and argv[i + 1] in ("down", "up"):
            state_arg = argv[i + 1]

    now = time.time()
    try:
        hb_text = HEARTBEAT_PATH.read_text(encoding="ascii")
    except Exception:
        hb_text = None
    is_down = resolve_is_down(state_arg, hb_text, now=now, max_age=HEARTBEAT_MAX_AGE_S)
    marker = _read_marker()
    last = marker.get("last_alert_ts")
    # Legacy markers (pre-recovery-pairing) have no `alerted` key -> False: we do
    # not retro-fire a ✅ for an outage that predates the feature.
    alerted = bool(marker.get("alerted", False))

    if should_notify_recovery(is_down=is_down, alerted=alerted):
        if _send_tg(recovery_text()):
            # Keep last_alert_ts: it still guards the DOWN cooldown, so a FLAPPING
            # runner cannot spam 🔴/✅ pairs. Only the pairing flag is cleared.
            _write_marker(last if last is not None else now, alerted=False)
            print("[chatter_watch_check] RECOVERY alert sent to admin")
        else:
            # DEV-18: do not clear the flag on a failed send -- retry next cycle
            # rather than silently swallowing the operator's ✅.
            print("[chatter_watch_check] recovered but TG alert failed (no token / network)")
        return 0

    if should_alert(is_down=is_down, last_alert_ts=last, now=now, cooldown=ALERT_COOLDOWN_S):
        if _send_tg(alert_text()):
            # alerted=True is what obliges us to send the paired ✅ later.
            _write_marker(now, alerted=True)
            print("[chatter_watch_check] DOWN alert sent to admin")
        else:
            print("[chatter_watch_check] DOWN but TG alert failed (no token / network)")
    else:
        print("[chatter_watch_check] ok" if not is_down else "[chatter_watch_check] down, within cooldown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
