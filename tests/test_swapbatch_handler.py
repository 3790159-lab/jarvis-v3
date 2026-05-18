# -*- coding: utf-8 -*-
"""Tests for :class:`FaceSwapHandler`. The orchestrator is real (in-memory),
the validator is mocked."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.handlers.face_swap_handler import FaceSwapHandler, HandlerReply
from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator,
    STATE_EXPECTING_SOURCE,
    STATE_EXPECTING_TARGETS,
    STATE_TARGETS_RECEIVED,
)


def _make_photo(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    return p


def _make_validator(face_count: int = 1) -> MagicMock:
    v = MagicMock()
    v.count_faces.return_value = face_count
    return v


def _make_handler(tmp_path: Path, face_count: int = 1) -> tuple[FaceSwapHandler, BatchOrchestrator]:
    orch = BatchOrchestrator(
        state_root=tmp_path / "batches",
        validator=_make_validator(face_count=face_count),
    )
    return FaceSwapHandler(orchestrator=orch), orch


# ── help / status ────────────────────────────────────────────────────────────


def test_help_lists_all_commands(tmp_path):
    handler, _ = _make_handler(tmp_path)
    r = handler.handle_help()
    for cmd in (
        "/swapbatch_source", "/swapbatch_batch", "/swapbatch_go",
        "/swapbatch_animate_yes", "/swapbatch_animate_no",
        "/swapbatch_cancel", "/swapbatch_status",
    ):
        assert cmd in r.text


def test_status_with_no_session(tmp_path):
    handler, _ = _make_handler(tmp_path)
    r = handler.handle_status(42)
    assert "Нет активного" in r.text


# ── happy flow ───────────────────────────────────────────────────────────────


def test_full_setup_flow_through_targets(tmp_path):
    handler, orch = _make_handler(tmp_path)
    src = _make_photo(tmp_path, "src.jpg")
    t1 = _make_photo(tmp_path, "t1.jpg")
    t2 = _make_photo(tmp_path, "t2.jpg")

    assert "лицом" in handler.handle_source_intent(42).text
    r = handler.consume_source(42, src)
    assert r.consumed is True
    assert "Source принят" in r.text

    assert handler.handle_batch_intent(42).consumed is True
    r = handler.consume_targets_album(42, [t1, t2])
    assert "📊" in r.text  # cost report header
    assert orch.status(42) == STATE_TARGETS_RECEIVED


def test_consume_source_returns_not_consumed_when_no_session(tmp_path):
    handler, _ = _make_handler(tmp_path)
    r = handler.consume_source(42, _make_photo(tmp_path, "x.jpg"))
    assert r.consumed is False
    assert r.text is None


def test_consume_targets_returns_not_consumed_when_state_wrong(tmp_path):
    handler, _ = _make_handler(tmp_path)
    handler.handle_source_intent(42)
    # We're in EXPECTING_SOURCE — album consume should NOT fire.
    r = handler.consume_targets_album(42, [_make_photo(tmp_path, "t.jpg")])
    assert r.consumed is False


# ── error flows ──────────────────────────────────────────────────────────────


def test_consume_source_no_face_keeps_state(tmp_path):
    handler, orch = _make_handler(tmp_path, face_count=0)
    handler.handle_source_intent(42)
    r = handler.consume_source(42, _make_photo(tmp_path, "src.jpg"))
    assert r.consumed is True
    assert "⚠️" in r.text
    assert orch.status(42) == STATE_EXPECTING_SOURCE  # still waiting


def test_cancel_when_no_session(tmp_path):
    handler, _ = _make_handler(tmp_path)
    r = handler.handle_cancel(42)
    assert "Нет активного" in r.text


def test_cancel_in_setup_clears_session(tmp_path):
    handler, orch = _make_handler(tmp_path)
    handler.handle_source_intent(42)
    r = handler.handle_cancel(42)
    assert "отмена" in r.text.lower()
    assert orch.get(42) is None


# ── animation phases ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_swap_phase_delivers_photos_when_done(tmp_path):
    handler, orch = _make_handler(tmp_path)
    src = _make_photo(tmp_path, "src.jpg")
    t1 = _make_photo(tmp_path, "t1.jpg")
    handler.handle_source_intent(42)
    handler.consume_source(42, src)
    handler.handle_batch_intent(42)
    handler.consume_targets_album(42, [t1])

    swapped = _make_photo(tmp_path, "sw.png")

    async def swap_fn(source, targets, cc):
        return [swapped]

    r = await handler.run_swap_phase(42, swap_fn)
    assert "Swap завершён" in r.text
    assert r.photos == [swapped]


def test_handle_animate_no_terminates(tmp_path):
    handler, orch = _make_handler(tmp_path)
    src = _make_photo(tmp_path, "src.jpg")
    t1 = _make_photo(tmp_path, "t1.jpg")
    handler.handle_source_intent(42)
    handler.consume_source(42, src)
    handler.handle_batch_intent(42)
    handler.consume_targets_album(42, [t1])
    sess = orch.get(42)
    sess.status = "SWAP_DONE"
    sess.targets[0].swap_result_path = str(_make_photo(tmp_path, "sw.png"))

    r = handler.handle_animate_no(42)
    assert "Готово" in r.text
    assert orch.get(42) is None  # pruned


@pytest.mark.anyio
async def test_run_animate_phase_collects_videos(tmp_path):
    handler, orch = _make_handler(tmp_path)
    src = _make_photo(tmp_path, "src.jpg")
    t1 = _make_photo(tmp_path, "t1.jpg")
    handler.handle_source_intent(42)
    handler.consume_source(42, src)
    handler.handle_batch_intent(42)
    handler.consume_targets_album(42, [t1])
    sess = orch.get(42)
    sess.status = "SWAP_DONE"
    swapped = _make_photo(tmp_path, "sw.png")
    sess.targets[0].swap_result_path = str(swapped)

    video = _make_photo(tmp_path, "v.mp4")

    async def animate_fn(swapped_path, idx, cc):
        return video

    r = await handler.run_animate_phase(42, animate_fn)
    assert "Animate завершён" in r.text
    assert r.videos == [video]
    assert orch.get(42) is None  # pruned after success
