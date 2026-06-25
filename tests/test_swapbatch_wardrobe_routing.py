# -*- coding: utf-8 -*-
"""Задача 2 (glue): sbward: callback routing + friend gate + live-state draws.

Mirror of test_swapbatch_smooth_routing.py. Thin dispatcher layer — verifies:
- the sbward:on/off branch reaches handle_wardrobe_button, acks the callback,
  and redraws the engine menu;
- friends (not just admin) may toggle wardrobe — this is Артём's direct case,
  he needs preserve against censor E005;
- the post-swap menu draw reads LIVE session wardrobe (mode set by command
  before the menu must show), via _swapbatch_engine_menu_kb;
- standalone /animate has no wardrobe toggle (no batch session → dead button).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _get_bot_module():
    spec = importlib.util.spec_from_file_location(
        "jarvis_tg_ctrl_wardrobe",
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


def _fake_handler(label: str = "ВКЛ", cb: str = "sbward:off"):
    h = MagicMock()
    h.handle_wardrobe_button.return_value = {
        "inline_keyboard": [[{"text": f"🩱 Не раздевать: {label}", "callback_data": cb}]]
    }
    return h


class _V:
    def count_faces(self, p):
        return 1


def _jpg(p: Path) -> Path:
    Image.new("RGB", (16, 16), (1, 2, 3)).save(p, "JPEG")
    return p


def _real_handler(tmp_path, chat: int, wardrobe: str):
    """A real FaceSwapHandler+orchestrator with a live session whose wardrobe
    was set (as if by /swapbatch_set_wardrobe) before the menu draws."""
    from app.handlers.face_swap_handler import FaceSwapHandler
    from app.services.block_m2_face_swap.batch_orchestrator import BatchOrchestrator
    orch = BatchOrchestrator(state_root=tmp_path / "b", validator=_V())
    orch.begin_source(chat)
    orch.set_wardrobe(chat, wardrobe)
    return FaceSwapHandler(orchestrator=orch), orch


def _buttons(kb: dict) -> list[dict]:
    return [b for row in kb["inline_keyboard"] for b in row]


# ── dispatcher routing ─────────────────────────────────────────────────────────


def test_sbward_on_routes_to_handler_and_redraws():
    bot = _get_bot_module()
    h = _fake_handler("ВКЛ", "sbward:off")
    with patch.object(bot, "_swapbatch_get_handler", return_value=(h, None)), \
         patch.object(bot, "answer_callback_query") as ack, \
         patch.object(bot, "edit_message_with_keyboard") as edit:
        bot.handle_callback_query(_cq("sbward:on"), {})
    h.handle_wardrobe_button.assert_called_once_with(123, True)
    ack.assert_called_once()                       # spinner cleared
    edit.assert_called_once()
    kb_arg = edit.call_args[0][3]
    assert any("ВКЛ" in b["text"] for row in kb_arg for b in row)


def test_sbward_off_routes_with_false():
    bot = _get_bot_module()
    h = _fake_handler("ВЫКЛ", "sbward:on")
    with patch.object(bot, "_swapbatch_get_handler", return_value=(h, None)), \
         patch.object(bot, "answer_callback_query"), \
         patch.object(bot, "edit_message_with_keyboard"):
        bot.handle_callback_query(_cq("sbward:off"), {})
    h.handle_wardrobe_button.assert_called_once_with(123, False)


def test_sbward_allowed_for_friend_not_just_admin():
    # A non-admin friend (chat 999) must be allowed to toggle wardrobe — это
    # прямой кейс Артёма (preserve против цензурных E005). Guards against
    # forgetting the prefix in FRIEND_ALLOWED_CALLBACK_PREFIXES.
    bot = _get_bot_module()
    assert "sbward:" in bot.FRIEND_ALLOWED_CALLBACK_PREFIXES
    h = _fake_handler()
    with patch.object(bot, "_swapbatch_get_handler", return_value=(h, None)), \
         patch.object(bot, "_is_admin_id", return_value=False), \
         patch.object(bot, "answer_callback_query") as ack, \
         patch.object(bot, "edit_message_with_keyboard"):
        bot.handle_callback_query(_cq("sbward:on", chat_id="999"), {})
    h.handle_wardrobe_button.assert_called_once_with(999, True)
    assert "Только для администратора" not in ack.call_args[0][1]


# ── initial post-swap menu reads LIVE wardrobe ──────────────────────────────────


def test_initial_menu_reads_live_wardrobe_spicy(tmp_path):
    # Wardrobe set to spicy by command BEFORE the go-menu draws → the menu must
    # show ВЫКЛ (live), NOT the preserve param default (ВКЛ).
    bot = _get_bot_module()
    h, orch = _real_handler(tmp_path, chat=321, wardrobe="spicy")
    with patch.object(bot, "_swapbatch_get_handler", return_value=(h, orch)):
        kb = bot._swapbatch_engine_menu_kb(321)
    wb = [b for b in _buttons(kb) if b["callback_data"].startswith("sbward:")]
    assert wb and "ВЫКЛ" in wb[0]["text"]


def test_initial_menu_reads_live_wardrobe_preserve(tmp_path):
    bot = _get_bot_module()
    h, orch = _real_handler(tmp_path, chat=322, wardrobe="preserve")
    with patch.object(bot, "_swapbatch_get_handler", return_value=(h, orch)):
        kb = bot._swapbatch_engine_menu_kb(322)
    wb = [b for b in _buttons(kb) if b["callback_data"].startswith("sbward:")]
    assert wb and "ВКЛ" in wb[0]["text"]


# ── standalone /animate has no wardrobe toggle ──────────────────────────────────


def test_standalone_animate_has_no_wardrobe_button(tmp_path):
    from app.handlers.face_swap_handler import FaceSwapHandler
    bot = _get_bot_module()
    chat = 777
    bot._ANIMATE_PENDING[chat] = {"photo": None}
    sent = {}

    def _capture(cid, text, kb):
        sent["kb"] = kb

    with patch.object(bot, "_download_telegram_file",
                      return_value=str(_jpg(tmp_path / "a.jpg"))), \
         patch.object(bot, "_swapbatch_get_handler",
                      return_value=(FaceSwapHandler(orchestrator=None), None)), \
         patch.object(bot, "send_with_keyboard", side_effect=_capture):
        handled = bot._animate_photo_intercept(str(chat), {
            "photo": [{"file_id": "f", "file_unique_id": "u", "file_size": 10}],
        })

    assert handled is True
    flat = [b for row in sent["kb"] for b in row]
    cbs = [b["callback_data"] for b in flat]
    assert not any(c.startswith("sbward:") for c in cbs)   # dead button hidden
    assert not any(c.startswith("sbsmooth:") for c in cbs)  # smooth hidden too
    assert "anim:spicy" in cbs                              # engine rows present
