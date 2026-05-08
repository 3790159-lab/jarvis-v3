from __future__ import annotations

"""Phase 11: Telegram UX Polish tests.

Verifies:
1. edit_message calls tg_call with correct payload
2. send_and_get_id returns message_id from Telegram response
3. /cancel clears pending state
4. /help returns extended text with categories
5. identity intent sends inline keyboard reply_markup
6. send() accepts reply_markup kwarg
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import tools.jarvis_smart_telegram_control as mod


def _make_state(**kw):
    s = mod.default_state()
    s.update(kw)
    return s


# ---------------------------------------------------------------------------
# edit_message
# ---------------------------------------------------------------------------

def test_edit_message_calls_editMessageText(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "tg_call", lambda method, payload: calls.append((method, payload)) or {})

    mod.edit_message("123", 42, "Updated text")

    assert len(calls) == 1
    method, payload = calls[0]
    assert method == "editMessageText"
    assert payload["chat_id"] == "123"
    assert payload["message_id"] == 42
    assert payload["text"] == "Updated text"


def test_edit_message_noop_when_message_id_none(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "tg_call", lambda method, payload: calls.append((method, payload)) or {})

    mod.edit_message("123", None, "text")

    assert len(calls) == 0


def test_edit_message_truncates_long_text(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "tg_call", lambda method, payload: calls.append((method, payload)) or {})

    mod.edit_message("123", 1, "x" * 5000)

    assert len(calls[0][1]["text"]) <= 3920


# ---------------------------------------------------------------------------
# send_and_get_id
# ---------------------------------------------------------------------------

def test_send_and_get_id_returns_message_id(monkeypatch):
    monkeypatch.setattr(mod, "tg_call", lambda method, payload: {"result": {"message_id": 99}})

    msg_id = mod.send_and_get_id("123", "hello")

    assert msg_id == 99


def test_send_and_get_id_returns_none_on_missing(monkeypatch):
    monkeypatch.setattr(mod, "tg_call", lambda method, payload: {"ok": False})

    msg_id = mod.send_and_get_id("123", "hello")

    assert msg_id is None


# ---------------------------------------------------------------------------
# /cancel command
# ---------------------------------------------------------------------------

def test_cancel_clears_pending(monkeypatch):
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    monkeypatch.setattr(mod, "save_state", lambda s: None)

    state = _make_state(pending_task_id="job_123", pending={"type": "table"})
    mod.handle_command("123", "/cancel", "", state)

    assert state["pending"] is None
    assert state["pending_task_id"] is None
    assert any("❌" in m or "отменена" in m for m in sent)


def test_cancel_when_no_pending(monkeypatch):
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))
    monkeypatch.setattr(mod, "save_state", lambda s: None)

    state = _make_state()
    mod.handle_command("123", "/cancel", "", state)

    assert len(sent) == 1
    assert "нет" in sent[0].lower() or "cancel" in sent[0].lower() or "активн" in sent[0].lower()


# ---------------------------------------------------------------------------
# /help extended
# ---------------------------------------------------------------------------

def test_help_contains_research_section(monkeypatch):
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))

    mod.handle_command("123", "/help", "", _make_state())

    text = sent[0]
    assert "/research" in text
    assert "/table" in text
    assert "/engineer" in text
    assert "/gen" in text
    assert "/cancel" in text


def test_help_contains_mode_info(monkeypatch):
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))

    mod.handle_command("123", "/smart_help", "", _make_state())

    assert any("/mode" in m for m in sent)


# ---------------------------------------------------------------------------
# Identity intent sends inline keyboard
# ---------------------------------------------------------------------------

def test_identity_sends_inline_keyboard(monkeypatch):
    sent_payloads = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, reply_markup=None, **kw:
        sent_payloads.append({"text": txt, "reply_markup": reply_markup}))

    mod.run_intent("123", {"intent": "identity", "query": ""}, _make_state())

    assert len(sent_payloads) == 1
    markup = sent_payloads[0]["reply_markup"]
    assert markup is not None
    assert "inline_keyboard" in markup
    buttons = [btn["text"] for row in markup["inline_keyboard"] for btn in row]
    assert any("умеешь" in b or "Что" in b for b in buttons)


# ---------------------------------------------------------------------------
# send() accepts reply_markup
# ---------------------------------------------------------------------------

def test_send_passes_reply_markup_to_tg_call(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "tg_call", lambda method, payload: calls.append(payload) or {})

    keyboard = {"inline_keyboard": [[{"text": "OK", "callback_data": "ok"}]]}
    mod.send("123", "hello", reply_markup=keyboard)

    assert "reply_markup" in calls[0]
    assert calls[0]["reply_markup"] == keyboard


def test_send_without_markup_no_reply_markup_key(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "tg_call", lambda method, payload: calls.append(payload) or {})

    mod.send("123", "hello")

    assert "reply_markup" not in calls[0]
