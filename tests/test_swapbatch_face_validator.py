# -*- coding: utf-8 -*-
"""Tests for ``app.services.block_m2_face_swap.face_validator``.

We do not exercise the real InsightFace / OpenCV cascade (heavy deps);
instead we mock the lazy-load step and the per-image detect methods.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.block_m2_face_swap import face_validator
from app.services.block_m2_face_swap.face_validator import (
    BACKEND_INSIGHTFACE,
    BACKEND_NONE,
    BACKEND_OPENCV,
    FaceValidator,
    get_active_backend,
)


# ── fixtures ─────────────────────────────────────────────────────────────────


def _make_image(tmp_path: Path, name: str = "x.jpg") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)  # tiny JPEG-ish stub
    return p


# ── error paths ──────────────────────────────────────────────────────────────


def test_count_faces_missing_image_raises(tmp_path):
    v = FaceValidator()
    with pytest.raises(ValueError, match="image not found"):
        v.count_faces(tmp_path / "nope.jpg")


def test_has_face_proxies_count_faces(tmp_path):
    img = _make_image(tmp_path)
    v = FaceValidator()
    # Inject a fake backend so _detect returns a deterministic list.
    v._loaded = True
    v._insightface_app = None
    v._opencv_cascade = None
    fake = MagicMock(return_value=[(0, 0, 10, 10)])
    v._detect = fake  # type: ignore[method-assign]
    assert v.has_face(img) is True
    fake.return_value = []
    assert v.has_face(img) is False


def test_get_largest_bbox_returns_max_area(tmp_path):
    img = _make_image(tmp_path)
    v = FaceValidator()
    v._loaded = True
    v._detect = MagicMock(  # type: ignore[method-assign]
        return_value=[
            (0, 0, 5, 5),         # area 25
            (10, 10, 60, 60),     # area 2500 — largest
            (50, 50, 60, 60),     # area 100
        ]
    )
    bbox = v.get_largest_face_bbox(img)
    assert bbox == (10, 10, 60, 60)


def test_get_largest_bbox_none_when_no_faces(tmp_path):
    img = _make_image(tmp_path)
    v = FaceValidator()
    v._loaded = True
    v._detect = MagicMock(return_value=[])  # type: ignore[method-assign]
    assert v.get_largest_face_bbox(img) is None


# ── backend selection ───────────────────────────────────────────────────────


def test_count_faces_with_no_backend_returns_zero(tmp_path, monkeypatch):
    """If both backends fail to load, _detect returns []."""
    img = _make_image(tmp_path)
    v = FaceValidator(prefer_insightface=True)

    def _no_insightface(*_a, **_kw):
        raise ImportError("insightface not installed (simulated)")

    def _no_cv2(*_a, **_kw):
        raise ImportError("cv2 not installed (simulated)")

    # Patch the builtins import so the lazy-load hits both failure paths.
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _patched_import(name, *args, **kwargs):
        if name == "insightface":
            _no_insightface()
        if name == "cv2":
            _no_cv2()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _patched_import)
    assert v.count_faces(img) == 0
    assert face_validator.VALIDATOR_BACKEND == BACKEND_NONE


def test_loaded_flag_prevents_double_init(tmp_path):
    v = FaceValidator()
    v._loaded = True
    v._opencv_cascade = MagicMock()
    # Spy on opencv detect to ensure _ensure_loaded does NOT re-init.
    v._detect_opencv = MagicMock(return_value=[(0, 0, 1, 1)])  # type: ignore[method-assign]
    img = _make_image(tmp_path)
    assert v.count_faces(img) == 1
    v._detect_opencv.assert_called_once()


def test_backend_constants_are_distinct():
    assert BACKEND_INSIGHTFACE != BACKEND_OPENCV != BACKEND_NONE


# ── public accessor ─────────────────────────────────────────────────────────


def test_get_active_backend_returns_one_of_known_values(monkeypatch):
    """The getter forces a lazy load and returns a known backend name."""
    monkeypatch.setattr(face_validator, "VALIDATOR_BACKEND", BACKEND_NONE)
    result = get_active_backend()
    assert result in (BACKEND_INSIGHTFACE, BACKEND_OPENCV, BACKEND_NONE)


def test_get_active_backend_is_idempotent(monkeypatch):
    """Calling twice does not re-init; second call returns same value."""
    monkeypatch.setattr(face_validator, "VALIDATOR_BACKEND", BACKEND_OPENCV)
    a = get_active_backend()
    b = get_active_backend()
    assert a == b == BACKEND_OPENCV


def test_opencv_detect_uses_loosened_min_neighbors(tmp_path, monkeypatch):
    """The OpenCV path passes minNeighbors=3 (loosened from 5)."""
    import sys
    from unittest.mock import MagicMock
    fake_cv2 = MagicMock()
    fake_cv2.imread.return_value = MagicMock()
    fake_cv2.cvtColor.return_value = MagicMock()
    fake_cv2.COLOR_BGR2GRAY = 6
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    v = FaceValidator()
    v._loaded = True
    cascade = MagicMock()
    cascade.detectMultiScale.return_value = [(10, 10, 50, 50)]
    v._opencv_cascade = cascade

    img = tmp_path / "x.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    v._detect_opencv(img)

    _, kwargs = cascade.detectMultiScale.call_args
    assert kwargs["minNeighbors"] == 3
    assert kwargs["scaleFactor"] == 1.1
    assert kwargs["minSize"] == (40, 40)
