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


# ── e2e: /suggest_tasks → pending confirm → tap → real dispatch (bug repro) ──
# Reported symptom: money-confirm shows correctly, but tapping [▶️ Запустить]
# replies "Не знаю такую команду" — as if the confirm callback never reaches
# handle_command's "/suggest_tasks" dispatch branch. Unlike the unit tests
# above (which stub handle_command or _suggest_tasks_dispatch away), this test
# drives the REAL handle_command + handle_callback_query + _handle_confirm_run
# + _suggest_tasks_dispatch chain end-to-end — only the network (send/
# send_with_keyboard/answer_callback_query), guard_spend and the LLM call are
# mocked (money-safety: zero real spend, zero real Anthropic calls).
def _mock_llm_and_money(monkeypatch, llm_reply):
    calls = {"llm": 0}

    def _fake_llm(system, messages):
        calls["llm"] += 1
        return llm_reply

    monkeypatch.setattr(mod, "guard_spend", lambda uid, uname, est, do: (do(), None))
    monkeypatch.setattr(mod, "_suggest_tasks_ask_llm", _fake_llm)
    monkeypatch.setattr(mod, "_regress_baseline", lambda: None)
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    return calls


def test_suggest_tasks_confirm_tap_reaches_real_dispatch_e2e(monkeypatch):
    calls = _mock_llm_and_money(
        monkeypatch,
        '[{"title": "T1", "signal": "s", "rationale": "r", "draft": "d1", "size": "S"}]',
    )
    sent, kb_sent, acked = [], [], []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, t, kb, *a, **k: kb_sent.append((t, kb)))
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: acked.append(a))

    state = {}
    # Step 1: admin types /suggest_tasks — money-confirm must show, NOT dispatch.
    mod.handle_command(ADMIN, "/suggest_tasks", "", state)
    assert calls["llm"] == 0
    assert len(kb_sent) == 1
    assert "/suggest_tasks" in kb_sent[0][0]
    assert any(btn.get("callback_data") == "confirm:run"
               for row in kb_sent[0][1] for btn in row)
    assert state["pending_confirm"]["cmd"] == "/suggest_tasks"

    # Step 2: admin taps [▶️ Запустить] — a real confirm:run callback_query.
    cq = {
        "id": "cq1", "data": "confirm:run",
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 55},
        "from": {"id": int(ADMIN)},
    }
    mod.handle_callback_query(cq, state)

    # The generator must actually run (LLM mock called exactly once) and the
    # top-3 suggestions must reach the chat — NOT "Не знаю такую команду".
    assert calls["llm"] == 1
    assert len(sent) == 1
    assert "не знаю" not in sent[0].lower()
    assert "T1" in sent[0]
    assert "/dev_task" in sent[0]
    assert not state.get("pending_confirm")


def test_suggest_tasks_tap_shows_honest_error_when_llm_raises_e2e(monkeypatch):
    """Live path (D2): the confirm:run tap reaches the real dispatch, but the paid
    LLM call raises (Anthropic 400 'credit balance too low', as seen in the prod
    log). guard_spend does NOT wrap do_spend, so the exception used to propagate
    out of the whole callback and the user saw NOTHING (silent), then re-tapped.
    After the fix the user must get an honest '$0 / недоступен' message and the
    handler must NOT raise — NOT silence, NOT 'Не знаю такую команду'."""
    def _raising_llm(system, messages):
        raise RuntimeError("Error code: 400 - Your credit balance is too low")

    # real-guard-like passthrough: do_spend() runs and its exception propagates,
    # exactly as the production guard_spend (spend_guard.py) does.
    monkeypatch.setattr(mod, "guard_spend", lambda uid, uname, est, do: (do(), None))
    monkeypatch.setattr(mod, "_suggest_tasks_ask_llm", _raising_llm)
    monkeypatch.setattr(mod, "_regress_baseline", lambda: None)
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)

    state = {"pending_confirm": {"cmd": "/suggest_tasks",
                                 "resume": {"kind": "cmd", "cmd": "/suggest_tasks", "query": ""}}}
    cq = {
        "id": "cq1", "data": "confirm:run",
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 55},
        "from": {"id": int(ADMIN)},
    }
    # Must NOT raise out of the handler (the whole point of D2).
    mod.handle_callback_query(cq, state)

    assert len(sent) == 1
    assert "не знаю" not in sent[0].lower()
    assert ("$0" in sent[0]) or ("недоступ" in sent[0].lower())


def test_suggest_tasks_stale_confirm_tap_is_not_unknown_command_e2e(monkeypatch):
    """Live path (D1): a SECOND tap on an already-consumed confirm button, i.e.
    pending_confirm is None (the first tap cleared it at _handle_confirm_run:4341
    before the paid call). The old code re-dispatched handle_command with an EMPTY
    cmd → fall-through 'Не знаю такую команду' (line 8072). After the fix the user
    must get a 'кнопка устарела' hint and NOTHING must be dispatched."""
    dispatched = {"n": 0}
    monkeypatch.setattr(mod, "_suggest_tasks_dispatch",
                        lambda cid: dispatched.__setitem__("n", dispatched["n"] + 1))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)

    state = {"pending_confirm": None}      # already consumed by the prior tap
    cq = {
        "id": "cq2", "data": "confirm:run",
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 55},
        "from": {"id": int(ADMIN)},
    }
    mod.handle_callback_query(cq, state)

    assert dispatched["n"] == 0                       # empty resume must NOT dispatch
    assert len(sent) == 1
    assert "не знаю" not in sent[0].lower()
    assert "устарел" in sent[0].lower()


def test_suggest_tasks_direct_call_with_token_bypasses_confirm_e2e(monkeypatch):
    """Direct invocation carrying the one-shot confirmed token (e.g. the same
    re-dispatch _handle_confirm_run performs) must reach the real generator
    without showing a second confirm — no UI round-trip required."""
    calls = _mock_llm_and_money(
        monkeypatch,
        '[{"title": "T2", "signal": "s", "rationale": "r", "draft": "d2", "size": "M"}]',
    )
    sent, kb_sent = [], []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, t, kb, *a, **k: kb_sent.append((t, kb)))

    state = {"_paid_confirmed": "/suggest_tasks"}
    mod.handle_command(ADMIN, "/suggest_tasks", "", state)

    assert kb_sent == []               # no second confirm prompt
    assert calls["llm"] == 1
    assert len(sent) == 1
    assert "T2" in sent[0]
