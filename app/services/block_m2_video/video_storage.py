# -*- coding: utf-8 -*-
"""Append-only JSONL storage for generated video artifacts."""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from app.services.block_m_common.logging_setup import get_logger

logger = get_logger("video_storage")

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PERSONAS_DIR = _ROOT / "state" / "personas"


@dataclass
class GeneratedVideo:
    """A single generated video artifact.

    Attributes:
        video_id: Unique identifier, e.g. "video_abc12345".
        persona_id: Owning persona identifier.
        source_photo_url: Input image URL used for image-to-video.
        video_url: Final video URL on Replicate CDN.
        prompt: Text description used for generation.
        engine: Generation engine — "kling_v21" | "wan22_fast".
        duration_sec: Video duration in seconds (5 or 10).
        cost_usd: Cost in USD.
        created_at: UTC creation timestamp.
        parent_video_id: For redo chains — original video this was redone from.
    """

    video_id: str
    persona_id: str
    source_photo_url: str
    video_url: str
    prompt: str
    engine: str
    duration_sec: int
    cost_usd: float
    created_at: datetime
    parent_video_id: str | None = None


def _new_video_id() -> str:
    return f"video_{uuid.uuid4().hex[:8]}"


class VideoStorage:
    """Append-only JSONL-backed storage for GeneratedVideo objects.

    Each persona stores videos in state/personas/{persona_id}/videos.jsonl.
    Writes are serialised with an asyncio.Lock to prevent interleaved appends
    within a single event loop.

    Args:
        storage_dir: Base personas directory. Defaults to state/personas/.
    """

    def __init__(self, storage_dir: Path | None = None) -> None:
        self._root = storage_dir or _PERSONAS_DIR
        self._lock = asyncio.Lock()

    def _video_file(self, persona_id: str) -> Path:
        p = self._root / persona_id
        p.mkdir(parents=True, exist_ok=True)
        return p / "videos.jsonl"

    async def save(self, video: GeneratedVideo) -> None:
        """Append a GeneratedVideo record to the persona's videos.jsonl.

        Args:
            video: The video record to persist.
        """
        async with self._lock:
            path = self._video_file(video.persona_id)
            line = json.dumps(self._to_dict(video), ensure_ascii=False)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        logger.info("Saved video %s for persona %s", video.video_id, video.persona_id)

    async def get(self, video_id: str) -> GeneratedVideo | None:
        """Return a GeneratedVideo by ID, searching across all personas.

        Args:
            video_id: The video identifier to look up.

        Returns:
            GeneratedVideo if found, else None.
        """
        async with self._lock:
            for path in self._root.glob("*/videos.jsonl"):
                for raw in self._read_file(path):
                    if raw.get("video_id") == video_id:
                        return self._from_dict(raw)
        return None

    async def list_for_persona(self, persona_id: str) -> list[GeneratedVideo]:
        """Return all videos owned by a specific persona, oldest first.

        Args:
            persona_id: The persona to list videos for.
        """
        async with self._lock:
            path = self._video_file(persona_id)
            return [self._from_dict(r) for r in self._read_file(path)]

    async def list_redos(self, parent_video_id: str) -> list[GeneratedVideo]:
        """Return all videos that are direct redos of the given parent.

        Args:
            parent_video_id: The original video's ID.

        Returns:
            List of redo GeneratedVideo objects.
        """
        async with self._lock:
            results: list[GeneratedVideo] = []
            for path in self._root.glob("*/videos.jsonl"):
                for raw in self._read_file(path):
                    if raw.get("parent_video_id") == parent_video_id:
                        results.append(self._from_dict(raw))
            return results

    async def delete(self, video_id: str) -> None:
        """Remove a video record by rewriting the backing JSONL without it.

        Args:
            video_id: The video identifier to remove.
        """
        async with self._lock:
            for path in self._root.glob("*/videos.jsonl"):
                records = self._read_file(path)
                filtered = [r for r in records if r.get("video_id") != video_id]
                if len(filtered) < len(records):
                    self._write_file(path, filtered)
                    logger.info("Deleted video %s", video_id)
                    return

    # ── private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _read_file(path: Path) -> list[dict]:
        if not path.exists():
            return []
        records: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return records

    @staticmethod
    def _write_file(path: Path, records: list[dict]) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)

    @staticmethod
    def _to_dict(video: GeneratedVideo) -> dict:
        d = asdict(video)
        d["created_at"] = video.created_at.isoformat()
        return d

    @staticmethod
    def _from_dict(raw: dict) -> GeneratedVideo:
        created_at = raw.get("created_at", "")
        if isinstance(created_at, str) and created_at:
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                created_at = datetime.utcnow()
        elif not isinstance(created_at, datetime):
            created_at = datetime.utcnow()

        return GeneratedVideo(
            video_id=raw["video_id"],
            persona_id=raw["persona_id"],
            source_photo_url=raw.get("source_photo_url", ""),
            video_url=raw.get("video_url", ""),
            prompt=raw.get("prompt", ""),
            engine=raw.get("engine", "kling_v21"),
            duration_sec=raw.get("duration_sec", 5),
            cost_usd=raw.get("cost_usd", 0.0),
            created_at=created_at,
            parent_video_id=raw.get("parent_video_id"),
        )
