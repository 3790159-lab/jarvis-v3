# -*- coding: utf-8 -*-
"""Integration: ``process_update`` emits audit events.

Same module-load pattern as ``tests/test_bot_whitelist_integration.py``.
``audit_logger.audit_event`` is patched to a spy; the real file write and
forward are bypassed.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _get_mod():
    mod_name = f"_test_audit_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _text_update(user_id: int, chat_id: int, text: str, username: str | None = "tester") -> dict:
    msg = {
        "from": {"id": user_id},
        "chat": {"id": chat_id},
        "text": text,
    }
    if username is not None:
        msg["from"]["username"] = username
    return {"update_id": 1, "message": msg}


# ── whitelist rejection ──────────────────────────────────────────────────────


def test_audit_records_whitelist_rejection(monkeypatch):
    """A blocked user fires a ``whitelist_rejected`` audit event."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "222")
    mod = _get_mod()

    events: list[tuple[int, str, str]] = []

    def _spy(user_id, username, chat_id, event, details=None, **kw):
        events.append((user_id, event, str(chat_id)))

    upd = _text_update(user_id=999, chat_id=999, text="hi", username="evil")
    with patch.object(mod._audit, "audit_event", _spy), \
         patch.object(mod, "send", lambda cid, txt: None), \
         patch.object(mod, "handle", lambda cid, txt: None):
        mod.process_update(upd)

    assert any(e[1] == "whitelist_rejected" and e[0] == 999 for e in events)


# ── command event ───────────────────────────────────────────────────────────


def test_audit_records_command_event(monkeypatch):
    """An allowed user sending /start emits a generic ``command`` event."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "111")
    mod = _get_mod()

    events: list[tuple[int, str, dict]] = []

    def _spy(user_id, username, chat_id, event, details=None, **kw):
        events.append((user_id, event, details or {}))

    upd = _text_update(user_id=111, chat_id=111, text="/start hello", username="admin")
    with patch.object(mod._audit, "audit_event", _spy), \
         patch.object(mod, "send", lambda cid, txt: None), \
         patch.object(mod, "handle", lambda cid, txt: None):
        mod.process_update(upd)

    cmd_events = [e for e in events if e[1] == "command"]
    assert cmd_events, f"expected a command event, got {events}"
    uid, _, details = cmd_events[0]
    assert uid == 111
    assert details.get("command") == "/start"
    assert details.get("args") == "hello"


def test_audit_records_swapbatch_go_as_dedicated_event(monkeypatch):
    """``/swapbatch_go`` maps to the ``swapbatch_go`` audit event, not generic command.

    Lets the admin forward template show "🚀 Swap started: …" instead of a
    plain command echo.
    """
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "111")
    mod = _get_mod()

    events: list[str] = []

    def _spy(user_id, username, chat_id, event, details=None, **kw):
        events.append(event)

    upd = _text_update(user_id=111, chat_id=111, text="/swapbatch_go")
    with patch.object(mod._audit, "audit_event", _spy), \
         patch.object(mod, "send", lambda cid, txt: None), \
         patch.object(mod, "handle", lambda cid, txt: None):
        mod.process_update(upd)

    assert "swapbatch_go" in events
    assert "command" not in events  # specific event replaces generic


def test_audit_records_animate_started_with_mode(monkeypatch):
    """``/swapbatch_animate_yes`` → ``animate_started`` with mode=yes."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "111")
    mod = _get_mod()

    captured: list[tuple[str, dict]] = []

    def _spy(user_id, username, chat_id, event, details=None, **kw):
        captured.append((event, details or {}))

    upd = _text_update(user_id=111, chat_id=111, text="/swapbatch_animate_yes")
    with patch.object(mod._audit, "audit_event", _spy), \
         patch.object(mod, "send", lambda cid, txt: None), \
         patch.object(mod, "handle", lambda cid, txt: None):
        mod.process_update(upd)

    animate_events = [(e, d) for e, d in captured if e == "animate_started"]
    assert animate_events, f"expected animate_started, got {captured}"
    assert animate_events[0][1].get("mode") == "yes"


def test_audit_skips_non_command_text(monkeypatch):
    """Plain (non-slash) text does NOT fire a command event.

    Keeps the audit log focused on intentional command invocations rather
    than every conversational turn.
    """
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "111")
    mod = _get_mod()

    captured: list[str] = []

    def _spy(user_id, username, chat_id, event, details=None, **kw):
        captured.append(event)

    upd = _text_update(user_id=111, chat_id=111, text="just a chat message")
    with patch.object(mod._audit, "audit_event", _spy), \
         patch.object(mod, "send", lambda cid, txt: None), \
         patch.object(mod, "handle", lambda cid, txt: None):
        mod.process_update(upd)

    assert "command" not in captured
    assert "swapbatch_go" not in captured
