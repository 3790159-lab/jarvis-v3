"""Shared API-key authorization for internal FastAPI surfaces.

Security audit 2026-07-15 (H1-H4): the tunnel exposes the whole :8010 backend,
yet only a handful of routers checked a key. This module is the single
``require_api_key`` dependency that the previously-anonymous admin surfaces
(file-tools, live-operator, …) hang off of, using the *same* key principle as
``app.api.public_api_v1``.

Accepted credentials (any one matches):
  * ``JARVIS_ADMIN_KEY``            — the existing admin key
  * ``JARVIS_INTERNAL_API_KEY``     — a dedicated key for internal service calls
  * any active key in ``state/api_keys.json`` (public_api_v1's store)

Fails **closed**: if none of these are configured/valid, every request is
rejected. Comparison is constant-time to avoid leaking key material by timing.
"""
from __future__ import annotations

import os
import secrets
from typing import List, Optional

from fastapi import Header, HTTPException

_KEY_ENVS = ("JARVIS_ADMIN_KEY", "JARVIS_INTERNAL_API_KEY")


def _configured_keys() -> List[str]:
    keys: List[str] = []
    for env in _KEY_ENVS:
        val = os.getenv(env, "").strip()
        if val:
            keys.append(val)
    return keys


def is_authorized(provided: Optional[str]) -> bool:
    """Return True iff ``provided`` matches a configured/active API key."""
    if not provided:
        return False
    provided = provided.strip()
    if not provided:
        return False

    for key in _configured_keys():
        if secrets.compare_digest(provided, key):
            return True

    # Fall back to the public_api_v1 key store (state/api_keys.json).
    try:
        from app.api.public_api_v1 import validate_api_key

        if validate_api_key(provided):
            return True
    except Exception:
        pass

    return False


def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> str:
    """FastAPI dependency: 401 unless a valid ``X-API-Key`` header is present."""
    if not is_authorized(x_api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return x_api_key or ""
