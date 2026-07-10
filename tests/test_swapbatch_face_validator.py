# -*- coding: utf-8 -*-
"""Tests for ``app.services.block_m2_face_swap.face_validator``.

We do not exercise the real InsightFace / OpenCV cascade (heavy deps);
instead we mock the lazy-load step and the per-image detect methods.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.block_m2_face_swap import face_validator
from app.services.block_m2_face_swap.face_validator import (
    BACKEND_INSIGHTFACE,
    BACKEND_MEDIAPIPE,
    BACKEND_NONE,
    BACKEND_OPENCV,
    FaceValidator,
    FaceValidatorUnavailableError,
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


def _deny_imports(monkeypatch, denied_names: set[str]) -> None:
    """Make ``import <name>`` raise ImportError for each name in denied_names,
    delegating everything else to the real import machinery."""
    real_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    def _patched_import(name, *args, **kwargs):
        if name in denied_names:
            raise ImportError(f"{name} not installed (simulated)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _patched_import)


def _install_fake_mediapipe(monkeypatch, *, detections=None) -> MagicMock:
    """Register a fake ``mediapipe`` package tree in sys.modules so
    ``_ensure_loaded``'s mediapipe branch imports it successfully, and stub
    out the model download. Returns the fake FaceDetector instance so tests
    can assert on ``.detect()`` calls / configure its return value."""
    detector_instance = MagicMock()
    detector_instance.detect.return_value = MagicMock(detections=detections or [])
    detector_cls = MagicMock()
    detector_cls.create_from_options.return_value = detector_instance

    fake_mp = types.ModuleType("mediapipe")
    fake_mp.ImageFormat = MagicMock(SRGB="SRGB")
    fake_mp.Image = MagicMock()

    fake_vision = types.ModuleType("mediapipe.tasks.python.vision")
    fake_vision.FaceDetector = detector_cls
    fake_vision.FaceDetectorOptions = MagicMock()

    fake_base_options_mod = types.ModuleType(
        "mediapipe.tasks.python.core.base_options"
    )
    fake_base_options_mod.BaseOptions = MagicMock()

    fake_tasks = types.ModuleType("mediapipe.tasks")
    fake_tasks_python = types.ModuleType("mediapipe.tasks.python")
    fake_tasks_python_core = types.ModuleType("mediapipe.tasks.python.core")
    fake_tasks_python.vision = fake_vision
    fake_tasks_python.core = fake_tasks_python_core
    fake_tasks_python_core.base_options = fake_base_options_mod

    for mod_name, mod in {
        "mediapipe": fake_mp,
        "mediapipe.tasks": fake_tasks,
        "mediapipe.tasks.python": fake_tasks_python,
        "mediapipe.tasks.python.vision": fake_vision,
        "mediapipe.tasks.python.core": fake_tasks_python_core,
        "mediapipe.tasks.python.core.base_options": fake_base_options_mod,
    }.items():
        monkeypatch.setitem(sys.modules, mod_name, mod)

    monkeypatch.setattr(
        face_validator, "_ensure_mediapipe_model", lambda *a, **kw: Path("fake.tflite")
    )
    return detector_instance


def test_count_faces_with_no_backend_raises_honest_error(tmp_path, monkeypatch):
    """All three backends unavailable → honest error, NOT a silent 0.

    This is the exact scenario that caused mass swap failures in prod: the
    validator used to return 0 faces for every photo (indistinguishable from
    a real "no face" result), silently rejecting every target for weeks.
    """
    img = _make_image(tmp_path)
    v = FaceValidator(prefer_insightface=True)
    _deny_imports(monkeypatch, {"insightface", "cv2", "mediapipe"})

    with pytest.raises(FaceValidatorUnavailableError):
        v.count_faces(img)
    assert face_validator.VALIDATOR_BACKEND == BACKEND_NONE


def test_has_face_raises_honest_error_when_no_backend(tmp_path, monkeypatch):
    img = _make_image(tmp_path)
    v = FaceValidator(prefer_insightface=True)
    _deny_imports(monkeypatch, {"insightface", "cv2", "mediapipe"})

    with pytest.raises(FaceValidatorUnavailableError):
        v.has_face(img)


def test_get_largest_face_bbox_raises_honest_error_when_no_backend(
    tmp_path, monkeypatch
):
    img = _make_image(tmp_path)
    v = FaceValidator(prefer_insightface=True)
    _deny_imports(monkeypatch, {"insightface", "cv2", "mediapipe"})

    with pytest.raises(FaceValidatorUnavailableError):
        v.get_largest_face_bbox(img)


def test_score_largest_face_raises_honest_error_when_no_backend(
    tmp_path, monkeypatch
):
    img = _make_image(tmp_path)
    v = FaceValidator(prefer_insightface=True)
    _deny_imports(monkeypatch, {"insightface", "cv2", "mediapipe"})

    with pytest.raises(FaceValidatorUnavailableError):
        v.score_largest_face(img)


def test_no_backend_logs_error_not_a_silent_simulation(tmp_path, monkeypatch, caplog):
    """The terminal failure must be logged at ERROR — never a quiet no-op."""
    img = _make_image(tmp_path)
    v = FaceValidator(prefer_insightface=True)
    _deny_imports(monkeypatch, {"insightface", "cv2", "mediapipe"})

    with caplog.at_level("WARNING", logger="app.services.block_m2_face_swap.face_validator"):
        with pytest.raises(FaceValidatorUnavailableError):
            v.count_faces(img)

    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "no backend available" in errors[0].message


def test_falls_back_to_mediapipe_when_insightface_and_opencv_unavailable(
    tmp_path, monkeypatch
):
    """cv2 → mediapipe fallback: MediaPipe is selected and actually used."""
    img = _make_image(tmp_path)
    v = FaceValidator(prefer_insightface=True)
    _deny_imports(monkeypatch, {"insightface", "cv2"})
    detector = _install_fake_mediapipe(monkeypatch)
    monkeypatch.setattr(
        v, "_run_mediapipe", lambda path: ([((1, 2, 11, 22), 0.9)], (100, 100))
    )

    assert v.count_faces(img) == 1
    assert face_validator.VALIDATOR_BACKEND == BACKEND_MEDIAPIPE
    assert v._mediapipe_detector is detector


def test_mediapipe_fallback_logs_warning_on_each_transition(
    tmp_path, monkeypatch, caplog
):
    img = _make_image(tmp_path)
    v = FaceValidator(prefer_insightface=True)
    _deny_imports(monkeypatch, {"insightface", "cv2"})
    _install_fake_mediapipe(monkeypatch, detections=[])
    monkeypatch.setattr(v, "_run_mediapipe", lambda path: ([], (100, 100)))

    with caplog.at_level("WARNING", logger="app.services.block_m2_face_swap.face_validator"):
        v.count_faces(img)

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("InsightFace unavailable" in w for w in warnings)
    assert any("OpenCV unavailable" in w for w in warnings)
    assert face_validator.VALIDATOR_BACKEND == BACKEND_MEDIAPIPE


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
    names = {BACKEND_INSIGHTFACE, BACKEND_OPENCV, BACKEND_MEDIAPIPE, BACKEND_NONE}
    assert len(names) == 4


def test_run_mediapipe_maps_bbox_and_score_from_real_image(tmp_path, monkeypatch):
    """Real Pillow decode + numpy array; only the ``mediapipe`` package itself
    is stubbed (it's an optional last-resort dep, not installed everywhere)."""
    from PIL import Image as RealPILImage

    img_path = tmp_path / "real.jpg"
    RealPILImage.new("RGB", (50, 40), color=(10, 20, 30)).save(img_path)

    fake_mp = types.ModuleType("mediapipe")
    fake_mp.ImageFormat = MagicMock(SRGB="SRGB")
    fake_mp.Image = MagicMock(return_value="mp-image-sentinel")
    monkeypatch.setitem(sys.modules, "mediapipe", fake_mp)

    fake_det = MagicMock()
    fake_det.bounding_box = MagicMock(origin_x=5, origin_y=6, width=20, height=25)
    fake_det.categories = [MagicMock(score=0.87)]

    v = FaceValidator()
    v._mediapipe_detector = MagicMock()
    v._mediapipe_detector.detect.return_value = MagicMock(detections=[fake_det])

    dets, (img_h, img_w) = v._run_mediapipe(img_path)

    assert dets == [((5, 6, 25, 31), 0.87)]
    assert (img_h, img_w) == (40, 50)
    v._mediapipe_detector.detect.assert_called_once_with("mp-image-sentinel")


# ── public accessor ─────────────────────────────────────────────────────────


def test_get_active_backend_returns_one_of_known_values(monkeypatch):
    """The getter forces a lazy load and returns a known backend name."""
    monkeypatch.setattr(face_validator, "VALIDATOR_BACKEND", BACKEND_NONE)
    result = get_active_backend()
    assert result in (
        BACKEND_INSIGHTFACE, BACKEND_OPENCV, BACKEND_MEDIAPIPE, BACKEND_NONE,
    )


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
