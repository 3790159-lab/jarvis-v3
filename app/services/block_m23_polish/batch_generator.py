# -*- coding: utf-8 -*-
"""Batch photo generation — generate N photos for a persona in sequence."""
from __future__ import annotations

from typing import Callable

from app.services.block_m1_persona.photo_generator import PhotoGenerator
from app.services.block_m_common.cost_tracker import DailyLimitExceeded
from app.services.block_m_common.logging_setup import get_logger

logger = get_logger("batch_generator")


class BatchGenerator:
    """Generate multiple photos for a persona sequentially.

    Stops cleanly on DailyLimitExceeded and returns whatever was generated
    up to that point rather than raising.

    Args:
        photo_gen: PhotoGenerator to use for each photo.
    """

    def __init__(self, photo_gen: PhotoGenerator) -> None:
        self._photo_gen = photo_gen

    async def generate_batch(
        self,
        persona_id: str,
        prompt: str,
        count: int,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> list[dict]:
        """Generate up to `count` photos for persona_id.

        Args:
            persona_id: Target persona (must have LoRA trained).
            prompt: Scene description.
            count: Number of photos to generate.
            progress_cb: Optional callback(done, total, url) after each photo.

        Returns:
            List of photo result dicts (may be shorter than `count` if
            DailyLimitExceeded is hit mid-batch).
        """
        results: list[dict] = []
        for i in range(count):
            try:
                result = await self._photo_gen.generate_photo(persona_id, prompt)
                results.append(result)
                logger.info(
                    "Batch %d/%d done persona=%s cost=$%.4f",
                    i + 1, count, persona_id, result["cost_usd"],
                )
                if progress_cb is not None:
                    progress_cb(i + 1, count, result["image_url"])
            except DailyLimitExceeded:
                logger.warning(
                    "Batch stopped at %d/%d: daily limit exceeded persona=%s",
                    i, count, persona_id,
                )
                break
        return results
