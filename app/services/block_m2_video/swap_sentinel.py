# -*- coding: utf-8 -*-
"""Cross-process 'video swap in progress' sentinel.

The bot process marks ``state/swap_active`` for the duration of a long video
generation (face swap / animate / persona video). The backend watchdog reads
:func:`is_swap_active` before restarting a bot whose heartbeat looks stale —
a multi-minute swap legitimately starves the heartbeat, and killing the bot
mid-swap is exactly the runaway-restart loop we are fixing.

A timestamp + max-age bound (``WATCHDOG_SWAP_MAX_SEC``) keeps the sentinel from
disabling the watchdog forever: if the bot is hard-killed mid-swap, the frozen
sentinel ages out and the watchdog resumes restarting a genuinely-dead bot.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

# .../app/services/block_m2_video/swap_sentinel.py → project root is parents[3]
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
SWAP_SENTINEL_FILE = _PROJECT_ROOT / "state" / "swap_active"


def _max_age_sec() -> int:
    try:
        return int(os.getenv("WATCHDOG_SWAP_MAX_SEC", "1800"))
    except ValueError:
        return 1800


def mark_swap_start(path: Path = SWAP_SENTINEL_FILE) -> None:
    """Create/refresh the sentinel with the current timestamp."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(int(time.time())), encoding="utf-8")


def mark_swap_end(path: Path = SWAP_SENTINEL_FILE) -> None:
    """Remove the sentinel. Safe to call when it does not exist."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def is_swap_active(path: Path = SWAP_SENTINEL_FILE) -> bool:
    """True if a swap is in progress and the sentinel is still fresh."""
    try:
        st = path.stat()
    except (FileNotFoundError, OSError):
        return False
    try:
        started = int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        # Corrupt/empty sentinel — fall back to the file's mtime so a fresh but
        # malformed sentinel still reads busy and an old one reads idle.
        started = st.st_mtime
    return (time.time() - started) < _max_age_sec()
