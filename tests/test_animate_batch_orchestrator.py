# -*- coding: utf-8 -*-
"""Задача 2 (Вариант A): animate ГОТОВЫХ фото без свапа — orchestrator layer.

Entry path that drops already-finished photos straight into ``swap_result_path``
and into ``SWAP_DONE`` WITHOUT ever running the swap phase, so the swap billing
(which lives only in ``run_swap_phase``) is never reached.
"""
from pathlib import Path

import pytest
from PIL import Image

from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator,
    OrchestratorError,
    STATE_SWAP_DONE,
    STATE_EXPECTING_READY_PHOTOS,
)


class _V:
    """Validator stub mirroring real semantics: opens the image (raises on a
    corrupt/unreadable file) and reports a face count."""

    def count_faces(self, p):
        Image.open(p).verify()  # raises on non-image bytes
        return 1


def _jpg(p: Path) -> Path:
    Image.new("RGB", (16, 16), (1, 2, 3)).save(p, "JPEG")
    return p


def _broken(p: Path) -> Path:
    p.write_bytes(b"not an image")
    return p


def _orch(tmp_path):
    return BatchOrchestrator(state_root=tmp_path / "b", validator=_V())


# ── Task 1: begin_ready_batch ────────────────────────────────────────────────

def test_begin_ready_batch_fresh_session_in_ready_state(tmp_path):
    orch = _orch(tmp_path)
    sess = orch.begin_ready_batch(10)
    assert sess.status == STATE_EXPECTING_READY_PHOTOS
    assert sess.targets == []


def test_begin_ready_batch_resets_money_safe_defaults(tmp_path):
    """Fresh session must reset smooth→OFF and wardrobe→preserve so a friend
    never inherits another user's enabled smooth (silent extra billing)."""
    orch = _orch(tmp_path)
    sess = orch.begin_ready_batch(11)
    assert sess.smooth_enabled is False
    assert sess.wardrobe_mode == "preserve"
    assert sess.video_engine == "spicy"


def test_begin_ready_batch_guards_inflight(tmp_path):
    orch = _orch(tmp_path)
    orch.begin_ready_batch(12)
    sess = orch.get(12)
    sess.status = "ANIMATING"  # in-flight lockable phase
    with pytest.raises(OrchestratorError):
        orch.begin_ready_batch(12)


# ── Task 2: add_ready_photos ─────────────────────────────────────────────────

def test_add_ready_photos_sets_swap_result_path(tmp_path):
    orch = _orch(tmp_path)
    orch.begin_ready_batch(13)
    sess = orch.add_ready_photos(13, [_jpg(tmp_path / "a.jpg"), _jpg(tmp_path / "b.jpg")])
    assert len(sess.targets) == 2
    for t in sess.targets:
        assert t.valid is True
        assert t.swap_result_path is not None
        assert Path(t.swap_result_path).exists()


def test_add_ready_photos_accumulates_across_albums(tmp_path):
    orch = _orch(tmp_path)
    orch.begin_ready_batch(14)
    orch.add_ready_photos(14, [_jpg(tmp_path / "a.jpg")])
    sess = orch.add_ready_photos(14, [_jpg(tmp_path / "b.jpg"), _jpg(tmp_path / "c.jpg")])
    assert len(sess.targets) == 3
    # Staged files keep distinct indices (no overwrite across albums).
    paths = {t.swap_result_path for t in sess.targets}
    assert len(paths) == 3


def test_add_ready_photos_skips_unreadable_without_swap_result(tmp_path):
    orch = _orch(tmp_path)
    orch.begin_ready_batch(15)
    sess = orch.add_ready_photos(
        15, [_jpg(tmp_path / "ok.jpg"), _broken(tmp_path / "broken.jpg")]
    )
    good = [t for t in sess.targets if t.swap_result_path]
    bad = [t for t in sess.targets if not t.swap_result_path]
    assert len(good) == 1
    assert len(bad) == 1
    assert bad[0].valid is False


def test_add_ready_photos_rejects_wrong_state(tmp_path):
    orch = _orch(tmp_path)
    orch.begin_ready_batch(16)
    orch.get(16).status = STATE_SWAP_DONE
    with pytest.raises(OrchestratorError):
        orch.add_ready_photos(16, [_jpg(tmp_path / "a.jpg")])


def test_add_ready_photos_enforces_max(tmp_path):
    from app.services.block_m2_face_swap.batch_orchestrator import MAX_TARGETS
    orch = _orch(tmp_path)
    orch.begin_ready_batch(17)
    paths = [_jpg(tmp_path / f"p{i}.jpg") for i in range(MAX_TARGETS + 1)]
    with pytest.raises(OrchestratorError):
        orch.add_ready_photos(17, paths)


# ── Task 3: finish_ready_batch ───────────────────────────────────────────────

def test_finish_ready_batch_enters_swap_done(tmp_path):
    orch = _orch(tmp_path)
    orch.begin_ready_batch(18)
    orch.add_ready_photos(18, [_jpg(tmp_path / "a.jpg")])
    sess = orch.finish_ready_batch(18)
    assert sess.status == STATE_SWAP_DONE


def test_finish_ready_batch_empty_raises(tmp_path):
    orch = _orch(tmp_path)
    orch.begin_ready_batch(19)
    with pytest.raises(OrchestratorError):
        orch.finish_ready_batch(19)


def test_finish_ready_batch_wrong_state_raises(tmp_path):
    orch = _orch(tmp_path)
    orch.begin_ready_batch(20)
    orch.add_ready_photos(20, [_jpg(tmp_path / "a.jpg")])
    orch.finish_ready_batch(20)
    # Already SWAP_DONE — a second finish is invalid.
    with pytest.raises(OrchestratorError):
        orch.finish_ready_batch(20)
