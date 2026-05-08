"""Phase I.4: Face Swap model versions and error handling."""
from __future__ import annotations

import sys
import os
import urllib.error
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_face_swap_versions_not_empty():
    from app.services.face_swap import _FACE_SWAP_VERSION, _FACE_SWAP_REACTOR_VERSION
    assert _FACE_SWAP_VERSION, "Primary face-swap version must be set"
    assert _FACE_SWAP_REACTOR_VERSION, "Reactor face-swap version must be set"
    assert len(_FACE_SWAP_VERSION) > 20, "Version ID should be a full hash"


def test_face_swap_versions_are_known_models():
    from app.services.face_swap import _FACE_SWAP_VERSION, _FACE_SWAP_REACTOR_VERSION
    assert _FACE_SWAP_VERSION == "d1d6ea8c8be89d664a07a457526f7128109dee7030fdac424788d762c71ed111"
    assert _FACE_SWAP_REACTOR_VERSION == "278a81e7ebb22db98bcba54de985d22cc1abeead2754eb1f2af717247be69b34"


def test_404_gives_friendly_error():
    from app.services import face_swap

    http_err = urllib.error.HTTPError(
        url="https://api.replicate.com/v1/predictions",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=None,
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        with patch.dict(os.environ, {"REPLICATE_API_KEY": "test_key"}):
            try:
                face_swap.face_swap_basic("http://src.jpg", "http://tgt.jpg")
                assert False, "Should have raised"
            except RuntimeError as e:
                msg = str(e)
                assert "недоступна" in msg or "enhance" in msg.lower() or "обновится" in msg


def test_non_404_raises_generic_error():
    from app.services import face_swap

    http_err = urllib.error.HTTPError(
        url="https://api.replicate.com/v1/predictions",
        code=500,
        msg="Internal Server Error",
        hdrs=None,
        fp=None,
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        with patch.dict(os.environ, {"REPLICATE_API_KEY": "test_key"}):
            try:
                face_swap.face_swap_basic("http://src.jpg", "http://tgt.jpg")
                assert False, "Should have raised"
            except RuntimeError as e:
                assert "500" in str(e)
