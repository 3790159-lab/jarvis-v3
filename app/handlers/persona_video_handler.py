# -*- coding: utf-8 -*-
"""Telegram handlers for ``/persona_video`` and ``/persona_video_redo``."""
from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx

from app.services.block_m2_video.engines.engine_protocol import (
    GenerationMode,
    VideoRequest,
    new_generation_id,
)
from app.services.block_m2_video.engines.history import (
    get_last_generation,
    save_result,
)
from app.services.block_m2_video.engines.router import EngineRouter
from app.services.block_m2_video.generation_history import GenerationHistory
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
        history: GenerationHistory | None = None,
    ) -> None:
        self.router = router or EngineRouter()
        self.storage = storage or PersonaStorage()
        self.history = history or GenerationHistory()

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

    async def _resolve_persona_id(self, persona_name: str) -> str:
        """Case-insensitive lookup of ``persona_id`` by display name.

        Raises ``ValueError`` if no persona matches.
        """
        personas = await self.storage.list_personas()
        target = persona_name.strip().lower()
        match = next(
            (p for p in personas if (p.name or "").lower() == target),
            None,
        )
        if match is None:
            raise ValueError(f"Persona '{persona_name}' not found")
        return match.persona_id

    async def _latest_photo_url(self, persona_id: str) -> str:
        """Return the latest photo URL for ``persona_id`` from history.jsonl.

        Order of preference:

        1. Most recent ``kind == "photo"`` record's ``output_url``.
        2. Otherwise the most recent record's ``input_url`` (source photo
           of a prior video generation).

        Raises ``ValueError`` if no usable URL is found.
        """
        records = await self.history.list_recent(persona_id, limit=50)
        photo_records = [r for r in records if r.kind == "photo" and r.output_url]
        if photo_records:
            return photo_records[0].output_url

        for rec in records:
            if rec.input_url:
                return rec.input_url

        raise ValueError(
            f"No photo or video history found for persona {persona_id!r}. "
            f"Generate a photo first via /persona_photo."
        )

    async def _download_to(self, url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        logger.info("Downloaded input photo %s -> %s", url, dest)

    async def _resolve_persona(
        self, persona_name: str, *, generation_id: str
    ) -> tuple[str, Path]:
        """Resolve persona by name and stage its latest photo locally.

        Returns ``(persona_id, local_input_path)``. The local file lives
        at ``state/personas/videos/{persona_id}/{generation_id}/input_source.png``
        so it is preserved alongside the resulting video.
        """
        persona_id = await self._resolve_persona_id(persona_name)
        url = await self._latest_photo_url(persona_id)

        local_path = (
            Path("state/personas/videos")
            / persona_id
            / generation_id
            / "input_source.png"
        )
        await self._download_to(url, local_path)
        return persona_id, local_path

    # ── commands ────────────────────────────────────────────────────────────

    async def handle_video(self, text: str, chat_id: int) -> dict:
        """Process ``/persona_video`` and return ``{output_path, summary}``.

        The caller is responsible for actually delivering the MP4 + summary
        to Telegram.
        """
        parsed = self.parse_command(text)
        generation_id = new_generation_id()
        persona_id, input_image = await self._resolve_persona(
            parsed["persona_name"], generation_id=generation_id
        )

        request = VideoRequest(
            persona_id=persona_id,
            persona_name=parsed["persona_name"],
            input_image_path=input_image,
            prompt=parsed["prompt"],
            seconds=parsed["seconds"],
            seed=parsed["seed"],
            mode=parsed["mode"],
            generation_id=generation_id,
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
        generation_id = new_generation_id()
        persona_id, input_image = await self._resolve_persona(
            persona_name, generation_id=generation_id
        )

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
            generation_id=generation_id,
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
