# -*- coding: utf-8 -*-
"""Задача 2 (Вариант A) — handler layer for animate-batch of ready photos.

These three handler methods wrap the orchestrator's ready-photo transitions and
prove the SWAP_DONE second stage (handle_animate_yes) reuses unchanged.
"""
from pathlib import Path

import pytest
from PIL import Image

from app.handlers.face_swap_handler import FaceSwapHandler
from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator,
    STATE_EXPECTING_READY_PHOTOS,
    STATE_SWAP_DONE,
)


class _V:
    def count_faces(self, p):
        Image.open(p).verify()
        return 1


def _jpg(p: Path) -> Path:
    Image.new("RGB", (16, 16), (1, 2, 3)).save(p, "JPEG")
    return p


def _orch(tmp_path):
    return BatchOrchestrator(state_root=tmp_path / "b", validator=_V())


# ── Task 4: handle_animate_batch_intent ──────────────────────────────────────

def test_intent_sets_ready_state_and_invites(tmp_path):
    orch = _orch(tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    reply = h.handle_animate_batch_intent(30)
    assert orch.status(30) == STATE_EXPECTING_READY_PHOTOS
    assert reply.text is not None
    assert "/animate_batch_go" in reply.text


# ── Task 5: consume_ready_album ──────────────────────────────────────────────

def test_consume_ready_album_accepts_in_ready_state(tmp_path):
    orch = _orch(tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    h.handle_animate_batch_intent(31)
    reply = h.consume_ready_album(31, [_jpg(tmp_path / "a.jpg"), _jpg(tmp_path / "b.jpg")])
    assert reply.consumed is True
    assert "2" in reply.text
    assert len(orch.get(31).targets) == 2


def test_consume_ready_album_ignored_outside_ready_state(tmp_path):
    """Must NOT hijack a swap-batch album: when the chat is not in the
    ready-photo state, consumed=False so the swap intake path keeps working."""
    orch = _orch(tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    # No ready-batch started (status IDLE / swap-batch states).
    reply = h.consume_ready_album(32, [_jpg(tmp_path / "a.jpg")])
    assert reply.consumed is False
    assert orch.status(32) != STATE_EXPECTING_READY_PHOTOS


# ── Task 6: handle_animate_batch_go ──────────────────────────────────────────

def test_go_enters_swap_done(tmp_path):
    orch = _orch(tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    h.handle_animate_batch_intent(33)
    h.consume_ready_album(33, [_jpg(tmp_path / "a.jpg")])
    reply = h.handle_animate_batch_go(33)
    assert orch.status(33) == STATE_SWAP_DONE
    assert reply.text is not None


def test_go_without_photos_gives_clear_error(tmp_path):
    orch = _orch(tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    h.handle_animate_batch_intent(34)
    reply = h.handle_animate_batch_go(34)
    assert "⚠️" in reply.text
    assert orch.status(34) == STATE_EXPECTING_READY_PHOTOS  # not advanced


def test_go_wrong_state_refused(tmp_path):
    orch = _orch(tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    # No ready-batch at all.
    reply = h.handle_animate_batch_go(35)
    assert "⚠️" in reply.text


def test_after_go_handle_animate_yes_shows_estimate(tmp_path):
    """Proof the entire second stage reuses 1:1 on ready photos: the SWAP_DONE
    estimate screen renders with a cost, exactly as after a real swap."""
    orch = _orch(tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    h.handle_animate_batch_intent(36)
    h.consume_ready_album(36, [_jpg(tmp_path / "a.jpg"), _jpg(tmp_path / "b.jpg")])
    h.handle_animate_batch_go(36)
    reply = h.handle_animate_yes(36)
    assert "$" in reply.text
    assert "/swapbatch_animate_go" in reply.text
    assert orch.status(36) == STATE_SWAP_DONE  # estimate does not run anything
