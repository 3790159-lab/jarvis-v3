# -*- coding: utf-8 -*-
"""Tests for ``PersonaVideoHandler``."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.handlers.persona_video_handler import PersonaVideoHandler
from app.services.block_m2_video.engines.engine_protocol import VideoResult


# ── parse_command ────────────────────────────────────────────────────────────


def test_parse_command_basic():
    out = PersonaVideoHandler.parse_command("/persona_video Vera a cinematic shot")
    assert out["persona_name"] == "Vera"
    assert out["prompt"] == "a cinematic shot"
    assert out["mode"] == "auto"
    assert out["seconds"] == 5
    assert out["seed"] is None


def test_parse_command_all_flags():
    out = PersonaVideoHandler.parse_command(
        "/persona_video Vera moonlit walk --fast --seconds 7 --seed 123"
    )
    assert out["persona_name"] == "Vera"
    assert out["prompt"] == "moonlit walk"
    assert out["mode"] == "fast"
    assert out["seconds"] == 7
    assert out["seed"] == 123


def test_parse_command_hq_mode():
    out = PersonaVideoHandler.parse_command("/persona_video Vera shot --hq")
    assert out["mode"] == "hq"


def test_parse_command_missing_prompt_raises():
    with pytest.raises(ValueError):
        PersonaVideoHandler.parse_command("/persona_video Vera")


def test_parse_command_missing_args_raises():
    with pytest.raises(ValueError):
        PersonaVideoHandler.parse_command("/persona_video")


# ── handle_video ─────────────────────────────────────────────────────────────


def _setup_persona(tmp_path: Path, *, name: str = "Vera", pid: str = "persona_v1"):
    photos_dir = tmp_path / "state" / "personas" / pid / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    photo = photos_dir / "latest.png"
    photo.write_bytes(b"\x89PNG\r\n\x1a\nIMG")
    persona = MagicMock()
    persona.persona_id = pid
    persona.name = name
    return persona, photo


def _make_storage(persona):
    storage = MagicMock()
    storage.list_personas = AsyncMock(return_value=[persona])
    return storage


def _make_router(result: VideoResult):
    engine = MagicMock()
    engine.engine_name = "replicate"
    engine.generate = AsyncMock(return_value=result)
    router = MagicMock()
    router.select = AsyncMock(return_value=engine)
    return router, engine


def _video_result(persona_id: str, output_dir: Path) -> VideoResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    out = output_dir / "output.mp4"
    out.write_bytes(b"MP4")
    return VideoResult(
        generation_id="gen_test01",
        persona_id=persona_id,
        output_path=out,
        engine="replicate",
        model="wan-video/wan-2.5-i2v-fast",
        seed=42,
        cost_usd=0.10,
        duration_sec=11.2,
        timestamp=datetime(2026, 5, 11, tzinfo=timezone.utc),
        prompt="hello",
        seconds=5,
        extra={},
    )


@pytest.mark.anyio
async def test_handle_video_calls_router_with_parsed_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    persona, _photo = _setup_persona(tmp_path)
    storage = _make_storage(persona)

    out_dir = tmp_path / "state" / "personas" / "videos" / persona.persona_id / "gen_test01"
    result = _video_result(persona.persona_id, out_dir)
    router, engine = _make_router(result)

    handler = PersonaVideoHandler(router=router, storage=storage)
    response = await handler.handle_video(
        "/persona_video Vera a shot --fast --seconds 4 --seed 7",
        chat_id=99,
    )

    router.select.assert_awaited_once_with("fast")
    engine.generate.assert_awaited_once()
    sent_request = engine.generate.await_args.args[0]
    assert sent_request.persona_id == persona.persona_id
    assert sent_request.prompt == "a shot"
    assert sent_request.seconds == 4
    assert sent_request.seed == 7
    assert sent_request.mode == "fast"

    assert response["output_path"] == result.output_path
    assert "Vera" in response["summary"]


@pytest.mark.anyio
async def test_handle_video_raises_when_no_photos(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    persona = MagicMock()
    persona.persona_id = "persona_empty"
    persona.name = "Empty"
    storage = _make_storage(persona)
    router = MagicMock()
    handler = PersonaVideoHandler(router=router, storage=storage)

    with pytest.raises(ValueError):
        await handler.handle_video("/persona_video Empty test prompt", chat_id=1)


@pytest.mark.anyio
async def test_handle_video_raises_when_persona_not_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    storage = MagicMock()
    storage.list_personas = AsyncMock(return_value=[])
    handler = PersonaVideoHandler(router=MagicMock(), storage=storage)

    with pytest.raises(ValueError):
        await handler.handle_video("/persona_video Ghost test", chat_id=1)


# ── handle_redo ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_handle_redo_uses_last_gen_prompt_and_seed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    persona, _photo = _setup_persona(tmp_path)
    storage = _make_storage(persona)

    # Seed history with one prior generation.
    prior_dir = (
        tmp_path / "state" / "personas" / "videos"
        / persona.persona_id / "gen_prior01"
    )
    prior_dir.mkdir(parents=True, exist_ok=True)
    prior_meta = {
        "generation_id": "gen_prior01",
        "persona_id": persona.persona_id,
        "engine": "replicate",
        "model": "wan-video/wan-2.5-i2v-fast",
        "seed": 555,
        "cost_usd": 0.10,
        "duration_sec": 12.0,
        "timestamp": "2026-05-10T12:00:00+00:00",
        "prompt": "original prompt",
        "seconds": 6,
        "extra": {},
    }
    (prior_dir / "metadata.json").write_text(
        json.dumps(prior_meta), encoding="utf-8"
    )

    new_dir = (
        tmp_path / "state" / "personas" / "videos"
        / persona.persona_id / "gen_redo002"
    )
    result = _video_result(persona.persona_id, new_dir)
    router, engine = _make_router(result)

    handler = PersonaVideoHandler(router=router, storage=storage)
    response = await handler.handle_redo("/persona_video_redo Vera", chat_id=99)

    router.select.assert_awaited_once_with("fast")
    sent = engine.generate.await_args.args[0]
    assert sent.prompt == "original prompt"
    assert sent.seed == 555
    assert sent.seconds == 6
    assert "♻️" in response["summary"] or "Redo" in response["summary"]


@pytest.mark.anyio
async def test_handle_redo_raises_when_no_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    persona, _photo = _setup_persona(tmp_path)
    storage = _make_storage(persona)
    handler = PersonaVideoHandler(router=MagicMock(), storage=storage)

    with pytest.raises(ValueError):
        await handler.handle_redo("/persona_video_redo Vera", chat_id=1)
