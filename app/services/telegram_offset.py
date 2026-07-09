# -*- coding: utf-8 -*-
"""Persisted getUpdates offset for the Telegram poll loop (Этап 1).

The poll loop resets ``offset`` to ``0`` on every process start, so updates that
arrive in the kill→start restart window are only recovered by luck (Telegram
redelivers *unconfirmed* updates, but a lingering second poller can confirm past
them, and every restart risks re-dispatching already-handled updates). Persisting
the highest processed ``update_id`` lets a restarted bot resume with
``offset = last + 1`` and dedupe anything ``<= last``.

Kept tiny and injectable (explicit ``path``) so it is trivially unit-testable on
tmp and never touches a hardcoded prod state file from tests. The atomic write is
delegated to :func:`app.services.block_l_common.save_json_safe` (tmp + replace),
the same primitive the dev-task queue uses.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

# Sentinel: nothing processed yet (fresh bot / missing / corrupt file). Chosen as
# -1 (not 0) so ``offset = last + 1`` collapses to 0 (fetch all pending) on a
# fresh start, and an id-less update (update_id defaulting to 0) is still greater
# than the sentinel and therefore dispatchable under a ``uid <= last`` guard.
NO_UPDATE = -1


def load_last_update_id(path: Union[str, Path]) -> int:
    """Return the highest persisted ``update_id``, or ``NO_UPDATE`` (-1) when the
    file is missing, unreadable, or does not carry a valid integer."""
    from app.services.block_l_common import load_json_safe

    data = load_json_safe(path, default=None)
    if not isinstance(data, dict):
        return NO_UPDATE
    try:
        return int(data["last_update_id"])
    except (KeyError, TypeError, ValueError):
        return NO_UPDATE


def save_last_update_id(path: Union[str, Path], update_id: int) -> None:
    """Atomically persist ``update_id`` as the last processed offset marker."""
    from app.services.block_l_common import save_json_safe

    save_json_safe(Path(path), {"last_update_id": int(update_id)})
