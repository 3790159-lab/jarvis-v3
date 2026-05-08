# -*- coding: utf-8 -*-
"""JSON-backed storage for AI persona definitions."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PERSONAS_DIR = _ROOT / "state" / "personas"


@dataclass
class Persona:
    """AI persona with associated LoRA weights and metadata.

    Attributes:
        persona_id: Unique identifier, e.g. "persona_001".
        name: Human-readable name, e.g. "Sofia".
        description: Physical/visual description.
        style: Content style tag, e.g. "fashion+lifestyle".
        lora_weights_url: URL to trained LoRA weights file, or None.
        trigger_word: Unique LoRA activation token.
        created_at: UTC creation timestamp.
        seed_photos: Paths to source training images.
        total_generations: Cumulative generation count.
        total_cost_usd: Cumulative USD cost.
    """

    persona_id: str
    name: str
    description: str
    style: str
    lora_weights_url: str | None
    trigger_word: str
    created_at: datetime
    seed_photos: list[str] = field(default_factory=list)
    total_generations: int = 0
    total_cost_usd: float = 0.0


class PersonaStorage:
    """Thread-safe JSON-backed storage for Persona objects.

    All mutation methods are guarded by an asyncio.Lock to prevent
    concurrent write races within a single event loop.

    Args:
        storage_dir: Base directory for personas.json and per-persona subdirs.
                     Defaults to state/personas/ relative to project root.
    """

    def __init__(self, storage_dir: Path | None = None) -> None:
        self._dir = storage_dir or _PERSONAS_DIR
        self._file = self._dir / "personas.json"
        self._lock = asyncio.Lock()
        self._dir.mkdir(parents=True, exist_ok=True)

    async def create_persona(
        self, name: str, description: str, style: str
    ) -> Persona:
        """Create, persist, and return a new Persona.

        Args:
            name: Display name.
            description: Visual description.
            style: Content style tag.

        Returns:
            The newly created Persona instance.
        """
        async with self._lock:
            persona_id = f"persona_{uuid.uuid4().hex[:8]}"
            trigger_word = f"sks_{persona_id}"
            persona = Persona(
                persona_id=persona_id,
                name=name,
                description=description,
                style=style,
                lora_weights_url=None,
                trigger_word=trigger_word,
                created_at=datetime.utcnow(),
            )
            (self._dir / persona_id).mkdir(parents=True, exist_ok=True)
            self._upsert_locked(persona)
            logger.info("Created persona %s (%s)", persona_id, name)
            return persona

    async def get_persona(self, persona_id: str) -> Persona | None:
        """Return Persona by ID, or None if not found.

        Args:
            persona_id: The persona identifier.
        """
        async with self._lock:
            raw = self._load_all().get(persona_id)
            return self._from_dict(raw) if raw else None

    async def list_personas(self) -> list[Persona]:
        """Return all stored personas."""
        async with self._lock:
            return [self._from_dict(v) for v in self._load_all().values()]

    async def update_persona(self, persona_id: str, **kwargs: Any) -> None:
        """Update one or more fields on an existing persona.

        Args:
            persona_id: Target persona identifier.
            **kwargs: Field names and new values to apply.

        Raises:
            KeyError: If persona_id does not exist.
        """
        async with self._lock:
            data = self._load_all()
            if persona_id not in data:
                raise KeyError(f"Persona {persona_id!r} not found")
            data[persona_id].update(kwargs)
            self._save_all(data)

    async def delete_persona(self, persona_id: str) -> None:
        """Remove a persona from storage. Silently ignores missing IDs.

        Args:
            persona_id: Target persona identifier.
        """
        async with self._lock:
            data = self._load_all()
            data.pop(persona_id, None)
            self._save_all(data)

    async def add_seed_photo(self, persona_id: str, photo_path: str) -> None:
        """Append a seed photo path to a persona's photo list.

        Args:
            persona_id: Target persona identifier.
            photo_path: Filesystem path or URL of the photo.

        Raises:
            KeyError: If persona_id does not exist.
        """
        async with self._lock:
            data = self._load_all()
            if persona_id not in data:
                raise KeyError(f"Persona {persona_id!r} not found")
            data[persona_id].setdefault("seed_photos", []).append(photo_path)
            self._save_all(data)

    async def set_lora_weights(
        self, persona_id: str, weights_url: str, trigger_word: str
    ) -> None:
        """Record trained LoRA weights for a persona.

        Args:
            persona_id: Target persona identifier.
            weights_url: URL to the .safetensors weights file.
            trigger_word: LoRA activation token.

        Raises:
            KeyError: If persona_id does not exist.
        """
        async with self._lock:
            data = self._load_all()
            if persona_id not in data:
                raise KeyError(f"Persona {persona_id!r} not found")
            data[persona_id]["lora_weights_url"] = weights_url
            data[persona_id]["trigger_word"] = trigger_word
            self._save_all(data)

    # ── private helpers ────────────────────────────────────────────────────────

    def _upsert_locked(self, persona: Persona) -> None:
        """Write persona to disk. Must be called inside self._lock."""
        data = self._load_all()
        data[persona.persona_id] = self._to_dict(persona)
        self._save_all(data)

    def _load_all(self) -> dict:
        try:
            return json.loads(self._file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_all(self, data: dict) -> None:
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        tmp.replace(self._file)

    @staticmethod
    def _to_dict(persona: Persona) -> dict:
        d = asdict(persona)
        d["created_at"] = persona.created_at.isoformat()
        return d

    @staticmethod
    def _from_dict(raw: dict) -> Persona:
        created_at = raw.get("created_at", "")
        if isinstance(created_at, str) and created_at:
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                created_at = datetime.utcnow()
        elif not isinstance(created_at, datetime):
            created_at = datetime.utcnow()

        return Persona(
            persona_id=raw["persona_id"],
            name=raw["name"],
            description=raw.get("description", ""),
            style=raw.get("style", ""),
            lora_weights_url=raw.get("lora_weights_url"),
            trigger_word=raw.get("trigger_word", ""),
            created_at=created_at,
            seed_photos=raw.get("seed_photos", []),
            total_generations=raw.get("total_generations", 0),
            total_cost_usd=raw.get("total_cost_usd", 0.0),
        )
