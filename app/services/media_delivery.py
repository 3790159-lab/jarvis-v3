# -*- coding: utf-8 -*-
"""Media-hosting dispatcher (Этап 3, кирпич 3a — замена litterbox).

Single entry point every pipeline uses to turn a local file into a public URL.
The backend is chosen by the ``MEDIA_STORAGE`` env flag:

  MEDIA_STORAGE=r2         (default) upload to Cloudflare R2, free hosting
  MEDIA_STORAGE=litterbox  legacy fallback to litterbox.catbox.moe

Any unrecognised value falls back to r2 (the default), so a typo can never
silently route media to a dead backend. A failed upload raises
:class:`MediaDeliveryError` — an honest delivery failure, never a silent
success. For the RIFE pipeline this raises BEFORE the billable POST, so a
hosting failure can never create a billable prediction.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .block_m2_video.litterbox_uploader import LitterboxError, upload_to_litterbox
from .r2_storage import R2Error, upload_file_async

logger = logging.getLogger(__name__)

__all__ = ["MediaDeliveryError", "host_media", "media_backend"]

_LITTERBOX = "litterbox"
_R2 = "r2"


class MediaDeliveryError(RuntimeError):
    """Raised when hosting a media file for delivery fails."""


def media_backend() -> str:
    """Return the active backend: ``"litterbox"`` or ``"r2"`` (default)."""
    value = os.getenv("MEDIA_STORAGE", _R2).strip().lower()
    if value == _LITTERBOX:
        return _LITTERBOX
    if value and value != _R2:
        logger.warning(
            "unknown MEDIA_STORAGE=%r; falling back to r2", value
        )
    return _R2


async def host_media(path: str | Path, *, retention: str = "24h") -> str:
    """Host ``path`` on the active backend and return its public URL.

    retention: only meaningful for litterbox; ignored by R2 (R2 objects do not
    auto-expire).

    Raises:
        MediaDeliveryError: the upload failed on the active backend.
    """
    path = Path(path)
    backend = media_backend()
    try:
        if backend == _LITTERBOX:
            return await upload_to_litterbox(path, retention=retention)
        return await upload_file_async(path)
    except (R2Error, LitterboxError) as exc:
        raise MediaDeliveryError(f"{backend} upload failed: {exc}") from exc
