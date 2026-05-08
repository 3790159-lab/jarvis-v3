# -*- coding: utf-8 -*-
"""Additional video generation engines wrapping ReplicateVideoClient."""
from __future__ import annotations

from app.services.block_m_common.logging_setup import get_logger
from app.services.block_m_common.replicate_video_client import ReplicateVideoClient

logger = get_logger("video_client_extras")

_WAN22_MODEL = "wan-video/wan-2.2-i2v-fast"
_COST_WAN22_PER_5SEC = 0.10

_SUPPORTED_ENGINES = ("kling_v21", "wan22_fast")


class VideoClientExtras:
    """Additional video generation engines for Block M.2.

    Delegates low-level HTTP and retry logic to the provided ReplicateVideoClient.

    Args:
        client: ReplicateVideoClient instance to use for API calls.
    """

    def __init__(self, client: ReplicateVideoClient) -> None:
        self._client = client

    async def generate_wan22_fast(
        self,
        image_url: str,
        prompt: str,
        duration: int = 5,
    ) -> dict:
        """Generate a video using Wan 2.2 Fast image-to-video.

        Wan 2.2 Fast is a cost-effective alternative to Kling v2.1.

        Args:
            image_url: URL of the source image.
            prompt: Text description of desired motion/animation.
            duration: Video duration in seconds (default 5).

        Returns:
            {"video_url": str, "cost_usd": float, "duration_sec": int}
        """
        payload = {
            "input": {
                "image": image_url,
                "prompt": prompt,
                "duration": duration,
            }
        }
        output = await self._client._run_prediction(_WAN22_MODEL, payload)
        video_url = output if isinstance(output, str) else (output[0] if output else "")
        cost = _COST_WAN22_PER_5SEC * (duration / 5)
        logger.info("Wan 2.2 Fast complete: url=%s cost=$%.4f", video_url, cost)
        return {"video_url": video_url, "cost_usd": cost, "duration_sec": duration}

    async def generate_video_dispatch(
        self,
        image_url: str,
        prompt: str,
        engine: str = "kling_v21",
        duration: int = 5,
    ) -> dict:
        """Unified video generation dispatcher.

        Routes the request to the appropriate engine.

        Args:
            image_url: URL of the source image.
            prompt: Text description of desired motion/animation.
            engine: Engine to use — "kling_v21" | "wan22_fast".
            duration: Video duration in seconds (default 5).

        Returns:
            {"video_url": str, "cost_usd": float, "duration_sec": int}

        Raises:
            ValueError: If engine is not one of the supported options.
        """
        if engine == "kling_v21":
            return await self._client.generate_kling_v21(image_url, prompt, duration)
        if engine == "wan22_fast":
            return await self.generate_wan22_fast(image_url, prompt, duration)
        raise ValueError(
            f"Unknown video engine {engine!r}. "
            f"Supported: {', '.join(repr(e) for e in _SUPPORTED_ENGINES)}."
        )
