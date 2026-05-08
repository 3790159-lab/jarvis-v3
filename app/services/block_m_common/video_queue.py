# -*- coding: utf-8 -*-
"""Async job queue for video generation and LoRA training tasks."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_QUEUE_FILE = _ROOT / "state" / "personas" / "video_queue.json"

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


@dataclass
class VideoJob:
    """A single video/training job in the queue.

    Attributes:
        job_id: Unique job identifier.
        job_type: One of "video", "lora_training", "photo_batch".
        persona_id: Associated persona identifier.
        params: Job-specific parameters (prompt, image_url, etc.).
        status: Current state: pending → running → done/failed.
        user_chat_id: Telegram chat ID to notify on completion.
        created_at: UTC timestamp when job was submitted.
        result: Output dict populated on success.
        error: Error message populated on failure.
        completed_at: UTC timestamp when job finished.
    """

    job_id: str
    job_type: str
    persona_id: str
    params: dict
    status: str
    user_chat_id: int
    created_at: datetime
    result: dict | None = None
    error: str | None = None
    completed_at: datetime | None = None


class VideoQueue:
    """Singleton async queue for video generation and LoRA training jobs.

    Persists all jobs to video_queue.json. A background worker coroutine
    processes pending jobs sequentially and emits completion events.

    Usage:
        queue = VideoQueue()
        job_id = await queue.submit(VideoJob(...))
    """

    _instance: VideoQueue | None = None

    def __new__(cls, *args: Any, **kwargs: Any) -> VideoQueue:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        queue_file: Path | None = None,
        on_complete: Callable[[VideoJob], None] | None = None,
    ) -> None:
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self._file = queue_file or _QUEUE_FILE
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._on_complete = on_complete
        self._worker_task: asyncio.Task | None = None
        self._processor: Callable | None = None

    @classmethod
    def reset(cls) -> None:
        """Destroy the singleton instance. Intended for testing only."""
        cls._instance = None

    async def submit(self, job: VideoJob) -> str:
        """Add a job to the queue.

        Args:
            job: The VideoJob to enqueue.

        Returns:
            The job_id string.
        """
        async with self._lock:
            jobs = self._load()
            jobs[job.job_id] = self._to_dict(job)
            self._save(jobs)
        logger.info("Queued job %s type=%s persona=%s", job.job_id, job.job_type, job.persona_id)
        return job.job_id

    async def get_status(self, job_id: str) -> VideoJob | None:
        """Return a job by ID, or None if not found.

        Args:
            job_id: Job identifier to look up.
        """
        async with self._lock:
            raw = self._load().get(job_id)
            return self._from_dict(raw) if raw else None

    async def list_pending(self) -> list[VideoJob]:
        """Return all jobs with status 'pending'."""
        async with self._lock:
            return [
                self._from_dict(v)
                for v in self._load().values()
                if v.get("status") == STATUS_PENDING
            ]

    async def list_for_user(self, chat_id: int) -> list[VideoJob]:
        """Return all jobs submitted by a specific Telegram user.

        Args:
            chat_id: Telegram chat ID to filter by.
        """
        async with self._lock:
            return [
                self._from_dict(v)
                for v in self._load().values()
                if v.get("user_chat_id") == chat_id
            ]

    def start_worker(self, processor: Callable[[VideoJob], Any]) -> None:
        """Launch the background worker coroutine.

        Args:
            processor: Async or sync callable that processes a VideoJob
                       and returns a result dict.
        """
        self._processor = processor
        try:
            loop = asyncio.get_running_loop()
            self._worker_task = loop.create_task(self._worker_loop())
        except RuntimeError:
            logger.warning("No running event loop; worker not started")

    async def stop_worker(self) -> None:
        """Cancel and await the background worker."""
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        self._worker_task = None

    async def _mark_running(self, job_id: str) -> None:
        async with self._lock:
            jobs = self._load()
            if job_id in jobs:
                jobs[job_id]["status"] = STATUS_RUNNING
                self._save(jobs)

    async def _mark_done(self, job_id: str, result: dict) -> None:
        async with self._lock:
            jobs = self._load()
            if job_id in jobs:
                jobs[job_id]["status"] = STATUS_DONE
                jobs[job_id]["result"] = result
                jobs[job_id]["completed_at"] = datetime.utcnow().isoformat()
                self._save(jobs)

    async def _mark_failed(self, job_id: str, error: str) -> None:
        async with self._lock:
            jobs = self._load()
            if job_id in jobs:
                jobs[job_id]["status"] = STATUS_FAILED
                jobs[job_id]["error"] = error
                jobs[job_id]["completed_at"] = datetime.utcnow().isoformat()
                self._save(jobs)

    async def _worker_loop(self) -> None:
        """Process pending jobs one at a time until cancelled."""
        while True:
            pending = await self.list_pending()
            if pending and self._processor is not None:
                job = pending[0]
                await self._mark_running(job.job_id)
                try:
                    if asyncio.iscoroutinefunction(self._processor):
                        result = await self._processor(job)
                    else:
                        result = self._processor(job)
                    await self._mark_done(job.job_id, result or {})
                    completed = await self.get_status(job.job_id)
                    if self._on_complete and completed:
                        self._on_complete(completed)
                except Exception as exc:
                    logger.error("Job %s failed: %s", job.job_id, exc, exc_info=True)
                    await self._mark_failed(job.job_id, str(exc))
            await asyncio.sleep(5)

    # ── private helpers ────────────────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self, data: dict) -> None:
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        tmp.replace(self._file)

    @staticmethod
    def _to_dict(job: VideoJob) -> dict:
        d = asdict(job)
        d["created_at"] = job.created_at.isoformat()
        if job.completed_at:
            d["completed_at"] = job.completed_at.isoformat()
        return d

    @staticmethod
    def _from_dict(raw: dict) -> VideoJob:
        def _parse_dt(val: Any) -> datetime | None:
            if isinstance(val, str) and val:
                try:
                    return datetime.fromisoformat(val)
                except ValueError:
                    return None
            return None

        created_at = _parse_dt(raw.get("created_at")) or datetime.utcnow()
        completed_at = _parse_dt(raw.get("completed_at"))

        return VideoJob(
            job_id=raw["job_id"],
            job_type=raw.get("job_type", "video"),
            persona_id=raw.get("persona_id", ""),
            params=raw.get("params", {}),
            status=raw.get("status", STATUS_PENDING),
            user_chat_id=raw.get("user_chat_id", 0),
            created_at=created_at,
            result=raw.get("result"),
            error=raw.get("error"),
            completed_at=completed_at,
        )
