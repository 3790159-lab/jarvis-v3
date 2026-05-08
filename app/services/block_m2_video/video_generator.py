# -*- coding: utf-8 -*-
"""Photo-to-Video orchestrator for persona-driven video generation."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Callable

from app.services.block_m_common.cost_tracker import CostTracker
from app.services.block_m_common.logging_setup import get_logger
from app.services.block_m1_persona.photo_generator import PhotoGenerator
from app.services.block_m2_video.generation_history import GenerationHistory
from app.services.block_m2_video.video_client_extras import VideoClientExtras
from app.services.block_m2_video.video_storage import GeneratedVideo, VideoStorage

logger = get_logger("video_generator")


class VideoGenerator:
    """Orchestrates the photo→video pipeline for a trained persona.

    Steps in generate_video():
    1. Generate photo via FLUX+LoRA (PhotoGenerator)
    2. Animate photo via Kling v2.1 or Wan 2.2 Fast (VideoClientExtras)
    3. Log video cost via CostTracker
    4. Persist results to VideoStorage and GenerationHistory

    Args:
        photo_gen: PhotoGenerator for FLUX+LoRA seed photo generation.
        video_extras: VideoClientExtras for video engine dispatch.
        video_storage: VideoStorage for persisting video artifacts.
        history: GenerationHistory for auditable generation records.
        tracker: CostTracker for logging video generation expense.
    """

    def __init__(
        self,
        photo_gen: PhotoGenerator,
        video_extras: VideoClientExtras,
        video_storage: VideoStorage,
        history: GenerationHistory,
        tracker: CostTracker,
    ) -> None:
        self._photo_gen = photo_gen
        self._video_extras = video_extras
        self._video_storage = video_storage
        self._history = history
        self._tracker = tracker

    async def generate_video(
        self,
        persona_id: str,
        prompt: str,
        engine: str = "kling_v21",
        duration: int = 5,
        progress_cb: Callable | None = None,
        parent_record_id: str | None = None,
    ) -> dict:
        """Run the full photo→video pipeline for a persona.

        Args:
            persona_id: Target persona (must have LoRA trained).
            prompt: Scene/motion description.
            engine: Video engine — "kling_v21" | "wan22_fast".
            duration: Video duration in seconds (default 5).
            progress_cb: Optional callback(status: str). The string
                "photo_ready:<url>" signals that the intermediate photo is ready.
            parent_record_id: Set when this is a redo of an existing record.

        Returns:
            {
                "video_url": str,
                "photo_url": str,
                "total_cost_usd": float,
                "record_id": str,
                "video_id": str,
                "prompt": str,
                "engine": str,
            }

        Raises:
            ValueError: If persona not found or LoRA not trained.
            DailyLimitExceeded: If the daily spending cap is exceeded.
        """
        def _cb(msg: str) -> None:
            if progress_cb is not None:
                progress_cb(msg)

        _cb("1/2: generating photo...")
        logger.info("VideoGenerator: generating photo persona=%s engine=%s", persona_id, engine)

        photo_result = await self._photo_gen.generate_photo(persona_id, prompt)
        photo_url: str = photo_result["image_url"]
        photo_cost: float = photo_result["cost_usd"]

        _cb(f"photo_ready:{photo_url}")
        _cb(f"2/2: animating with {engine}...")
        logger.info("VideoGenerator: animating photo engine=%s duration=%ds", engine, duration)

        video_result = await self._video_extras.generate_video_dispatch(
            image_url=photo_url,
            prompt=prompt,
            engine=engine,
            duration=duration,
        )
        video_url: str = video_result["video_url"]
        video_cost: float = video_result["cost_usd"]

        await self._tracker.log_expense(f"{engine}_video", video_cost, persona_id)

        total_cost = photo_cost + video_cost
        video_id = f"video_{uuid.uuid4().hex[:8]}"

        video = GeneratedVideo(
            video_id=video_id,
            persona_id=persona_id,
            source_photo_url=photo_url,
            video_url=video_url,
            prompt=prompt,
            engine=engine,
            duration_sec=duration,
            cost_usd=video_cost,
            created_at=datetime.utcnow(),
        )
        await self._video_storage.save(video)

        record_id = await self._history.record(
            persona_id=persona_id,
            kind="video",
            prompt=prompt,
            input_url=photo_url,
            output_url=video_url,
            engine=engine,
            cost_usd=total_cost,
            parent_record_id=parent_record_id,
        )

        logger.info(
            "VideoGenerator: done persona=%s engine=%s total=$%.4f record=%s",
            persona_id, engine, total_cost, record_id,
        )
        return {
            "video_url": video_url,
            "photo_url": photo_url,
            "total_cost_usd": total_cost,
            "record_id": record_id,
            "video_id": video_id,
            "prompt": prompt,
            "engine": engine,
        }

    async def redo_video(
        self,
        record_id: str,
        new_prompt: str | None = None,
        new_engine: str | None = None,
    ) -> dict:
        """Regenerate a video from a previous GenerationRecord.

        The original record is preserved; the new record's parent_record_id
        is set to record_id to form an auditable redo chain.

        Args:
            record_id: ID of the GenerationRecord to redo.
            new_prompt: Override prompt, or None to reuse the original.
            new_engine: Override engine, or None to reuse the original.

        Returns:
            Same structure as generate_video().

        Raises:
            ValueError: If record_id is not found in history.
        """
        original = await self._history.get(record_id)
        if original is None:
            raise ValueError(f"Record {record_id!r} not found")

        prompt = new_prompt if new_prompt is not None else original.prompt
        engine = new_engine if new_engine is not None else original.engine
        persona_id = original.persona_id

        logger.info(
            "VideoGenerator: redo record=%s persona=%s engine=%s",
            record_id, persona_id, engine,
        )
        return await self.generate_video(
            persona_id=persona_id,
            prompt=prompt,
            engine=engine,
            parent_record_id=record_id,
        )
