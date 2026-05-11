# -*- coding: utf-8 -*-
"""Tests for the Replicate Phase A engine. All Replicate calls are mocked."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.block_m2_video.engines.engine_protocol import VideoRequest
from app.services.block_m2_video.engines.replicate_engine import (
    REPLICATE_MODELS,
    ReplicateEngine,
    ReplicateEngineError,
)


@pytest.fixture
def fake_input_image(tmp_path: Path) -> Path:
    img = tmp_path / "input.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nFAKE")
    return img


@pytest.fixture(autouse=True)
def _set_token(monkeypatch):
    monkeypatch.setenv("REPLICATE_API_TOKEN", "rpl_test_token_xyz")


def _make_request(input_image: Path, persona_id: str = "persona_test") -> VideoRequest:
    return VideoRequest(
        persona_id=persona_id,
        persona_name="Test",
        input_image_path=input_image,
        prompt="a cinematic test shot",
        seconds=5,
        seed=42,
    )


def _fake_response(content: bytes) -> httpx.Response:
    request = httpx.Request("GET", "https://example/replicate.mp4")
    return httpx.Response(
        status_code=200, content=content, request=request
    )


def _mocked_httpx_client(content: bytes) -> MagicMock:
    fake = MagicMock()
    fake.__aenter__ = AsyncMock(return_value=fake)
    fake.__aexit__ = AsyncMock(return_value=False)
    fake.get = AsyncMock(return_value=_fake_response(content))
    return fake


# ── output normalization (URL / FileOutput / list) ──────────────────────────


@pytest.mark.anyio
async def test_generate_accepts_string_url_output(tmp_path, fake_input_image, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine = ReplicateEngine()

    fake_video = b"FAKEMP4DATA"
    with patch(
        "app.services.block_m2_video.engines.replicate_engine.replicate.run",
        return_value="https://replicate.delivery/out/abc.mp4",
    ), patch(
        "app.services.block_m2_video.engines.replicate_engine.httpx.AsyncClient",
        return_value=_mocked_httpx_client(fake_video),
    ):
        result = await engine.generate(_make_request(fake_input_image))

    assert result.output_path.exists()
    assert result.output_path.read_bytes() == fake_video


@pytest.mark.anyio
async def test_generate_accepts_file_output_object(tmp_path, fake_input_image, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine = ReplicateEngine()

    file_output = MagicMock()
    file_output.url = "https://replicate.delivery/out/file_output.mp4"

    with patch(
        "app.services.block_m2_video.engines.replicate_engine.replicate.run",
        return_value=file_output,
    ), patch(
        "app.services.block_m2_video.engines.replicate_engine.httpx.AsyncClient",
        return_value=_mocked_httpx_client(b"BIN"),
    ):
        result = await engine.generate(_make_request(fake_input_image))

    assert result.extra["video_url"] == file_output.url


@pytest.mark.anyio
async def test_generate_accepts_list_output(tmp_path, fake_input_image, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine = ReplicateEngine()

    with patch(
        "app.services.block_m2_video.engines.replicate_engine.replicate.run",
        return_value=["https://replicate.delivery/out/list_url.mp4"],
    ), patch(
        "app.services.block_m2_video.engines.replicate_engine.httpx.AsyncClient",
        return_value=_mocked_httpx_client(b"X"),
    ):
        result = await engine.generate(_make_request(fake_input_image))

    assert result.extra["video_url"].endswith("list_url.mp4")


# ── cost, seed, file layout ──────────────────────────────────────────────────


@pytest.mark.anyio
async def test_cost_matches_seconds_times_rate(tmp_path, fake_input_image, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine = ReplicateEngine()
    expected_rate = next(
        m["cost_per_sec"] for m in REPLICATE_MODELS
        if m["id"] == "wan-video/wan-2.5-i2v-fast"
    )

    with patch(
        "app.services.block_m2_video.engines.replicate_engine.replicate.run",
        return_value="https://x/y.mp4",
    ), patch(
        "app.services.block_m2_video.engines.replicate_engine.httpx.AsyncClient",
        return_value=_mocked_httpx_client(b"X"),
    ):
        result = await engine.generate(_make_request(fake_input_image))

    assert result.cost_usd == pytest.approx(expected_rate * 5)


@pytest.mark.anyio
async def test_seed_is_passed_through(tmp_path, fake_input_image, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine = ReplicateEngine()

    captured: dict = {}

    def _capture(model_id, input):  # noqa: ARG001
        captured["input"] = input
        return "https://x/y.mp4"

    with patch(
        "app.services.block_m2_video.engines.replicate_engine.replicate.run",
        side_effect=_capture,
    ), patch(
        "app.services.block_m2_video.engines.replicate_engine.httpx.AsyncClient",
        return_value=_mocked_httpx_client(b"X"),
    ):
        result = await engine.generate(_make_request(fake_input_image))

    assert captured["input"]["seed"] == 42
    assert captured["input"]["prompt"] == "a cinematic test shot"
    assert captured["input"]["duration"] == 5
    assert result.seed == 42


@pytest.mark.anyio
async def test_output_path_under_state_personas_videos(
    tmp_path, fake_input_image, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    engine = ReplicateEngine()

    with patch(
        "app.services.block_m2_video.engines.replicate_engine.replicate.run",
        return_value="https://x/y.mp4",
    ), patch(
        "app.services.block_m2_video.engines.replicate_engine.httpx.AsyncClient",
        return_value=_mocked_httpx_client(b"X"),
    ):
        result = await engine.generate(
            _make_request(fake_input_image, persona_id="persona_zzz")
        )

    expected_dir = Path("state/personas/videos") / "persona_zzz" / result.generation_id
    assert result.output_path == expected_dir / "output.mp4"
    assert result.output_path.exists()


# ── errors ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_missing_input_image_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine = ReplicateEngine()
    request = VideoRequest(
        persona_id="persona_x",
        persona_name="X",
        input_image_path=tmp_path / "does-not-exist.png",
        prompt="p",
        seconds=3,
        seed=1,
    )

    with pytest.raises(ReplicateEngineError):
        await engine.generate(request)


@pytest.mark.anyio
async def test_unexpected_output_shape_raises(tmp_path, fake_input_image, monkeypatch):
    monkeypatch.chdir(tmp_path)
    engine = ReplicateEngine()

    with patch(
        "app.services.block_m2_video.engines.replicate_engine.replicate.run",
        return_value=42,  # neither str nor FileOutput nor list
    ):
        with pytest.raises(ReplicateEngineError):
            await engine.generate(_make_request(fake_input_image))


def test_missing_api_token_raises(monkeypatch):
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    monkeypatch.delenv("REPLICATE_API_KEY", raising=False)
    with pytest.raises(ReplicateEngineError):
        ReplicateEngine()


@pytest.mark.anyio
async def test_is_available_true_when_token_set():
    engine = ReplicateEngine()
    assert await engine.is_available() is True
