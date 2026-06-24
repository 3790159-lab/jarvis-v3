# -*- coding: utf-8 -*-
"""WaveSpeed RIFE frame-interpolation client (video smoothness, Этап 4).

Confirmed by live spike (Задача 0):
  * POST https://api.wavespeed.ai/api/v3/wavespeed-ai/rife  {"video": <public url>}
  * x2 smoothness (24->48fps) == ``num_frames: 1`` (1 inserted frame per pair).
  * Response envelope is identical to the wan-2.6 spicy engine, so submit/poll/
    download are reused from :class:`WaveSpeedHTTPClient` (no duplication).

Input delivery: RIFE needs a PUBLIC video URL, so the local mp4 is hosted via
the in-house litterbox uploader first. That upload happens BEFORE the billable
RIFE POST, so a hosting failure never creates a billable prediction. The
litterbox URL is ephemeral — we use it only within this single
upload -> submit -> poll -> download pass and never persist it.
"""
from __future__ import annotations

import functools
import logging
from pathlib import Path

import httpx

from ..litterbox_uploader import upload_to_litterbox
from .wavespeed_http import (
    BASE_URL,
    WaveSpeedEngineError,
    WaveSpeedHTTPClient,
    WaveSpeedTransientError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "WaveSpeedRifeClient",
    "WaveSpeedEngineError",
    "WaveSpeedTransientError",
]

_MODEL = "wavespeed-ai/rife"
_SUBMIT = f"{BASE_URL}/api/v3/{_MODEL}"


class WaveSpeedRifeClient(WaveSpeedHTTPClient):
    """Smooth a local mp4 via WaveSpeed RIFE; return the smoothed local mp4."""

    engine_name = "wavespeed_rife"

    def __init__(self, api_key: str | None = None, *, uploader=None, **kw) -> None:
        super().__init__(api_key, **kw)
        # Default: host on litterbox with a short retention (URL is single-use).
        self._uploader = uploader or functools.partial(
            upload_to_litterbox, retention="1h")

    async def interpolate(self, local_mp4: Path, *, num_frames: int = 1) -> Path:
        """Return a smoothed copy of ``local_mp4`` (x2 fps at num_frames=1).

        Raises:
            WaveSpeedEngineError: missing input, 4xx, or completed-but-failed.
            WaveSpeedTransientError: 429/network with no prediction created.
            LitterboxError: hosting failed (before any billable RIFE call).
        """
        local_mp4 = Path(local_mp4)
        if not local_mp4.exists():
            raise WaveSpeedEngineError(f"input video not found: {local_mp4}")

        # Host first; a litterbox failure raises here -> no billable prediction.
        public_url = await self._uploader(local_mp4)
        logger.info("RIFE submit: num_frames=%d src=%s", num_frames, local_mp4.name)

        payload = {"video": public_url, "num_frames": num_frames}
        poll_url = await self._submit_with_retry(_SUBMIT, payload)
        out_url = await self._poll(poll_url)
        dest = local_mp4.with_name(f"{local_mp4.stem}.smooth.mp4")
        return await self._download(out_url, dest)
