"""Tests for Phase H3.4: Face Swap Engine."""
from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, call, patch

import pytest

from app.services.face_swap import (
    enhance_face,
    estimate_swap_cost,
    face_swap_basic,
    face_swap_reactor,
    face_swap_with_polish,
    poll_replicate,
)

_SOURCE = "https://example.com/source.jpg"
_TARGET = "https://example.com/target.jpg"
_RESULT = "https://cdn.replicate.com/result.jpg"


def _make_response(data: dict) -> MagicMock:
    m = MagicMock()
    m.read.return_value = json.dumps(data).encode()
    m.__enter__ = lambda s: s
    m.__exit__ = MagicMock(return_value=False)
    return m


class TestPollReplicate:
    def test_returns_output_on_success(self, monkeypatch):
        responses = [
            _make_response({"status": "processing"}),
            _make_response({"status": "succeeded", "output": [_RESULT]}),
        ]
        idx = [0]

        def fake_urlopen(req, timeout=None):
            r = responses[idx[0]]
            idx[0] += 1
            return r

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        monkeypatch.setattr("time.sleep", lambda _: None)

        result = poll_replicate("pred_123", "test_key")
        assert result == _RESULT

    def test_returns_string_output_directly(self, monkeypatch):
        responses = [
            _make_response({"status": "succeeded", "output": _RESULT}),
        ]
        idx = [0]

        def fake_urlopen(req, timeout=None):
            r = responses[idx[0]]
            idx[0] += 1
            return r

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        monkeypatch.setattr("time.sleep", lambda _: None)
        result = poll_replicate("pred_abc", "key")
        assert result == _RESULT

    def test_raises_on_failed(self, monkeypatch):
        resp = _make_response({"status": "failed", "error": "model crashed"})
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)
        monkeypatch.setattr("time.sleep", lambda _: None)
        with pytest.raises(RuntimeError, match="model crashed"):
            poll_replicate("pred_fail", "key")

    def test_raises_on_canceled(self, monkeypatch):
        resp = _make_response({"status": "canceled"})
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)
        monkeypatch.setattr("time.sleep", lambda _: None)
        with pytest.raises(RuntimeError):
            poll_replicate("pred_cancel", "key")

    def test_raises_timeout_when_never_done(self, monkeypatch):
        resp = _make_response({"status": "processing"})
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: resp)
        monkeypatch.setattr("time.sleep", lambda _: None)
        with pytest.raises(TimeoutError):
            poll_replicate("pred_stuck", "key", max_wait=3)


class TestFaceSwapBasic:
    def test_returns_result_url(self, monkeypatch):
        post_resp = _make_response({"id": "pred_001"})
        poll_resp = _make_response({"status": "succeeded", "output": [_RESULT]})
        responses = [post_resp, poll_resp]
        idx = [0]

        def fake_urlopen(req, timeout=None):
            r = responses[idx[0]]
            idx[0] += 1
            return r

        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        monkeypatch.setattr("time.sleep", lambda _: None)

        result = face_swap_basic(_SOURCE, _TARGET)
        assert result == _RESULT

    def test_raises_without_api_key(self, monkeypatch):
        monkeypatch.delenv("REPLICATE_API_KEY", raising=False)
        with pytest.raises(ValueError, match="REPLICATE_API_KEY"):
            face_swap_basic(_SOURCE, _TARGET)

    def test_payload_contains_source_and_target(self, monkeypatch):
        captured = {}
        post_resp = _make_response({"id": "pred_001"})
        poll_resp = _make_response({"status": "succeeded", "output": [_RESULT]})
        responses = [post_resp, poll_resp]
        idx = [0]

        def fake_urlopen(req, timeout=None):
            if idx[0] == 0:
                body = json.loads(req.data.decode())
                captured["body"] = body
            r = responses[idx[0]]
            idx[0] += 1
            return r

        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        monkeypatch.setattr("time.sleep", lambda _: None)

        face_swap_basic(_SOURCE, _TARGET)
        body = captured["body"]
        assert "version" in body
        inp = body["input"]
        assert inp["swap_image"] == _SOURCE
        assert inp["input_image"] == _TARGET


class TestFaceSwapReactor:
    def test_returns_result_url(self, monkeypatch):
        post_resp = _make_response({"id": "pred_002"})
        poll_resp = _make_response({"status": "succeeded", "output": [_RESULT]})
        responses = [post_resp, poll_resp]
        idx = [0]

        def fake_urlopen(req, timeout=None):
            r = responses[idx[0]]
            idx[0] += 1
            return r

        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        monkeypatch.setattr("time.sleep", lambda _: None)

        result = face_swap_reactor(_SOURCE, _TARGET)
        assert result == _RESULT

    def test_raises_without_api_key(self, monkeypatch):
        monkeypatch.delenv("REPLICATE_API_KEY", raising=False)
        with pytest.raises(ValueError):
            face_swap_reactor(_SOURCE, _TARGET)


