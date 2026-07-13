# -*- coding: utf-8 -*-
"""Yesterday-vs-today IG snapshot for the morning digest (``state/daily_metrics.json``).

One JSON, key — ``account_key``, value — ``{followers, media_count}`` (the
LAST recorded measurement). Same storage contract as ``app.services.ig_accounts``:
relative default path with an env override (``DAILY_METRICS_FILE``) for test
isolation, atomic write (``.tmp`` + ``os.replace``), in-process ``RLock``.

:func:`record_and_diff` is the only entry point: it compares the current
reading against the stored snapshot, returns the delta, then overwrites the
snapshot with the current reading — so tomorrow's run diffs against today's.
A ``None`` reading (failed fetch) never clobbers the stored baseline: the
previous value is kept so a transient failure doesn't erase yesterday's
measurement.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_STATE_FILE = Path("state") / "daily_metrics.json"
_LOCK = threading.RLock()


def _state_file() -> Path:
    raw = os.getenv("DAILY_METRICS_FILE", "").strip()
    return Path(raw) if raw else _DEFAULT_STATE_FILE


def _load() -> Dict[str, Any]:
    f = _state_file()
    if not f.exists():
        return {"accounts": {}}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("daily_metrics: state unreadable (%s); treating as empty", exc)
        return {"accounts": {}}
    if not isinstance(data, dict):
        return {"accounts": {}}
    data.setdefault("accounts", {})
    return data


def _save_atomic(state: Dict[str, Any]) -> None:
    f = _state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)


def record_and_diff(
    account_key: str, followers: Optional[int], media_count: Optional[int]
) -> Dict[str, Optional[int]]:
    """Diff ``(followers, media_count)`` against the stored snapshot, then save it.

    Returns ``{"followers_delta", "media_delta"}`` — ``None`` for a field when
    either the current reading or the prior snapshot is missing.
    """
    with _LOCK:
        state = _load()
        prev = state["accounts"].get(account_key) or {}
        prev_followers = prev.get("followers")
        prev_media = prev.get("media_count")

        followers_delta = (
            followers - prev_followers
            if followers is not None and prev_followers is not None
            else None
        )
        media_delta = (
            media_count - prev_media
            if media_count is not None and prev_media is not None
            else None
        )

        state["accounts"][account_key] = {
            "followers": followers if followers is not None else prev_followers,
            "media_count": media_count if media_count is not None else prev_media,
        }
        _save_atomic(state)

    return {"followers_delta": followers_delta, "media_delta": media_delta}
