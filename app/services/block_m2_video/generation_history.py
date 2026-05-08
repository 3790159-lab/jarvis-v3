# -*- coding: utf-8 -*-
"""Universal generation history for photos and videos, with redo support."""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from app.services.block_m_common.logging_setup import get_logger

logger = get_logger("generation_history")

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PERSONAS_DIR = _ROOT / "state" / "personas"


@dataclass
class GenerationRecord:
    """A single photo or video generation event.

    Attributes:
        record_id: Unique identifier, e.g. "rec_abc12345".
        persona_id: Owning persona identifier.
        kind: Generation type — "photo" | "video" | "face_swap_video".
        prompt: Text prompt used for generation.
        input_url: Source URL for video (source photo), or None for photos.
        output_url: Final output URL (image or video).
        engine: Engine used — "flux_lora" | "kling_v21" | "wan22" | "faceswap_video".
        cost_usd: Cost in USD.
        created_at: UTC creation timestamp.
        parent_record_id: For redo — ID of the record this was redone from.
    """

    record_id: str
    persona_id: str
    kind: str
    prompt: str
    input_url: str | None
    output_url: str
    engine: str
    cost_usd: float
    created_at: datetime
    parent_record_id: str | None = None


class GenerationHistory:
    """Append-only JSONL-backed history of all generation events.

    Each persona stores records in state/personas/{persona_id}/history.jsonl.
    Redo records carry a parent_record_id but the original record is preserved.

    Args:
        storage_dir: Base personas directory. Defaults to state/personas/.
    """

    def __init__(self, storage_dir: Path | None = None) -> None:
        self._root = storage_dir or _PERSONAS_DIR
        self._lock = asyncio.Lock()

    def _history_file(self, persona_id: str) -> Path:
        p = self._root / persona_id
        p.mkdir(parents=True, exist_ok=True)
        return p / "history.jsonl"

    async def record(self, **kwargs) -> str:
        """Append a new generation record and return its record_id.

        If record_id is omitted, one is auto-generated.
        If created_at is omitted, datetime.utcnow() is used.

        Args:
            **kwargs: Fields matching GenerationRecord (except record_id/created_at
                      which are optional).

        Returns:
            record_id of the newly created record.
        """
        record_id = kwargs.pop("record_id", f"rec_{uuid.uuid4().hex[:8]}")
        if "created_at" not in kwargs:
            kwargs["created_at"] = datetime.utcnow()

        rec = GenerationRecord(record_id=record_id, **kwargs)

        async with self._lock:
            path = self._history_file(rec.persona_id)
            line = json.dumps(self._to_dict(rec), ensure_ascii=False)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

        logger.info(
            "History record %s (%s) persona=%s cost=$%.4f",
            record_id, rec.kind, rec.persona_id, rec.cost_usd,
        )
        return record_id

    async def get(self, record_id: str) -> GenerationRecord | None:
        """Return a GenerationRecord by ID, searching all personas.

        Args:
            record_id: The record identifier to look up.

        Returns:
            GenerationRecord if found, else None.
        """
        async with self._lock:
            for path in self._root.glob("*/history.jsonl"):
                for raw in self._read_file(path):
                    if raw.get("record_id") == record_id:
                        return self._from_dict(raw)
        return None

    async def list_recent(
        self, persona_id: str, limit: int = 20
    ) -> list[GenerationRecord]:
        """Return the most recent generation records for a persona, newest first.

        Args:
            persona_id: The persona to query.
            limit: Maximum records to return (default 20).

        Returns:
            List of GenerationRecord sorted by created_at descending.
        """
        async with self._lock:
            path = self._history_file(persona_id)
            all_records = [self._from_dict(r) for r in self._read_file(path)]

        all_records.sort(key=lambda r: r.created_at, reverse=True)
        return all_records[:limit]

    async def redo_chain(self, record_id: str) -> list[GenerationRecord]:
        """Return all records that are direct or transitive redos of record_id.

        Performs a BFS over parent_record_id links. The original record is
        NOT included in the result.

        Args:
            record_id: The original record to find redos for.

        Returns:
            All descendant redo records (unordered).
        """
        async with self._lock:
            all_records: list[GenerationRecord] = []
            for path in self._root.glob("*/history.jsonl"):
                all_records.extend(self._from_dict(r) for r in self._read_file(path))

        descendants: list[GenerationRecord] = []
        frontier: set[str] = {record_id}
        visited: set[str] = set()

        while frontier:
            current = frontier.pop()
            visited.add(current)
            children = [
                r for r in all_records
                if r.parent_record_id == current and r.record_id not in visited
            ]
            descendants.extend(children)
            frontier.update(c.record_id for c in children)

        return descendants

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
    def _to_dict(rec: GenerationRecord) -> dict:
        d = asdict(rec)
        d["created_at"] = rec.created_at.isoformat()
        return d

    @staticmethod
    def _from_dict(raw: dict) -> GenerationRecord:
        created_at = raw.get("created_at", "")
        if isinstance(created_at, str) and created_at:
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                created_at = datetime.utcnow()
        elif not isinstance(created_at, datetime):
            created_at = datetime.utcnow()

        return GenerationRecord(
            record_id=raw["record_id"],
            persona_id=raw["persona_id"],
            kind=raw.get("kind", "photo"),
            prompt=raw.get("prompt", ""),
            input_url=raw.get("input_url"),
            output_url=raw.get("output_url", ""),
            engine=raw.get("engine", "flux_lora"),
            cost_usd=raw.get("cost_usd", 0.0),
            created_at=created_at,
            parent_record_id=raw.get("parent_record_id"),
        )
