# -*- coding: utf-8 -*-
"""/ig_caption bot wiring (control module). $0, mocks only, no real Anthropic call.

Covers: paid-registry membership, admin-only-by-omission, the LLM call
isolated behind guard_spend (money-safety mirror of /suggest_tasks), honest
fallback on missing topic / guard_spend block / empty LLM reply, and the
happy path sending a generated caption. Money-confirm itself is exercised by
the shared ``handle_command`` chokepoint (same machinery /suggest_tasks
uses) — covered end-to-end below.
"""
import importlib

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


def test_ig_caption_is_paid():
    from tools import intent_router as _ir
    assert _ir.is_paid("/ig_caption") is True


def test_ig_caption_is_admin_only_not_friend():
    assert "/ig_caption" not in mod.FRIEND_ALLOWED_COMMANDS


def test_ig_caption_dispatch_calls_llm_under_guard_spend(monkeypatch):
    calls = {"llm": 0, "guard_spend_args": None}

    def _fake_guard_spend(uid, uname, est, do):
        calls["guard_spend_args"] = (uid, uname, est)
        return do(), None

    def _fake_llm(system, messages):
        calls["llm"] += 1
        return "Смачна кава чекає! ☕ #кава #ранок"

    monkeypatch.setattr(mod, "guard_spend", _fake_guard_spend)
    monkeypatch.setattr(mod, "_ig_caption_ask_llm", _fake_llm)
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))

    mod._ig_caption_dispatch(ADMIN, "новий сезонний напій")

    assert calls["llm"] == 1
    assert calls["guard_spend_args"][0] == ADMIN
    from app.services import ig_caption as _cap
    assert calls["guard_spend_args"][2] == _cap.EST_USD
    assert "Смачна кава чекає" in sent["t"]


def test_ig_caption_dispatch_requires_topic(monkeypatch):
    seen = {"llm": 0}
    monkeypatch.setattr(mod, "guard_spend", lambda uid, uname, est, do: (do(), None))
    monkeypatch.setattr(mod, "_ig_caption_ask_llm", lambda s, m: seen.__setitem__("llm", seen["llm"] + 1) or "x")
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))

    mod._ig_caption_dispatch(ADMIN, "   ")

    assert seen["llm"] == 0
    assert "тему" in sent["t"].lower()


def test_ig_caption_dispatch_honest_fallback_when_gate_blocks(monkeypatch):
    seen = {"llm": 0}

    def _blocking_guard_spend(uid, uname, est, do):
        return None, "🚫 лимит исчерпан"

    monkeypatch.setattr(mod, "guard_spend", _blocking_guard_spend)
    monkeypatch.setattr(mod, "_ig_caption_ask_llm", lambda s, m: seen.__setitem__("llm", 1) or "x")
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))

    mod._ig_caption_dispatch(ADMIN, "тема")

    assert seen["llm"] == 0
    assert "лимит" in sent["t"]


def test_ig_caption_dispatch_honest_fallback_on_empty_reply(monkeypatch):
    monkeypatch.setattr(mod, "guard_spend", lambda uid, uname, est, do: (do(), None))
    monkeypatch.setattr(mod, "_ig_caption_ask_llm", lambda s, m: "")
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))

    mod._ig_caption_dispatch(ADMIN, "тема")

    assert "не удалось" in sent["t"].lower()


def test_ig_caption_dispatch_llm_raises_gives_honest_zero_dollar_message(monkeypatch):
    def _raising_llm(system, messages):
        raise RuntimeError("Error code: 400 - Your credit balance is too low")

    monkeypatch.setattr(mod, "guard_spend", lambda uid, uname, est, do: (do(), None))
    monkeypatch.setattr(mod, "_ig_caption_ask_llm", _raising_llm)
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))

    mod._ig_caption_dispatch(ADMIN, "тема")

    assert "$0" in sent["t"] or "недоступ" in sent["t"].lower()


def test_ig_caption_command_dispatches_with_query_as_topic(monkeypatch):
    fired = {}
    monkeypatch.setattr(mod, "_ig_caption_dispatch",
                        lambda cid, topic: fired.update(cid=cid, topic=topic))
    state = {"_paid_confirmed": "/ig_caption"}
    mod.handle_command(ADMIN, "/ig_caption", "новинка сезону", state)
    assert fired == {"cid": ADMIN, "topic": "новинка сезону"}


def test_ig_caption_command_gated_by_money_gate_without_token(monkeypatch):
    fired = {"dispatch": 0, "confirm": 0}
    monkeypatch.setattr(mod, "_ig_caption_dispatch",
                        lambda cid, topic: fired.__setitem__("dispatch", 1))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: fired.__setitem__("confirm", 1))
    state = {}
    mod.handle_command(ADMIN, "/ig_caption", "тема", state)
    assert fired["dispatch"] == 0 and fired["confirm"] == 1


def test_ig_caption_confirm_tap_reaches_real_dispatch_e2e(monkeypatch):
    calls = {"llm": 0}

    def _fake_llm(system, messages):
        calls["llm"] += 1
        return "Смачна кава! #кава #ранок"

    monkeypatch.setattr(mod, "guard_spend", lambda uid, uname, est, do: (do(), None))
    monkeypatch.setattr(mod, "_ig_caption_ask_llm", _fake_llm)
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    sent, kb_sent, acked = [], [], []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, t, kb, *a, **k: kb_sent.append((t, kb)))
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: acked.append(a))

    state = {}
    # Step 1: admin types /ig_caption — money-confirm must show, NOT dispatch.
    mod.handle_command(ADMIN, "/ig_caption", "новинка", state)
    assert calls["llm"] == 0
    assert len(kb_sent) == 1
    assert "/ig_caption" in kb_sent[0][0]
    assert any(btn.get("callback_data") == "confirm:run"
               for row in kb_sent[0][1] for btn in row)
    assert state["pending_confirm"]["cmd"] == "/ig_caption"
    assert state["pending_confirm"]["resume"]["query"] == "новинка"

    # Step 2: admin taps [▶️ Запустить] — a real confirm:run callback_query.
    cq = {
        "id": "cq1", "data": "confirm:run",
        "message": {"chat": {"id": int(ADMIN)}, "message_id": 55},
        "from": {"id": int(ADMIN)},
    }
    mod.handle_callback_query(cq, state)

    assert calls["llm"] == 1
    assert len(sent) == 1
    assert "не знаю" not in sent[0].lower()
    assert "Смачна кава" in sent[0]
    assert not state.get("pending_confirm")
