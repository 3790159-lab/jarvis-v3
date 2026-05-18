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


def test_parse_command_missing_prompt_uses_default():
    out = PersonaVideoHandler.parse_command("/persona_video Vera")
    assert out["action"] == "video"
    assert out["persona_name"] == "Vera"
    assert out["prompt"] == PersonaVideoHandler.DEFAULT_PROMPT


def test_parse_command_bare_returns_help():
    out = PersonaVideoHandler.parse_command("/persona_video")
    assert out == {"action": "help"}


def test_parse_command_bare_with_whitespace_returns_help():
    out = PersonaVideoHandler.parse_command("/persona_video   ")
    assert out == {"action": "help"}


def test_parse_command_flags_only_returns_help():
    # all tokens consumed by flag parsing — no persona, no prompt
    out = PersonaVideoHandler.parse_command("/persona_video --fast --seconds 5")
    assert out == {"action": "help"}


def test_parse_command_persona_id_token():
    # persona_id-shaped token works the same as a name at the parse layer;
    # resolution-by-id is a downstream concern.
    out = PersonaVideoHandler.parse_command(
        "/persona_video persona_af2f a portrait"
    )
    assert out["persona_token"] == "persona_af2f"
    assert out["persona_name"] == "persona_af2f"
    assert out["prompt"] == "a portrait"


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


# ── handle_help ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_handle_help_lists_personas():
    p1 = _make_persona(name="Vera", pid="persona_v1")
    p2 = _make_persona(name="Sofia", pid="persona_s2")
    storage = MagicMock()
    storage.list_personas = AsyncMock(return_value=[p1, p2])
    handler = PersonaVideoHandler(
        router=MagicMock(), storage=storage, history=_make_history([])
    )

    text = await handler.handle_help()
    assert "/persona_video" in text
    assert "Vera" in text and "persona_v1" in text
    assert "Sofia" in text and "persona_s2" in text


@pytest.mark.anyio
async def test_handle_help_when_no_personas():
    storage = MagicMock()
    storage.list_personas = AsyncMock(return_value=[])
    handler = PersonaVideoHandler(
        router=MagicMock(), storage=storage, history=_make_history([])
    )
    text = await handler.handle_help()
    assert "/persona_video" in text
    # no entries shown — points user at /create_persona
    assert "create_persona" in text


@pytest.mark.anyio
async def test_handle_help_tolerates_storage_failure():
    storage = MagicMock()
    storage.list_personas = AsyncMock(side_effect=RuntimeError("disk gone"))
    handler = PersonaVideoHandler(
        router=MagicMock(), storage=storage, history=_make_history([])
    )
    text = await handler.handle_help()
    assert "/persona_video" in text


# ── resolve by persona_id ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_resolve_persona_id_matches_persona_id_first():
    by_id_persona = _make_persona(name="Vera", pid="persona_af2f")
    storage = MagicMock()
    storage.get_persona = AsyncMock(return_value=by_id_persona)
    # list_personas should never be hit when get_persona returns a match
    storage.list_personas = AsyncMock(return_value=[])
    handler = PersonaVideoHandler(
        router=MagicMock(), storage=storage, history=_make_history([])
    )

    pid = await handler._resolve_persona_id("persona_af2f")
    assert pid == "persona_af2f"
    storage.get_persona.assert_awaited_once_with("persona_af2f")
    storage.list_personas.assert_not_awaited()


@pytest.mark.anyio
async def test_resolve_persona_id_falls_back_to_name_when_id_misses():
    by_name_persona = _make_persona(name="Vera", pid="persona_af2f")
    storage = MagicMock()
    storage.get_persona = AsyncMock(return_value=None)
    storage.list_personas = AsyncMock(return_value=[by_name_persona])
    handler = PersonaVideoHandler(
        router=MagicMock(), storage=storage, history=_make_history([])
    )

    pid = await handler._resolve_persona_id("Vera")
    assert pid == "persona_af2f"
    storage.get_persona.assert_awaited_once_with("Vera")
    storage.list_personas.assert_awaited_once()


# ── handle_video kwargs: input_photo_path, progress_cb ──────────────────────


@pytest.mark.anyio
async def test_handle_video_uses_input_photo_path_override(tmp_path, monkeypatch):
    """When the caller supplies a photo, history lookup is bypassed."""
    monkeypatch.chdir(tmp_path)
    persona = _make_persona()
    storage = _make_storage(persona)
    # empty history would normally cause _latest_photo_url to raise —
    # input_photo_path override must short-circuit that lookup.
    history = _make_history([])

    out_dir = tmp_path / "out"
    result = _video_result(persona.persona_id, out_dir)
    router, engine = _make_router(result)

    handler = PersonaVideoHandler(router=router, storage=storage, history=history)

    supplied_photo = tmp_path / "user_supplied.jpg"
    supplied_photo.write_bytes(b"USER-PHOTO")

    response = await handler.handle_video(
        "/persona_video Vera a portrait",
        chat_id=42,
        input_photo_path=supplied_photo,
    )

    sent = engine.generate.await_args.args[0]
    assert sent.input_image_path == supplied_photo
    # history was not consulted for a fallback photo
    history.list_recent.assert_not_called()
    assert response["output_path"] == result.output_path


@pytest.mark.anyio
async def test_handle_video_invokes_progress_cb(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    persona = _make_persona()
    storage = _make_storage(persona)
    history = _make_history([_photo_record()])

    out_dir = tmp_path / "out"
    result = _video_result(persona.persona_id, out_dir)
    router, engine = _make_router(result)

    handler = PersonaVideoHandler(router=router, storage=storage, history=history)

    stages: list[tuple[str, dict]] = []

    def cb(stage: str, payload: dict) -> None:
        stages.append((stage, payload))

    fake_client = _mock_httpx_response()
    with patch(
        "app.handlers.persona_video_handler.httpx.AsyncClient",
        return_value=fake_client,
    ):
        await handler.handle_video(
            "/persona_video Vera a portrait --fast",
            chat_id=1,
            progress_cb=cb,
        )

    stage_names = [s[0] for s in stages]
    assert stage_names == ["persona_resolved", "engine_selected"]
    assert stages[0][1]["persona_id"] == persona.persona_id
    assert stages[0][1]["persona_name"] == "Vera"
    assert stages[1][1]["engine_name"] == "replicate"
    assert stages[1][1]["mode"] == "fast"


@pytest.mark.anyio
async def test_handle_video_progress_cb_exception_is_swallowed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    persona = _make_persona()
    storage = _make_storage(persona)
    history = _make_history([_photo_record()])
    out_dir = tmp_path / "out"
    result = _video_result(persona.persona_id, out_dir)
    router, engine = _make_router(result)
    handler = PersonaVideoHandler(router=router, storage=storage, history=history)

    def bad_cb(stage, payload):
        raise RuntimeError("send failed")

    fake_client = _mock_httpx_response()
    with patch(
        "app.handlers.persona_video_handler.httpx.AsyncClient",
        return_value=fake_client,
    ):
        # must not propagate — generation should still complete
        response = await handler.handle_video(
            "/persona_video Vera a portrait",
            chat_id=1,
            progress_cb=bad_cb,
        )
    assert response["output_path"] == result.output_path


@pytest.mark.anyio
async def test_handle_video_raises_on_bare_command():
    handler = PersonaVideoHandler(
        router=MagicMock(),
        storage=MagicMock(list_personas=AsyncMock(return_value=[])),
        history=_make_history([]),
    )
    with pytest.raises(ValueError, match="handle_help"):
        await handler.handle_video("/persona_video", chat_id=1)
