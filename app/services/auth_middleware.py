"""Default-deny auth middleware — Phase 1 (canary).

Security audit 2026-07-15 follow-up. The tunnel exposes the whole :8010 backend;
gating ~40 routers one-by-one is error-prone. This middleware is the single
choke point: every request is either on the public allow-list or must carry a
valid API key.

Phase 1 (this file) runs in **canary** mode only — it computes the allow/deny
decision but NEVER blocks; it logs and records every request that *would* be
denied, so we can find internal callers lacking a key before enforcing. Phase 2
adds the enforce path (return 401 on deny) once the canary list is reviewed.

Modes (env ``JARVIS_AUTH_MIDDLEWARE_MODE``):
  * ``off``    — do nothing (default; safe to merge and deploy).
  * ``canary`` — decide + record would-deny, always pass through.
Any other value is treated as canary in this phase, so enforcement can never be
switched on before Phase 2 lands.

The tunnel forwards internet traffic to localhost, so the client address is
always 127.0.0.1 — authorization is key-based only; loopback is NOT trusted.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from app.services.api_auth import is_authorized

_log = logging.getLogger("jarvis.auth_canary")

# Paths reachable without a key: exact matches plus the "*/health" suffix rule.
PUBLIC_EXACT = frozenset(
    {
        "/health",
        "/oauth/callback",
        "/telegram/webhook",  # self-secured by its own secret-token check
    }
)


def _mode() -> str:
    return (os.getenv("JARVIS_AUTH_MIDDLEWARE_MODE", "off").strip().lower() or "off")


def is_public_path(path: str) -> bool:
    """True if ``path`` is reachable without authentication."""
    if path in PUBLIC_EXACT:
        return True
    if path.endswith("/health"):
        return True
    return False


def classify(path: str, provided_key: Optional[str]) -> str:
    """Return ``"allow"`` or ``"deny"`` — shared by canary and (future) enforce."""
    if is_public_path(path):
        return "allow"
    if is_authorized(provided_key):
        return "allow"
    return "deny"


# In-memory dedup so a chatty caller does not flood the log/file. Keyed by
# (method, path); persists for the process lifetime (fine for a canary run).
_seen: set = set()


def _canary_path() -> Path:
    return Path("state") / "auth_canary.jsonl"


def record_canary_event(event: dict) -> bool:
    """Log + persist a would-deny event once per (method, path).

    Returns True if newly recorded, False if a duplicate was suppressed.
    """
    key = (event.get("method"), event.get("path"))
    if key in _seen:
        return False
    _seen.add(key)
    _log.warning(
        "AUTH-CANARY would-deny method=%s path=%s client=%s ua=%s had_key=%s",
        event.get("method"),
        event.get("path"),
        event.get("client"),
        event.get("user_agent"),
        event.get("had_key"),
    )
    try:
        p = _canary_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return True


async def auth_guard_middleware(request, call_next):
    """Canary middleware: record would-deny requests, never block."""
    if _mode() == "off":
        return await call_next(request)
    try:
        path = request.url.path
        provided = request.headers.get("X-API-Key")
        if classify(path, provided) == "deny":
            record_canary_event(
                {
                    "method": request.method,
                    "path": path,
                    "client": request.client.host if request.client else "unknown",
                    "user_agent": request.headers.get("user-agent", ""),
                    "had_key": bool(provided),
                }
            )
    except Exception:
        # The canary must never break the request path.
        pass
    return await call_next(request)
