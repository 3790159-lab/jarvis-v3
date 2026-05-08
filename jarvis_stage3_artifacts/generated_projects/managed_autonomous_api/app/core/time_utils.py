from __future__ import annotations

from datetime import datetime, UTC


def utc_now() -> datetime:
    return datetime.now(UTC)
