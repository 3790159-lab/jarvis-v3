"""Shared internal-backend auth header helper.

The default-deny auth middleware (``app/services/auth_middleware.py``) rejects
any non-public request to the :8010 backend that lacks a valid ``X-API-Key``.
Several in-process / self-call HTTP consumers (smart_router's agent mesh,
scheduler, result_synthesizer, the obsidian savers, the dashboard, mcp_server,
agent_health_mesh) call the backend over loopback HTTP and would 401 under
``MODE=enforce``. This module is the single place that attaches the internal
key to those requests — mirroring the bot's ``http_json`` key-injection, but
reusable across both ``urllib`` and ``requests`` callers (which only build a
headers dict, so a header helper fits both).

Safety (same principle as ``http_json``): the key is attached ONLY when the URL
targets the local backend. An external URL (e.g. api.telegram.org, an n8n cloud
host) never receives the key, so a shared caller cannot leak it.

The key is read from ``os.environ`` at call time — never cached at import —
because ``env_bootstrap`` loads ``.env`` into the environment, and a module may
import before that has run.
"""
from __future__ import annotations

import os
from typing import Dict, Optional
from urllib.parse import urlparse

# Hosts that always designate the local backend.
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Env vars from which the various consumers resolve their backend base URL. If
# any is set to a non-loopback host, that host is also treated as the backend.
_BACKEND_BASE_ENVS = ("BACKEND_BASE_URL", "TELEGRAM_BACKEND_URL", "JARVIS_BACKEND")


def internal_api_key() -> str:
    """The internal API key (``JARVIS_INTERNAL_API_KEY``, falling back to
    ``JARVIS_ADMIN_KEY``), or ``""`` if neither is configured."""
    return (
        os.environ.get("JARVIS_INTERNAL_API_KEY", "").strip()
        or os.environ.get("JARVIS_ADMIN_KEY", "").strip()
    )


def _backend_hosts() -> frozenset:
    hosts = set(_LOCAL_HOSTS)
    for env in _BACKEND_BASE_ENVS:
        val = os.environ.get(env, "").strip()
        if not val:
            continue
        try:
            host = urlparse(val).hostname
        except Exception:
            host = None
        if host:
            hosts.add(host)
    return frozenset(hosts)


def is_backend_url(url: str) -> bool:
    """True iff ``url`` points at the local Jarvis backend (loopback, or a
    configured backend-base host)."""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return host in _backend_hosts()


def backend_headers(
    url: str, headers: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    """Return a NEW headers dict = ``headers`` plus ``X-API-Key`` when ``url``
    targets the local backend and a key is configured.

    * Never mutates the caller's ``headers`` (returns a copy).
    * Never overwrites a caller-supplied ``X-API-Key``.
    * Attaches nothing for external URLs (no key leak) or when no key is set
      (the request proceeds keyless, exactly as before — graceful degradation
      when the middleware is ``off``/``canary``).
    """
    out: Dict[str, str] = dict(headers or {})
    if "X-API-Key" not in out and is_backend_url(url):
        key = internal_api_key()
        if key:
            out["X-API-Key"] = key
    return out
