# -*- coding: utf-8 -*-
"""Tests for ``app.services.block_m2_face_swap.batch_orchestrator``."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator,
    BatchSession,
    OrchestratorError,
    STATE_ANIMATING,
    STATE_DONE,
    STATE_EXPECTING_SOURCE,
    STATE_EXPECTING_TARGETS,
    STATE_FAILED_RESUMED,
    STATE_IDLE,
    STATE_SOURCE_RECEIVED,
    STATE_SWAPPING,
    STATE_SWAP_DONE,
    STATE_TARGETS_RECEIVED,
    TargetItem,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_photo(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    return p


def _make_validator(face_count: int = 1) -> MagicMock:
    v = MagicMock()
    v.count_faces.return_value = face_count
    return v


def _make_orch(tmp_path: Path, validator=None) -> BatchOrchestrator:
    return BatchOrchestrator(
        state_root=tmp_path / "batches",
        validator=validator or _make_validator(1),
    )


# ── setup transitions ───────────────────────────────────────────────────────


def test_initial_status_is_idle(tmp_path):
    orch = _make_orch(tmp_path)
    assert orch.status(42) == STATE_IDLE
    assert orch.get(42) is None


def test_begin_source_creates_session(tmp_path):
    orch = _make_orch(tmp_path)
    sess = orch.begin_source(42)
    assert sess.status == STATE_EXPECTING_SOURCE
    assert orch.is_waiting_for_source(42)
    assert (tmp_path / "batches" / "42" / "session.json").exists()


def test_submit_source_with_face_advances_to_source_received(tmp_path):
    img = _make_photo(tmp_path, "src.jpg")
    orch = _make_orch(tmp_path, validator=_make_validator(face_count=1))
    orch.begin_source(42)
    sess = orch.submit_source(42, img)
    assert sess.status == STATE_SOURCE_RECEIVED
    assert sess.source_face_count == 1
    assert sess.source_path is not None
    assert Path(sess.source_path).exists()


def test_submit_source_with_no_face_stays_in_expecting_source(tmp_path):
    img = _make_photo(tmp_path, "src.jpg")
    orch = _make_orch(tmp_path, validator=_make_validator(face_count=0))
    orch.begin_source(42)
    with pytest.raises(OrchestratorError, match="не найдено лицо"):
        orch.submit_source(42, img)
    assert orch.status(42) == STATE_EXPECTING_SOURCE


def test_submit_source_without_begin_raises(tmp_path):
    img = _make_photo(tmp_path, "src.jpg")
    orch = _make_orch(tmp_path)
    with pytest.raises(OrchestratorError, match="no batch session"):
        orch.submit_source(42, img)


def test_begin_targets_requires_source_received(tmp_path):
    orch = _make_orch(tmp_path)
    orch.begin_source(42)
    with pytest.raises(OrchestratorError, match="expected one of"):
        orch.begin_targets(42)


def test_submit_targets_computes_cost_estimate(tmp_path):
    src = _make_photo(tmp_path, "src.jpg")
    t1 = _make_photo(tmp_path, "t1.jpg")
    t2 = _make_photo(tmp_path, "t2.jpg")
    t3 = _make_photo(tmp_path, "t3.jpg")

    validator = MagicMock()
    # Source: 1 face. Targets: face / face / no-face.
    validator.count_faces.side_effect = [1, 1, 1, 0]
    orch = _make_orch(tmp_path, validator=validator)

    orch.begin_source(42)
    orch.submit_source(42, src)
    orch.begin_targets(42)
    sess, est = orch.submit_targets(42, [t1, t2, t3])

    assert sess.status == STATE_TARGETS_RECEIVED
    assert est.valid_count == 2
    assert est.skipped_count == 1
    assert est.total_usd > 0
    assert sess.cost_estimate is not None


def test_submit_targets_empty_list_raises(tmp_path):
    src = _make_photo(tmp_path, "src.jpg")
    orch = _make_orch(tmp_path)
    orch.begin_source(42)
    orch.submit_source(42, src)
    orch.begin_targets(42)
    with pytest.raises(OrchestratorError):
        orch.submit_targets(42, [])


# ── confirm_swap / confirm_animate ──────────────────────────────────────────


@pytest.mark.anyio
async def test_confirm_swap_happy_path(tmp_path):
    src = _make_photo(tmp_path, "src.jpg")
    t1 = _make_photo(tmp_path, "t1.jpg")
    t2 = _make_photo(tmp_path, "t2.jpg")
    orch = _make_orch(tmp_path)
    orch.begin_source(42); orch.submit_source(42, src)
    orch.begin_targets(42); orch.submit_targets(42, [t1, t2])

    swapped1 = tmp_path / "swap_0.png"; swapped1.write_bytes(b"x")
    swapped2 = tmp_path / "swap_1.png"; swapped2.write_bytes(b"x")

    async def fake_swap(source, targets, cancel_check):
        assert len(targets) == 2
        assert cancel_check() is False
        return [swapped1, swapped2]

    results = await orch.confirm_swap(42, swap_fn=fake_swap)
    assert results == [swapped1, swapped2]
    sess = orch.get(42)
    assert sess.status == STATE_SWAP_DONE
    assert sess.targets[0].swap_result_path == str(swapped1)
    assert sess.targets[1].swap_result_path == str(swapped2)


@pytest.mark.anyio
async def test_confirm_swap_partial_failure_records_errors(tmp_path):
    src = _make_photo(tmp_path, "src.jpg")
    t1 = _make_photo(tmp_path, "t1.jpg")
    t2 = _make_photo(tmp_path, "t2.jpg")
    orch = _make_orch(tmp_path)
    orch.begin_source(42); orch.submit_source(42, src)
    orch.begin_targets(42); orch.submit_targets(42, [t1, t2])

    swapped1 = tmp_path / "swap_0.png"; swapped1.write_bytes(b"x")

    async def fake_swap(source, targets, cancel_check):
        return [swapped1, None]  # second swap failed

    await orch.confirm_swap(42, swap_fn=fake_swap)
    sess = orch.get(42)
    assert sess.targets[0].swap_result_path == str(swapped1)
    assert sess.targets[1].swap_result_path is None
    assert "swap failed" in (sess.targets[1].error or "")


@pytest.mark.anyio
async def test_confirm_swap_engine_exception_returns_to_targets_received(tmp_path):
    src = _make_photo(tmp_path, "src.jpg")
    t1 = _make_photo(tmp_path, "t1.jpg")
    orch = _make_orch(tmp_path)
    orch.begin_source(42); orch.submit_source(42, src)
    orch.begin_targets(42); orch.submit_targets(42, [t1])

    async def boom(*a, **kw):
        raise RuntimeError("pod went away")

    with pytest.raises(RuntimeError, match="pod went away"):
        await orch.confirm_swap(42, swap_fn=boom)

    sess = orch.get(42)
    assert sess.status == STATE_TARGETS_RECEIVED  # can retry
    assert "pod went away" in (sess.last_error or "")


@pytest.mark.anyio
async def test_confirm_animate_skips_failed_animation(tmp_path):
    src = _make_photo(tmp_path, "src.jpg")
    t1 = _make_photo(tmp_path, "t1.jpg")
    t2 = _make_photo(tmp_path, "t2.jpg")
    orch = _make_orch(tmp_path)
    orch.begin_source(42); orch.submit_source(42, src)
    orch.begin_targets(42); orch.submit_targets(42, [t1, t2])

    swapped1 = tmp_path / "swap_0.png"; swapped1.write_bytes(b"x")
    swapped2 = tmp_path / "swap_1.png"; swapped2.write_bytes(b"x")

    async def good_swap(source, targets, cc):
        return [swapped1, swapped2]
    await orch.confirm_swap(42, swap_fn=good_swap)

    video_ok = tmp_path / "v0.mp4"; video_ok.write_bytes(b"x")

    async def animate_fn(swapped, idx, cc):
        if idx == 1:
            raise RuntimeError("animate failed")
        return video_ok

    results = await orch.confirm_animate(42, animate_fn=animate_fn)
    assert results == [video_ok, None]
    sess = orch.get(42)
    assert sess.status == STATE_DONE
    assert sess.targets[0].animate_result_path == str(video_ok)
    assert sess.targets[1].animate_result_path is None
    assert "animate failed" in (sess.targets[1].error or "")


def test_skip_animate_terminates_at_done(tmp_path):
    src = _make_photo(tmp_path, "src.jpg")
    t1 = _make_photo(tmp_path, "t1.jpg")
    orch = _make_orch(tmp_path)
    orch.begin_source(42); orch.submit_source(42, src)
    orch.begin_targets(42); orch.submit_targets(42, [t1])
    # Force into SWAP_DONE.
    sess = orch.get(42)
    sess.status = STATE_SWAP_DONE
    sess.targets[0].swap_result_path = str(_make_photo(tmp_path, "sw.png"))
    out = orch.skip_animate(42)
    assert out.status == STATE_DONE


# ── cancellation + pruning ──────────────────────────────────────────────────


def test_cancel_in_setup_deletes_session(tmp_path):
    orch = _make_orch(tmp_path)
    orch.begin_source(42)
    assert orch.cancel(42) is True
    assert orch.get(42) is None


def test_cancel_in_swapping_sets_cancel_flag(tmp_path):
    orch = _make_orch(tmp_path)
    orch.begin_source(42)
    sess = orch.get(42)
    sess.status = STATE_SWAPPING
    assert orch.cancel(42) is True
    cur = orch.get(42)
    assert cur is not None
    assert cur.cancel_requested is True


def test_cancel_when_no_session_returns_false(tmp_path):
    orch = _make_orch(tmp_path)
    assert orch.cancel(42) is False


def test_begin_source_while_swapping_raises(tmp_path):
    orch = _make_orch(tmp_path)
    orch.begin_source(42)
    sess = orch.get(42)
    sess.status = STATE_SWAPPING
    with pytest.raises(OrchestratorError, match="in-flight batch"):
        orch.begin_source(42)


# ── persistence + reload ────────────────────────────────────────────────────


def test_persistence_round_trip(tmp_path):
    src = _make_photo(tmp_path, "src.jpg")
    orch = _make_orch(tmp_path)
    orch.begin_source(42); orch.submit_source(42, src)
    p = orch.session_file(42)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["chat_id"] == 42
    assert data["status"] == STATE_SOURCE_RECEIVED
    assert data["source_face_count"] == 1


def test_reload_from_disk_marks_inflight_as_failed_resumed(tmp_path):
    # Manually seed a session.json in SWAPPING state.
    sess_dir = tmp_path / "batches" / "42"
    sess_dir.mkdir(parents=True)
    sess_file = sess_dir / "session.json"
    sess_file.write_text(json.dumps({
        "chat_id": 42,
        "status": STATE_SWAPPING,
        "source_path": None,
        "source_face_count": 0,
        "targets": [],
        "cost_estimate": None,
        "cancel_requested": False,
        "created_at_unix": 0,
        "updated_at_unix": 0,
        "last_error": None,
    }), encoding="utf-8")

    orch = BatchOrchestrator(state_root=tmp_path / "batches")
    restored = orch.reload_from_disk()
    assert restored == [42]
    assert orch.status(42) == STATE_FAILED_RESUMED
    assert "manual restart" in (orch.get(42).last_error or "")


# ── dataclass plumbing ──────────────────────────────────────────────────────


def test_batchsession_to_dict_and_from_dict_roundtrip():
    sess = BatchSession(
        chat_id=7,
        status=STATE_TARGETS_RECEIVED,
        source_path="/tmp/src.jpg",
        source_face_count=1,
        targets=[TargetItem(path="/tmp/t.jpg", face_count=1, valid=True)],
        cost_estimate={"total_usd": 1.23},
    )
    blob = sess.to_dict()
    revived = BatchSession.from_dict(blob)
    assert revived.chat_id == sess.chat_id
    assert revived.status == sess.status
    assert len(revived.targets) == 1
    assert revived.targets[0].valid is True
