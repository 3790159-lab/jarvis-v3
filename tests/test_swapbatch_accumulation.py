# -*- coding: utf-8 -*-
"""Tests for multi-album accumulation + advisory FaceValidator in add_targets.

Key invariants:
- add_targets appends across albums (does not reset like submit_targets).
- Cumulative cap of MAX_TARGETS is enforced.
- Advisory semantics: 0 faces from validator → valid=True (sent to engine).
- Unreadable target (validator raises) → valid=False (real skip).
- no_face_advisory count is correct for the cost report.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator,
    OrchestratorError,
    STATE_TARGETS_RECEIVED,
)
from app.services.block_m2_face_swap.face_validator import FaceValidatorUnavailableError


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_validator(face_count: int = 1) -> MagicMock:
    v = MagicMock()
    v.count_faces.return_value = face_count
    return v


def _jpg(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    Image.new("RGB", (32, 32), (10, 20, 30)).save(p, "JPEG")
    return p


def _orch(tmp_path: Path, validator=None) -> BatchOrchestrator:
    return BatchOrchestrator(
        state_root=tmp_path / "batches",
        validator=validator or _make_validator(1),
    )


def _seed_source(orch: BatchOrchestrator, chat_id: int, tmp_path: Path) -> None:
    """Drive orchestrator to EXPECTING_TARGETS state."""
    orch.begin_source(chat_id)
    # Use a real validator that always returns 1 face for source.
    src = _jpg(tmp_path, "src.jpg")
    # Temporarily set validator to always-1 for source submission.
    orig = orch._validator.count_faces
    orch._validator.count_faces = MagicMock(return_value=1)
    orch.submit_source(chat_id, src)
    orch._validator.count_faces = orig
    orch.begin_targets(chat_id)


# ── plan-derived accumulation tests ──────────────────────────────────────────


def test_two_albums_accumulate(tmp_path):
    """Second add_targets call appends, not resets."""
    orch = _orch(tmp_path)
    chat = 1
    _seed_source(orch, chat, tmp_path)
    a = [_jpg(tmp_path, f"a{i}.jpg") for i in range(3)]
    b = [_jpg(tmp_path, f"b{i}.jpg") for i in range(2)]

    sess, est = orch.add_targets(chat, a)
    assert len(sess.targets) == 3
    assert sess.status == STATE_TARGETS_RECEIVED

    sess, est = orch.add_targets(chat, b)  # second album appends, not resets
    assert len(sess.targets) == 5
    assert est.valid_count == 5


def test_cap_enforced_across_albums(tmp_path):
    """add_targets raises when cumulative count would exceed MAX_TARGETS."""
    orch = _orch(tmp_path)
    chat = 2
    _seed_source(orch, chat, tmp_path)
    first = [_jpg(tmp_path, f"f{i}.jpg") for i in range(99)]
    orch.add_targets(chat, first)
    with pytest.raises(OrchestratorError):
        orch.add_targets(chat, [_jpg(tmp_path, "over1.jpg"),
                                _jpg(tmp_path, "over2.jpg")])  # 99+2 > 100


# ── advisory validator tests ─────────────────────────────────────────────────


def test_no_face_target_is_valid_and_included_in_cost(tmp_path):
    """Validator returning 0 faces → valid=True, included in estimate (advisory)."""
    val = _make_validator(face_count=1)
    orch = _orch(tmp_path, validator=val)
    chat = 3
    _seed_source(orch, chat, tmp_path)

    # Make one of the targets return 0 faces from the validator.
    photos = [_jpg(tmp_path, f"p{i}.jpg") for i in range(3)]

    call_count = {"n": 0}
    def side_effect(path):
        call_count["n"] += 1
        # First call during _seed_source is for the source; targets are next.
        # Return 0 for the second target photo (index 1 among target calls).
        return 0 if call_count["n"] == 2 else 1

    val.count_faces.side_effect = side_effect

    sess, est = orch.add_targets(chat, photos)

    # All 3 should be valid (0 face → advisory, still sent).
    assert all(t.valid for t in sess.targets), (
        "Expected all targets valid under advisory semantics, got: "
        + str([(t.path, t.valid, t.face_count) for t in sess.targets])
    )
    # The no-face one should have face_count == 0.
    no_face = [t for t in sess.targets if t.face_count == 0]
    assert len(no_face) == 1
    assert no_face[0].valid is True
    assert no_face[0].error is None

    # Cost is billed over all 3 (all valid).
    assert est.valid_count == 3


def test_unreadable_target_is_excluded(tmp_path):
    """Validator raising → valid=False, excluded from cost (real skip)."""
    val = _make_validator(face_count=1)
    orch = _orch(tmp_path, validator=val)
    chat = 4
    _seed_source(orch, chat, tmp_path)

    photos = [_jpg(tmp_path, f"q{i}.jpg") for i in range(3)]

    call_count = {"n": 0}
    def side_effect(path):
        call_count["n"] += 1
        # Raise for the third target to simulate a corrupt/unreadable image.
        if call_count["n"] == 3:
            raise OSError("corrupt image")
        return 1

    val.count_faces.side_effect = side_effect

    sess, est = orch.add_targets(chat, photos)

    valid_targets = [t for t in sess.targets if t.valid]
    invalid_targets = [t for t in sess.targets if not t.valid]
    assert len(valid_targets) == 2
    assert len(invalid_targets) == 1
    assert "unreadable" in invalid_targets[0].error

    # Cost only over 2 readable photos.
    assert est.valid_count == 2
    assert est.skipped_count == 1


def test_validator_unavailable_target_is_excluded_and_labeled(tmp_path):
    """FaceValidatorUnavailableError → real skip, honestly labeled.

    Distinct from "unreadable" (corrupt file) — the validator infra itself is
    down. Must NOT be waved through to the paid engine like an advisory 0.
    """
    val = _make_validator(face_count=1)
    orch = _orch(tmp_path, validator=val)
    chat = 5
    _seed_source(orch, chat, tmp_path)

    photos = [_jpg(tmp_path, f"r{i}.jpg") for i in range(2)]
    val.count_faces.side_effect = FaceValidatorUnavailableError(
        "валидация недоступна: ни один backend не загружен"
    )

    sess, est = orch.add_targets(chat, photos)

    assert all(not t.valid for t in sess.targets)
    assert all("validator unavailable" in (t.error or "") for t in sess.targets)
    assert est.valid_count == 0
    assert est.skipped_count == 2


def test_advisory_no_face_count_computed_correctly(tmp_path):
    """no_face_advisory = count of valid targets with face_count == 0."""
    val = _make_validator(face_count=1)
    orch = _orch(tmp_path, validator=val)
    chat = 5
    _seed_source(orch, chat, tmp_path)

    photos = [_jpg(tmp_path, f"r{i}.jpg") for i in range(4)]

    # Make 2 of the 4 targets return 0 faces.
    face_counts = [1, 0, 0, 1]
    idx_ref = {"i": 0}
    def side_effect(path):
        fc = face_counts[idx_ref["i"] % len(face_counts)]
        idx_ref["i"] += 1
        return fc

    val.count_faces.side_effect = side_effect

    sess, est = orch.add_targets(chat, photos)

    # All 4 valid (advisory), 2 with face_count == 0.
    assert est.valid_count == 4
    no_face_advisory = sum(
        1 for t in sess.targets if t.valid and t.face_count == 0
    )
    assert no_face_advisory == 2


def test_add_targets_requires_expecting_or_received_state(tmp_path):
    """add_targets rejects wrong states (e.g. EXPECTING_SOURCE)."""
    orch = _orch(tmp_path)
    chat = 6
    orch.begin_source(chat)  # EXPECTING_SOURCE — wrong state for add_targets
    with pytest.raises(OrchestratorError):
        orch.add_targets(chat, [_jpg(tmp_path, "x.jpg")])


def test_add_targets_accepts_targets_received_state(tmp_path):
    """Second album (already TARGETS_RECEIVED) is accepted without error."""
    orch = _orch(tmp_path)
    chat = 7
    _seed_source(orch, chat, tmp_path)

    first = [_jpg(tmp_path, "first0.jpg")]
    orch.add_targets(chat, first)
    assert orch.status(chat) == STATE_TARGETS_RECEIVED

    # Second album must not raise.
    second = [_jpg(tmp_path, "second0.jpg"), _jpg(tmp_path, "second1.jpg")]
    sess, est = orch.add_targets(chat, second)
    assert len(sess.targets) == 3
    assert est.valid_count == 3
