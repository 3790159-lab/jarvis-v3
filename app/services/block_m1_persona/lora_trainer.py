# -*- coding: utf-8 -*-
"""LoRA training pipeline for AI personas via Replicate flux-dev-lora-trainer."""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from app.services.block_m_common.cost_tracker import CostTracker, DailyLimitExceeded
from app.services.block_m_common.logging_setup import get_logger
from app.services.block_m_common.persona_storage import PersonaStorage
from app.services.block_m_common.replicate_video_client import ReplicateVideoClient
from app.services.block_m_common.video_queue import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    VideoJob,
    VideoQueue,
)

logger = get_logger("lora_trainer")

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_LORA_JOBS_FILE = _ROOT / "state" / "personas" / "lora_jobs.json"

MIN_PHOTOS = 10
COST_ESTIMATE_USD = 2.00


class LoRATrainer:
    """Orchestrates LoRA training jobs via Replicate flux-dev-lora-trainer.

    Training is long-running (15-25 min). ``start_training`` submits a
    VideoJob and kicks off a daemon thread so the caller is never blocked.

    Args:
        client: Replicate API client.
        storage: Persona storage.
        tracker: Cost tracker for budget enforcement.
        queue: Video job queue.
        state_file: Path to persona_id→job_id mapping JSON.
    """

    def __init__(
        self,
        client: ReplicateVideoClient,
        storage: PersonaStorage,
        tracker: CostTracker,
        queue: VideoQueue,
        state_file: Path | None = None,
    ) -> None:
        self._client = client
        self._storage = storage
        self._tracker = tracker
        self._queue = queue
        self._state_file = state_file or _LORA_JOBS_FILE
        self._state_file.parent.mkdir(parents=True, exist_ok=True)

    async def start_training(
        self,
        persona_id: str,
        photo_urls: list[str] | None = None,
        steps: int = 1000,
        user_chat_id: int = 0,
        notify_fn: Callable[[int, str], None] | None = None,
        on_success_cost: Callable[[float], None] | None = None,
    ) -> str:
        """Submit a LoRA training job and launch a background worker thread.

        Cost: ~$2.  Time: 15-25 minutes.

        Args:
            persona_id: Target persona identifier.
            photo_urls: Images to train on. None uses all persona.seed_photos.
            steps: Training steps (default 1000).
            user_chat_id: Telegram chat ID for completion notification.
            notify_fn: Callable(chat_id, text) invoked when training finishes.

        Returns:
            Job ID string for status tracking.

        Raises:
            ValueError: If persona not found or has fewer than MIN_PHOTOS seed photos.
            DailyLimitExceeded: If the daily cost budget is already exhausted.
        """
        persona = await self._storage.get_persona(persona_id)
        if not persona:
            raise ValueError(f"Persona {persona_id!r} not found")

        photos = photo_urls if photo_urls is not None else persona.seed_photos
        if len(photos) < MIN_PHOTOS:
            raise ValueError(
                f"Persona {persona_id!r} has only {len(photos)} seed photos; "
                f"minimum {MIN_PHOTOS} required for LoRA training"
            )

        can_proceed, _ = await self._tracker.check_limit()
        if not can_proceed:
            raise DailyLimitExceeded(
                f"Daily limit exceeded; cannot start LoRA training for {persona_id!r}"
            )

        job_id = f"lora_{uuid.uuid4().hex[:8]}"
        job = VideoJob(
            job_id=job_id,
            job_type="lora_training",
            persona_id=persona_id,
            params={"photo_count": len(photos), "steps": steps},
            status=STATUS_PENDING,
            user_chat_id=user_chat_id,
            created_at=datetime.utcnow(),
        )
        await self._queue.submit(job)

        jobs = self._load_jobs()
        jobs[persona_id] = job_id
        self._save_jobs(jobs)

        logger.info(
            "LoRA training job submitted: persona=%s job=%s photos=%d steps=%d",
            persona_id, job_id, len(photos), steps,
        )

        t = threading.Thread(
            target=lambda: asyncio.run(
                self._run_training(
                    job_id, persona, photos, steps, user_chat_id, notify_fn,
                    on_success_cost=on_success_cost,
                )
            ),
            daemon=True,
        )
        t.start()

        return job_id

    async def _run_training(
        self,
        job_id: str,
        persona,
        photos: list[str],
        steps: int,
        user_chat_id: int,
        notify_fn: Callable[[int, str], None] | None,
        on_success_cost: Callable[[float], None] | None = None,
    ) -> None:
        """Execute LoRA training and update queue + storage on completion."""
        try:
            await self._queue._mark_running(job_id)
            logger.info(
                "LoRA training started: job=%s persona=%s photos=%d",
                job_id, persona.persona_id, len(photos),
            )

            result = await self._client.train_flux_lora(
                images=photos,
                trigger_word=persona.trigger_word,
                steps=steps,
            )

            weights_url = result["weights_url"]
            await self._storage.set_lora_weights(
                persona.persona_id, weights_url, persona.trigger_word
            )
            actual_cost = result.get("cost_usd", COST_ESTIMATE_USD)
            await self._tracker.log_expense(
                "lora_training",
                actual_cost,
                persona.persona_id,
            )
            await self._queue._mark_done(job_id, {"weights_url": weights_url})

            # Вариант B: пишем ФАКТИЧЕСКУЮ стоимость в friend-леджер ТОЛЬКО на успехе
            # (провал/прерывание → not reached → не платим).
            if on_success_cost is not None:
                try:
                    on_success_cost(actual_cost)
                except Exception as _exc:  # noqa: BLE001 — учёт не должен ронять тренировку
                    logger.warning("on_success_cost failed: %s", _exc)

            logger.info(
                "LoRA training complete: persona=%s weights=%s",
                persona.persona_id, weights_url,
            )

            if notify_fn and user_chat_id:
                notify_fn(
                    user_chat_id,
                    f"LoRA готова для {persona.name}!\n"
                    f"Trigger word: {persona.trigger_word}\n\n"
                    f"Проверить: /lora_status {persona.persona_id}",
                )

        except Exception as exc:
            logger.error(
                "LoRA training failed: job=%s persona=%s: %s",
                job_id, persona.persona_id, exc, exc_info=True,
            )
            await self._queue._mark_failed(job_id, str(exc))
            if notify_fn and user_chat_id:
                notify_fn(
                    user_chat_id,
                    f"Ошибка тренировки LoRA для {persona.name}: {exc}",
                )

    async def check_status(self, persona_id: str) -> dict | None:
        """Return current training status for a persona.

        Returns:
            dict(status, progress_pct, weights_url, error) or None if no job exists.
        """
        jobs = self._load_jobs()
        job_id = jobs.get(persona_id)
        if not job_id:
            return None

        job = await self._queue.get_status(job_id)
        if not job:
            return None

        _progress = {
            STATUS_PENDING: 0,
            STATUS_RUNNING: 50,
            STATUS_DONE: 100,
            STATUS_FAILED: 0,
        }
        _labels = {
            STATUS_PENDING: "pending",
            STATUS_RUNNING: "training",
            STATUS_DONE: "done",
            STATUS_FAILED: "failed",
        }

        persona = await self._storage.get_persona(persona_id)
        weights_url = (persona.lora_weights_url if persona else None) if job.status == STATUS_DONE else None

        return {
            "status": _labels.get(job.status, job.status),
            "progress_pct": _progress.get(job.status, 0),
            "weights_url": weights_url,
            "error": job.error,
        }

    async def list_trained(self) -> list[dict]:
        """Return all personas that have completed LoRA weights."""
        personas = await self._storage.list_personas()
        return [
            {
                "persona_id": p.persona_id,
                "name": p.name,
                "weights_url": p.lora_weights_url,
                "trigger_word": p.trigger_word,
            }
            for p in personas
            if p.lora_weights_url
        ]

    async def cancel_training(self, persona_id: str) -> bool:
        """Cancel a pending or running training job.

        Returns:
            True if cancelled. False if no job found or job already terminal.
        """
        jobs = self._load_jobs()
        job_id = jobs.get(persona_id)
        if not job_id:
            return False

        job = await self._queue.get_status(job_id)
        if not job:
            return False

        if job.status in (STATUS_DONE, STATUS_FAILED):
            return False

        await self._queue._mark_failed(job_id, "Cancelled by user")
        logger.info("LoRA training cancelled: job=%s persona=%s", job_id, persona_id)
        return True

    # ── private helpers ──────────────────────────────────────────────────────────

    def _load_jobs(self) -> dict:
        """Load persona_id → job_id mapping from disk."""
        try:
            return json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_jobs(self, data: dict) -> None:
        """Atomically write persona_id → job_id mapping to disk."""
        tmp = self._state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._state_file)
