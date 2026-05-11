# -*- coding: utf-8 -*-
"""Tests for ``PersonaVideoHandler``."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.handlers.persona_video_handler import PersonaVideoHandler
from app.services.block_m2_video.engines.engine_protocol import VideoResult
from app.services.block_m2_video.generation_history import GenerationRecord


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


# ── shared test helpers ─────────────────────────────────────────────────────


def _make_persona(name: str = "Vera", pid: str = "persona_v1"):
    persona = MagicMock()
    persona.persona_id = pid
    persona.name = name
    return persona


def _make_storage(persona):
    storage = MagicMock()
    storage.list_personas = AsyncMock(return_value=[persona])
    return storage


def _make_history(records: list[GenerationRecord]):
    history = MagicMock()
    history.list_recent = AsyncMock(return_value=records)
    return history


def _video_record(
    persona_id: str = "persona_v1",
    *,
    kind: str = "video",
    input_url: str | None = "https://replicate.delivery/in.png",
    output_url: str = "https://replicate.delivery/out.mp4",
) -> GenerationRecord:
    return GenerationRecord(
        record_id="rec_x1",
        persona_id=persona_id,
        kind=kind,
        prompt="prior prompt",
        input_url=input_url,
        output_url=output_url,
        engine="wan22_fast",
        cost_usd=0.10,
        created_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
        parent_record_id=None,
    )


def _photo_record(persona_id: str = "persona_v1") -> GenerationRecord:
    return GenerationRecord(
        record_id="rec_p1",
        persona_id=persona_id,
        kind="photo",
        prompt="prior photo prompt",
        input_url=None,
        output_url="https://replicate.delivery/photo.webp",
        engine="flux_lora",
        cost_usd=0.02,
        created_at=datetime(2026, 5, 6, tzinfo=timezone.utc),
        parent_record_id=None,
    )


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


def _mock_httpx_response(content: bytes = b"PNGBYTES") -> MagicMock:
    """Return a mocked AsyncClient yielding ``content`` from .get()."""
    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.content = content
    response.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.get = AsyncMock(return_value=response)
    return fake_client


# ── _resolve_persona_id / _latest_photo_url / _resolve_persona ───────────────


@pytest.mark.anyio
async def test_resolve_persona_finds_by_case_insensitive_name():
    persona = _make_persona(name="Вера", pid="persona_af2f")
    storage = _make_storage(persona)
    handler = PersonaVideoHandler(
        router=MagicMock(),
        storage=storage,
        history=_make_history([]),
    )

    pid = await handler._resolve_persona_id("вера")
    assert pid == "persona_af2f"

    pid_upper = await handler._resolve_persona_id("ВЕРА")
    assert pid_upper == "persona_af2f"

    pid_spaced = await handler._resolve_persona_id("  Вера  ")
    assert pid_spaced == "persona_af2f"


@pytest.mark.anyio
async def test_resolve_persona_id_raises_when_unknown():
    storage = MagicMock()
    storage.list_personas = AsyncMock(return_value=[])
    handler = PersonaVideoHandler(
        router=MagicMock(), storage=storage, history=_make_history([])
    )

    with pytest.raises(ValueError):
        await handler._resolve_persona_id("Ghost")


@pytest.mark.anyio
async def test_latest_photo_url_prefers_photo_record():
    photo = _photo_record()
    video = _video_record()
    handler = PersonaVideoHandler(
        router=MagicMock(),
        storage=MagicMock(),
        history=_make_history([photo, video]),
    )

    url = await handler._latest_photo_url("persona_v1")
    assert url == photo.output_url


@pytest.mark.anyio
async def test_latest_photo_url_falls_back_to_video_input_url():
    video = _video_record()
    handler = PersonaVideoHandler(
        router=MagicMock(),
        storage=MagicMock(),
        history=_make_history([video]),
    )

    url = await handler._latest_photo_url("persona_v1")
    assert url == video.input_url


@pytest.mark.anyio
async def test_latest_photo_url_raises_when_empty():
    handler = PersonaVideoHandler(
        router=MagicMock(),
        storage=MagicMock(),
        history=_make_history([]),
    )

    with pytest.raises(ValueError):
        await handler._latest_photo_url("persona_v1")


@pytest.mark.anyio
async def test_resolve_persona_downloads_latest_photo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    persona = _make_persona()
    storage = _make_storage(persona)
    history = _make_history([_photo_record()])

    handler = PersonaVideoHandler(
        router=MagicMock(), storage=storage, history=history
    )

    fake_client = _mock_httpx_response(content=b"\x89PNG_DOWNLOADED")
    with patch(
        "app.handlers.persona_video_handler.httpx.AsyncClient",
        return_value=fake_client,
    ):
        persona_id, local_path = await handler._resolve_persona(
            "Vera", generation_id="gen_dl00001"
        )

    assert persona_id == "persona_v1"
    assert local_path == (
        Path("state/personas/videos") / "persona_v1" / "gen_dl00001" / "input_source.png"
    )
    assert local_path.exists()
    assert local_path.read_bytes() == b"\x89PNG_DOWNLOADED"
    fake_client.get.assert_awaited_once_with("https://replicate.delivery/photo.webp")


# ── handle_video ─────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_handle_video_calls_router_with_parsed_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    persona = _make_persona()
    storage = _make_storage(persona)
    history = _make_history([_photo_record()])

    out_dir = tmp_path / "state" / "personas" / "videos" / persona.persona_id / "gen_x1"
    result = _video_result(persona.persona_id, out_dir)
    router, engine = _make_router(result)

    handler = PersonaVideoHandler(router=router, storage=storage, history=history)

    fake_client = _mock_httpx_response()
    with patch(
        "app.handlers.persona_video_handler.httpx.AsyncClient",
        return_value=fake_client,
    ):
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
    # generation_id allocated by handler and propagated through to VideoRequest
    assert sent_request.generation_id is not None
    assert sent_request.input_image_path.name == "input_source.png"

    assert response["output_path"] == result.output_path
    assert "Vera" in response["summary"]


@pytest.mark.anyio
async def test_handle_video_raises_when_no_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    persona = _make_persona(name="Empty", pid="persona_empty")
    storage = _make_storage(persona)
    history = _make_history([])  # no records at all
    handler = PersonaVideoHandler(
        router=MagicMock(), storage=storage, history=history
    )

    with pytest.raises(ValueError):
        await handler.handle_video("/persona_video Empty test prompt", chat_id=1)


@pytest.mark.anyio
async def test_handle_video_raises_when_persona_not_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    storage = MagicMock()
    storage.list_personas = AsyncMock(return_value=[])
    handler = PersonaVideoHandler(
        router=MagicMock(), storage=storage, history=_make_history([])
    )

    with pytest.raises(ValueError):
        await handler.handle_video("/persona_video Ghost test", chat_id=1)


# ── handle_redo ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_handle_redo_uses_last_gen_prompt_and_seed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    persona = _make_persona()
    storage = _make_storage(persona)
    history = _make_history([_photo_record()])

    # Seed a Phase-A history (separate from M.2.1 history.jsonl)
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

    handler = PersonaVideoHandler(router=router, storage=storage, history=history)

    fake_client = _mock_httpx_response()
    with patch(
        "app.handlers.persona_video_handler.httpx.AsyncClient",
        return_value=fake_client,
    ):
        response = await handler.handle_redo(
            "/persona_video_redo Vera", chat_id=99
        )

    router.select.assert_awaited_once_with("fast")
    sent = engine.generate.await_args.args[0]
    assert sent.prompt == "original prompt"
    assert sent.seed == 555
    assert sent.seconds == 6
    assert sent.generation_id is not None
    assert sent.input_image_path.name == "input_source.png"
    assert "♻️" in response["summary"] or "Redo" in response["summary"]


@pytest.mark.anyio
async def test_handle_redo_raises_when_no_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    persona = _make_persona()
    storage = _make_storage(persona)
    history = _make_history([_photo_record()])  # has source photo but no Phase-A redo target
    handler = PersonaVideoHandler(
        router=MagicMock(), storage=storage, history=history
    )

    fake_client = _mock_httpx_response()
    with patch(
        "app.handlers.persona_video_handler.httpx.AsyncClient",
        return_value=fake_client,
    ):
        with pytest.raises(ValueError):
            await handler.handle_redo("/persona_video_redo Vera", chat_id=1)
