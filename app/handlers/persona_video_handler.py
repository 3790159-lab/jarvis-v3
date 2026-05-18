# -*- coding: utf-8 -*-
"""Telegram handlers for ``/persona_video`` and ``/persona_video_redo``."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable

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

ProgressCallback = Callable[[str, dict[str, Any]], None]


class PersonaVideoHandler:
    """Handle ``/persona_video <name> <prompt> [--fast|--hq] [--seconds N] [--seed N]``.

    The handler is constructed with a :class:`EngineRouter` (so tests can
    inject a mock router) and a :class:`PersonaStorage` (likewise mockable).
    Persona resolution looks up the persona by name via
    :meth:`PersonaStorage.list_personas`, matching case-insensitively.
    """

    # Used when caller supplies a persona but no prompt. Spec says
    # "use persona's default photo + default prompt"; Persona has no
    # default_prompt field, so we hardcode a sensible fallback.
    DEFAULT_PROMPT = "a cinematic portrait, soft natural light"

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

    @classmethod
    def parse_command(cls, text: str) -> dict:
        """Parse a ``/persona_video`` command into a structured dict.

        Returns one of:
          - ``{"action": "help"}`` for a bare ``/persona_video`` invocation.
          - ``{"action": "video", "persona_token", "prompt", "mode",
                "seconds", "seed"}`` otherwise.

        ``persona_token`` may be either a persona_id or a display name —
        resolution happens later in :meth:`_resolve_persona_id`.
        """
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return {"action": "help"}
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
        if not pieces or not pieces[0]:
            return {"action": "help"}
        persona_token = pieces[0]
        prompt = pieces[1] if len(pieces) > 1 else cls.DEFAULT_PROMPT
        return {
            "action": "video",
            "persona_token": persona_token,
            # back-compat alias: existing callers/tests read persona_name
            "persona_name": persona_token,
            "prompt": prompt,
            "mode": mode,
            "seconds": seconds,
            "seed": seed,
        }

    # ── persona resolution ──────────────────────────────────────────────────

    async def _resolve_persona_id(self, persona_token: str) -> str:
        """Resolve ``persona_token`` to a persona_id.

        Tries ``storage.get_persona(token)`` first (token-as-id), then falls
        back to a case-insensitive name match. Raises ``ValueError`` if
        nothing matches.
        """
        # Try direct persona_id lookup. Tolerate stores that don't implement
        # ``get_persona`` (e.g. legacy mocks in tests).
        get_persona = getattr(self.storage, "get_persona", None)
        if callable(get_persona):
            try:
                by_id = await get_persona(persona_token)
            except Exception:
                by_id = None
            if by_id is not None:
                return by_id.persona_id

        raw_codes = " ".join(f"U+{ord(c):04X}" for c in persona_token)
        logger.info(
            "Persona lookup: query=%r codepoints=[%s]",
            persona_token, raw_codes
        )
        personas = await self.storage.list_personas()
        for p in personas:
            p_codes = " ".join(f"U+{ord(c):04X}" for c in p.name)
            logger.info(
                "  candidate: id=%s name=%r codepoints=[%s]",
                p.persona_id, p.name, p_codes,
            )
        target = persona_token.strip().lower()
        match = next(
            (p for p in personas if p.name.strip().lower() == target),
            None,
        )
        if match is None:
            raise ValueError(f"Persona '{persona_token}' not found")
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

    @staticmethod
    def _fire(cb: ProgressCallback | None, stage: str, payload: dict) -> None:
        """Invoke a progress callback, swallowing any exception it raises."""
        if cb is None:
            return
        try:
            cb(stage, payload)
        except Exception:
            logger.exception("progress_cb stage=%s failed", stage)

    async def handle_help(self) -> str:
        """Return help text for ``/persona_video``, including a persona list."""
        try:
            personas = await self.storage.list_personas()
        except Exception:
            personas = []
        lines = [
            "Использование: /persona_video <persona_id или имя> [prompt]",
            "Флаги: --fast | --hq | --seconds N | --seed N",
            "",
            "Можно прикрепить фото к сообщению или ответить на сообщение с фото —",
            "тогда оно будет использовано как стартовый кадр.",
            "",
        ]
        if personas:
            lines.append("Доступные персоны:")
            for p in personas:
                lines.append(f"  • {p.name}  ({p.persona_id})")
        else:
            lines.append("(Нет сохранённых персон — создай через /create_persona)")
        return "\n".join(lines)

    async def handle_video(
        self,
        text: str,
        chat_id: int,
        *,
        progress_cb: ProgressCallback | None = None,
        input_photo_path: Path | None = None,
    ) -> dict:
        """Process ``/persona_video`` and return ``{output_path, summary}``.

        Args:
            text: Full Telegram message text, starting with ``/persona_video``.
            chat_id: Telegram chat id (for logging).
            progress_cb: Optional callback invoked at stage transitions.
                Fired stages: ``"persona_resolved"`` (payload:
                ``persona_id``, ``persona_name``) and ``"engine_selected"``
                (payload: ``engine_name``, ``mode``). The caller can use these
                to send interim Telegram updates.
            input_photo_path: Optional local path to a photo supplied by the
                caller (attached photo / reply-to-photo). When provided,
                history-based photo lookup is skipped.

        The caller is responsible for actually delivering the MP4 + summary
        to Telegram.
        """
        parsed = self.parse_command(text)
        if parsed.get("action") == "help":
            raise ValueError(
                "Bare /persona_video — caller should invoke handle_help() instead."
            )
        generation_id = new_generation_id()
        persona_token = parsed["persona_name"]

        if input_photo_path is not None:
            persona_id = await self._resolve_persona_id(persona_token)
            input_image = Path(input_photo_path)
        else:
            persona_id, input_image = await self._resolve_persona(
                persona_token, generation_id=generation_id
            )

        self._fire(progress_cb, "persona_resolved", {
            "persona_id": persona_id,
            "persona_name": persona_token,
        })

        request = VideoRequest(
            persona_id=persona_id,
            persona_name=persona_token,
            input_image_path=input_image,
            prompt=parsed["prompt"],
            seconds=parsed["seconds"],
            seed=parsed["seed"],
            mode=parsed["mode"],
            generation_id=generation_id,
        )

        engine = await self.router.select(parsed["mode"])
        self._fire(progress_cb, "engine_selected", {
            "engine_name": engine.engine_name,
            "mode": parsed["mode"],
        })

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
                f"✅ Video generated for {persona_token}\n"
                f"Engine: {result.engine} | Model: {result.model}\n"
                f"Duration: {result.duration_sec:.1f}s | "
                f"Cost: ${result.cost_usd:.3f}\n"
                f"Seed: {result.seed} | ID: {result.generation_id}"
            ),
        }

    async def handle_redo(
        self,
        text: str,
        chat_id: int,
        *,
        progress_cb: ProgressCallback | None = None,
        input_photo_path: Path | None = None,
    ) -> dict:
        """Re-generate the last video for a persona using the same prompt+seed."""
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            raise ValueError("Usage: /persona_video_redo <name>")
        persona_name = parts[1].strip()
        generation_id = new_generation_id()

        if input_photo_path is not None:
            persona_id = await self._resolve_persona_id(persona_name)
            input_image = Path(input_photo_path)
        else:
            persona_id, input_image = await self._resolve_persona(
                persona_name, generation_id=generation_id
            )

        last = get_last_generation(persona_id)
        if not last:
            raise ValueError(f"No previous generation for {persona_name}")

        self._fire(progress_cb, "persona_resolved", {
            "persona_id": persona_id,
            "persona_name": persona_name,
        })

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
        self._fire(progress_cb, "engine_selected", {
            "engine_name": engine.engine_name,
            "mode": mode,
        })
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
