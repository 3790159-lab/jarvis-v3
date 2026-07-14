# -*- coding: utf-8 -*-
"""Photo generation with trained FLUX LoRA for a persona."""
from __future__ import annotations

from typing import Callable

from app.services.block_m_common.cost_tracker import CostTracker, DailyLimitExceeded
from app.services.block_m_common.logging_setup import get_logger
from app.services.block_m_common.persona_storage import PersonaStorage
from app.services.block_m_common.replicate_video_client import ReplicateVideoClient

logger = get_logger("photo_generator")


class PhotoGenerator:
    """Generate persona photos using a trained FLUX LoRA."""

    def __init__(
        self,
        client: ReplicateVideoClient,
        storage: PersonaStorage,
        tracker: CostTracker,
    ) -> None:
        self._client = client
        self._storage = storage
        self._tracker = tracker

    async def generate_photo(
        self,
        persona_id: str,
        prompt: str,
        progress_cb: Callable | None = None,
        lora_scale: float | None = None,
        guidance: float | None = None,
        aspect_ratio: str | None = None,
    ) -> dict:
        """Generate a photo for the given persona using its trained LoRA.

        Args:
            persona_id: Persona identifier.
            prompt: User prompt (trigger word is prepended automatically).
            progress_cb: Optional callback(status: str) for progress updates.
            lora_scale: Per-call LoRA strength override (None → client default).
            guidance: Per-call prompt-guidance override (None → client default).
            aspect_ratio: Per-call output aspect (e.g. "3:4"; None → 1:1 default).

        Returns:
            {"image_url": str, "cost_usd": float, "full_prompt": str}

        Raises:
            ValueError: If persona not found or LoRA not trained.
            DailyLimitExceeded: If daily cost limit is exceeded.
        """
        persona = await self._storage.get_persona(persona_id)
        if persona is None:
            raise ValueError(f"Persona {persona_id!r} not found")

        if not persona.lora_weights_url:
            raise ValueError(
                f"Persona {persona_id!r} has no trained LoRA. Run /train_lora first."
            )

        ok, spent = await self._tracker.check_limit()
        if not ok:
            raise DailyLimitExceeded(f"Daily limit reached (spent ${spent:.2f})")

        if progress_cb is not None:
            progress_cb("generating")

        full_prompt = f"{persona.trigger_word} {prompt}"
        logger.info(
            "Generating photo for persona=%s trigger=%s",
            persona_id,
            persona.trigger_word,
        )

        result = await self._client.generate_flux_with_lora(
            prompt=prompt,
            lora_url=persona.lora_weights_url,
            trigger_word=persona.trigger_word,
            lora_scale=lora_scale,
            guidance=guidance,
            aspect_ratio=aspect_ratio,
        )

        cost = result["cost_usd"]
        image_url = result["image_url"]

        await self._tracker.log_expense("flux_lora_inference", cost, persona_id)

        await self._storage.update_persona(
            persona_id,
            total_generations=persona.total_generations + 1,
            total_cost_usd=persona.total_cost_usd + cost,
        )

        logger.info(
            "Photo generated for persona=%s url=%.60s cost=$%.4f",
            persona_id, image_url, cost,
        )
        return {"image_url": image_url, "cost_usd": cost, "full_prompt": full_prompt}