class TestEnhanceFace:
    def test_returns_enhanced_url(self, monkeypatch):
        post_resp = _make_response({"id": "pred_003"})
        poll_resp = _make_response({"status": "succeeded", "output": [_RESULT]})
        responses = [post_resp, poll_resp]
        idx = [0]

        def fake_urlopen(req, timeout=None):
            r = responses[idx[0]]
            idx[0] += 1
            return r

        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        monkeypatch.setattr("time.sleep", lambda _: None)

        result = enhance_face("https://example.com/face.jpg")
        assert result == _RESULT

    def test_payload_contains_version(self, monkeypatch):
        captured = {}
        post_resp = _make_response({"id": "pred_003"})
        poll_resp = _make_response({"status": "succeeded", "output": [_RESULT]})
        responses = [post_resp, poll_resp]
        idx = [0]

        def fake_urlopen(req, timeout=None):
            if idx[0] == 0:
                captured["body"] = json.loads(req.data.decode())
            r = responses[idx[0]]
            idx[0] += 1
            return r

        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        monkeypatch.setattr("time.sleep", lambda _: None)

        enhance_face("https://example.com/face.jpg")
        assert captured["body"]["input"]["version"] == "v1.4"

    def test_raises_without_api_key(self, monkeypatch):
        monkeypatch.delenv("REPLICATE_API_KEY", raising=False)
        with pytest.raises(ValueError):
            enhance_face("https://example.com/img.jpg")


class TestFaceSwapWithPolish:
    @patch("app.services.face_swap.enhance_face", return_value=_RESULT)
    @patch("app.services.face_swap.face_swap_basic", return_value="https://example.com/swapped.jpg")
    def test_calls_both_steps(self, mock_swap, mock_enhance):
        result = face_swap_with_polish(_SOURCE, _TARGET)
        mock_swap.assert_called_once_with(_SOURCE, _TARGET)
        mock_enhance.assert_called_once_with("https://example.com/swapped.jpg")
        assert result == _RESULT

    @patch("app.services.face_swap.enhance_face", return_value=_RESULT)
    @patch("app.services.face_swap.face_swap_basic", return_value="https://example.com/swapped.jpg")
    def test_returns_polished_result(self, mock_swap, mock_enhance):
        result = face_swap_with_polish(_SOURCE, _TARGET)
        assert result == _RESULT


class TestEstimateSwapCost:
    def test_basic_cost(self):
        cost = estimate_swap_cost(polished=False)
        assert cost == pytest.approx(0.005, abs=0.001)

    def test_polished_cost(self):
        cost = estimate_swap_cost(polished=True)
        assert cost == pytest.approx(0.007, abs=0.001)

    def test_polished_more_expensive(self):
        assert estimate_swap_cost(True) > estimate_swap_cost(False)


class TestFaceSwapModelVersions:
    def test_basic_uses_version_based_endpoint(self, monkeypatch):
        """face_swap_basic must POST to _PREDICTIONS_BASE with version field."""
        import app.services.face_swap as fs
        captured = {}
        post_resp = _make_response({"id": "pred_v"})
        poll_resp = _make_response({"status": "succeeded", "output": [_RESULT]})
        responses = [post_resp, poll_resp]
        idx = [0]

        def fake_urlopen(req, timeout=None):
            if idx[0] == 0:
                captured["url"] = req.full_url
                captured["body"] = json.loads(req.data.decode())
            r = responses[idx[0]]
            idx[0] += 1
            return r

        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        monkeypatch.setattr("time.sleep", lambda _: None)

        face_swap_basic(_SOURCE, _TARGET)
        assert captured["url"] == fs._PREDICTIONS_BASE
        assert captured["body"]["version"] == fs._FACE_SWAP_VERSION

    def test_reactor_uses_version_based_endpoint(self, monkeypatch):
        """face_swap_reactor must POST to _PREDICTIONS_BASE with reactor version."""
        import app.services.face_swap as fs
        captured = {}
        post_resp = _make_response({"id": "pred_r"})
        poll_resp = _make_response({"status": "succeeded", "output": [_RESULT]})
        responses = [post_resp, poll_resp]
        idx = [0]

        def fake_urlopen(req, timeout=None):
            if idx[0] == 0:
                captured["url"] = req.full_url
                captured["body"] = json.loads(req.data.decode())
            r = responses[idx[0]]
            idx[0] += 1
            return r

        monkeypatch.setenv("REPLICATE_API_KEY", "test_key")
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        monkeypatch.setattr("time.sleep", lambda _: None)

        face_swap_reactor(_SOURCE, _TARGET)
        assert captured["url"] == fs._PREDICTIONS_BASE
        assert captured["body"]["version"] == fs._FACE_SWAP_REACTOR_VERSION

    def test_version_strings_are_hex(self):
        """Version IDs must be valid SHA256-like hex strings (64 chars)."""
        import app.services.face_swap as fs
        for v in (fs._FACE_SWAP_VERSION, fs._FACE_SWAP_REACTOR_VERSION):
            assert len(v) == 64
            assert all(c in "0123456789abcdef" for c in v)
