# -*- coding: utf-8 -*-
"""Tests for M.2.2 — Fun Mode: SeedCollector, MePersonaManager, VideoFaceSwapPipeline,
and the handle_me_seed / handle_me_done / handle_photo_message / handle_me_swap_video handlers.
"""
from __future__ import annotations

import json
import threading
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.block_m22_fun.me_persona import MePersonaData, MePersonaManager
from app.services.block_m22_fun.seed_collector import SeedCollectionSession, SeedCollector
from app.services.block_m22_fun.video_face_swap import VideoFaceSwapPipeline


# ── SeedCollector tests ───────────────────────────────────────────────────────

def test_seed_collector_start_session():
    sc = SeedCollector()
    session = sc.start_session(42, max_photos=5)
    assert isinstance(session, SeedCollectionSession)
    assert session.chat_id == 42
    assert session.max_photos == 5
    assert session.count == 0


def test_seed_collector_add_photo():
    sc = SeedCollector()
    sc.start_session(42)
    session = sc.add_photo(42, "https://example.com/face.jpg")
    assert session is not None
    assert session.count == 1
    assert session.photo_urls[0] == "https://example.com/face.jpg"


def test_seed_collector_add_photo_no_session():
    sc = SeedCollector()
    result = sc.add_photo(99, "https://example.com/face.jpg")
    assert result is None


def test_seed_collector_end_session():
    sc = SeedCollector()
    sc.start_session(42)
    sc.add_photo(42, "https://example.com/face.jpg")
    session = sc.end_session(42)
    assert session is not None
    assert session.count == 1
    assert not sc.has_session(42)


def test_seed_collector_end_session_no_session():
    sc = SeedCollector()
    result = sc.end_session(99)
    assert result is None


def test_seed_collector_has_session():
    sc = SeedCollector()
    assert not sc.has_session(42)
    sc.start_session(42)
    assert sc.has_session(42)
    sc.end_session(42)
    assert not sc.has_session(42)


def test_seed_collector_is_complete():
    sc = SeedCollector()
    sc.start_session(42, max_photos=2)
    sc.add_photo(42, "https://example.com/1.jpg")
    assert not sc.is_complete(42)
    sc.add_photo(42, "https://example.com/2.jpg")
    assert sc.is_complete(42)


def test_seed_collector_multiple_sessions():
    sc = SeedCollector()
    sc.start_session(1, max_photos=3)
    sc.start_session(2, max_photos=5)
    sc.add_photo(1, "https://example.com/1.jpg")
    assert sc.get_session(1).count == 1
    assert sc.get_session(2).count == 0


def test_seed_collector_start_overwrites_existing():
    sc = SeedCollector()
    sc.start_session(42)
    sc.add_photo(42, "https://example.com/old.jpg")
    sc.start_session(42, max_photos=5)
    assert sc.get_session(42).count == 0


# ── MePersonaManager tests ────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_me_persona_manager_get_nonexistent(tmp_path):
    manager = MePersonaManager(storage_dir=tmp_path)
    result = await manager.get(42)
    assert result is None


@pytest.mark.anyio
async def test_me_persona_manager_save_and_get(tmp_path):
    manager = MePersonaManager(storage_dir=tmp_path)
    data = MePersonaData(
        chat_id=42,
        persona_id="me_persona_42",
        trigger_word="me42",
        lora_weights_url="",
        seed_photos=["https://example.com/face.jpg"],
        created_at=datetime(2026, 5, 6, 12, 0, 0),
    )
    await manager.save(data)
    loaded = await manager.get(42)
    assert loaded is not None
    assert loaded.chat_id == 42
    assert loaded.trigger_word == "me42"
    assert loaded.seed_photos == ["https://example.com/face.jpg"]


@pytest.mark.anyio
async def test_me_persona_manager_delete(tmp_path):
    manager = MePersonaManager(storage_dir=tmp_path)
    data = MePersonaData(
        chat_id=42,
        persona_id="me_persona_42",
        trigger_word="me42",
        lora_weights_url="",
        seed_photos=[],
        created_at=datetime(2026, 5, 6, 12, 0, 0),
    )
    await manager.save(data)
    assert manager.exists(42)
    await manager.delete(42)
    assert not manager.exists(42)
    assert await manager.get(42) is None


@pytest.mark.anyio
async def test_me_persona_is_trained_flag(tmp_path):
    manager = MePersonaManager(storage_dir=tmp_path)
    data = MePersonaData(
        chat_id=42,
        persona_id="me_persona_42",
        trigger_word="me42",
        lora_weights_url="",
        seed_photos=[],
        created_at=datetime.utcnow(),
    )
    assert not data.is_trained
    data.lora_weights_url = "https://replicate.delivery/weights/abc.tar"
    assert data.is_trained


