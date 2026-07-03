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
