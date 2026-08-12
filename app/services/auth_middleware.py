"""Default-deny auth middleware — Phase 1 (canary).

Security audit 2026-07-15 follow-up. The tunnel exposes the whole :8010 backend;
gating ~40 routers one-by-one is error-prone. This middleware is the single
choke point: every request is either on the public allow-list or must carry a
valid API key.

Modes (env ``JARVIS_AUTH_MIDDLEWARE_MODE``):
  * ``off``     — do nothing (default; safe to merge and deploy).
  * ``canary``  — decide + record would-deny, always pass through (Phase 1).
  * ``enforce`` — return 401 on a deny decision (Phase 2).
Any other value is treated as non-blocking (canary), so a typo can never take
the surface down — only ``enforce`` blocks.

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

# Paths reachable without a key: exact matches, the "*/health" suffix rule, and
# a small set of public prefixes.
PUBLIC_EXACT = frozenset(
    {
        "/health",
        "/oauth/callback",     # external: Instagram OAuth redirect
        "/telegram/webhook",   # external: Telegram (self-secured by secret-token)
    }
)

# Prefixes whose whole subtree is public. /api/jarvis/ops/* is low-sensitivity
# liveness (heartbeat / cloudflared / disk / restart-storm) polled by the
# external Uptime Kuma monitor, which cannot easily carry a key.
#
# /panel/* — панели владельца. «Public» здесь означает только «этот guard не
# участвует»: он умеет единственный механизм, заголовок X-API-Key, а панель
# открывается ключом владельца в query/cookie, которого guard не видит — и
# потому глухо отдавал 401 ещё до роутера. Субтри закрыт зависимостью
# `require_owner` на каждой ручке, кроме самой двери /panel/login (она сама
# сверяет ключ и fail-closed'ит 503 без него). Что это остаётся правдой,
# держит tests/test_panel_routes_owner_guarded.py.
PUBLIC_PREFIXES = ("/api/jarvis/ops/", "/panel/")


def _mode() -> str:
    return (os.getenv("JARVIS_AUTH_MIDDLEWARE_MODE", "off").strip().lower() or "off")


def is_public_path(path: str) -> bool:
    """True if ``path`` is reachable without authentication."""
    if path in PUBLIC_EXACT:
        return True
    if path.endswith("/health"):
        return True
    if any(path.startswith(pfx) for pfx in PUBLIC_PREFIXES):
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
    """Default-deny auth guard.

    Modes (env ``JARVIS_AUTH_MIDDLEWARE_MODE``):
      * ``off``     — no-op, pass everything through (default).
      * ``enforce`` — return 401 on a deny decision.
      * anything else (``canary`` / unknown) — record would-deny, pass through.
        Treating unknown modes as non-blocking is deliberate: a typo can never
        take the surface down, only ``enforce`` blocks.
    """
    mode = _mode()
    if mode == "off":
        return await call_next(request)

    deny = False
    try:
        path = request.url.path
        provided = request.headers.get("X-API-Key")
        if classify(path, provided) == "deny":
            deny = True
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
        # The guard must never break the request path on its own error.
        return await call_next(request)

    if deny and mode == "enforce":
        from starlette.responses import JSONResponse

        return JSONResponse(
            status_code=401, content={"detail": "Invalid or missing API key"}
        )
    return await call_next(request)
