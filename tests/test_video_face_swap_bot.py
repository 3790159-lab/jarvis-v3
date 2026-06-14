# -*- coding: utf-8 -*-
"""Tests for the Telegram-side wiring of video face swap.

Only the *routing* logic is exercised (does the right message trigger the flow,
do unrelated messages pass through untouched). The actual pod work
(``_video_face_swap_run`` → RunPod ComfyUI) is monkeypatched out — it is verified
manually on a live pod, never in CI.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _get_bot():
    mod_name = f"_test_vswap_bot_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_intercept_ignores_plain_text_message():
    """Backward compat: a normal text message is NOT consumed by the intercept."""
    mod = _get_bot()
    assert mod._video_face_swap_intercept("123", {"text": "привет"}) is False


def test_intercept_ignores_plain_photo_without_trigger():
    """A photo without the /video_face_swap caption must fall through."""
    mod = _get_bot()
    msg = {"photo": [{"file_id": "x"}]}
    assert mod._video_face_swap_intercept("123", msg) is False


def test_intercept_routes_photo_caption_replying_to_video(monkeypatch):
    mod = _get_bot()
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(
        mod, "_download_telegram_file", lambda fid, name: f"/tmp/{name}"
    )
    runs = []
    monkeypatch.setattr(
        mod, "_video_face_swap_run",
        lambda chat, face, video: runs.append((chat, face, video)),
    )
    msg = {
        "photo": [{"file_id": "facebig"}],
        "caption": "/video_face_swap",
        "reply_to_message": {"video": {"file_id": "vid1"}},
    }
    assert mod._video_face_swap_intercept("123", msg) is True
    assert len(runs) == 1
    _chat, face, video = runs[0]
    assert "vswap_face" in str(face)
    assert "vswap_vid" in str(video)


def test_intercept_routes_video_caption_replying_to_photo(monkeypatch):
    mod = _get_bot()
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(
        mod, "_download_telegram_file", lambda fid, name: f"/tmp/{name}"
    )
    runs = []
    monkeypatch.setattr(
        mod, "_video_face_swap_run",
        lambda chat, face, video: runs.append((chat, face, video)),
    )
    msg = {
        "video": {"file_id": "vid1"},
        "caption": "/video_face_swap",
        "reply_to_message": {"photo": [{"file_id": "facebig"}]},
    }
    assert mod._video_face_swap_intercept("123", msg) is True
    assert len(runs) == 1


def test_dispatch_sends_instructions(monkeypatch):
    mod = _get_bot()
    sent = []
    monkeypatch.setattr(mod, "send", lambda chat, text, *a, **k: sent.append(text))
    mod._video_face_swap_dispatch(123)
    assert sent and "видео" in sent[0].lower()
