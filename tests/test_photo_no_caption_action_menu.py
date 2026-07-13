# -*- coding: utf-8 -*-
"""A single photo, no caption, no active session: don't auto-run paid Vision.

Money-safety: Vision (Claude Haiku) is a billable call. Firing it on every bare
photo is an unconfirmed spend. Instead we save the file and offer action
buttons — [Analyze $] [IG-post] [Animate] [Nothing]. Only a tap on Analyze
spends money, and it goes through the SAME money-confirm chokepoint every
other paid slash command uses (handle_command -> _money_gate -> confirm:run).

Captioned photos and active me_seed/photo_studio (lora_collecting) sessions
keep their existing routing — untouched by this change.

All Telegram / network / Anthropic boundaries are mocked.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ctrl = importlib.import_module("tools.jarvis_smart_telegram_control")


def _photo_msg(chat_id=555, *, caption=None, file_id="AgACAgIA_x", uid="UQ_x"):
    m = {
        "chat": {"id": chat_id},
        "photo": [{"file_id": file_id, "file_unique_id": uid, "file_size": 500}],
    }
    if caption is not None:
        m["caption"] = caption
    return m


def _base_patches(monkeypatch, *, me_seed_consumes=False, photo_studio_step=None,
                   photo_studio_consumes=False):
    """Common no-network scaffolding for _handle_file_message."""
    monkeypatch.setattr(ctrl, "_download_telegram_file",
                         lambda fid, fn: f"/tmp/incoming/{fn}")
    monkeypatch.setattr(ctrl, "_parse_file_safe",
                         lambda path, mime="": {"_summary": "📄 x", "text": ""})
    monkeypatch.setattr(ctrl, "save_state", lambda s: None)
    monkeypatch.setattr("tools.photo_studio_telegram.get_telegram_photo_url",
                         lambda fid, token: "https://t.me/fake.jpg")
    monkeypatch.setattr("app.handlers.persona_handler.handle_photo_message",
                         lambda chat_id, url: me_seed_consumes)
    monkeypatch.setattr("app.handlers.persona_handler.init_bot", lambda *a, **k: None)
    monkeypatch.setattr(
        "tools.photo_studio_telegram.load_conv",
        lambda chat_id: {"step": photo_studio_step} if photo_studio_step else {},
    )
    monkeypatch.setattr("tools.photo_studio_telegram.handle_faceswap_photo_step",
                         lambda *a, **k: photo_studio_consumes)


# ── No caption, no active session: buttons, NOT auto-Vision ─────────────────

def test_no_caption_no_session_shows_action_buttons(monkeypatch):
    _base_patches(monkeypatch)
    sends = []
    kb_calls = []
    monkeypatch.setattr(ctrl, "send", lambda cid, txt, **k: sends.append(txt))
    monkeypatch.setattr(ctrl, "send_with_keyboard",
                         lambda cid, txt, kb: kb_calls.append((cid, txt, kb)))
    with patch("app.services.vision.analyze_image") as mock_analyze:
        state = {}
        ctrl._handle_file_message("555", _photo_msg(), state)
        mock_analyze.assert_not_called()
    assert len(kb_calls) == 1
    _cid, _txt, kb = kb_calls[0]
    flat_data = [b["callback_data"] for row in kb for b in row]
    assert "photo_act:analyze" in flat_data
    assert "photo_act:igpost" in flat_data
    assert "photo_act:animate" in flat_data
    assert "photo_act:none" in flat_data


def test_no_caption_no_session_saves_file_before_buttons(monkeypatch):
    _base_patches(monkeypatch)
    monkeypatch.setattr(ctrl, "send", lambda *a, **k: None)
    monkeypatch.setattr(ctrl, "send_with_keyboard", lambda *a, **k: None)
    state = {}
    ctrl._handle_file_message("555", _photo_msg(), state)
    assert state["last_uploaded_file"]["path"] == "/tmp/incoming/photo_UQ_x.jpg"


# ── Captioned photo (question) keeps prior Vision routing, untouched ────────

def test_captioned_question_photo_still_uses_vision_directly(monkeypatch):
    _base_patches(monkeypatch)
    monkeypatch.setattr(ctrl, "send", lambda *a, **k: None)
    sent_kb = []
    monkeypatch.setattr(ctrl, "send_with_keyboard", lambda *a, **k: sent_kb.append(1))
    with patch("app.services.vision.is_vision_supported", return_value=True), \
         patch("app.services.vision.analyze_image", return_value="кот") as mock_analyze:
        state = {}
        ctrl._handle_file_message("555", _photo_msg(caption="что за животное?"), state)
        mock_analyze.assert_called_once()
    assert not sent_kb    # captioned path never shows the no-caption button menu


# ── Active me_seed session keeps prior per-frame routing, untouched ─────────

def test_active_me_seed_session_not_offered_buttons(monkeypatch):
    _base_patches(monkeypatch, me_seed_consumes=True)
    kb_calls = []
    monkeypatch.setattr(ctrl, "send", lambda *a, **k: None)
    monkeypatch.setattr(ctrl, "send_with_keyboard", lambda *a, **k: kb_calls.append(1))
    with patch("app.services.vision.analyze_image") as mock_analyze:
        state = {}
        ctrl._handle_file_message("555", _photo_msg(), state)
        mock_analyze.assert_not_called()
    assert not kb_calls


# ── Active photo-studio (lora_collecting) session: same, untouched ─────────

def test_active_lora_collecting_session_not_offered_buttons(monkeypatch):
    _base_patches(monkeypatch, photo_studio_step="lora_collecting", photo_studio_consumes=True)
    kb_calls = []
    monkeypatch.setattr(ctrl, "send", lambda *a, **k: None)
    monkeypatch.setattr(ctrl, "send_with_keyboard", lambda *a, **k: kb_calls.append(1))
    with patch("app.services.vision.analyze_image") as mock_analyze:
        state = {}
        ctrl._handle_file_message("555", _photo_msg(), state)
        mock_analyze.assert_not_called()
    assert not kb_calls


# ── /vision_analyze is a PAID slash command, gated by the standard chokepoint ──

def test_vision_analyze_registered_paid_with_price():
    from tools import intent_router as ir
    assert ir.is_paid("/vision_analyze")
    assert ir.price_hint("/vision_analyze")


def test_vision_analyze_slash_command_gated_not_direct_exec(monkeypatch):
    fired = {"exec": 0, "confirm": 0}
    monkeypatch.setattr(ctrl, "save_state", lambda s: None)
    monkeypatch.setattr(ctrl, "send_with_keyboard", lambda *a, **k: fired.__setitem__("confirm", 1))
    monkeypatch.setattr(ctrl, "cmd_vision_analyze", lambda cid, st: fired.__setitem__("exec", 1))
    state = {}
    ctrl.handle_command("42", "/vision_analyze", "", state)
    assert fired["confirm"] == 1 and fired["exec"] == 0


def test_vision_analyze_slash_command_runs_after_confirm_token(monkeypatch):
    fired = {"exec": 0}
    monkeypatch.setattr(ctrl, "save_state", lambda s: None)
    monkeypatch.setattr(ctrl, "cmd_vision_analyze", lambda cid, st: fired.__setitem__("exec", 1))
    state = {"_paid_confirmed": "/vision_analyze"}
    ctrl.handle_command("42", "/vision_analyze", "", state)
    assert fired["exec"] == 1


# ── cmd_vision_analyze: reads last_uploaded_file, spends under guard_spend ──

def test_cmd_vision_analyze_no_file_sends_hint(monkeypatch):
    sent = []
    monkeypatch.setattr(ctrl, "send", lambda cid, txt, **k: sent.append(txt))
    with patch("app.services.vision.analyze_image") as mock_analyze:
        ctrl.cmd_vision_analyze("42", {})
        mock_analyze.assert_not_called()
    assert sent


def test_cmd_vision_analyze_spends_and_sends_result(monkeypatch, tmp_path):
    img = tmp_path / "p.jpg"
    img.write_bytes(b"fake")
    sent = []
    monkeypatch.setattr(ctrl, "send", lambda cid, txt, **k: sent.append(txt))
    with patch("app.services.auth.spend_guard.check_limit", return_value=(True, None)), \
         patch("app.services.audit.cost_tracker.record_cost") as mock_record, \
         patch("app.services.vision.is_vision_supported", return_value=True), \
         patch("app.services.vision.analyze_image", return_value="На фото кот.") as mock_analyze:
        state = {"last_uploaded_file": {"path": str(img)}}
        ctrl.cmd_vision_analyze("42", state)
        mock_analyze.assert_called_once_with(str(img))
        mock_record.assert_called_once()
    assert any("кот" in s for s in sent)


def test_cmd_vision_analyze_blocked_by_limit_does_not_call_vision(monkeypatch, tmp_path):
    img = tmp_path / "p.jpg"
    img.write_bytes(b"fake")
    sent = []
    monkeypatch.setattr(ctrl, "send", lambda cid, txt, **k: sent.append(txt))
    with patch("app.services.auth.spend_guard.check_limit", return_value=(False, "лимит исчерпан")), \
         patch("app.services.vision.is_vision_supported", return_value=True), \
         patch("app.services.vision.analyze_image") as mock_analyze:
        state = {"last_uploaded_file": {"path": str(img)}}
        ctrl.cmd_vision_analyze("42", state)
        mock_analyze.assert_not_called()
    assert any("лимит" in s for s in sent)


# ── photo_act: callback_query routing ────────────────────────────────────────

def _cq(data, chat_id=42, uid=42, message_id=5):
    return {"id": "cq1", "data": data, "from": {"id": uid},
            "message": {"chat": {"id": chat_id}, "message_id": message_id}}


def test_callback_photo_act_analyze_dispatches_to_vision_command(monkeypatch):
    monkeypatch.setattr(ctrl, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(ctrl, "_menu_role", lambda uid: "admin")
    monkeypatch.setattr(ctrl, "_is_admin_id", lambda uid: True)
    calls = []
    monkeypatch.setattr(ctrl, "handle_command",
                         lambda cid, cmd, q, st: calls.append((cmd, q)))
    state = {}
    ctrl.handle_callback_query(_cq("photo_act:analyze"), state)
    assert calls == [("/vision_analyze", "")]


def test_callback_photo_act_igpost_remembers_media_and_prompts_topic(monkeypatch):
    monkeypatch.setattr(ctrl, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(ctrl, "_menu_role", lambda uid: "admin")
    monkeypatch.setattr(ctrl, "_is_admin_id", lambda uid: True)
    sent = []
    monkeypatch.setattr(ctrl, "send", lambda cid, txt, **k: sent.append(txt))
    state = {"last_uploaded_file": {"path": "/tmp/incoming/photo_UQ_x.jpg"}}
    ctrl.handle_callback_query(_cq("photo_act:igpost"), state)
    assert ctrl._LAST_IG_MEDIA.get("42") == "/tmp/incoming/photo_UQ_x.jpg"
    assert any("ig_post" in s for s in sent)


def test_callback_photo_act_animate_arms_pending_and_shows_engine_menu(monkeypatch):
    monkeypatch.setattr(ctrl, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(ctrl, "_menu_role", lambda uid: "admin")
    monkeypatch.setattr(ctrl, "_is_admin_id", lambda uid: True)
    monkeypatch.setattr(ctrl, "_swapbatch_get_handler", lambda: (None, None))
    kb_calls = []
    monkeypatch.setattr(ctrl, "send_with_keyboard",
                         lambda cid, txt, kb: kb_calls.append((txt, kb)))
    state = {"last_uploaded_file": {"path": "/tmp/incoming/photo_UQ_x.jpg"}}
    ctrl.handle_callback_query(_cq("photo_act:animate"), state)
    assert ctrl._ANIMATE_PENDING[42]["photo"] == "/tmp/incoming/photo_UQ_x.jpg"
    assert len(kb_calls) == 1


def test_callback_photo_act_none_is_a_noop(monkeypatch):
    acked = []
    monkeypatch.setattr(ctrl, "answer_callback_query", lambda cid, txt="": acked.append(txt))
    monkeypatch.setattr(ctrl, "_menu_role", lambda uid: "admin")
    monkeypatch.setattr(ctrl, "_is_admin_id", lambda uid: True)
    sent = []
    monkeypatch.setattr(ctrl, "send", lambda *a, **k: sent.append(1))
    monkeypatch.setattr(ctrl, "send_with_keyboard", lambda *a, **k: sent.append(1))
    state = {}
    ctrl.handle_callback_query(_cq("photo_act:none"), state)
    assert not sent
    assert acked
