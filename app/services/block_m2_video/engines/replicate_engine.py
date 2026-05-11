# -*- coding: utf-8 -*-
"""Replicate engine using Wan 2.5 I2V Fast (or fallback to wan-2.2-i2v-fast)."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import replicate

from .engine_protocol import VideoRequest, VideoResult, new_generation_id

logger = logging.getLogger(__name__)

# Per-second pricing (as of May 2026). Source: replicate.com/pricing.
# Fallback chain — newest fast model first, older as backup.
REPLICATE_MODELS: list[dict[str, Any]] = [
    {"id": "wan-video/wan-2.5-i2v-fast", "cost_per_sec": 0.020},
    {"id": "wan-video/wan-2.2-i2v-fast", "cost_per_sec": 0.060},
]


class ReplicateEngineError(Exception):
    """Raised when the Replicate engine fails to produce a video."""


class ReplicateEngine:
    """Fast cloud engine via Replicate API."""

    engine_name = "replicate"

    def __init__(
        self,
        api_token: str | None = None,
        model_id: str | None = None,
    ) -> None:
        self.api_token = (
            api_token
            or os.environ.get("REPLICATE_API_TOKEN")
            or os.environ.get("REPLICATE_API_KEY")
        )
        if not self.api_token:
            raise ReplicateEngineError(
                "REPLICATE_API_TOKEN (or REPLICATE_API_KEY) is not set"
            )
        # The replicate library checks REPLICATE_API_TOKEN specifically.
        os.environ["REPLICATE_API_TOKEN"] = self.api_token

        self.model_id = model_id or REPLICATE_MODELS[0]["id"]
        self.cost_per_sec = next(
            (m["cost_per_sec"] for m in REPLICATE_MODELS if m["id"] == self.model_id),
            0.04,
        )

    async def is_available(self) -> bool:
        return bool(self.api_token)

    async def generate(self, request: VideoRequest) -> VideoResult:
        start_time = time.monotonic()
        generation_id = request.generation_id or new_generation_id()
        seed = request.seed if request.seed is not None else int(time.time())

        logger.info(
            "ReplicateEngine: starting generation %s "
            "(persona=%s, model=%s, seconds=%d, seed=%d)",
            generation_id,
            request.persona_id,
            self.model_id,
            request.seconds,
            seed,
        )

        if not request.input_image_path.exists():
            raise ReplicateEngineError(
                f"Input image not found: {request.input_image_path}"
            )

        def _run_sync():
            with open(request.input_image_path, "rb") as fh:
                return replicate.run(
                    self.model_id,
                    input={
                        "image": fh,
                        "prompt": request.prompt,
                        "duration": request.seconds,
                        "seed": seed,
                    },
                )

        try:
            output = await asyncio.to_thread(_run_sync)
        except Exception as exc:
            raise ReplicateEngineError(f"Replicate API error: {exc}") from exc

        video_url = self._extract_url(output)
        if not video_url:
            raise ReplicateEngineError(f"Unexpected Replicate output: {output!r}")

        output_dir = Path("state/personas/videos") / request.persona_id / generation_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "output.mp4"

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(video_url)
            resp.raise_for_status()
            output_path.write_bytes(resp.content)

        duration = time.monotonic() - start_time
        cost = self.cost_per_sec * request.seconds

        logger.info(
            "ReplicateEngine: generation %s done in %.1fs, cost=$%.3f",
            generation_id,
            duration,
            cost,
        )

        return VideoResult(
            generation_id=generation_id,
            persona_id=request.persona_id,
            output_path=output_path,
            engine=self.engine_name,
            model=self.model_id,
            seed=seed,
            cost_usd=cost,
            duration_sec=duration,
            timestamp=datetime.now(timezone.utc),
            prompt=request.prompt,
            seconds=request.seconds,
            extra={"video_url": video_url},
        )

    @staticmethod
    def _extract_url(output: Any) -> str | None:
        """Normalize replicate.run output into a URL string.

        Replicate may return: a ``FileOutput`` (has ``.url``), a plain URL
        string, or a list of either. We accept any of these.
        """
        if hasattr(output, "url"):
            return output.url
        if isinstance(output, str):
            return output
        if isinstance(output, list) and output:
            first = output[0]
            if hasattr(first, "url"):
                return first.url
            if isinstance(first, str):
                return first
        return None
