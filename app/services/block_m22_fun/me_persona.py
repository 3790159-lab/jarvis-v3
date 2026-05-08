# -*- coding: utf-8 -*-
"""Me-Persona storage — owner's own face persona for face-swap Fun Mode."""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from app.services.block_m_common.logging_setup import get_logger

logger = get_logger("me_persona")

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_ME_PERSONAS_DIR = _ROOT / "state" / "me_personas"


@dataclass
class MePersonaData:
    """Data for the owner's personal Me-Persona.

    Attributes:
        chat_id: Telegram chat ID that owns this persona.
        persona_id: Unique ID, always "me_persona_{chat_id}".
        trigger_word: LoRA activation token.
        lora_weights_url: URL to trained LoRA weights, or empty string if not yet trained.
        seed_photos: List of seed photo URLs collected via /me_seed.
        created_at: UTC creation timestamp.
    """

    chat_id: int
    persona_id: str
    trigger_word: str
    lora_weights_url: str
    seed_photos: list[str]
    created_at: datetime

    @property
    def is_trained(self) -> bool:
        return bool(self.lora_weights_url)


class MePersonaManager:
    """Manages per-chat Me-Persona data in state/me_personas/{chat_id}.json.

    Each chat has at most one Me-Persona. Storage is per-chat JSON (not JSONL)
    since the record is updated in-place rather than appended.
    """

    def __init__(self, storage_dir: Path | None = None) -> None:
        self._root = storage_dir or _ME_PERSONAS_DIR
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _path(self, chat_id: int) -> Path:
        return self._root / f"{chat_id}.json"

    async def get(self, chat_id: int) -> MePersonaData | None:
        """Return Me-Persona for chat_id, or None if it doesn't exist."""
        async with self._lock:
            path = self._path(chat_id)
            if not path.exists():
                return None
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                return self._from_dict(raw)
            except Exception as exc:
                logger.error("Failed to load me_persona chat=%s: %s", chat_id, exc)
                return None

    async def save(self, data: MePersonaData) -> None:
        """Persist (create or overwrite) a Me-Persona record."""
        async with self._lock:
            path = self._path(data.chat_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._to_dict(data), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        logger.info("me_persona saved chat=%s lora_trained=%s", data.chat_id, data.is_trained)

    async def delete(self, chat_id: int) -> None:
        """Delete Me-Persona for chat_id (no-op if not found)."""
        async with self._lock:
            path = self._path(chat_id)
            if path.exists():
                path.unlink()
        logger.info("me_persona deleted chat=%s", chat_id)

    def exists(self, chat_id: int) -> bool:
        """Return True if a Me-Persona file exists for chat_id (sync check)."""
        return self._path(chat_id).exists()

    @staticmethod
    def _to_dict(data: MePersonaData) -> dict:
        d = asdict(data)
        d["created_at"] = data.created_at.isoformat()
        return d

    @staticmethod
    def _from_dict(raw: dict) -> MePersonaData:
        created_at = raw.get("created_at", "")
        if isinstance(created_at, str) and created_at:
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                created_at = datetime.utcnow()
        else:
            created_at = datetime.utcnow()
        return MePersonaData(
            chat_id=raw["chat_id"],
            persona_id=raw["persona_id"],
            trigger_word=raw.get("trigger_word", ""),
            lora_weights_url=raw.get("lora_weights_url", ""),
            seed_photos=raw.get("seed_photos", []),
            created_at=created_at,
        )
