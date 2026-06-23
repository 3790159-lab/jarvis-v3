# tests/test_friend_access_integration.py
# -*- coding: utf-8 -*-
"""Friend-access: process_update enforces roles (isolation, generative allow)."""
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
    name = f"_test_fa_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _text_update(user_id: int, chat_id: int, text: str) -> dict:
    return {"update_id": 1, "message": {
        "from": {"id": user_id, "username": "petya"},
        "chat": {"id": chat_id}, "text": text}}


@pytest.fixture
def friend_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "111")
    monkeypatch.delenv("JARVIS_ALLOWED_USER_IDS", raising=False)
    from app.services.auth import users_store
    users_store.add_friend(555, "petya", added_by="111")
    return tmp_path


def test_friend_blocked_from_admin_command(friend_env):
    mod = _get_mod()
    sent, handled = [], []
    with patch.object(mod, "send", lambda c, t, **k: sent.append(t)), \
         patch.object(mod, "handle_command", lambda *a, **k: handled.append(a)):
        mod.handle("555", "/restart_bot")
    assert handled == []  # admin command never dispatched for friend
    assert any("админ" in t.lower() or "access" in t.lower() for t in sent)


def test_friend_allowed_generative_command(friend_env):
    mod = _get_mod()
    handled = []
    # classify_message returns {"intent": "command", "command": "<cmd>", "query": "<rest>"}
    # (real shape: `command` is a plain string), and handle() dispatches via
    # handle_command(chat_id, pack["command"], pack["query"], state).
    with patch.object(mod, "send", lambda c, t, **k: None), \
         patch.object(mod, "handle_command",
                      lambda cid, cmd, q, st: handled.append(cmd)), \
         patch.object(mod, "classify_message",
                      lambda text, st: {"intent": "command",
                                        "command": "/swapbatch_source", "query": ""}):
        mod.handle("555", "/swapbatch_source")
    assert handled == ["/swapbatch_source"]


def test_stranger_creates_pending_and_pings_admin(friend_env):
    mod = _get_mod()
    sent = []  # (chat_id, text, reply_markup)
    upd = _text_update(999, 999, "привет")
    with patch.object(mod, "send",
                      lambda c, t, reply_markup=None: sent.append((str(c), t, reply_markup))):
        mod.process_update(upd)
    from app.services.auth.whitelist import REJECT_MESSAGE
    from app.services.auth import users_store
    # вежливый отказ юзеру
    assert any(str(c) == "999" and REJECT_MESSAGE in t for c, t, _ in sent)
    # уведомление АДМИНУ (111) с inline-кнопками approve/reject
    admin_msgs = [(c, t, kb) for c, t, kb in sent if c == "111" and kb]
    assert admin_msgs, "admin not pinged with buttons"
    flat = [b["callback_data"] for row in admin_msgs[0][2]["inline_keyboard"] for b in row]
    assert "access:approve:999" in flat and "access:reject:999" in flat
    # pending записан
    assert any(p["user_id"] == "999" for p in users_store.list_pending())


def test_stranger_repeat_does_not_respam_admin(friend_env):
    mod = _get_mod()
    sent = []
    with patch.object(mod, "send",
                      lambda c, t, reply_markup=None: sent.append((str(c), t, reply_markup))):
        mod.process_update(_text_update(999, 999, "1"))
        mod.process_update(_text_update(999, 999, "2"))
    admin_button_pings = [1 for c, t, kb in sent if c == "111" and kb]
    assert len(admin_button_pings) == 1  # только первый запрос пингует админа


def test_admin_runs_everything(friend_env):
    mod = _get_mod()
    handled = []
    with patch.object(mod, "send", lambda c, t, **k: None), \
         patch.object(mod, "handle_command",
                      lambda cid, cmd, q, st: handled.append(cmd)), \
         patch.object(mod, "classify_message",
                      lambda text, st: {"intent": "command",
                                        "command": "/restart_bot", "query": ""}):
        mod.handle("111", "/restart_bot")
    assert handled == ["/restart_bot"]
