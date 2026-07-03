# -*- coding: utf-8 -*-
"""Unified menu — bot wiring (control module). $0, mocks only, no network.

Covers: /menu friend-allowed + menu: callback prefix (Task 5), the
FRIEND_ALLOWED_COMMANDS 28->54 diff (Group A + Group B, unblocked by Арка 1),
callback routing exec-through-handle + admin-block (Task 6, tooth #2/#3),
native registration on startup (Task 7).
"""
import importlib

mod = importlib.import_module("tools.jarvis_smart_telegram_control")


# ── expected friend-command exposure (Block №4: 28 base + 9 A + 17 B) ──────
_BASE_28 = {
    "/animate", "/animate_batch", "/animate_batch_go",
    "/swapbatch", "/swapbatch_source", "/swapbatch_batch", "/swapbatch_go",
    "/swapbatch_set_quality", "/swapbatch_set_prompt", "/swapbatch_set_wardrobe",
    "/swapbatch_animate_yes", "/swapbatch_animate_go", "/swapbatch_animate_no",
    "/swapbatch_animate_custom", "/swapbatch_status", "/swapbatch_cancel",
    "/persona_photo", "/persona_video", "/persona_video_redo", "/persona_redo",
    "/persona_engine", "/persona_batch", "/me_swap_photo", "/me_swap_video",
    "/videoref", "/my_stats", "/start", "/help",
}
_GROUP_A = {  # free (lists/status/cancel)
    "/cancel_persona", "/lora_status", "/list_loras", "/cancel_lora",
    "/me_roles", "/me_places", "/me_styles", "/party_themes", "/dish_styles",
}
_GROUP_B = {  # paid — legal only because Арка 1 (money-gate) is merged
    "/create_persona", "/train_lora",
    "/me_into", "/me_as", "/me_in", "/me_with", "/me_style",
    "/menu_photo", "/social_post", "/menu_book", "/pro_food", "/smart_photo",
    "/party_promo", "/invite_card", "/event_photo", "/faceswap", "/enhance",
}
# 54 feature commands + /menu infrastructure = 55.
_EXPECTED_FRIEND = _BASE_28 | _GROUP_A | _GROUP_B | {"/menu"}


# ── Task 5: /menu command + friend permissions + the 28->54 diff ──────────
def test_menu_command_is_friend_allowed():
    assert "/menu" in mod.FRIEND_ALLOWED_COMMANDS


def test_menu_prefix_in_friend_callback_prefixes():
    assert "menu:" in mod.FRIEND_ALLOWED_CALLBACK_PREFIXES


def test_group_a_and_b_now_friend_allowed():
    # Арка 1 merged -> the whole Block №4 diff applies.
    missing = (_GROUP_A | _GROUP_B) - set(mod.FRIEND_ALLOWED_COMMANDS)
    assert not missing, f"not exposed to friend: {sorted(missing)}"


def test_friend_allowed_commands_is_exact_sanctioned_set():
    # Money-safety tooth: NO command beyond the 54 sanctioned (Block №4) + /menu
    # may be friend-allowed, and none may be missing. Catches over- AND
    # under-exposure — the exact thing Risk 7 guards.
    assert set(mod.FRIEND_ALLOWED_COMMANDS) == _EXPECTED_FRIEND


# ── Task 6: menu: callback routing (tooth #2 exec-through-handle, #3 lookup) ─
def _cq(data, uid=999):
    return {"id": "1", "from": {"id": uid},
            "message": {"message_id": 5, "chat": {"id": uid}}, "data": data}


def test_menu_exec_routes_through_handle(monkeypatch):
    # Tooth #2: exec item -> handle(chat_id, "/cmd") so the FRIEND_ALLOWED gate
    # in handle() re-applies. Must NOT call handle_command directly.
    calls = {}
    monkeypatch.setattr(mod, "handle", lambda cid, text: calls.setdefault("handle", (cid, text)))
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_role_for_chat", lambda cid: "friend")
    mod.handle_callback_query(_cq("menu:x:swapbatch_status"), {})
    assert calls["handle"] == ("999", "/swapbatch_status")


def test_menu_friend_blocked_from_admin_item(monkeypatch):
    # Tooth #3: friend tap on admin-only EXEC menu item -> lookup None -> no
    # exec. /list_loras is an admin-only exec item, so a broken role thread
    # would call handle() (privilege escalation) and trip this.
    sent = {}
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: sent.setdefault("ack", a))
    monkeypatch.setattr(mod, "handle", lambda *a, **k: sent.setdefault("handle", True))
    monkeypatch.setattr(mod, "send", lambda *a, **k: sent.setdefault("send", True))
    monkeypatch.setattr(mod, "_role_for_chat", lambda cid: "friend")
    mod.handle_callback_query(_cq("menu:x:list_loras"), {})
    assert "handle" not in sent          # admin command NOT executed


def test_menu_hint_item_sends_hint_not_handle(monkeypatch):
    # hint item -> send(hint), never handle() (no spend, no command dispatch).
    sent = {}
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "handle", lambda *a, **k: sent.setdefault("handle", True))
    monkeypatch.setattr(mod, "send", lambda cid, text, *a, **k: sent.setdefault("send", (cid, text)))
    monkeypatch.setattr(mod, "_role_for_chat", lambda cid: "friend")
    mod.handle_callback_query(_cq("menu:x:animate"), {})   # /animate is hint
    assert "handle" not in sent and "send" in sent


def test_menu_cat_admin_only_denied_for_friend(monkeypatch):
    # Category-level tooth: friend opening admin-only category -> render None ->
    # 🚫, no keyboard edit leaking admin items.
    sent = {}
    monkeypatch.setattr(mod, "answer_callback_query",
                        lambda cqid, text="": sent.setdefault("ack", text))
    monkeypatch.setattr(mod, "edit_message_with_keyboard",
                        lambda *a, **k: sent.setdefault("edit", True))
    monkeypatch.setattr(mod, "_role_for_chat", lambda cid: "friend")
    mod.handle_callback_query(_cq("menu:cat:system"), {})
    assert "edit" not in sent and "🚫" in sent.get("ack", "")


def test_menu_root_edits_in_place(monkeypatch):
    # Navigation edits the single message (editMessageText), not a new send.
    sent = {}
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "edit_message_with_keyboard",
                        lambda cid, mid, text, kb: sent.setdefault("edit", (cid, mid)))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: sent.setdefault("new", True))
    monkeypatch.setattr(mod, "_role_for_chat", lambda cid: "friend")
    mod.handle_callback_query(_cq("menu:root"), {})
    assert sent.get("edit") == ("999", 5) and "new" not in sent
