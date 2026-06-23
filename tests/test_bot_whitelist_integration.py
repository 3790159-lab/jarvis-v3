# -*- coding: utf-8 -*-
"""Integration: ``process_update`` enforces :mod:`app.services.auth.whitelist`.

Pattern adapted from ``tests/test_webhook_mode.py``: load the bot module
fresh by path, patch ``send`` / ``handle`` / ``handle_callback_query`` to
spies, then drive ``process_update`` with synthetic Telegram updates.

The bot's polling loop in ``_main_inner`` is exercised live (real
``getUpdates`` HTTP) — out of scope for unit tests. The shared
``process_update`` chokepoint is, and is the one used by webhook mode.
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
    mod_name = f"_test_wl_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _text_update(user_id: int, chat_id: int, text: str = "hello") -> dict:
    return {
        "update_id": 1,
        "message": {
            "from": {"id": user_id, "username": "tester"},
            "chat": {"id": chat_id},
            "text": text,
        },
    }


def _callback_update(user_id: int, chat_id: int, data: str = "feedback:positive:dec1") -> dict:
    return {
        "update_id": 2,
        "callback_query": {
            "id": "cq1",
            "data": data,
            "from": {"id": user_id, "username": "tester"},
            "message": {"message_id": 1, "chat": {"id": chat_id}},
        },
    }


# ── reject path ──────────────────────────────────────────────────────────────


def test_bot_rejects_non_whitelisted_user(monkeypatch):
    """A user_id outside both admin and allowed list gets the polite reject
    text and never reaches the message handler.
    """
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "222,333")
    mod = _get_mod()
    from app.services.auth.whitelist import REJECT_MESSAGE

    sent: list[tuple[str, str]] = []
    handled: list[str] = []
    upd = _text_update(user_id=999, chat_id=999, text="hi")
    with patch.object(mod, "send", lambda cid, txt: sent.append((str(cid), txt))), \
         patch.object(mod, "handle", lambda cid, txt: handled.append(txt)):
        mod.process_update(upd)

    assert handled == []  # never reached the dispatcher
    assert any(REJECT_MESSAGE in t for _, t in sent), (
        f"expected REJECT_MESSAGE in sends, got: {sent}"
    )


def test_bot_rejects_non_whitelisted_callback_user(monkeypatch):
    """Callback queries from non-allowed users are rejected too — not just
    text messages. Guards against the inline-keyboard bypass.
    """
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "222,333")
    mod = _get_mod()
    from app.services.auth.whitelist import REJECT_MESSAGE

    sent: list[tuple[str, str]] = []
    handled: list[str] = []
    upd = _callback_update(user_id=999, chat_id=999)
    with patch.object(mod, "send", lambda cid, txt: sent.append((str(cid), txt))), \
         patch.object(mod, "handle_callback_query", lambda cq, st: handled.append(cq["data"])), \
         patch.object(mod, "load_state", lambda: {}):
        mod.process_update(upd)

    assert handled == []
    assert any(REJECT_MESSAGE in t for _, t in sent)


# ── allow path ───────────────────────────────────────────────────────────────


def test_bot_allows_admin(monkeypatch):
    """The admin user_id bypasses the whitelist and reaches the handler.

    chat_id matches ``TELEGRAM_ALLOWED_CHAT_ID`` here so the legacy
    chat-id gate inside ``handle()`` doesn't bounce the message back.
    """
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", "")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "111")
    mod = _get_mod()

    handled: list[str] = []
    sent: list[str] = []
    upd = _text_update(user_id=111, chat_id=111, text="привет")
    with patch.object(mod, "send", lambda cid, txt: sent.append(txt)), \
         patch.object(mod, "handle", lambda cid, txt: handled.append(txt)):
        mod.process_update(upd)

    assert handled == ["привет"]


def test_bot_open_mode_allows_anyone(monkeypatch, tmp_path):
    """When neither env var is set, dispatch keeps its existing behaviour."""
    monkeypatch.delenv("JARVIS_ADMIN_USER_ID", raising=False)
    monkeypatch.delenv("JARVIS_ALLOWED_USER_IDS", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "555")
    monkeypatch.setenv("JARVIS_USERS_FILE", str(tmp_path / "users.json"))
    mod = _get_mod()

    handled: list[str] = []
    upd = _text_update(user_id=555, chat_id=555, text="open")
    with patch.object(mod, "send", lambda cid, txt: None), \
         patch.object(mod, "handle", lambda cid, txt: handled.append(txt)):
        mod.process_update(upd)

    assert handled == ["open"]
