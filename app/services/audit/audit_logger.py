# -*- coding: utf-8 -*-
"""Per-day JSONL audit log of bot user activity, plus admin Telegram forwards.

The log lives under ``state/audit/{YYYY-MM-DD}.jsonl`` (override with
``JARVIS_AUDIT_DIR`` for tests). Every event from every user — admin
included — is appended; this is the system of record. Selected events
(:data:`FORWARDABLE_EVENTS`) are additionally forwarded to
``JARVIS_ADMIN_USER_ID`` via Telegram ``sendMessage`` so the operator
gets real-time visibility into key moments without having to tail logs.

``user_first_seen`` is synthetic: the first event for any ``user_id``
not present in any prior day's log auto-emits ``user_first_seen`` BEFORE
the real event.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_AUDIT_DIR = Path("state/audit")

FORWARDABLE_EVENTS: frozenset[str] = frozenset({
    "user_first_seen",
    "swapbatch_batch_received",
    "swapbatch_go",
    "animate_started",
    "animate_batch_done",
    "error",
})


def _audit_dir() -> Path:
    raw = os.getenv("JARVIS_AUDIT_DIR", "").strip()
    return Path(raw) if raw else _DEFAULT_AUDIT_DIR


def _today_file(now: Optional[datetime] = None) -> Path:
    when = now or datetime.now().astimezone()
    return _audit_dir() / f"{when.strftime('%Y-%m-%d')}.jsonl"


# In-memory set of user_ids that have appeared in any audit file. Used to
# detect first_seen without re-scanning disk on every event. Reset when the
# audit dir changes — see _reset_seen_users_cache().
_seen_users_cache: Optional[set[int]] = None
_seen_users_cache_dir: Optional[str] = None


def _load_seen_users() -> set[int]:
    global _seen_users_cache, _seen_users_cache_dir
    cur_dir = str(_audit_dir())
    if _seen_users_cache is not None and _seen_users_cache_dir == cur_dir:
        return _seen_users_cache
    seen: set[int] = set()
    d = _audit_dir()
    if d.exists():
        for f in sorted(d.glob("*.jsonl")):
            try:
                with f.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        uid = obj.get("user_id")
                        if isinstance(uid, int):
                            seen.add(uid)
            except OSError:
                pass
    _seen_users_cache = seen
    _seen_users_cache_dir = cur_dir
    return _seen_users_cache


def _reset_seen_users_cache() -> None:
    """Test-only: drop the in-memory seen-users cache."""
    global _seen_users_cache, _seen_users_cache_dir
    _seen_users_cache = None
    _seen_users_cache_dir = None


def _write_event(event_obj: Dict[str, Any], now: Optional[datetime] = None) -> None:
    f = _today_file(now)
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event_obj, ensure_ascii=False) + "\n")


def _format_admin_message(
    event: str,
    username: Optional[str],
    user_id: int,
    details: Dict[str, Any],
) -> str:
    name = username or "(no @username)"
    header = f"👤 {name} ({user_id})"
    if event == "user_first_seen":
        body = "📸 New user: first interaction"
    elif event == "swapbatch_batch_received":
        n = details.get("after_dedupe_count")
        if n is None:
            n = details.get("photos_count", 0)
        est = details.get("total_usd")
        eta = details.get("minutes")
        if est is not None or eta is not None:
            body = (
                f"📊 Batch: {n} photos | est ${(est or 0):.2f} "
                f"| ETA ~{int(eta or 0)}min"
            )
        else:
            body = f"📊 Batch: {n} photos received"
    elif event == "swapbatch_go":
        targets = details.get("targets", 0)
        body = f"🚀 Swap started: {targets} targets"
    elif event == "animate_started":
        targets = details.get("targets", 0)
        fps = details.get("fps")
        dur = details.get("duration_sec")
        mode = details.get("mode")
        suffix = ""
        if fps and dur:
            suffix = f" | {fps}fps × {dur}s"
        prefix = f"🎬 Animation started ({mode}): " if mode else "🎬 Animation started: "
        body = f"{prefix}{targets} videos{suffix}"
    elif event == "animate_batch_done":
        ok = details.get("success", 0)
        failed = details.get("failed", 0)
        cost = float(details.get("total_cost_usd", 0.0) or 0.0)
        mins = details.get("minutes")
        body = f"✅ Done: {ok} videos"
        if failed:
            body += f" ({failed} failed)"
        body += f" | ${cost:.2f}"
        if mins is not None:
            body += f" | {int(mins)}min"
    elif event == "error":
        handler = details.get("handler", "unknown")
        exc_cls = details.get("exception_class", "Error")
        msg = details.get("message", "")
        body = f"⚠️ Error in {handler}: {exc_cls}: {msg}"
    else:
        body = f"ℹ️ {event}"
    return f"{header}\n{body}"


def _forward_to_admin(text: str) -> None:
    """Send ``text`` to the admin chat via Telegram ``sendMessage``.

    Pure network call — gating (forward enabled, admin set, bot token set)
    lives in :func:`_maybe_forward` so tests can spy on this function
    without env-flag interference.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    admin_raw = os.getenv("JARVIS_ADMIN_USER_ID", "").strip()
    if not bot_token or not admin_raw:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": admin_raw,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def _maybe_forward(obj: Dict[str, Any]) -> None:
    event = obj.get("event", "")
    if event not in FORWARDABLE_EVENTS:
        return
    if os.getenv("JARVIS_AUDIT_FORWARD_ENABLED", "1").strip() != "1":
        return
    admin_raw = os.getenv("JARVIS_ADMIN_USER_ID", "").strip()
    if not admin_raw:
        return  # nobody to forward to
    user_id = obj.get("user_id")
    if not isinstance(user_id, int):
        return
    try:
        if int(admin_raw) == user_id:
            return  # don't forward the admin's own activity to themselves
    except ValueError:
        return
    text = _format_admin_message(
        event, obj.get("username"), user_id, obj.get("details") or {}
    )
    try:
        _forward_to_admin(text)
    except Exception as exc:  # noqa: BLE001 - forwarding must never raise
        logger.warning("audit: forward to admin failed: %s", exc)


def audit_event(
    user_id: int,
    username: Optional[str],
    chat_id: str,
    event: str,
    details: Optional[Dict[str, Any]] = None,
    *,
    _now: Optional[datetime] = None,
) -> None:
    """Append an audit event and (best-effort) forward to admin.

    The first event observed for any ``user_id`` (across all historical days)
    auto-prepends a ``user_first_seen`` event with the same identity fields.
    """
    details = details or {}
    when = _now or datetime.now().astimezone()

    seen = _load_seen_users()
    if user_id not in seen and event != "user_first_seen":
        first_obj = {
            "ts": when.isoformat(),
            "user_id": user_id,
            "username": username,
            "chat_id": str(chat_id),
            "event": "user_first_seen",
            "details": {},
        }
        try:
            _write_event(first_obj, when)
        except Exception as exc:  # noqa: BLE001 - audit must not raise
            logger.warning("audit: write user_first_seen failed: %s", exc)
        seen.add(user_id)
        _maybe_forward(first_obj)

    obj = {
        "ts": when.isoformat(),
        "user_id": user_id,
        "username": username,
        "chat_id": str(chat_id),
        "event": event,
        "details": details,
    }
    try:
        _write_event(obj, when)
    except Exception as exc:  # noqa: BLE001 - audit must not raise
        logger.warning("audit: write %s failed: %s", event, exc)
    seen.add(user_id)
    _maybe_forward(obj)
