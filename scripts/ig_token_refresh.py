# -*- coding: utf-8 -*-
"""Instagram long-lived token auto-refresh job (Path B / Instagram Login).

Runs from the JarvisIgTokenRefresh scheduled task (daily). It refreshes the
long-lived IG token before it can expire, but only when it is old enough
(default: >= 30 days since the last successful refresh) — an age-gate so a
missed run (PC off on the exact day) can't let the token lapse.

Fail-closed by construction:
  * The refresh call must return a non-empty access_token, or we abort.
  * The old token is overwritten ONLY after a successful refresh.
  * ANY failure -> the old token is left untouched and a Telegram ALARM is sent.

Multi-account (see ``app.services.ig_accounts``): when
``state/ig_accounts.json`` has entries, every account is walked and refreshed
independently — its own age-gate (per-account ``token_refreshed_at`` field,
not a shared stamp file) and its own alarm naming the account. No json yet
(pre-migration machines) -> falls back to the original single-account ``.env``
flow untouched (``_refresh_legacy`` — same functions/behaviour as before
multi-account support existed).

Standalone: loads .env via env_bootstrap (does NOT depend on the live bot).
Notifications go straight to the Bot API (TELEGRAM_BOT_TOKEN +
TELEGRAM_ALLOWED_CHAT_ID), so alerts work even if the bot process is down.

    python scripts/ig_token_refresh.py            # run the age-gated job
    python scripts/ig_token_refresh.py --force    # refresh regardless of age
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

logger = logging.getLogger("jarvis.oauth")

ENV_PATH = _ROOT / ".env"
STAMP_PATH = _ROOT / "state" / "ig_token_refreshed_at"
MIN_AGE_DAYS = 30.0
_TG_TIMEOUT = 20


# ── pure helpers (unit-tested) ─────────────────────────────────────────────

def read_age_days(stamp_path: str) -> "float | None":
    """Days since the last successful refresh, or None if never recorded."""
    p = Path(stamp_path)
    if not p.exists():
        return None
    try:
        ts = float(p.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None
    return (time.time() - ts) / 86400.0


def age_days_from_timestamp(ts) -> "float | None":
    """Same as :func:`read_age_days` but from an in-memory timestamp (used for
    the per-account ``token_refreshed_at`` field instead of a stamp file)."""
    if ts is None:
        return None
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return None
    return (time.time() - ts) / 86400.0


def should_refresh(age_days: "float | None", min_age_days: float = MIN_AGE_DAYS) -> bool:
    """Refresh if we have no baseline yet, or the token is old enough.

    Never refresh a token younger than 24h — Meta rejects that — but the
    age-gate (>= min_age_days) already keeps us well clear of that floor.
    """
    if age_days is None:
        return True
    return age_days >= min_age_days


def update_env_token(env_path: str, new_token: str) -> None:
    """Replace the IG_ACCESS_TOKEN value in .env, surgically and atomically.

    Backs up the original first. Fail-closed: refuses an empty token and
    refuses to write if no IG_ACCESS_TOKEN line exists (that is a config error,
    not something to paper over by appending).
    """
    if not new_token:
        raise ValueError("refusing to write an empty IG_ACCESS_TOKEN")
    p = Path(env_path)
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)

    idx = next((i for i, ln in enumerate(lines)
                if ln.lstrip().startswith("IG_ACCESS_TOKEN=")), None)
    if idx is None:
        raise ValueError("IG_ACCESS_TOKEN line not found in .env — aborting")

    # backup (single rolling backup, timestamped for traceability)
    backup = p.with_name(p.name + f".bak_igrefresh_{int(time.time())}")
    backup.write_text("".join(lines), encoding="utf-8")

    newline = "\n" if lines[idx].endswith("\n") else ""
    lines[idx] = f"IG_ACCESS_TOKEN={new_token}{newline}"

    tmp = p.with_name(p.name + ".tmp_igrefresh")
    tmp.write_text("".join(lines), encoding="utf-8")
    os.replace(str(tmp), str(p))  # atomic on same volume


def write_stamp(stamp_path: str) -> None:
    p = Path(stamp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(time.time()), encoding="utf-8")


def send_telegram(text: str) -> bool:
    """Best-effort admin notification via the Bot API. Never raises.

    Standalone job (see module docstring) — it does not go through
    ``app.services.notifications``, so it must consult the shared
    isolation guard itself or a test invoking this function directly
    (rather than via a subprocess) would reach the real admin chat.
    """
    from app.core.notify_isolation import telegram_send_blocked
    if telegram_send_blocked():
        logger.info("ig_token_refresh: telegram send suppressed under test isolation")
        return False
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    if not token or not chat_id:
        logger.warning("ig_token_refresh: no TELEGRAM_BOT_TOKEN/CHAT_ID — skipping notify")
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
        with urllib.request.urlopen(req, timeout=_TG_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8")).get("ok", False)
    except Exception as exc:
        logger.warning("ig_token_refresh: telegram notify failed: %s", exc)
        return False


# ── orchestration ──────────────────────────────────────────────────────────

def _refresh_legacy(force: bool) -> int:
    """Original single-account flow — unchanged, used only when
    ``state/ig_accounts.json`` has no accounts yet (pre-migration)."""
    age = read_age_days(str(STAMP_PATH))
    if not force and not should_refresh(age):
        age_str = f"{age:.1f}d" if age is not None else "n/a"
        logger.info("ig_token_refresh: token age %s < %.0fd — skip", age_str, MIN_AGE_DAYS)
        return 0

    current = os.getenv("IG_ACCESS_TOKEN", "").strip()
    if not current:
        send_telegram("⚠️ IG token refresh ALARM: IG_ACCESS_TOKEN отсутствует в .env — не могу обновить. Старый токен не тронут.")
        logger.error("ig_token_refresh: no IG_ACCESS_TOKEN in env — abort")
        return 2

    try:
        from app.services.instagram_api import refresh_long_lived_token
        new_token = refresh_long_lived_token(current)          # fail-closed inside
        update_env_token(str(ENV_PATH), new_token)             # backup + atomic
        write_stamp(str(STAMP_PATH))
    except Exception as exc:
        # FAIL-CLOSED: .env untouched (update_env_token only writes on success),
        # old token remains valid. Alarm the admin.
        send_telegram(f"⚠️ IG token refresh ПРОВАЛ: {exc}. Старый токен НЕ тронут, "
                      f"он ещё жив. Разберись до истечения ~60д.")
        logger.error("ig_token_refresh: FAILED (old token kept): %s", exc)
        return 1

    send_telegram(f"✅ IG token обновлён (long-lived, ещё ~60 дней). "
                  f"prefix={new_token[:8]}…, обновлён в .env.")
    logger.info("ig_token_refresh: success, token refreshed (prefix=%s...)", new_token[:8])
    return 0


def _refresh_account(account_key: str, acct: dict, force: bool) -> int:
    """Age-gate + refresh ONE account from the json store. Fail-closed: the
    stored token is overwritten only after a successful refresh; any failure
    leaves it untouched and alarms the admin BY ACCOUNT NAME."""
    from app.services import ig_accounts as _iga

    age = age_days_from_timestamp(acct.get("token_refreshed_at"))
    if not force and not should_refresh(age):
        age_str = f"{age:.1f}d" if age is not None else "n/a"
        logger.info("ig_token_refresh[%s]: token age %s < %.0fd — skip",
                    account_key, age_str, MIN_AGE_DAYS)
        return 0

    current = str(acct.get("access_token") or "").strip()
    if not current:
        send_telegram(f"⚠️ IG token refresh ALARM [{account_key}]: access_token отсутствует "
                      f"в state/ig_accounts.json — не могу обновить.")
        logger.error("ig_token_refresh[%s]: no access_token — abort", account_key)
        return 2

    try:
        from app.services.instagram_api import refresh_long_lived_token
        new_token = refresh_long_lived_token(current)          # fail-closed inside
        _iga.save_account(
            account_key, ig_user_id=str(acct.get("ig_user_id") or ""),
            username=str(acct.get("username") or ""),
            access_token=new_token, token_refreshed_at=time.time(),
        )
    except Exception as exc:
        # FAIL-CLOSED: save_account only called on success, old token kept.
        send_telegram(f"⚠️ IG token refresh ПРОВАЛ [{account_key}]: {exc}. "
                      f"Старый токен НЕ тронут, он ещё жив. Разберись до истечения ~60д.")
        logger.error("ig_token_refresh[%s]: FAILED (old token kept): %s", account_key, exc)
        return 1

    send_telegram(f"✅ IG token обновлён [{account_key}] (long-lived, ещё ~60 дней). "
                  f"prefix={new_token[:8]}…")
    logger.info("ig_token_refresh[%s]: success, token refreshed (prefix=%s...)",
               account_key, new_token[:8])
    return 0


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    force = "--force" in argv

    # load .env into os.environ (P2), same as the guardians
    try:
        import app.env_bootstrap  # noqa: F401  side-effect: loads .env
    except Exception as exc:
        logger.error("ig_token_refresh: env bootstrap failed: %s", exc)

    from app.services import ig_accounts as _iga
    _iga.ensure_migrated()
    accounts = _iga.list_accounts()

    if not accounts:
        return _refresh_legacy(force)

    overall_rc = 0
    for account_key, acct in accounts.items():
        rc = _refresh_account(account_key, acct, force)
        if rc != 0 and overall_rc == 0:
            overall_rc = rc
    return overall_rc


if __name__ == "__main__":
    raise SystemExit(main())
