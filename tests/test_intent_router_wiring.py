# -*- coding: utf-8 -*-
"""IR-1 wiring into the bot: classify_message tail + run_intent + callback.

$0, mocks only, no network. Proves the router routes residual free text AND
that existing intents/FSMs are NOT regressed (invariant: only the former
`chat` catch-all changes).
"""
import tools.jarvis_smart_telegram_control as mod


# ── Task 5: classify_message routes residual free text (admin) ─────────────
def test_classify_routes_bot_health_phrase_to_ir():
    pack = mod.classify_message("глянь что с ботом", {})
    assert pack["intent"] == "ir_route"
    assert pack["command"] == "/health"


def test_classify_routes_spending_phrase_to_ir():
    pack = mod.classify_message("что я потратил сегодня", {})
    assert pack["intent"] == "ir_route"
    assert pack["command"] in ("/costs", "/my_stats")


def test_classify_unknown_free_text_is_ir_unknown():
    pack = mod.classify_message("асдфгхйцукен блаблабла", {})
    assert pack["intent"] == "ir_unknown"


# ── Sentinels: existing intents must NOT regress (invariant) ───────────────
def test_sentinel_health_trigger_preserved():
    assert mod.classify_message("проверить системы", {})["intent"] == "health"


def test_sentinel_greeting_preserved():
    assert mod.classify_message("привет", {})["intent"] == "greeting"


def test_sentinel_capabilities_preserved():
    assert mod.classify_message("что ты умеешь", {})["intent"] == "capabilities"


def test_sentinel_generate_trigger_still_wins_over_ir():
    # broad existing trigger catches this BEFORE the IR tail — IR must not hijack
    assert mod.classify_message("сделай фото блюда для меню", {})["intent"] == "generate"


def test_sentinel_slash_command_untouched():
    pack = mod.classify_message("/health", {})
    assert pack["intent"] == "command" and pack["command"] == "/health"


# ── Task 6: run_intent ir_route/ir_clarify/ir_unknown + confirm ────────────
def test_ir_route_free_command_auto_execs(monkeypatch):
    called = {}
    monkeypatch.setattr(mod, "handle", lambda cid, text: called.setdefault("h", (cid, text)))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: called.setdefault("kb", True))
    mod.run_intent("123", {"intent": "ir_route", "command": "/health", "arg": ""}, {})
    assert called.get("h") == ("123", "/health")   # free read → executed at once
    assert "kb" not in called                       # no confirm button


def test_ir_route_paid_command_asks_confirm(monkeypatch):
    sent = {}
    monkeypatch.setattr(mod, "handle", lambda cid, text: sent.setdefault("h", True))
    monkeypatch.setattr(mod, "send_with_keyboard",
                        lambda cid, text, kb: sent.update(text=text, kb=kb))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    st = {}
    mod.run_intent("123", {"intent": "ir_route", "command": "/menu_photo", "arg": ""}, st)
    assert "h" not in sent                           # paid → NOT auto-executed
    assert "ir:run" in str(sent.get("kb"))           # confirm keyboard
    assert "$" in sent.get("text", "")               # price shown
    assert st.get("pending_ir", {}).get("cmd") == "/menu_photo"


def test_ir_unknown_honest_fallback_no_research(monkeypatch):
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, text, *a, **k: sent.setdefault("t", text))
    monkeypatch.setattr(mod, "backend_post",
                        lambda *a, **k: sent.setdefault("research", True) or {})
    mod.run_intent("123", {"intent": "ir_unknown", "query": "блабла непонятное"}, {})
    assert "research" not in sent                     # no paid Perplexity guess
    assert "menu" in sent["t"].lower() or "меню" in sent["t"].lower()


def test_ir_clarify_sends_candidate_buttons(monkeypatch):
    sent = {}
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, text, kb: sent.update(kb=kb))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    st = {}
    mod.run_intent("123", {"intent": "ir_clarify",
                           "candidates": ["/menu_photo", "/pro_food"]}, st)
    flat = str(sent.get("kb"))
    assert "ir:pick:0" in flat and "ir:pick:1" in flat
    assert st.get("pending_ir", {}).get("candidates") == ["/menu_photo", "/pro_food"]
