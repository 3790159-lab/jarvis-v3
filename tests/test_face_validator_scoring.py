# -*- coding: utf-8 -*-
"""Tests for ``FaceValidator.score_largest_face`` (Веха C / Задача 1).

Frame-quality scoring built on the SCRFD detection model (det_score + kps)
that ``buffalo_l`` already loads. We mock the InsightFace app / OpenCV cascade
the same way ``test_swapbatch_face_validator.py`` does — no heavy deps run.

Scoring is $0 / local / 0 vision.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.block_m2_face_swap.face_validator import (
    BACKEND_INSIGHTFACE,
    BACKEND_OPENCV,
    FaceScore,
    FaceValidator,
)


# ── fixtures ─────────────────────────────────────────────────────────────────


def _img(tmp_path: Path, name: str = "x.jpg") -> Path:
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    return p


def _face(bbox, det_score, kps):
    """A stand-in for an InsightFace SCRFD result object."""
    return types.SimpleNamespace(bbox=list(bbox), det_score=det_score, kps=kps)


def _frontal_kps(cx: int):
    """5 kps with nose centred between eyes → maximally frontal."""
    # [left_eye, right_eye, nose, l_mouth, r_mouth]
    return [(cx - 20, 100), (cx + 20, 100), (cx, 120), (cx - 15, 150), (cx + 15, 150)]


def _profile_kps(cx: int):
    """Nose shifted hard toward right eye → strongly off-frontal."""
    return [(cx - 20, 100), (cx + 20, 100), (cx + 18, 120), (cx + 10, 150), (cx + 25, 150)]


def _setup_insightface(v: FaceValidator, faces, monkeypatch, h: int = 400, w: int = 400):
    """Wire a FaceValidator to a mocked InsightFace backend + cv2.imread."""
    v._loaded = True
    app = MagicMock()
    app.get.return_value = faces
    v._insightface_app = app
    v._opencv_cascade = None

    fake_cv2 = MagicMock()
    fake_img = MagicMock()
    fake_img.shape = (h, w, 3)
    fake_cv2.imread.return_value = fake_img
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)


# ── frontality: profile vs frontal ───────────────────────────────────────────


def test_profile_scores_lower_frontality_than_frontal(tmp_path, monkeypatch):
    img = _img(tmp_path)

    v_front = FaceValidator()
    _setup_insightface(v_front, [_face((100, 100, 200, 200), 0.9, _frontal_kps(150))], monkeypatch)
    front = v_front.score_largest_face(img)

    v_prof = FaceValidator()
    _setup_insightface(v_prof, [_face((100, 100, 200, 200), 0.9, _profile_kps(150))], monkeypatch)
    prof = v_prof.score_largest_face(img)

    assert front.frontality > prof.frontality
    assert front.composite > prof.composite


# ── area: small vs large ─────────────────────────────────────────────────────


def test_small_face_scores_lower_area_than_large(tmp_path, monkeypatch):
    img = _img(tmp_path)

    v_big = FaceValidator()
    _setup_insightface(v_big, [_face((0, 0, 300, 300), 0.9, _frontal_kps(150))], monkeypatch)
    big = v_big.score_largest_face(img)

    v_small = FaceValidator()
    _setup_insightface(v_small, [_face((0, 0, 30, 30), 0.9, _frontal_kps(15))], monkeypatch)
    small = v_small.score_largest_face(img)

    assert big.area_fraction > small.area_fraction
    assert big.composite > small.composite


# ── det_score: blur lowers composite ─────────────────────────────────────────


def test_low_det_score_lowers_composite(tmp_path, monkeypatch):
    img = _img(tmp_path)
    bbox, kps = (100, 100, 200, 200), _frontal_kps(150)

    v_sharp = FaceValidator()
    _setup_insightface(v_sharp, [_face(bbox, 0.95, kps)], monkeypatch)
    sharp = v_sharp.score_largest_face(img)

    v_blur = FaceValidator()
    _setup_insightface(v_blur, [_face(bbox, 0.30, kps)], monkeypatch)
    blur = v_blur.score_largest_face(img)

    assert sharp.det_score > blur.det_score
    assert sharp.composite > blur.composite


# ── multiple faces → largest ─────────────────────────────────────────────────


def test_multiple_faces_scores_largest(tmp_path, monkeypatch):
    img = _img(tmp_path)
    small = _face((0, 0, 20, 20), 0.9, _frontal_kps(10))
    large = _face((100, 100, 300, 300), 0.9, _frontal_kps(200))

    v = FaceValidator()
    _setup_insightface(v, [small, large], monkeypatch)
    s = v.score_largest_face(img)

    assert s.bbox == (100, 100, 300, 300)


# ── no face → None ───────────────────────────────────────────────────────────


def test_no_face_returns_none(tmp_path, monkeypatch):
    img = _img(tmp_path)
    v = FaceValidator()
    _setup_insightface(v, [], monkeypatch)
    assert v.score_largest_face(img) is None


# ── degenerate kps → frontality excluded (renorm) ────────────────────────────


def test_degenerate_kps_excludes_frontality_from_composite(tmp_path, monkeypatch):
    img = _img(tmp_path)
    # eyes share the same x → eye_dist == 0 → frontality unavailable
    deg = [(150, 100), (150, 100), (150, 120), (150, 150), (150, 150)]
    v = FaceValidator()
    _setup_insightface(v, [_face((0, 0, 200, 200), 0.9, deg)], monkeypatch)
    s = v.score_largest_face(img)

    # bbox area 40000 / (400*400) = 0.25 → area_norm = min(1, 0.25/0.10) = 1.0
    # frontality excluded → renorm over W_DET + W_AREA only
    expected = (0.40 * 0.9 + 0.25 * 1.0) / (0.40 + 0.25)
    assert s.frontality == 0.0
    assert s.composite == pytest.approx(expected)


# ── opencv backend degradation ───────────────────────────────────────────────


def test_opencv_backend_uses_area_only(tmp_path, monkeypatch):
    img = _img(tmp_path)
    v = FaceValidator()
    v._loaded = True
    v._insightface_app = None
    cascade = MagicMock()
    cascade.detectMultiScale.return_value = [(0, 0, 200, 200)]  # x, y, w, h
    v._opencv_cascade = cascade

    fake_cv2 = MagicMock()
    fake_img = MagicMock()
    fake_img.shape = (400, 400, 3)
    fake_cv2.imread.return_value = fake_img
    fake_cv2.cvtColor.return_value = MagicMock()
    fake_cv2.COLOR_BGR2GRAY = 6
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    s = v.score_largest_face(img)
    assert s.backend == BACKEND_OPENCV
    assert s.det_score == 0.0
    assert s.frontality == 0.0
    # bbox (0,0,200,200) → area_fraction 0.25 → area_norm 1.0 → composite 1.0
    assert s.composite == pytest.approx(1.0)


def test_insightface_backend_reported(tmp_path, monkeypatch):
    img = _img(tmp_path)
    v = FaceValidator()
    _setup_insightface(v, [_face((100, 100, 200, 200), 0.9, _frontal_kps(150))], monkeypatch)
    s = v.score_largest_face(img)
    assert s.backend == BACKEND_INSIGHTFACE


# ── error paths ──────────────────────────────────────────────────────────────


def test_missing_file_raises(tmp_path):
    v = FaceValidator()
    with pytest.raises(ValueError, match="image not found"):
        v.score_largest_face(tmp_path / "nope.jpg")


def test_unreadable_file_raises(tmp_path, monkeypatch):
    img = _img(tmp_path)
    v = FaceValidator()
    v._loaded = True
    v._insightface_app = MagicMock()
    v._opencv_cascade = None
    fake_cv2 = MagicMock()
    fake_cv2.imread.return_value = None  # cv2 cannot decode
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    with pytest.raises(ValueError):
        v.score_largest_face(img)


# ── composite bounds ─────────────────────────────────────────────────────────


def test_composite_within_unit_interval_on_extremes(tmp_path, monkeypatch):
    img = _img(tmp_path)
    # bbox far bigger than frame, det_score > 1 (must clamp), frontal kps
    v = FaceValidator()
    _setup_insightface(v, [_face((0, 0, 9999, 9999), 5.0, _frontal_kps(150))], monkeypatch)
    s = v.score_largest_face(img)
    assert 0.0 <= s.composite <= 1.0
    assert 0.0 <= s.det_score <= 1.0
    assert 0.0 <= s.frontality <= 1.0
    assert isinstance(s, FaceScore)
