# -*- coding: utf-8 -*-
"""Задача 4 (glue): sbsmooth: callback routing in handle_callback_query.

Thin dispatcher layer — characterization tests written after the glue (per
agreement). Verifies the sbsmooth:on/off branch reaches handle_smooth_button
(Задача 4 handler), acknowledges the callback (no spinner stuck on the button),
redraws the engine menu, and is allowed for friends (not just admin).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _get_bot_module():
    spec = importlib.util.spec_from_file_location(
        "jarvis_tg_ctrl_smooth",
        ROOT / "tools" / "jarvis_smart_telegram_control.py",
    )
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_CHAT_ID": "123",
    }):
        spec.loader.exec_module(mod)
    return mod


def _cq(data: str, chat_id: str = "123", msg_id: int = 42) -> dict:
    return {
        "id": "cq-test-id",
        "data": data,
        "from": {"id": int(chat_id)},
        "message": {"message_id": msg_id, "chat": {"id": int(chat_id)}},
    }


def _fake_handler(label: str = "ВКЛ", cb: str = "sbsmooth:off"):
    h = MagicMock()
    h.handle_smooth_button.return_value = {
        "inline_keyboard": [[{"text": f"🪶 Плавность 48fps: {label}", "callback_data": cb}]]
    }
    return h


def test_sbsmooth_on_routes_to_handler_and_redraws():
    bot = _get_bot_module()
    h = _fake_handler("ВКЛ", "sbsmooth:off")
    with patch.object(bot, "_swapbatch_get_handler", return_value=(h, None)), \
         patch.object(bot, "answer_callback_query") as ack, \
         patch.object(bot, "edit_message_with_keyboard") as edit:
        bot.handle_callback_query(_cq("sbsmooth:on"), {})
    h.handle_smooth_button.assert_called_once_with(123, True)
    ack.assert_called_once()                       # spinner cleared
    edit.assert_called_once()
    kb_arg = edit.call_args[0][3]
    assert any("ВКЛ" in b["text"] for row in kb_arg for b in row)


def test_sbsmooth_off_routes_with_false():
    bot = _get_bot_module()
    h = _fake_handler("ВЫКЛ", "sbsmooth:on")
    with patch.object(bot, "_swapbatch_get_handler", return_value=(h, None)), \
         patch.object(bot, "answer_callback_query"), \
         patch.object(bot, "edit_message_with_keyboard"):
        bot.handle_callback_query(_cq("sbsmooth:off"), {})
    h.handle_smooth_button.assert_called_once_with(123, False)


def test_sbsmooth_allowed_for_friend_not_just_admin():
    # A non-admin friend (chat 999) must be allowed to toggle smooth — the
    # whole feature targets the friend's batch. Guards against forgetting the
    # prefix in FRIEND_ALLOWED_CALLBACK_PREFIXES.
    bot = _get_bot_module()
    assert "sbsmooth:" in bot.FRIEND_ALLOWED_CALLBACK_PREFIXES
    h = _fake_handler()
    with patch.object(bot, "_swapbatch_get_handler", return_value=(h, None)), \
         patch.object(bot, "_is_admin_id", return_value=False), \
         patch.object(bot, "answer_callback_query") as ack, \
         patch.object(bot, "edit_message_with_keyboard"):
        bot.handle_callback_query(_cq("sbsmooth:on", chat_id="999"), {})
    # Reached the handler → was NOT rejected by the role gate.
    h.handle_smooth_button.assert_called_once_with(999, True)
    # And the ack text is the toggle confirmation, not the admin-only refusal.
    assert "Только для администратора" not in ack.call_args[0][1]
