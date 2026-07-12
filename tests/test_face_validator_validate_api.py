# -*- coding: utf-8 -*-
"""Tests for ``FaceValidator.validate()`` — the ``(ok, confidence, reason)``
contract required by Pipeline B / Stage 4 (character swap in video).

Fail-closed: when no detection backend can be loaded, ``validate()`` must
raise :class:`FaceValidatorUnavailableError` (same as every other public
method on this class) rather than silently returning ``ok=False`` — a
silent False would be indistinguishable from a genuine "no face" result
and would repeat the 2026-07 incident described in the module docstring.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.block_m2_face_swap.face_validator import (
    FaceScore,
    FaceValidator,
    FaceValidatorUnavailableError,
    ValidationResult,
)
from tests.test_swapbatch_face_validator import _deny_imports


def _img(tmp_path: Path, name: str = "x.jpg") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    return p


def test_validate_ok_true_with_score_when_face_detected(tmp_path):
    img = _img(tmp_path)
    v = FaceValidator()
    score = FaceScore(
        composite=0.82, det_score=0.9, area_fraction=0.2, frontality=0.7,
        bbox=(0, 0, 10, 10), backend="insightface",
    )
    v.score_largest_face = MagicMock(return_value=score)  # type: ignore[method-assign]

    result = v.validate(img)

    assert isinstance(result, ValidationResult)
    ok, confidence, reason = result
    assert ok is True
    assert confidence == pytest.approx(0.82)
    assert reason == "ok"


def test_validate_ok_false_zero_confidence_when_no_face(tmp_path):
    img = _img(tmp_path)
    v = FaceValidator()
    v.score_largest_face = MagicMock(return_value=None)  # type: ignore[method-assign]

    ok, confidence, reason = v.validate(img)

    assert ok is False
    assert confidence == 0.0
    assert reason == "no_face_detected"


def test_validate_raises_honest_error_when_no_backend(tmp_path, monkeypatch):
    """Fail-closed: engine unavailable -> exception, never a fake (False, 0, ...)."""
    img = _img(tmp_path)
    v = FaceValidator(prefer_insightface=True)
    _deny_imports(monkeypatch, {"insightface", "cv2", "mediapipe"})

    with pytest.raises(FaceValidatorUnavailableError):
        v.validate(img)


def test_validate_missing_image_raises(tmp_path):
    v = FaceValidator()
    with pytest.raises(ValueError, match="image not found"):
        v.validate(tmp_path / "nope.jpg")
