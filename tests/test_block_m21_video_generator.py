# -*- coding: utf-8 -*-
"""Tests for M.2.1 — VideoGenerator and handle_persona_video/redo/engine handlers."""
from __future__ import annotations

import threading
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.block_m_common.cost_tracker import DailyLimitExceeded
from app.services.block_m2_video.generation_history import GenerationRecord
from app.services.block_m2_video.video_generator import VideoGenerator


# ── shared fixtures ───────────────────────────────────────────────────────────

def _default_video_result() -> dict:
    return {
        "video_url": "https://replicate.delivery/out/vid.mp4",
        "photo_url": "https://replicate.delivery/out/photo.jpg",
        "total_cost_usd": 0.12,
        "record_id": "rec_test001",
        "video_id": "video_test001",
        "prompt": "walking on the beach",
        "engine": "kling_v21",
    }


def _make_video_gen(
    photo_url: str = "https://replicate.delivery/out/photo.jpg",
    video_url: str = "https://replicate.delivery/out/vid.mp4",
    photo_cost: float = 0.02,
    video_cost: float = 0.10,
):
    photo_gen = MagicMock()
    video_extras = MagicMock()
    video_storage = MagicMock()
    history = MagicMock()
    tracker = MagicMock()

    photo_gen.generate_photo = AsyncMock(
        return_value={"image_url": photo_url, "cost_usd": photo_cost, "full_prompt": "sks_test prompt"}
    )
    video_extras.generate_video_dispatch = AsyncMock(
        return_value={"video_url": video_url, "cost_usd": video_cost, "duration_sec": 5}
    )
    video_storage.save = AsyncMock(return_value=None)
    history.record = AsyncMock(return_value="rec_test001")
    history.get = AsyncMock(return_value=None)
    tracker.log_expense = AsyncMock(return_value=None)

    gen = VideoGenerator(photo_gen, video_extras, video_storage, history, tracker)
    return gen, photo_gen, video_extras, video_storage, history, tracker


def _make_original_record(
    record_id: str = "rec_orig",
    persona_id: str = "persona_test",
    prompt: str = "walking on the beach",
    engine: str = "kling_v21",
) -> GenerationRecord:
    return GenerationRecord(
        record_id=record_id,
        persona_id=persona_id,
        kind="video",
        prompt=prompt,
        input_url="https://example.com/photo.jpg",
        output_url="https://example.com/old_vid.mp4",
        engine=engine,
        cost_usd=0.12,
        created_at=datetime(2026, 5, 6, 12, 0, 0),
        parent_record_id=None,
    )


# ── VideoGenerator tests ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_generate_video_calls_photo_then_animate():
    gen, photo_gen, video_extras, _, _, _ = _make_video_gen()
    await gen.generate_video("persona_test", "walking on the beach")
    photo_gen.generate_photo.assert_awaited_once_with("persona_test", "walking on the beach")
    video_extras.generate_video_dispatch.assert_awaited_once()


@pytest.mark.anyio
async def test_generate_video_uses_default_kling_engine():
    gen, _, video_extras, _, _, _ = _make_video_gen()
    result = await gen.generate_video("persona_test", "running")
    call_kwargs = video_extras.generate_video_dispatch.call_args.kwargs
    assert call_kwargs["engine"] == "kling_v21"
    assert result["engine"] == "kling_v21"


@pytest.mark.anyio
async def test_generate_video_respects_engine_param():
    gen, _, video_extras, _, _, _ = _make_video_gen()
    result = await gen.generate_video("persona_test", "swimming", engine="wan22_fast")
    call_kwargs = video_extras.generate_video_dispatch.call_args.kwargs
    assert call_kwargs["engine"] == "wan22_fast"
    assert result["engine"] == "wan22_fast"


@pytest.mark.anyio
async def test_generate_video_saves_to_video_storage():
    gen, _, _, video_storage, _, _ = _make_video_gen()
    await gen.generate_video("persona_test", "dancing")
    video_storage.save.assert_awaited_once()
    saved_video = video_storage.save.call_args[0][0]
    assert saved_video.persona_id == "persona_test"
    assert saved_video.prompt == "dancing"


@pytest.mark.anyio
async def test_generate_video_records_to_history():
    gen, _, _, _, history, _ = _make_video_gen()
    await gen.generate_video("persona_test", "posing")
    history.record.assert_awaited_once()
    kwargs = history.record.call_args.kwargs
    assert kwargs["kind"] == "video"
    assert kwargs["persona_id"] == "persona_test"
    assert kwargs["prompt"] == "posing"


@pytest.mark.anyio
async def test_generate_video_total_cost_includes_both_steps():
    gen, _, _, _, _, _ = _make_video_gen(photo_cost=0.02, video_cost=0.10)
    result = await gen.generate_video("persona_test", "jumping")
    assert result["total_cost_usd"] == pytest.approx(0.12)


@pytest.mark.anyio
async def test_generate_video_progress_callback_called():
    gen, _, _, _, _, _ = _make_video_gen()
    cb = MagicMock()
    await gen.generate_video("persona_test", "smiling", progress_cb=cb)
    call_args = [c[0][0] for c in cb.call_args_list]
    assert any("1/2" in s for s in call_args), f"Expected 1/2 progress, got {call_args}"
    assert any(s.startswith("photo_ready:") for s in call_args), f"Expected photo_ready, got {call_args}"
    assert any("2/2" in s for s in call_args), f"Expected 2/2 progress, got {call_args}"


