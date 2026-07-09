# -*- coding: utf-8 -*-
"""Canary credit preflight — refuse a doomed CC BEFORE the expensive spawn.

Money-safety (Этап 1, Фаза 8.1): a copek-cheap canary request
(``POST /v1/messages``, haiku, ``max_tokens=1``) is fired against the live
Anthropic key to classify balance/key health. A dead balance answers
``400 "credit balance is too low"`` for **$0**, so we can honestly refuse the
task in the queue instead of spawning a Claude Code that will die on its first
request (incident 2026-07-09).

Design: pure and injectable. ``post`` is the ONLY transport seam; tests inject a
fake for every branch and NEVER touch the real API. The canary is a cheap early
cut, NOT a hard gate — any transport error / timeout / unrecognised response
fails **OPEN** (``ok=True``), because a mid-run dead balance is already surfaced
on the fly as ``cc_error`` (commit b658603). We only ever *block* on the two
responses that unambiguously mean "this CC cannot possibly succeed".
"""
from __future__ import annotations

import os
from typing import Callable, Dict, Optional

MESSAGES_URL = "https://api.anthropic.com/v1/messages"
CANARY_MODEL = "claude-haiku-4-5"
ANTHROPIC_VERSION = "2023-06-01"
_TIMEOUT_S = 15

_CREDIT_LOW = "credit balance is too low"
_INVALID_KEY = "invalid x-api-key"


def _default_post(url, **kwargs):
    import requests
    return requests.post(url, **kwargs)


def preflight_credit_check(post: Optional[Callable] = None) -> Dict[str, object]:
    """Classify Anthropic balance/key health via a $0 canary.

    Returns ``{"ok": bool, "reason": str|None}``:
      * HTTP 200 → ``ok=True``
      * HTTP 400 + "credit balance is too low" (case-insensitive) →
        ``ok=False, reason="credit balance too low"``
      * HTTP 401 + "invalid x-api-key" →
        ``ok=False, reason="ANTHROPIC_API_KEY невалиден"``
      * network / timeout / any other response → **fail-OPEN** ``ok=True``.

    ``post(url, headers=…, json=…, timeout=…)`` is injected in tests; the default
    uses ``requests.post`` against the real API (never exercised under pytest).
    """
    post = post or _default_post
    headers = {
        "x-api-key": os.getenv("ANTHROPIC_API_KEY", ""),
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    payload = {
        "model": CANARY_MODEL,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    try:
        resp = post(MESSAGES_URL, headers=headers, json=payload, timeout=_TIMEOUT_S)
        status = getattr(resp, "status_code", None)
        body = (getattr(resp, "text", "") or "").lower()
    except Exception:
        return {"ok": True, "reason": None}  # fail-OPEN: transport/timeout

    if status == 200:
        return {"ok": True, "reason": None}
    if status == 400 and _CREDIT_LOW in body:
        return {"ok": False, "reason": "credit balance too low"}
    if status == 401 and _INVALID_KEY in body:
        return {"ok": False, "reason": "ANTHROPIC_API_KEY невалиден"}
    return {"ok": True, "reason": None}  # fail-OPEN: any other response
