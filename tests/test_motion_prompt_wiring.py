"""Tests for Task 4: bot wiring of the ✨ generate-motion-prompt button.

The vision call is blocking (~5s) so the callback must ack instantly and run
the work in a daemon worker thread (unlike the instant sbward: toggle).

Covered:
  - "sbgen:run" button present in build_engine_keyboard (both session menus)
  - "sbgen:" in FRIEND_ALLOWED_CALLBACK_PREFIXES (friends may press it)
  - callback acks FIRST, then spawns the worker (does not block long-poll)
  - worker calls handle_generate_prompt with the REAL user_id/username
  - friend over-limit (🚫 reply) -> admin notification
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.handlers.face_swap_handler import FaceSwapHandler, HandlerReply

ROOT = Path(__file__).resolve().parent.parent


def _get_bot_module():
    spec = importlib.util.spec_from_file_location(
        "jarvis_tg_ctrl_sbgen",
        ROOT / "tools" / "jarvis_smart_telegram_control.py",
    )
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_CHAT_ID": "123",
    }):
        spec.loader.exec_module(mod)
    return mod


def _cq(data: str, chat_id: str = "123", msg_id: int = 42, user_id: int | None = None) -> dict:
    uid = int(chat_id) if user_id is None else user_id
    return {
        "id": "cq-test-id",
        "data": data,
        "from": {"id": uid},
        "message": {"message_id": msg_id, "chat": {"id": int(chat_id)}},
    }


# ── 1. Button in keyboard ─────────────────────────────────────────────────────
def test_generate_button_in_engine_keyboard():
    kb = FaceSwapHandler.build_engine_keyboard()
    buttons = [b for row in kb["inline_keyboard"] for b in row]
    assert any(b["callback_data"] == "sbgen:run" for b in buttons)
    assert any("Сгенерировать промт" in b["text"] for b in buttons)


def test_generate_button_hidden_when_show_generate_false():
    kb = FaceSwapHandler.build_engine_keyboard(show_generate=False)
    buttons = [b for row in kb["inline_keyboard"] for b in row]
    assert not any(b["callback_data"] == "sbgen:run" for b in buttons)


# ── 2. Friend allowlist ───────────────────────────────────────────────────────
def test_sbgen_in_friend_allowed_prefixes():
    bot = _get_bot_module()
    assert "sbgen:" in bot.FRIEND_ALLOWED_CALLBACK_PREFIXES


# ── 3. Callback acks first, then starts the worker thread ─────────────────────
def test_sbgen_callback_acks_then_starts_worker():
    bot = _get_bot_module()
    manager = MagicMock()
    with patch.object(bot, "answer_callback_query") as ack, \
         patch.object(bot, "_sbgen_start") as start:
        manager.attach_mock(ack, "ack")
        manager.attach_mock(start, "start")
        bot.handle_callback_query(_cq("sbgen:run", chat_id="123", msg_id=42, user_id=555), {})

    ack.assert_called_once()
    start.assert_called_once()
    # real user_id (555) and message_id (42) propagated to the worker spawn
    assert start.call_args[0] == ("123", 42, 555)
    # ack must come BEFORE the worker spawn (don't block long-poll)
    assert manager.mock_calls[0][0] == "ack"


def test_sbgen_friend_not_rejected():
    bot = _get_bot_module()
    with patch.object(bot, "_is_admin_id", return_value=False), \
         patch.object(bot, "answer_callback_query") as ack, \
         patch.object(bot, "_sbgen_start") as start:
        bot.handle_callback_query(_cq("sbgen:run", chat_id="999"), {})
    start.assert_called_once()  # friend allowed through the role-gate
    assert "Только для администратора" not in (ack.call_args[0][1] if ack.call_args and len(ack.call_args[0]) > 1 else "")


# ── 4. Worker runs the handler with REAL identity and redraws ─────────────────
def test_sbgen_worker_calls_handler_with_real_user_and_redraws():
    bot = _get_bot_module()
    bot._USERNAME_BY_CHAT["123"] = "petya"
    h = MagicMock()
    h.handle_generate_prompt.return_value = HandlerReply(text="✨ Сгенерирован промт движения:\n«slow gentle»")
    h._redraw_engine_keyboard.return_value = {
        "inline_keyboard": [[{"text": "🎬 X", "callback_data": "sbeng:spicy"}]]
    }
    with patch.object(bot, "_swapbatch_get_handler", return_value=(h, None)), \
         patch.object(bot, "_swapbatch_apply_reply") as apply_reply, \
         patch.object(bot, "edit_message_with_keyboard") as edit:
        bot._sbgen_worker("123", 42, 555)

    # REAL user_id + username flow into the gated handler (check_limit lives there)
    h.handle_generate_prompt.assert_called_once_with(123, user_id=555, username="petya")
    apply_reply.assert_called_once()  # prompt + options surfaced to the user
    edit.assert_called_once()  # engine menu redrawn


def test_sbgen_worker_admin_notify_on_over_limit():
    bot = _get_bot_module()
    h = MagicMock()
    h.handle_generate_prompt.return_value = HandlerReply(text="🚫 Дневной лимит исчерпан")
    h._redraw_engine_keyboard.return_value = {"inline_keyboard": []}
    with patch.object(bot, "_swapbatch_get_handler", return_value=(h, None)), \
         patch.object(bot, "_swapbatch_apply_reply"), \
         patch.object(bot, "edit_message_with_keyboard"), \
         patch.object(bot._whitelist, "load_admin_user_id", return_value=111), \
         patch.object(bot, "send") as send:
        bot._sbgen_worker("999", 42, 999)  # friend id 999, over limit

    # admin (111) gets notified that the friend hit the limit
    admin_calls = [c for c in send.call_args_list if str(c[0][0]) == "111"]
    assert len(admin_calls) == 1
    assert "уперся в лимит" in admin_calls[0][0][1]


def test_sbgen_start_spawns_daemon_thread():
    bot = _get_bot_module()
    with patch.object(bot, "threading") as thr:
        bot._sbgen_start("123", 42, 555)
    thr.Thread.assert_called_once()
    assert thr.Thread.call_args.kwargs.get("daemon") is True
    thr.Thread.return_value.start.assert_called_once()
