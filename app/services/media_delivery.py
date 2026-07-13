# -*- coding: utf-8 -*-
"""Media-hosting dispatcher (Этап 3, кирпич 3a — замена litterbox).

Single entry point every pipeline uses to turn a local file into a public URL,
for internal/transient uploads: LoRA training datasets and short-lived
hosting so a paid API (Replicate/WaveSpeed) can download the file. Objects go
under the R2 ``tmp/`` prefix (see :data:`app.services.r2_storage.TMP_PREFIX`),
meant to be lifecycle-expired after 7 days
(:func:`app.services.r2_storage.ensure_tmp_lifecycle_rule`).

The backend is chosen by the ``MEDIA_STORAGE`` env flag:

  MEDIA_STORAGE=r2         (default) upload to Cloudflare R2, free hosting.
                           If R2 is unavailable (misconfigured or a transient
                           failure), fails open to litterbox.catbox.moe with a
                           WARNING log — an honest degraded delivery, not a
                           silent one.
  MEDIA_STORAGE=litterbox  force litterbox directly (explicit ops override,
                           no R2 attempt).

Any unrecognised value falls back to r2 (the default), so a typo can never
silently route media to a dead backend. A failure on every available backend
raises :class:`MediaDeliveryError` — an honest delivery failure, never a
silent success. For the RIFE pipeline this raises BEFORE the billable POST,
so a hosting failure can never create a billable prediction.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .block_m2_video.litterbox_uploader import LitterboxError, upload_to_litterbox
from .r2_storage import TMP_PREFIX, R2Error, upload_file_async

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
    auto-expire, they are lifecycle-deleted after 7 days instead).

    On the r2 backend (default, or explicit MEDIA_STORAGE=r2), an R2 failure
    fails open to litterbox automatically (WARNING logged) rather than
    failing the whole delivery. MEDIA_STORAGE=litterbox forces litterbox
    directly with no R2 attempt and no further fallback.

    Raises:
        MediaDeliveryError: the upload failed on every backend attempted.
    """
    path = Path(path)
    backend = media_backend()

    if backend == _LITTERBOX:
        try:
            return await upload_to_litterbox(path, retention=retention)
        except LitterboxError as exc:
            raise MediaDeliveryError(f"litterbox upload failed: {exc}") from exc

    try:
        return await upload_file_async(path, prefix=TMP_PREFIX)
    except R2Error as r2_exc:
        logger.warning(
            "R2 upload unavailable, falling back to litterbox: %s", r2_exc
        )
        try:
            return await upload_to_litterbox(path, retention=retention)
        except LitterboxError as lb_exc:
            raise MediaDeliveryError(
                f"r2 upload failed ({r2_exc}); "
                f"litterbox fallback also failed ({lb_exc})"
            ) from lb_exc
