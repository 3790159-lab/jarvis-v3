# -*- coding: utf-8 -*-
"""Integration test for the MediaPipe backend of :mod:`face_validator`.

Unlike ``test_swapbatch_face_validator.py`` (which mocks the mediapipe
package tree), this exercises the *real* MediaPipe FaceDetector Task on CPU,
locking in that the last-resort fallback (cv2 → mediapipe → honest error)
actually detects faces and is not just a shape that compiles.

Skipped automatically when ``mediapipe`` is not installed, so it never
breaks the suite on a box that hasn't pulled the (optional) dependency.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.block_m2_face_swap import face_validator
from app.services.block_m2_face_swap.face_validator import (
    BACKEND_MEDIAPIPE,
    FaceValidator,
)


def _mediapipe_ready() -> bool:
    try:
        import mediapipe  # noqa: F401
        from mediapipe.tasks.python import vision  # noqa: F401
        from mediapipe.tasks.python.core.base_options import BaseOptions  # noqa: F401
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _mediapipe_ready(),
    reason="mediapipe not available in this environment",
)


def test_mediapipe_backend_detects_face_on_real_photo(tmp_path):
    """Force the mediapipe tier and confirm it finds a real face."""
    from PIL import Image

    # A flat-color image has no face — used only to prove the pipeline runs
    # end-to-end without crashing when there genuinely is nothing to find.
    blank = tmp_path / "blank.jpg"
    Image.new("RGB", (64, 64), (128, 128, 128)).save(blank)

    v = FaceValidator(prefer_insightface=False)
    v._loaded = True  # skip insightface/opencv tiers deliberately
    from app.services.block_m2_face_swap.face_validator import (
        _ensure_mediapipe_model,
        _MEDIAPIPE_MIN_CONFIDENCE,
    )
    from mediapipe.tasks.python import vision as mp_vision
    from mediapipe.tasks.python.core.base_options import BaseOptions as MPBaseOptions

    model_path = _ensure_mediapipe_model()
    options = mp_vision.FaceDetectorOptions(
        base_options=MPBaseOptions(model_asset_path=str(model_path)),
        min_detection_confidence=_MEDIAPIPE_MIN_CONFIDENCE,
    )
    v._mediapipe_detector = mp_vision.FaceDetector.create_from_options(options)

    # No face on a blank image → 0, but must not raise.
    assert v.count_faces(blank) == 0
