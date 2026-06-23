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


def _cb(uid, chat, data):
    return {"id": "cq1", "data": data,
            "from": {"id": uid, "username": "admin"},
            "message": {"message_id": 7, "chat": {"id": chat}}}


def test_admin_approve_adds_friend(friend_env):
    mod = _get_mod()
    from app.services.auth import users_store
    users_store.add_pending(999, "newguy")
    sent = []
    with patch.object(mod, "send", lambda c, t, **k: sent.append((str(c), t))), \
         patch.object(mod, "answer_callback_query", lambda *a, **k: None):
        mod.handle_callback_query(_cb(111, 111, "access:approve:999"), {})
    assert users_store.get_role(999) == "friend"
    assert users_store.get_limit(999) == 5.0
    assert any(str(c) == "999" for c, _ in sent)  # друг уведомлён


def test_admin_reject_blocks(friend_env):
    mod = _get_mod()
    from app.services.auth import users_store
    users_store.add_pending(999, "newguy")
    with patch.object(mod, "send", lambda c, t, **k: None), \
         patch.object(mod, "answer_callback_query", lambda *a, **k: None):
        mod.handle_callback_query(_cb(111, 111, "access:reject:999"), {})
    assert users_store.get_role(999) is None
    assert users_store.pop_pending(999) is None  # вынут из pending


def test_friend_cannot_use_access_callback(friend_env):
    mod = _get_mod()
    from app.services.auth import users_store
    users_store.add_pending(999, "newguy")
    answered = []
    with patch.object(mod, "send", lambda c, t, **k: None), \
         patch.object(mod, "answer_callback_query",
                      lambda cid, txt="": answered.append(txt)):
        # friend (555) пытается сам себя одобрить
        mod.handle_callback_query(_cb(555, 555, "access:approve:999"), {})
    assert users_store.get_role(999) is None  # не сработало
    assert any("админ" in a.lower() for a in answered)


def test_single_animate_blocked_over_limit_before_spend(friend_env, monkeypatch, tmp_path):
    mod = _get_mod()
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    from app.services.audit import cost_tracker as ct
    from app.services.auth import users_store
    users_store.set_limit(555, 0.10)          # tiny limit
    ct.record_cost(555, "petya", 0.10)        # already at limit
    mod._ANIMATE_PENDING[555] = {"photo": "x.jpg"}
    sent = []

    def _boom():
        raise AssertionError("must not reach video lock / generation")

    with patch.object(mod, "send", lambda c, t, **k: sent.append(t)), \
         patch.object(mod, "_get_video_lock", _boom):
        mod._animate_run_single("555", "spicy")
    # blocked with soft limit message; generation never started
    assert any("лимит" in t.lower() for t in sent)


def test_blocked_user_silently_dropped(friend_env):
    mod = _get_mod()
    from app.services.auth import users_store
    users_store.add_friend(999, "newguy", added_by="111")
    users_store.set_status(999, "blocked")
    sent = []
    with patch.object(mod, "send",
                      lambda c, t, reply_markup=None: sent.append((str(c), t))):
        mod.process_update(_text_update(999, 999, "привет"))
    from app.services.auth.whitelist import REJECT_MESSAGE
    # blocked user: НЕ получает REJECT_MESSAGE, админ НЕ пингуется
    assert not any(REJECT_MESSAGE in t for _, t in sent)
    assert not any(str(c) == "111" for c, _ in sent)