@pytest.mark.anyio
async def test_generate_video_propagates_daily_limit_error():
    gen, photo_gen, _, _, _, _ = _make_video_gen()
    photo_gen.generate_photo = AsyncMock(
        side_effect=DailyLimitExceeded("Daily limit reached")
    )
    with pytest.raises(DailyLimitExceeded):
        await gen.generate_video("persona_test", "smiling")


@pytest.mark.anyio
async def test_redo_video_preserves_original():
    gen, _, _, _, history, _ = _make_video_gen()
    original = _make_original_record()
    history.get = AsyncMock(return_value=original)

    await gen.redo_video("rec_orig")

    history.get.assert_awaited_once_with("rec_orig")
    record_kwargs = history.record.call_args.kwargs
    assert record_kwargs["parent_record_id"] == "rec_orig"


@pytest.mark.anyio
async def test_redo_video_with_new_prompt_uses_new_prompt():
    gen, photo_gen, _, _, history, _ = _make_video_gen()
    original = _make_original_record(prompt="old prompt")
    history.get = AsyncMock(return_value=original)

    await gen.redo_video("rec_orig", new_prompt="new prompt")

    photo_gen.generate_photo.assert_awaited_once_with("persona_test", "new prompt")


# ── Handler tests ─────────────────────────────────────────────────────────────

def _init_handler():
    import app.handlers.persona_handler as ph
    send = MagicMock()
    send_photo = MagicMock()
    send_video = MagicMock()
    ph.init_bot(send, send_photo, send_video)
    return send, send_photo, send_video


def _fake_start_sync(completed: threading.Event):
    def _start(self):
        self._target()
        completed.set()
    return _start


def _vg_patches(mock_result: dict | None = None, side_effect=None):
    """Return patch list + mock VideoGenerator instance for handler tests."""
    mock_vg_instance = MagicMock()
    if side_effect:
        mock_vg_instance.generate_video = AsyncMock(side_effect=side_effect)
        mock_vg_instance.redo_video = AsyncMock(side_effect=side_effect)
    else:
        result = mock_result or _default_video_result()
        mock_vg_instance.generate_video = AsyncMock(return_value=result)
        mock_vg_instance.redo_video = AsyncMock(return_value=result)
    mock_vg_cls = MagicMock(return_value=mock_vg_instance)

    patches = [
        # money-consolidation hole a: /persona_redo now pre-gates on check_limit;
        # open the gate so the redo flow runs.
        patch("app.handlers.persona_handler.check_limit",
              MagicMock(return_value=(True, 1.0))),
        patch("app.handlers.persona_handler.ReplicateVideoClient", MagicMock()),
        patch("app.handlers.persona_handler.PersonaStorage", MagicMock()),
        patch("app.handlers.persona_handler.CostTracker", MagicMock()),
        patch("app.services.block_m2_video.video_generator.VideoGenerator", mock_vg_cls),
        patch("app.services.block_m2_video.video_storage.VideoStorage", MagicMock()),
        patch("app.services.block_m2_video.generation_history.GenerationHistory", MagicMock()),
        patch("app.services.block_m1_persona.photo_generator.PhotoGenerator", MagicMock()),
        patch("app.services.block_m2_video.video_client_extras.VideoClientExtras", MagicMock()),
    ]
    return patches, mock_vg_instance


def test_handle_persona_video_parses_args():
    from app.handlers.persona_handler import handle_persona_video

    send, send_photo, send_video = _init_handler()
    completed = threading.Event()
    patches, mock_vg = _vg_patches()

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with patch.object(threading.Thread, "start", _fake_start_sync(completed)):
            handle_persona_video(42, "persona_test walking on the beach")
            completed.wait(timeout=5)

    mock_vg.generate_video.assert_awaited_once()
    call_args = mock_vg.generate_video.call_args
    assert call_args.args[0] == "persona_test"
    assert call_args.args[1] == "walking on the beach"
    send_video.assert_called_once()


def test_handle_persona_video_rejects_empty_args():
    from app.handlers.persona_handler import handle_persona_video

    send, _, _ = _init_handler()
    handle_persona_video(42, "")
    send.assert_called_once()
    assert "persona_id" in send.call_args[0][1].lower() or "укажите" in send.call_args[0][1].lower()


def test_handle_persona_redo_uses_original_prompt_if_none_provided():
    from app.handlers.persona_handler import handle_persona_redo

    send, _, send_video = _init_handler()
    completed = threading.Event()
    patches, mock_vg = _vg_patches()

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with patch.object(threading.Thread, "start", _fake_start_sync(completed)):
            handle_persona_redo(42, "rec_test001")
            completed.wait(timeout=5)

    mock_vg.redo_video.assert_awaited_once()
    call_kwargs = mock_vg.redo_video.call_args.kwargs
    assert call_kwargs.get("new_prompt") is None
    send_video.assert_called_once()


def test_handle_persona_engine_sets_preference():
    from app.handlers.persona_handler import handle_persona_engine

    send, _, _ = _init_handler()
    with patch("app.handlers.persona_handler._set_engine_pref") as mock_set:
        handle_persona_engine(42, "kling")
        mock_set.assert_called_once_with(42, "kling_v21")

    send.assert_called_once()
    assert "kling_v21" in send.call_args[0][1]


def test_handle_persona_engine_shows_current_if_no_args():
    from app.handlers.persona_handler import handle_persona_engine

    send, _, _ = _init_handler()
    with patch("app.handlers.persona_handler._get_engine_pref", return_value="wan22_fast"):
        handle_persona_engine(42, "")

    send.assert_called_once()
    assert "wan22_fast" in send.call_args[0][1]
