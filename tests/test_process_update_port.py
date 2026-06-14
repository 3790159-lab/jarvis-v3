# -*- coding: utf-8 -*-
"""Characterization + port tests for ``process_update`` unification.

Phase-4 collapses the long-poll dispatch loop into ``process_update`` so the
polling and webhook paths share one code path. Before the collapse,
``process_update`` was missing three behaviours the poll loop had:

  * single-photo swapbatch intercept   (``_swapbatch_photo_intercept``)
  * numbered-prompt swapbatch intercept (``_swapbatch_text_intercept``)
  * B-51 media_group dedupe-on-append   (``_buffer_media_group_msg``)

These tests lock the *target* dispatch contract of ``process_update``:
  - the two swap intercepts fire (and short-circuit) for a whitelisted user in
    the ALLOWED_CHAT_ID chat, and fall through cleanly when no session is active;
  - album photos are deduped by ``file_unique_id`` (B-51) instead of naively
    appended — this is the same fix that closes the webhook media_group gap;
  - voice routes through the unified ``_route_voice`` for ANY whitelisted user
    (no ALLOWED_CHAT_ID gate), per Phase-4 Step 2.

Same fresh-module-load harness as tests/test_bot_voice_integration.py — all
Telegram / handler / network seams are mocked, no bot token, no HTTP, no pods.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


ALLOWED_CHAT = 12345          # == TELEGRAM_ALLOWED_CHAT_ID below
OTHER_WHITELISTED = 222       # whitelisted, but NOT the ALLOWED_CHAT_ID chat


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", f"{ALLOWED_CHAT},{OTHER_WHITELISTED}")
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("JARVIS_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "0")
    monkeypatch.setenv("JARVIS_VOICE_REPLY_ENABLED", "0")
    # ALLOWED_CHAT_ID is read from env at module import; set it before _get_mod().
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", str(ALLOWED_CHAT))


def _get_mod():
    mod_name = f"_test_pu_port_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _photo_update(chat_id, file_unique_id, file_id, *, media_group_id=None):
    msg = {
        "from": {"id": chat_id, "username": "tester"},
        "chat": {"id": chat_id},
        "photo": [
            {"file_id": file_id, "file_unique_id": file_unique_id, "file_size": 1000}
        ],
    }
    if media_group_id is not None:
        msg["media_group_id"] = media_group_id
    return {"update_id": 1, "message": msg}


def _text_update(chat_id, text):
    return {
        "update_id": 1,
        "message": {
            "from": {"id": chat_id, "username": "tester"},
            "chat": {"id": chat_id},
            "text": text,
        },
    }


def _voice_update(chat_id, duration=3):
    return {
        "update_id": 1,
        "message": {
            "from": {"id": chat_id, "username": "tester"},
            "chat": {"id": chat_id},
            "voice": {"file_id": "vf_1", "duration": duration},
        },
    }


# ── single-photo swapbatch intercept ────────────────────────────────────────


def test_single_photo_routed_to_swapbatch_intercept(monkeypatch):
    """An active swap session consumes a single photo via the intercept; the
    default file handler must NOT also run."""
    mod = _get_mod()
    intercept = MagicMock(return_value=True)
    handle_file = MagicMock()
    monkeypatch.setattr(mod, "_persona_video_intercept", lambda *a, **k: False)
    monkeypatch.setattr(mod, "_swapbatch_photo_intercept", intercept)
    monkeypatch.setattr(mod, "_handle_file_message", handle_file)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    upd = _photo_update(ALLOWED_CHAT, "U_A", "fid_a")
    mod.process_update(upd, {})

    intercept.assert_called_once()
    assert intercept.call_args[0][0] == str(ALLOWED_CHAT)
    assert intercept.call_args[0][1] is upd["message"]
    handle_file.assert_not_called()


def test_single_photo_falls_through_to_file_handler_when_no_session(monkeypatch):
    """No swap session → intercept is still consulted, then the photo falls
    through to the normal file handler."""
    mod = _get_mod()
    intercept = MagicMock(return_value=False)
    handle_file = MagicMock()
    monkeypatch.setattr(mod, "_persona_video_intercept", lambda *a, **k: False)
    monkeypatch.setattr(mod, "_swapbatch_photo_intercept", intercept)
    monkeypatch.setattr(mod, "_handle_file_message", handle_file)
    monkeypatch.setattr(mod, "load_state", lambda: {})
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    mod.process_update(_photo_update(ALLOWED_CHAT, "U_A", "fid_a"), {})

    intercept.assert_called_once()
    handle_file.assert_called_once()


# ── numbered-prompt swapbatch text intercept ────────────────────────────────


def test_numbered_prompt_text_routed_to_swapbatch(monkeypatch):
    """A custom-prompts message is consumed by the text intercept; neither the
    LLM router nor the legacy dispatcher runs."""
    mod = _get_mod()
    text_intercept = MagicMock(return_value=True)
    handle = MagicMock()
    route_plain = MagicMock(return_value=False)
    monkeypatch.setattr(mod, "_persona_video_intercept", lambda *a, **k: False)
    monkeypatch.setattr(mod, "_swapbatch_text_intercept", text_intercept)
    monkeypatch.setattr(mod, "handle", handle)
    monkeypatch.setattr(mod, "_route_plain_text", route_plain)

    mod.process_update(_text_update(ALLOWED_CHAT, "1. neon samurai"), {})

    text_intercept.assert_called_once_with(str(ALLOWED_CHAT), "1. neon samurai")
    handle.assert_not_called()
    route_plain.assert_not_called()


def test_text_falls_through_when_no_custom_flow(monkeypatch):
    """No custom-prompts flow active → text intercept is consulted, then the
    message falls through to the legacy dispatcher (router off)."""
    mod = _get_mod()
    text_intercept = MagicMock(return_value=False)
    handle = MagicMock()
    monkeypatch.setattr(mod, "_persona_video_intercept", lambda *a, **k: False)
    monkeypatch.setattr(mod, "_swapbatch_text_intercept", text_intercept)
    monkeypatch.setattr(mod, "handle", handle)
    monkeypatch.setattr(mod, "_route_plain_text", lambda *a, **k: False)

    mod.process_update(_text_update(ALLOWED_CHAT, "обычный текст"), {})

    text_intercept.assert_called_once_with(str(ALLOWED_CHAT), "обычный текст")
    handle.assert_called_once_with(str(ALLOWED_CHAT), "обычный текст")


# ── B-51 media_group dedupe in process_update ───────────────────────────────


def test_album_photos_deduped_by_unique_id(monkeypatch):
    """Same physical photo (file_unique_id 'U_A') redelivered under two
    different file_ids collapses to one buffered msg — proving process_update
    uses _buffer_media_group_msg, not a naive append. This is the same fix
    that closes the webhook media_group total-loss gap (Day 8)."""
    mod = _get_mod()
    monkeypatch.setattr(mod, "_persona_video_intercept", lambda *a, **k: False)

    buffer: dict = {}
    mod.process_update(_photo_update(ALLOWED_CHAT, "U_A", "fid_a1", media_group_id="mg1"), buffer)
    mod.process_update(_photo_update(ALLOWED_CHAT, "U_A", "fid_a2", media_group_id="mg1"), buffer)

    assert len(buffer["mg1"]["msgs"]) == 1
    assert "seen_uids" in buffer["mg1"]          # dedupe bookkeeping present
    assert "U_A" in buffer["mg1"]["seen_uids"]


def test_album_keeps_distinct_unique_ids(monkeypatch):
    """Two genuinely distinct photos in one album are both buffered."""
    mod = _get_mod()
    monkeypatch.setattr(mod, "_persona_video_intercept", lambda *a, **k: False)

    buffer: dict = {}
    mod.process_update(_photo_update(ALLOWED_CHAT, "U_A", "fid_a", media_group_id="mg1"), buffer)
    mod.process_update(_photo_update(ALLOWED_CHAT, "U_B", "fid_b", media_group_id="mg1"), buffer)

    assert len(buffer["mg1"]["msgs"]) == 2


# ── voice: unified _route_voice for any whitelisted user (Phase-4 Step 2) ────


def test_voice_routed_to_unified_route_voice(monkeypatch):
    mod = _get_mod()
    route_voice = MagicMock(return_value=True)
    monkeypatch.setattr(mod, "_persona_video_intercept", lambda *a, **k: False)
    monkeypatch.setattr(mod, "_route_voice", route_voice)

    upd = _voice_update(ALLOWED_CHAT)
    mod.process_update(upd, {})

    route_voice.assert_called_once()
    assert route_voice.call_args[0][0] == str(ALLOWED_CHAT)
    assert route_voice.call_args[0][1] is upd["message"]


def test_voice_available_to_any_whitelisted_user(monkeypatch):
    """Voice is NOT gated to ALLOWED_CHAT_ID — a whitelisted non-admin in a
    different chat is still routed through _route_voice."""
    mod = _get_mod()
    route_voice = MagicMock(return_value=True)
    monkeypatch.setattr(mod, "_persona_video_intercept", lambda *a, **k: False)
    monkeypatch.setattr(mod, "_route_voice", route_voice)

    mod.process_update(_voice_update(OTHER_WHITELISTED), {})

    route_voice.assert_called_once()
    assert route_voice.call_args[0][0] == str(OTHER_WHITELISTED)