def test_single_animate_records_cost(friend_env, monkeypatch, tmp_path):
    """Gap (a): a successful standalone /animate must record per-user cost."""
    mod = _get_mod()
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    from app.services.audit import cost_tracker as ct

    mod._ANIMATE_PENDING[555] = {"photo": "x.jpg"}
    mod._USERNAME_BY_CHAT["555"] = "petya"
    monkeypatch.setattr(mod, "_send_local_video", lambda *a, **k: None)

    # Generation is invoked inside a worker thread; stub the network boundary
    # at the SOURCE modules (the function imports them locally).
    import app.services.block_m2_video.batch_animate as ba

    async def _ok(engine, reqs, concurrency=1):
        return ["out.mp4"]

    monkeypatch.setattr(ba, "animate_batch", _ok)

    class _Eng:
        ...

    class _Router:
        async def select(self, mode):
            return _Eng()

    import app.services.block_m2_video.engines.router as router_mod
    monkeypatch.setattr(router_mod, "EngineRouter", lambda: _Router())

    handler = type("H", (), {
        "build_single_animate_request": staticmethod(lambda *a, **k: object()),
    })()
    monkeypatch.setattr(mod, "_swapbatch_get_handler", lambda: (handler, None))

    class _Lock:
        def acquire(self, c):
            return "tok"

        def release(self, t):
            pass

    monkeypatch.setattr(mod, "_get_video_lock", lambda: _Lock())

    with patch.object(mod, "send", lambda c, t, **k: None):
        mod._animate_run_single("555", "spicy")
        import time
        time.sleep(0.5)  # worker thread completes

    assert ct.get_user_stats(555)["today"] > 0.0


def _admin_text(text):
    return {"update_id": 1, "message": {
        "from": {"id": 111, "username": "daniil"},
        "chat": {"id": 111}, "text": text}}


def test_admin_users_lists_members(friend_env):
    mod = _get_mod()
    sent = []
    with patch.object(mod, "send", lambda c, t, **k: sent.append(t)):
        consumed = mod._admin_command_intercept(_admin_text("/admin_users"))
    assert consumed is True
    assert any("555" in t for t in sent)  # friend petya listed


def test_admin_setlimit_changes_limit(friend_env):
    mod = _get_mod()
    from app.services.auth import users_store
    with patch.object(mod, "send", lambda c, t, **k: None):
        mod._admin_command_intercept(_admin_text("/admin_setlimit 555 12"))
    assert users_store.get_limit(555) == 12.0


def test_admin_resetlimit_forgives_today(friend_env, monkeypatch, tmp_path):
    mod = _get_mod()
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    from app.services.audit import cost_tracker as ct
    from app.services.auth import users_store
    ct.record_cost(555, "petya", 4.0)
    with patch.object(mod, "send", lambda c, t, **k: None):
        mod._admin_command_intercept(_admin_text("/admin_resetlimit 555"))
    assert users_store.effective_spent(555, spent_today=4.0) == 0.0


def test_friend_cannot_run_admin_command(friend_env):
    mod = _get_mod()
    sent = []
    upd = {"update_id": 1, "message": {
        "from": {"id": 555, "username": "petya"},
        "chat": {"id": 555}, "text": "/admin_users"}}
    with patch.object(mod, "send", lambda c, t, **k: sent.append(t)):
        consumed = mod._admin_command_intercept(upd)
    assert consumed is True
    assert any("админ" in t.lower() for t in sent)  # refusal, not the list


def test_persona_video_dispatch_records_cost(friend_env, monkeypatch, tmp_path):
    """Gap: /persona_video → PersonaVideoHandler had NO cost tracking at all."""
    mod = _get_mod()
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    from app.services.audit import cost_tracker as ct

    mod._USERNAME_BY_CHAT["555"] = "petya"
    monkeypatch.setattr(mod, "_send_local_video", lambda *a, **k: None)

    class _Lock:
        def acquire(self, c):
            return "tok"

        def release(self, t):
            pass

    monkeypatch.setattr(mod, "_get_video_lock", lambda: _Lock())

    class _Handler:
        async def handle_video(self, text, chat_id, progress_cb=None,
                               input_photo_path=None):
            return {"output_path": "out.mp4", "cost_usd": 0.28,
                    "summary": "ok"}

    import app.handlers.persona_video_handler as pvh
    monkeypatch.setattr(pvh, "PersonaVideoHandler", lambda: _Handler())

    with patch.object(mod, "send", lambda c, t, **k: None):
        mod._persona_video_dispatch("555", "p1 dance")
        import time
        time.sleep(0.5)  # worker thread completes

    assert ct.get_user_stats(555)["today"] == pytest.approx(0.28)
