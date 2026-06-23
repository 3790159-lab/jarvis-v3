# -*- coding: utf-8 -*-
"""Telegram bot access control — env-driven user-id whitelist.

The bot dispatch calls :func:`is_allowed` for every inbound update and
silently rejects non-allowed users with :data:`REJECT_MESSAGE`. Two env
vars control access:

* ``JARVIS_ADMIN_USER_ID`` — single int; full-access bypass.
* ``JARVIS_ALLOWED_USER_IDS`` — comma-separated ints; the whitelist.

Both empty → open mode (everyone allowed), preserving the existing dev
experience until the operator opts in.

Env is read lazily on each call so ops can rotate the whitelist without
restarting the bot.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

REJECT_MESSAGE = (
    "🚫 Извини, доступ к этому боту только по приглашению.\n\n"
    "Свяжись с @daniil_lapin для доступа."
)


def load_admin_user_id() -> int | None:
    """Return the admin user_id from ``JARVIS_ADMIN_USER_ID``, or ``None``.

    Empty/unset → ``None``. A non-int value is logged as a WARNING and treated
    as unset so a typo doesn't silently disable the admin bypass.
    """
    raw = os.getenv("JARVIS_ADMIN_USER_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "JARVIS_ADMIN_USER_ID is not a valid int: %r (treating as unset)",
            raw,
        )
        return None


def load_allowed_user_ids() -> set[int]:
    """Parse ``JARVIS_ALLOWED_USER_IDS`` CSV into a set of ints.

    Whitespace around entries is stripped; empty entries are ignored; entries
    that don't parse as int are skipped with a WARNING (a typo must not
    silently lock out other listed users).
    """
    raw = os.getenv("JARVIS_ALLOWED_USER_IDS", "")
    out: set[int] = set()
    for entry in raw.split(","):
        s = entry.strip()
        if not s:
            continue
        try:
            out.add(int(s))
        except ValueError:
            logger.warning(
                "JARVIS_ALLOWED_USER_IDS contains a non-int entry %r — skipping",
                s,
            )
    return out


def is_allowed(user_id: int) -> bool:
    """Return ``True`` if ``user_id`` should reach the bot's handlers.

    Decision order:

    1. Active member of ``state/users.json`` (incl. env bootstrap-admin) → allow.
    2. ``user_id`` equals env admin → allow (bypass).
    3. ``user_id`` is in env allowed-list → allow.
    4. Nothing configured anywhere (no env, no members) → open mode → allow.
    5. Otherwise → reject.
    """
    # Lazy import avoids a module-load cycle (users_store imports this module).
    try:
        from app.services.auth import users_store
        if users_store.is_member(user_id):
            return True
        has_members = users_store.has_members()
    except Exception:  # noqa: BLE001 - users_store must never harden us into a lockout
        logger.exception("whitelist: users_store membership check failed; failing open to env gate")
        has_members = False

    admin = load_admin_user_id()
    allowed = load_allowed_user_ids()
    if admin is not None and user_id == admin:
        return True
    if user_id in allowed:
        return True
    if admin is None and not allowed and not has_members:
        return True  # open mode (dev): nothing configured at all
    return False
