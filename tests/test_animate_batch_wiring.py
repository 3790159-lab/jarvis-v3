# -*- coding: utf-8 -*-
"""Группа 4/4 (Вариант A) — bot wiring for animate-batch of ready photos.

Mechanical glue mirroring the /swapbatch_batch + /swapbatch_go wiring:
  * /animate_batch + /animate_batch_go registered in handle_command → dispatch.
  * _swapbatch_dispatch routes the two new commands to the handler methods and
    draws the same engine menu on _go.
  * The album/photo intercepts also fire on EXPECTING_READY_PHOTOS and route to
    consume_ready_album — WITHOUT breaking the swap intake (EXPECTING_TARGETS).
  * Both commands are in FRIEND_ALLOWED_COMMANDS (friends animate ready photos).
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

STATE_EXPECTING_TARGETS = "EXPECTING_TARGETS"
STATE_EXPECTING_READY_PHOTOS = "EXPECTING_READY_PHOTOS"


def _get_bot_module():
    spec = importlib.util.spec_from_file_location(
        "jarvis_tg_ctrl_animbatch",
        ROOT / "tools" / "jarvis_smart_telegram_control.py",
    )
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_CHAT_ID": "123",
    }):
        spec.loader.exec_module(mod)
    return mod


def _photo_msg(uid: str = "u1") -> dict:
    return {"photo": [{"file_id": f"f-{uid}", "file_unique_id": uid, "file_size": 100}]}


# ── command registration (handle_command → dispatch) ─────────────────────────

def test_animate_batch_command_registered():
    bot = _get_bot_module()
    with patch.object(bot, "_swapbatch_dispatch") as disp:
        bot.handle_command("123", "/animate_batch", "", {})
    disp.assert_called_once_with("123", "animate_batch")


def test_animate_batch_go_command_registered():
    bot = _get_bot_module()
    with patch.object(bot, "_swapbatch_dispatch") as disp:
        bot.handle_command("123", "/animate_batch_go", "", {})
    disp.assert_called_once_with("123", "animate_batch_go")


# ── dispatch routing ─────────────────────────────────────────────────────────

def test_dispatch_animate_batch_calls_intent():
    bot = _get_bot_module()
    h = MagicMock()
    with patch.object(bot, "_swapbatch_get_handler", return_value=(h, MagicMock())), \
         patch.object(bot, "_swapbatch_apply_reply"):
        bot._swapbatch_dispatch("123", "animate_batch")
    h.handle_animate_batch_intent.assert_called_once_with(123)


def test_dispatch_animate_batch_go_draws_engine_menu():
    bot = _get_bot_module()
    h = MagicMock()
    kb = {"inline_keyboard": [[{"text": "WaveSpeed", "callback_data": "sbeng:wavespeed"}]]}
    with patch.dict(os.environ, {"SWAPBATCH_ANIMATE_ENABLED": "1"}), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(h, MagicMock())), \
         patch.object(bot, "_swapbatch_apply_reply"), \
         patch.object(bot, "_swapbatch_engine_menu_kb", return_value=kb), \
         patch.object(bot, "send_with_keyboard") as swk:
        bot._swapbatch_dispatch("123", "animate_batch_go")
    h.handle_animate_batch_go.assert_called_once_with(123)
    swk.assert_called_once()
    assert swk.call_args[0][2] == kb["inline_keyboard"]


def test_dispatch_animate_batch_go_skips_menu_when_animate_disabled():
    """Symmetry with swap-go: when SWAPBATCH_ANIMATE_ENABLED is off, the engine
    menu is NOT drawn (the subsequent animate commands are guarded too), so the
    flag-off behaviour does not diverge between the swap and ready-photo paths.
    The go reply still applies (intake closes), only the menu is suppressed."""
    bot = _get_bot_module()
    h = MagicMock()
    kb = {"inline_keyboard": [[{"text": "WaveSpeed", "callback_data": "sbeng:wavespeed"}]]}
    with patch.dict(os.environ, {"SWAPBATCH_ANIMATE_ENABLED": "0"}), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(h, MagicMock())), \
         patch.object(bot, "_swapbatch_apply_reply") as apply_reply, \
         patch.object(bot, "_swapbatch_engine_menu_kb", return_value=kb), \
         patch.object(bot, "send_with_keyboard") as swk:
        bot._swapbatch_dispatch("123", "animate_batch_go")
    h.handle_animate_batch_go.assert_called_once_with(123)
    apply_reply.assert_called_once()      # intake still closes (→ SWAP_DONE)
    swk.assert_not_called()               # but no engine menu when flag off


# ── album intercept: ready photos accepted, swap NOT broken ──────────────────

def test_ready_album_routes_to_consume_ready_album():
    bot = _get_bot_module()
    h, orch = MagicMock(), MagicMock()
    orch.status.return_value = STATE_EXPECTING_READY_PHOTOS
    with patch.object(bot, "_swapbatch_get_handler", return_value=(h, orch)), \
         patch.object(bot, "_download_telegram_file", return_value="/tmp/x.jpg"), \
         patch.object(bot, "_swapbatch_apply_reply"):
        consumed = bot._swapbatch_album_intercept("123", [_photo_msg("a"), _photo_msg("b")])
    assert consumed is True
    h.consume_ready_album.assert_called_once()
    h.consume_targets_album.assert_not_called()


def test_swap_album_still_routes_to_consume_targets_album():
    """CRITICAL: extending the gate must NOT break the swap-batch album path."""
    bot = _get_bot_module()
    h, orch = MagicMock(), MagicMock()
    orch.status.return_value = STATE_EXPECTING_TARGETS
    with patch.object(bot, "_swapbatch_get_handler", return_value=(h, orch)), \
         patch.object(bot, "_download_telegram_file", return_value="/tmp/x.jpg"), \
         patch.object(bot, "_swapbatch_apply_reply"):
        consumed = bot._swapbatch_album_intercept("123", [_photo_msg("a")])
    assert consumed is True
    h.consume_targets_album.assert_called_once()
    h.consume_ready_album.assert_not_called()


# ── single-photo intercept: parallel coverage ────────────────────────────────

def test_ready_single_photo_routes_to_consume_ready_album():
    bot = _get_bot_module()
    h, orch = MagicMock(), MagicMock()
    orch.is_waiting_for_source.return_value = False
    orch.status.return_value = STATE_EXPECTING_READY_PHOTOS
    with patch.object(bot, "_swapbatch_get_handler", return_value=(h, orch)), \
         patch.object(bot, "_download_telegram_file", return_value="/tmp/x.jpg"), \
         patch.object(bot, "_swapbatch_apply_reply"):
        consumed = bot._swapbatch_photo_intercept("123", _photo_msg("a"))
    assert consumed is True
    h.consume_ready_album.assert_called_once()
    h.consume_targets_album.assert_not_called()


def test_swap_single_photo_still_routes_to_consume_targets_album():
    bot = _get_bot_module()
    h, orch = MagicMock(), MagicMock()
    orch.is_waiting_for_source.return_value = False
    orch.status.return_value = STATE_EXPECTING_TARGETS
    with patch.object(bot, "_swapbatch_get_handler", return_value=(h, orch)), \
         patch.object(bot, "_download_telegram_file", return_value="/tmp/x.jpg"), \
         patch.object(bot, "_swapbatch_apply_reply"):
        consumed = bot._swapbatch_photo_intercept("123", _photo_msg("a"))
    assert consumed is True
    h.consume_targets_album.assert_called_once()
    h.consume_ready_album.assert_not_called()


# ── friend gate ──────────────────────────────────────────────────────────────

def test_animate_batch_commands_allowed_for_friend():
    bot = _get_bot_module()
    assert "/animate_batch" in bot.FRIEND_ALLOWED_COMMANDS
    assert "/animate_batch_go" in bot.FRIEND_ALLOWED_COMMANDS
