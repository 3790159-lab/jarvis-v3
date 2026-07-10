# -*- coding: utf-8 -*-
"""/suggest_tasks bot wiring (control module). $0, mocks only, no real Anthropic call.

Covers: admin-only-by-omission, the LLM call isolated behind guard_spend (money-
safety mirror of IR-2's _ir2_ask_haiku), honest fallback on guard_spend block,
honest fallback on unparseable LLM reply, and the happy path sending a
formatted top-3.
"""
import importlib

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


def test_suggest_tasks_is_admin_only_not_friend():
    assert "/suggest_tasks" not in mod.FRIEND_ALLOWED_COMMANDS


def test_suggest_tasks_is_paid():
    from tools import intent_router as _ir
    assert _ir.is_paid("/suggest_tasks") is True


def test_suggest_tasks_dispatch_calls_llm_under_guard_spend(monkeypatch):
    calls = {"llm": 0, "guard_spend_args": None}

    def _fake_guard_spend(uid, uname, est, do):
        calls["guard_spend_args"] = (uid, uname, est)
        return do(), None

    def _fake_llm(system, messages):
        calls["llm"] += 1
        assert "JSON" in system
        return '[{"title": "T", "signal": "s", "rationale": "r", "draft": "d", "size": "M"}]'

    monkeypatch.setattr(mod, "guard_spend", _fake_guard_spend)
    monkeypatch.setattr(mod, "_suggest_tasks_ask_llm", _fake_llm)
    monkeypatch.setattr(mod, "_regress_baseline", lambda: {"failed": 1, "passed": 2, "errors": 0})
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))

    mod._suggest_tasks_dispatch(ADMIN)

    assert calls["llm"] == 1
    assert calls["guard_spend_args"][0] == ADMIN
    assert "T" in sent["t"]
    assert "/dev_task" in sent["t"]  # nudges admin to copy the draft, not auto-run it


def test_suggest_tasks_dispatch_honest_fallback_when_gate_blocks(monkeypatch):
    seen = {"llm": 0}

    def _blocking_guard_spend(uid, uname, est, do):
        return None, "🚫 лимит исчерпан"

    def _fake_llm(system, messages):
        seen["llm"] += 1
        return "[]"

    monkeypatch.setattr(mod, "guard_spend", _blocking_guard_spend)
    monkeypatch.setattr(mod, "_suggest_tasks_ask_llm", _fake_llm)
    monkeypatch.setattr(mod, "_regress_baseline", lambda: None)
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))

    mod._suggest_tasks_dispatch(ADMIN)

    assert seen["llm"] == 0                     # do_spend never ran -> $0
    assert "лимит" in sent["t"]


def test_suggest_tasks_dispatch_honest_fallback_on_garbage_reply(monkeypatch):
    monkeypatch.setattr(mod, "guard_spend", lambda uid, uname, est, do: (do(), None))
    monkeypatch.setattr(mod, "_suggest_tasks_ask_llm", lambda s, m: "not json")
    monkeypatch.setattr(mod, "_regress_baseline", lambda: None)
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))

    mod._suggest_tasks_dispatch(ADMIN)

    assert "не удалось" in sent["t"].lower()


def test_suggest_tasks_command_dispatches(monkeypatch):
    fired = {}
    monkeypatch.setattr(mod, "_suggest_tasks_dispatch", lambda cid: fired.setdefault("cid", cid))
    state = {"_paid_confirmed": "/suggest_tasks"}
    mod.handle_command(ADMIN, "/suggest_tasks", "", state)
    assert fired["cid"] == ADMIN


def test_suggest_tasks_command_gated_by_money_gate_without_token(monkeypatch):
    fired = {"dispatch": 0, "confirm": 0}
    monkeypatch.setattr(mod, "_suggest_tasks_dispatch", lambda cid: fired.__setitem__("dispatch", 1))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: fired.__setitem__("confirm", 1))
    state = {}
    mod.handle_command(ADMIN, "/suggest_tasks", "", state)
    assert fired["dispatch"] == 0 and fired["confirm"] == 1
