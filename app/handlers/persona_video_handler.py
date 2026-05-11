# -*- coding: utf-8 -*-
"""Telegram handlers for ``/persona_video`` and ``/persona_video_redo``."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from app.services.block_m2_video.engines.engine_protocol import (
    GenerationMode,
    VideoRequest,
)
from app.services.block_m2_video.engines.history import (
    get_last_generation,
    save_result,
)
from app.services.block_m2_video.engines.router import EngineRouter
from app.services.block_m_common.persona_storage import PersonaStorage

logger = logging.getLogger(__name__)


class PersonaVideoHandler:
    """Handle ``/persona_video <name> <prompt> [--fast|--hq] [--seconds N] [--seed N]``.

    The handler is constructed with a :class:`EngineRouter` (so tests can
    inject a mock router) and a :class:`PersonaStorage` (likewise mockable).
    Persona resolution looks up the persona by name via
    :meth:`PersonaStorage.list_personas`, matching case-insensitively.
    """

    def __init__(
        self,
        router: EngineRouter | None = None,
        storage: PersonaStorage | None = None,
    ) -> None:
        self.router = router or EngineRouter()
        self.storage = storage or PersonaStorage()

    # ── parsing ─────────────────────────────────────────────────────────────

    @staticmethod
    def parse_command(text: str) -> dict:
        """Parse a ``/persona_video`` command into a structured dict.

        Returns keys: ``persona_name``, ``prompt``, ``mode``, ``seconds``, ``seed``.
        """
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            raise ValueError(
                "Usage: /persona_video <name> <prompt> "
                "[--fast|--hq] [--seconds N] [--seed N]"
            )
        rest = parts[1]

        mode: GenerationMode = "auto"
        seconds = 5
        seed: int | None = None

        if "--fast" in rest:
            mode = "fast"
            rest = rest.replace("--fast", "").strip()
        if "--hq" in rest:
            mode = "hq"
            rest = rest.replace("--hq", "").strip()

        m = re.search(r"--seconds\s+(\d+)", rest)
        if m:
            seconds = int(m.group(1))
            rest = re.sub(r"--seconds\s+\d+", "", rest).strip()

        m = re.search(r"--seed\s+(\d+)", rest)
        if m:
            seed = int(m.group(1))
            rest = re.sub(r"--seed\s+\d+", "", rest).strip()

        pieces = rest.split(maxsplit=1)
        if len(pieces) < 2:
            raise ValueError(
                "Missing prompt. Usage: /persona_video <name> <prompt>"
            )
        return {
            "persona_name": pieces[0],
            "prompt": pieces[1],
            "mode": mode,
            "seconds": seconds,
            "seed": seed,
        }

    # ── persona resolution ──────────────────────────────────────────────────

    async def _resolve_persona(self, persona_name: str) -> tuple[str, Path]:
        """Find persona id by name and return (persona_id, latest_photo_path)."""
        personas = await self.storage.list_personas()
        target = persona_name.strip().lower()
        match = next(
            (p for p in personas if (p.name or "").lower() == target),
            None,
        )
        if match is None:
            raise ValueError(f"Persona '{persona_name}' not found")
        persona_id = match.persona_id

        photos_dir = Path("state/personas") / persona_id / "photos"
        if not photos_dir.exists():
            raise ValueError(
                f"No photos directory for persona '{persona_name}'. "
                f"Run /persona_photo first."
            )
        photos = sorted(
            photos_dir.glob("*.png"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not photos:
            raise ValueError(
                f"No photos found for persona '{persona_name}'. "
                f"Run /persona_photo first."
            )
        return persona_id, photos[0]

    # ── commands ────────────────────────────────────────────────────────────

    async def handle_video(self, text: str, chat_id: int) -> dict:
        """Process ``/persona_video`` and return ``{output_path, summary}``.

        The caller is responsible for actually delivering the MP4 + summary
        to Telegram.
        """
        parsed = self.parse_command(text)
        persona_id, input_image = await self._resolve_persona(parsed["persona_name"])

        request = VideoRequest(
            persona_id=persona_id,
            persona_name=parsed["persona_name"],
            input_image_path=input_image,
            prompt=parsed["prompt"],
            seconds=parsed["seconds"],
            seed=parsed["seed"],
            mode=parsed["mode"],
        )

        engine = await self.router.select(parsed["mode"])
        logger.info(
            "Generating video for chat_id=%s, persona=%s, engine=%s",
            chat_id,
            persona_id,
            engine.engine_name,
        )
        result = await engine.generate(request)
        save_result(result, request)

        return {
            "output_path": result.output_path,
            "summary": (
                f"✅ Video generated for {parsed['persona_name']}\n"
                f"Engine: {result.engine} | Model: {result.model}\n"
                f"Duration: {result.duration_sec:.1f}s | "
                f"Cost: ${result.cost_usd:.3f}\n"
                f"Seed: {result.seed} | ID: {result.generation_id}"
            ),
        }

    async def handle_redo(self, text: str, chat_id: int) -> dict:
        """Re-generate the last video for a persona using the same prompt+seed."""
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            raise ValueError("Usage: /persona_video_redo <name>")
        persona_name = parts[1].strip()
        persona_id, input_image = await self._resolve_persona(persona_name)

        last = get_last_generation(persona_id)
        if not last:
            raise ValueError(f"No previous generation for {persona_name}")

        mode: GenerationMode = "fast" if last["engine"] == "replicate" else "hq"
        request = VideoRequest(
            persona_id=persona_id,
            persona_name=persona_name,
            input_image_path=input_image,
            prompt=last["prompt"],
            seconds=last["seconds"],
            seed=last["seed"],
            mode=mode,
        )
        engine = await self.router.select(mode)
        logger.info(
            "Redo: chat_id=%s, persona=%s, engine=%s, last_id=%s",
            chat_id,
            persona_id,
            engine.engine_name,
            last["generation_id"],
        )
        result = await engine.generate(request)
        save_result(result, request)
        return {
            "output_path": result.output_path,
            "summary": (
                f"♻️ Redo: {result.generation_id} "
                f"(orig prompt+seed from {last['generation_id']})"
            ),
        }