# ── VideoFaceSwapPipeline tests ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_video_face_swap_calls_replicate():
    mock_client = MagicMock()
    mock_client._run_prediction = AsyncMock(
        return_value="https://replicate.delivery/out/swapped.mp4"
    )
    pipeline = VideoFaceSwapPipeline(mock_client)
    me_persona = MePersonaData(
        chat_id=42,
        persona_id="me_persona_42",
        trigger_word="me42",
        lora_weights_url="",
        seed_photos=["https://example.com/face.jpg"],
        created_at=datetime.utcnow(),
    )
    result = await pipeline.swap_video(42, "https://example.com/video.mp4", me_persona)
    assert result["output_url"] == "https://replicate.delivery/out/swapped.mp4"
    assert result["cost_usd"] == pytest.approx(0.05)
    mock_client._run_prediction.assert_awaited_once()


@pytest.mark.anyio
async def test_video_face_swap_security_check():
    mock_client = MagicMock()
    pipeline = VideoFaceSwapPipeline(mock_client)
    me_persona = MePersonaData(
        chat_id=99,  # different chat_id
        persona_id="me_persona_99",
        trigger_word="me99",
        lora_weights_url="",
        seed_photos=["https://example.com/face.jpg"],
        created_at=datetime.utcnow(),
    )
    with pytest.raises(PermissionError):
        await pipeline.swap_video(42, "https://example.com/video.mp4", me_persona)


@pytest.mark.anyio
async def test_video_face_swap_no_seed_photos():
    mock_client = MagicMock()
    pipeline = VideoFaceSwapPipeline(mock_client)
    me_persona = MePersonaData(
        chat_id=42,
        persona_id="me_persona_42",
        trigger_word="me42",
        lora_weights_url="",
        seed_photos=[],
        created_at=datetime.utcnow(),
    )
    with pytest.raises(ValueError, match="no seed photos"):
        await pipeline.swap_video(42, "https://example.com/video.mp4", me_persona)


# ── Handler tests ─────────────────────────────────────────────────────────────

def _init_handler_m22():
    import app.handlers.persona_handler as ph
    send = MagicMock()
    send_photo = MagicMock()
    send_video = MagicMock()
    ph.init_bot(send, send_photo, send_video)
    return send, send_photo, send_video


def _fake_start_sync_m22(completed: threading.Event):
    def _start(self):
        self._target()
        completed.set()
    return _start


def test_handle_me_seed_starts_session():
    import app.handlers.persona_handler as ph
    send, _, _ = _init_handler_m22()
    mock_collector = MagicMock()
    mock_session = MagicMock()
    mock_session.max_photos = 10
    mock_collector.start_session.return_value = mock_session
    ph._seed_collector = mock_collector

    ph.handle_me_seed(42)

    mock_collector.start_session.assert_called_once_with(42, max_photos=10)
    send.assert_called_once()
    assert "/me_done" in send.call_args[0][1]


def test_handle_me_done_no_session():
    import app.handlers.persona_handler as ph
    send, _, _ = _init_handler_m22()
    mock_collector = MagicMock()
    mock_collector.get_session.return_value = None
    ph._seed_collector = mock_collector

    ph.handle_me_done(42)

    send.assert_called_once()
    assert "/me_seed" in send.call_args[0][1]


def test_handle_me_done_insufficient_photos():
    import app.handlers.persona_handler as ph
    send, _, _ = _init_handler_m22()
    mock_session = MagicMock()
    mock_session.count = 1
    mock_collector = MagicMock()
    mock_collector.get_session.return_value = mock_session
    ph._seed_collector = mock_collector

    ph.handle_me_done(42)

    send.assert_called_once()
    assert "Недостаточно" in send.call_args[0][1]


def test_handle_photo_message_routes_to_collector():
    import app.handlers.persona_handler as ph
    send, _, _ = _init_handler_m22()
    mock_session = MagicMock()
    mock_session.is_complete = False
    mock_session.count = 1
    mock_session.max_photos = 10
    mock_collector = MagicMock()
    mock_collector.has_session.return_value = True
    mock_collector.add_photo.return_value = mock_session
    ph._seed_collector = mock_collector

    result = ph.handle_photo_message(42, "https://example.com/face.jpg")

    assert result is True
    mock_collector.add_photo.assert_called_once_with(42, "https://example.com/face.jpg")
    send.assert_called_once()


def test_handle_photo_message_returns_false_no_session():
    import app.handlers.persona_handler as ph
    send, _, _ = _init_handler_m22()
    mock_collector = MagicMock()
    mock_collector.has_session.return_value = False
    ph._seed_collector = mock_collector

    result = ph.handle_photo_message(42, "https://example.com/face.jpg")

    assert result is False
    send.assert_not_called()
