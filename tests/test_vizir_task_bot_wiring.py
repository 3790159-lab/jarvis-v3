# -*- coding: utf-8 -*-
"""Vizir /task bot wiring — pure helpers + admin-only + isolation regression. $0."""
import importlib

mod = importlib.import_module("tools.jarvis_smart_telegram_control")


def test_confirm_keyboard_shows_cap_and_run_cancel():
    kb = mod._task_confirm_keyboard(cap_usd=0.90)
    # inline_keyboard: list of rows of {"text","callback_data"}
    flat = [btn for row in kb["inline_keyboard"] for btn in row]
    datas = [b["callback_data"] for b in flat]
    texts = " ".join(b["text"] for b in flat)
    assert "task:run" in datas and "task:cancel" in datas
    assert "0.90" in texts  # cap shown


def test_progress_text_attempt_and_rejection():
    assert "1" in mod._task_progress_text("attempt_started", {"attempt": 1})
    txt = mod._task_progress_text("attempt_rejected",
                                  {"attempt": 1, "reasons": ["run did not complete"]})
    assert "did not complete" in txt and ("фидбек" in txt.lower() or "feedback" in txt.lower())


def test_task_is_admin_only_not_in_friend_lists():
    # /task must NOT be friend-allowed, and task: callbacks must NOT be friend-allowed
    friend_cmds = getattr(mod, "FRIEND_ALLOWED_COMMANDS", None)
    assert friend_cmds is not None
    assert "/task" not in friend_cmds
    # friend-allowed callback prefixes (tuple) must not include task:
    prefixes = getattr(mod, "FRIEND_ALLOWED_CALLBACK_PREFIXES", None) \
        or getattr(mod, "_FRIEND_ALLOWED_CALLBACK_PREFIXES", None)
    assert prefixes is not None
    assert not any(str(p).startswith("task:") or "task:".startswith(str(p)) for p in prefixes)


def test_prod_task_handler_wired_with_money_hooks():
    # Variant B: the prod /task handler must carry BOTH money hooks — the
    # pre-spend check_limit gate and the record_cost ledger sink — so spend is
    # gated and recorded (once, on acceptance). A None hook = money blind spot.
    h = mod._task_get_handler()
    assert h._check_limit is not None, "check_limit gate not wired into prod /task handler"
    assert h._record_cost is not None, "record_cost ledger not wired into prod /task handler"
    assert h._record_cost is mod._cost.record_cost  # the isolated visibility ledger sink


def test_existing_commands_still_present_isolation_regression():
    # the dispatcher glue for existing paid commands must be intact (we only added)
    for name in ("_swapbatch_dispatch", "_send_local_document", "_get_video_lock",
                 "handle_command", "handle_callback_query"):
        assert hasattr(mod, name), f"existing symbol {name} missing — integration broke the bot"
    # our additive symbols exist alongside them
    for name in ("_task_dispatch", "_task_run_phase", "_task_apply_reply",
                 "_task_confirm_keyboard", "_task_progress_text"):
        assert hasattr(mod, name)
