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
        refine: bool = False,
        refine_creativity: float = 0.30,
        refine_resemblance: float = 0.8,
        refine_prompt: str | None = None,
    ) -> dict:
        """Generate a photo for the given persona using its trained LoRA.

        Args:
            persona_id: Persona identifier.
            prompt: User prompt (trigger word is prepended automatically).
            progress_cb: Optional callback(status: str) for progress updates.
            lora_scale: Per-call LoRA strength override (None → client default).
            guidance: Per-call prompt-guidance override (None → client default).
            aspect_ratio: Per-call output aspect (e.g. "3:4"; None → 1:1 default).
            refine: If True, run a post-gen img2img refine pass to de-wax the
                LoRA's baked glam bias (spike 2026-07-14). Graceful: a refiner
                failure falls back to the un-refined frame (post-flow never breaks).
            refine_creativity: Refiner denoise strength (low → identity kept).
            refine_resemblance: Refiner ControlNet conditioning (holds composition).
            refine_prompt: Optional refine prompt (e.g. de-wax text + identity
                anchors). None → refiner uses its neutral anti-wax default.

        Returns:
            {"image_url": str, "cost_usd": float, "full_prompt": str,
             "refined": bool} — ``refined`` is True only if the refine pass ran and
            succeeded; False if refine was requested but fell back, or not requested.

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

        # Пост-ген img2img рефайн (de-wax). Fail-safe: будь-яка помилка рефайнера
        # → віддаємо НЕрефайнений кадр (refined=False), пост-флоу не ламається,
        # рефайн НЕ тарифікується.
        refined = False
        if refine:
            if progress_cb is not None:
                progress_cb("refining")
            try:
                ref = await self._client.refine_image(
                    image_url,
                    creativity=refine_creativity,
                    resemblance=refine_resemblance,
                    prompt=refine_prompt,
                )
                refined_url = ref.get("image_url")
                if not refined_url:
                    raise RuntimeError("refiner returned empty image_url")
                refine_cost = ref.get("cost_usd", 0.0)
                image_url = refined_url
                cost += refine_cost
                refined = True
                await self._tracker.log_expense("magic_refiner", refine_cost, persona_id)
            except Exception as exc:  # noqa: BLE001 — рефайн опційний, кадр вже є
                logger.warning(
                    "Refine failed for persona=%s (%s) — returning un-refined frame",
                    persona_id, exc,
                )

        await self._storage.update_persona(
            persona_id,
            total_generations=persona.total_generations + 1,
            total_cost_usd=persona.total_cost_usd + cost,
        )

        logger.info(
            "Photo generated for persona=%s url=%.60s cost=$%.4f refined=%s",
            persona_id, image_url, cost, refined,
        )
        return {
            "image_url": image_url, "cost_usd": cost,
            "full_prompt": full_prompt, "refined": refined,
        }
