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


# ── Task 7: end-to-end — unknown text no longer guesses via paid research ──
def test_unknown_text_end_to_end_no_paid_research(monkeypatch):
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, text, *a, **k: sent.setdefault("t", text))
    monkeypatch.setattr(mod, "backend_post",
                        lambda *a, **k: sent.setdefault("research", True) or {})
    pack = mod.classify_message("асдфгхй совсем непонятная фраза без смысла", {})
    mod.run_intent("123", pack, {})
    assert "research" not in sent                     # money-safety: no Perplexity guess
    assert "menu" in sent["t"].lower() or "меню" in sent["t"].lower()


# ── Task 8: callback ir: + friend narrow path + teeth ──────────────────────
def _cq(data, uid=1, chat="123"):
    return {"id": "c", "data": data, "from": {"id": uid},
            "message": {"chat": {"id": chat}, "message_id": 5}}


def test_ir_prefix_is_friend_allowed():
    assert "ir:" in mod.FRIEND_ALLOWED_CALLBACK_PREFIXES


def test_callback_ir_run_executes_pending(monkeypatch):
    called = {}
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "handle", lambda cid, text: called.setdefault("h", (cid, text)))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "_is_admin_id", lambda uid: True)
    st = {"pending_ir": {"cmd": "/menu_photo", "arg": ""}}
    mod.handle_callback_query(_cq("ir:run"), st)
    assert called["h"] == ("123", "/menu_photo")
    assert st.get("pending_ir") is None


def test_callback_ir_cancel_clears_without_exec(monkeypatch):
    called = {}
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "handle", lambda cid, text: called.setdefault("h", True))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "_is_admin_id", lambda uid: True)
    st = {"pending_ir": {"cmd": "/menu_photo", "arg": ""}}
    mod.handle_callback_query(_cq("ir:cancel"), st)
    assert "h" not in called
    assert st.get("pending_ir") is None


def test_callback_ir_pick_free_command_execs(monkeypatch):
    called = {}
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "handle", lambda cid, text: called.setdefault("h", (cid, text)))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: called.setdefault("kb", True))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "_is_admin_id", lambda uid: True)
    monkeypatch.setattr(mod, "_menu_role", lambda uid: "admin")
    st = {"pending_ir": {"candidates": ["/health", "/git_status"]}}
    mod.handle_callback_query(_cq("ir:pick:0"), st)
    assert called.get("h") == ("123", "/health")   # free → exec
    assert "kb" not in called


def test_callback_ir_pick_paid_command_asks_confirm(monkeypatch):
    called = {}
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "handle", lambda cid, text: called.setdefault("h", True))
    monkeypatch.setattr(mod, "send_with_keyboard",
                        lambda cid, text, kb: called.update(kb=kb))
    monkeypatch.setattr(mod, "save_state", lambda s: None)
    monkeypatch.setattr(mod, "_is_admin_id", lambda uid: True)
    st = {"pending_ir": {"candidates": ["/menu_photo", "/pro_food"]}}
    mod.handle_callback_query(_cq("ir:pick:0"), st)
    assert "h" not in called                        # paid pick → NOT executed
    assert "ir:run" in str(called.get("kb"))        # second confirm required


def test_friend_free_text_routes_allowed_command(monkeypatch):
    routed = {}
    monkeypatch.setattr(mod, "_role_for_chat", lambda cid: "friend")
    monkeypatch.setattr(mod, "load_state", lambda: {})
    monkeypatch.setattr(mod, "run_intent", lambda cid, pack, st: routed.update(pack))
    monkeypatch.setattr(mod, "send", lambda *a, **k: routed.setdefault("sent", True))
    mod.handle("777", "анимировать одно фото")       # /animate is friend-allowed
    assert routed.get("intent") == "ir_route"
    assert routed.get("command") == "/animate"


def test_friend_free_text_admin_only_command_no_leak(monkeypatch):
    out = {}
    monkeypatch.setattr(mod, "_role_for_chat", lambda cid: "friend")
    monkeypatch.setattr(mod, "load_state", lambda: {})
    monkeypatch.setattr(mod, "run_intent", lambda cid, pack, st: out.update(pack))
    monkeypatch.setattr(mod, "send", lambda cid, text, *a, **k: out.setdefault("sent", text))
    mod.handle("777", "статус гита")                 # /git_status is admin-only
    assert out.get("command") != "/git_status"       # no leak
    assert "sent" in out                             # honest fallback


def test_friend_slash_command_gate_still_denies(monkeypatch):
    out = {}
    monkeypatch.setattr(mod, "_role_for_chat", lambda cid: "friend")
    monkeypatch.setattr(mod, "send", lambda cid, text, *a, **k: out.setdefault("sent", text))
    mod.handle("777", "/git_status")                 # admin-only slash command
    assert "администратор" in out.get("sent", "").lower()
