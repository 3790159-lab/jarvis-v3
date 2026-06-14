# -*- coding: utf-8 -*-
"""Integration tests for the InsightFace backend of :mod:`face_validator`.

Unlike ``test_swapbatch_face_validator.py`` (which mocks the heavy backends),
these exercise the *real* InsightFace 1.0.1 + ``buffalo_l`` model on CPU. They
lock in the Phase-4 fix: after upgrading insightface 0.2.1 → 1.0.1 the validator
must select the ``insightface`` backend (not the weak OpenCV Haar fallback) and
actually detect faces.

They are skipped automatically when insightface or the ``buffalo_l`` model pack
is not present (e.g. a fresh CI box that hasn't warmed the ~300 MB download), so
they never break the suite — they assert the fix only where it can be observed.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services.block_m2_face_swap import face_validator
from app.services.block_m2_face_swap.face_validator import (
    BACKEND_INSIGHTFACE,
    FaceValidator,
    get_active_backend,
)

_MODEL_DIR = Path(os.path.expanduser("~/.insightface/models/buffalo_l"))


def _insightface_ready() -> bool:
    try:
        import insightface  # noqa: F401
    except Exception:
        return False
    return _MODEL_DIR.is_dir() and any(_MODEL_DIR.glob("*.onnx"))


pytestmark = pytest.mark.skipif(
    not _insightface_ready(),
    reason="insightface + buffalo_l model not available in this environment",
)


def test_active_backend_is_insightface_not_opencv():
    """The validator must pick InsightFace, not fall back to OpenCV Haar."""
    # Force a clean lazy-load so the answer reflects a fresh selection.
    face_validator.VALIDATOR_BACKEND = face_validator.BACKEND_NONE
    assert get_active_backend() == BACKEND_INSIGHTFACE


def test_insightface_detects_faces_on_bundled_image(tmp_path):
    """The real InsightFace path detects faces on the bundled sample photo."""
    import cv2
    import insightface.data as ifdata

    img = ifdata.get_image("t1")  # bundled multi-face sample shipped with the lib
    sample = tmp_path / "t1.jpg"
    cv2.imwrite(str(sample), img)

    v = FaceValidator(prefer_insightface=True)
    assert v.has_face(sample) is True
    assert v.count_faces(sample) >= 1
    # And the backend actually used was InsightFace.
    assert v._insightface_app is not None
