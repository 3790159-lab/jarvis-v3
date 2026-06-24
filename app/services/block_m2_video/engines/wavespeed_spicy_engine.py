# -*- coding: utf-8 -*-
"""WaveSpeed wan-2.6 spicy image-to-video engine (uncensored, managed).

Patient 429 backoff; WaveSpeedTransientError (429/network, no prediction created
-> retry/sweep) vs WaveSpeedEngineError (completed-but-failed / 4xx -> terminal,
billable, never retry). Local image sent as a base64 data-URI (no hosting).
Never logs the API key. Cost/snap come from WAVESPEED_CAPS.
"""
from __future__ import annotations

import base64
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from .capabilities import WAVESPEED_CAPS
from .engine_protocol import VideoRequest, VideoResult, new_generation_id
# Re-exported for back-compat: callers/tests import these names from here.
from .wavespeed_http import (  # noqa: F401
    BASE_URL as _BASE,
    WaveSpeedEngineError,
    WaveSpeedHTTPClient,
    WaveSpeedTransientError,
)

logger = logging.getLogger(__name__)

_MODEL = "alibaba/wan-2.6/image-to-video-spicy"
_SUBMIT = f"{_BASE}/api/v3/{_MODEL}"


class WaveSpeedSpicyEngine(WaveSpeedHTTPClient):
    engine_name = "wavespeed_spicy"

    async def is_available(self) -> bool:
        return bool(self._key)

    async def generate(self, request: VideoRequest) -> VideoResult:
        start = time.monotonic()
        gen_id = request.generation_id or new_generation_id()
        seconds = WAVESPEED_CAPS.snap_duration(request.seconds)
        resolution = WAVESPEED_CAPS.snap_resolution(request.resolution)
        if not request.input_image_path.exists():
            raise WaveSpeedEngineError(f"input image not found: {request.input_image_path}")

        payload = {
            "image": self._data_uri(request.input_image_path),
            "prompt": request.prompt,
            "duration": seconds,
            "resolution": resolution,
            "negative_prompt": request.negative_prompt or "",
            "enable_prompt_expansion": request.enable_prompt_expansion,
        }
        if request.shot_type:
            payload["shot_type"] = request.shot_type
        if request.seed is not None:
            payload["seed"] = request.seed

        # Diagnostic: log EXACTLY what goes to WaveSpeed (no key, no image bytes).
        logger.info(
            "WaveSpeed submit: dur=%s res=%s expansion=%s shot=%s "
            "prompt(len=%d)=%r negative(len=%d)=%r",
            seconds, resolution, payload["enable_prompt_expansion"],
            payload.get("shot_type"), len(request.prompt or ""),
            (request.prompt or "")[:400], len(request.negative_prompt or ""),
            (request.negative_prompt or "")[:200],
        )
        poll_url = await self._submit_with_retry(_SUBMIT, payload)
        video_url = await self._poll(poll_url)
        dest = Path("state/personas/videos") / request.persona_id / gen_id / "output.mp4"
        out_path = await self._download(video_url, dest)

        return VideoResult(
            generation_id=gen_id, persona_id=request.persona_id, output_path=out_path,
            engine=self.engine_name, model=_MODEL,
            seed=request.seed if request.seed is not None else -1,
            cost_usd=WAVESPEED_CAPS.cost_for(seconds, resolution),
            duration_sec=time.monotonic() - start, timestamp=datetime.now(timezone.utc),
            prompt=request.prompt, seconds=seconds,
            extra={"video_url": video_url, "resolution": resolution},
        )

    @staticmethod
    def _data_uri(path: Path) -> str:
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        b64 = base64.b64encode(path.read_bytes()).decode()
        return f"data:{mime};base64,{b64}"
