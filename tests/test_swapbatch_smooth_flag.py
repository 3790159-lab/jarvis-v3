# -*- coding: utf-8 -*-
"""Задача 3 (TDD): per-batch RIFE *smooth* session flag.

Mirrors the existing per-batch setter pattern (``set_video_engine`` /
``set_animate_quality``): a flag lives on :class:`BatchSession`, has a
money-safe default of OFF, is mutated by a setter, and — crucially for
multi-user safety — is reset to OFF whenever a new batch starts (a fresh
``BatchSession`` is created in ``begin_source``), so one user can never
inherit another user's enabled smooth and silently spend money.

``smooth_multiplier`` is reserved for Задача 4 (only ×2 for now → 1 native).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator,
    BatchSession,
)


def _make_validator(face_count: int = 1) -> MagicMock:
    v = MagicMock()
    v.count_faces.return_value = face_count
    return v


def _make_orch(tmp_path: Path) -> BatchOrchestrator:
    return BatchOrchestrator(
        state_root=tmp_path / "batches",
        validator=_make_validator(1),
    )


def test_smooth_defaults_off(tmp_path):
    # Money-safe default: smooth never engages unless explicitly turned on.
    orch = _make_orch(tmp_path)
    sess = orch.begin_source(42)
    assert sess.smooth_enabled is False
    assert sess.smooth_multiplier == 1


def test_set_smooth_enables(tmp_path):
    orch = _make_orch(tmp_path)
    orch.begin_source(42)
    orch.set_smooth(42, True)
    assert orch.get(42).smooth_enabled is True
    assert orch.get(42).smooth_multiplier == 1   # default ×2 placeholder


def test_set_smooth_toggles_off(tmp_path):
    orch = _make_orch(tmp_path)
    orch.begin_source(42)
    orch.set_smooth(42, True)
    orch.set_smooth(42, False)
    assert orch.get(42).smooth_enabled is False


def test_set_smooth_multiplier_defaults_to_one(tmp_path):
    # Only ×2 exists today (num_frames:1); the field carries the multiplier
    # for Задача 4 but defaults to 1 (native, no interpolation requested).
    orch = _make_orch(tmp_path)
    orch.begin_source(42)
    orch.set_smooth(42, True)
    assert orch.get(42).smooth_multiplier == 1


def test_new_batch_resets_smooth_to_off(tmp_path):
    # The multi-user money-safety guarantee: starting a new batch wipes the
    # previous session's enabled smooth back to OFF.
    orch = _make_orch(tmp_path)
    orch.begin_source(42)
    orch.set_smooth(42, True)
    assert orch.get(42).smooth_enabled is True
    orch.begin_source(42)   # new batch for the same chat
    assert orch.get(42).smooth_enabled is False
    assert orch.get(42).smooth_multiplier == 1


def test_set_smooth_without_session_is_noop(tmp_path):
    # Mirrors set_video_engine: no active batch → silently do nothing.
    orch = _make_orch(tmp_path)
    orch.set_smooth(999, True)   # must not raise
    assert orch.get(999) is None


def test_smooth_survives_persist_reload_roundtrip(tmp_path):
    # to_dict/from_dict must carry the new fields through a persist/reload.
    sess = BatchSession(chat_id=42)
    sess.smooth_enabled = True
    sess.smooth_multiplier = 1
    restored = BatchSession.from_dict(sess.to_dict())
    assert restored.smooth_enabled is True
    assert restored.smooth_multiplier == 1
