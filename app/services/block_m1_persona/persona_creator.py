# -*- coding: utf-8 -*-
"""Generates seed photos for a persona using FLUX 1.1 Pro.

Concurrency model: all ``count`` tasks are submitted at once to
``asyncio.gather``, but a ``asyncio.Semaphore(_MAX_CONCURRENT)`` caps
simultaneous in-flight Replicate API calls.  ``_MAX_CONCURRENT=1`` enforces
sequential submission to avoid FLUX 1.1 Pro HTTP 429 rate-limit errors;
each task also sleeps 1.5 s before its API call to maintain safe spacing.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable

from app.services.block_m_common.cost_tracker import CostTracker, DailyLimitExceeded
from app.services.block_m_common.logging_setup import get_logger
from app.services.block_m_common.persona_storage import Persona, PersonaStorage
from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
from app.services.block_m1_persona.prompt_builder import PersonaPromptBuilder

logger = get_logger("persona_creator")

_MAX_CONCURRENT = 1  # serialise Replicate calls: FLUX 1.1 Pro rate-limits burst traffic


class PersonaCreator:
    """Generates FLUX Pro seed photos for a persona and updates its storage record.

    Args:
        client: Replicate API client for image generation.
        storage: Persona storage for updating records.
        tracker: Cost tracker for budget enforcement.
    """

    def __init__(
        self,
        client: ReplicateVideoClient,
        storage: PersonaStorage,
        tracker: CostTracker,
    ) -> None:
        self._client = client
        self._storage = storage
        self._tracker = tracker

    async def generate_seed_photos(
        self,
        persona: Persona,
        count: int = 20,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> list[str]:
        """Generate ``count`` seed photos with capped concurrency.

        Runs all tasks through ``asyncio.gather`` with a semaphore that limits
        concurrent Replicate API calls to ``_MAX_CONCURRENT``.  Each task logs
        its own result, so a per-photo failure never silently swallows others.

        Args:
            persona: The target persona.
            count: Total number of photos to generate (default 20).
            progress_cb: Optional callback(done, total, url) after each success.

        Returns:
            List of generated image URLs (may be fewer than count on failures).

        Raises:
            DailyLimitExceeded: Propagated immediately when the budget is hit.
        """
        builder = PersonaPromptBuilder()
        prompts = builder.build_seed_prompts(persona.description, persona.style, count)

        logger.info(
            "Starting generation: persona=%s count=%d concurrency=%d",
            persona.persona_id, count, _MAX_CONCURRENT,
        )

        sem = asyncio.Semaphore(_MAX_CONCURRENT)
        urls: list[str] = []

        async def _task(idx: int, prompt: str) -> str | Exception:
            async with sem:
                await asyncio.sleep(1.5)  # spacing to stay within FLUX 1.1 Pro rate limit
                logger.debug(
                    "Photo %d/%d submitting (persona=%s)", idx + 1, count, persona.persona_id
                )
                try:
                    result = await self._client.generate_flux_pro(prompt)
                    await self._tracker.log_expense(
                        "flux_pro_seed", result["cost_usd"], persona.persona_id
                    )
                    url = result["image_url"]
                    logger.info(
                        "Photo %d/%d OK: cost=$%.4f url=%.60s",
                        idx + 1, count, result["cost_usd"], url,
                    )
                    return url
                except DailyLimitExceeded:
                    logger.warning("Photo %d/%d: daily limit hit", idx + 1, count)
                    raise
                except Exception as exc:
                    logger.warning(
                        "Photo %d/%d failed: %s: %s",
                        idx + 1, count, type(exc).__name__, exc,
                    )
                    return exc

        tasks = [_task(i, p) for i, p in enumerate(prompts)]
        raw = await asyncio.gather(*tasks, return_exceptions=True)

        for r in raw:
            if isinstance(r, DailyLimitExceeded):
                logger.error("DailyLimitExceeded during generation, aborting")
                raise r
            if isinstance(r, Exception):
                continue
            urls.append(r)
            if progress_cb:
                progress_cb(len(urls), count, r)

        logger.info(
            "Generation done: persona=%s got=%d/%d",
            persona.persona_id, len(urls), count,
        )

        if urls:
            await self._storage.update_persona(
                persona.persona_id,
                seed_photos=urls,
                total_generations=persona.total_generations + len(urls),
            )

        return urls
