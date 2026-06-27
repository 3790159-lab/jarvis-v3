"""Bot wiring for /videoref reference-video slicing (Веха B, Task 3).

Mirrors the harness in test_animate_batch_wiring.py: load the bot module via
importlib with env patched, then patch the download + slice boundaries. The
slicer/validator live in app.services.block_m2_video.video_frames (Tasks 1-2);
here we only test routing, the awaiting-flag lifecycle, and the friend gate.

Веха B is free: no vision / paid calls anywhere in this flow.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.services.block_m2_video.video_frames as vf  # noqa: E402


def _get_bot_module():
    spec = importlib.util.spec_from_file_location(
        "jarvis_tg_ctrl_videoref",
        ROOT / "tools" / "jarvis_smart_telegram_control.py",
    )
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_CHAT_ID": "123",
    }):
        spec.loader.exec_module(mod)
    return mod


def _video_msg(file_size: int = 1_000_000, duration: float = 5) -> dict:
    return {"video": {"file_id": "vid-1", "file_size": file_size, "duration": duration}}


# ── command arms the awaiting flag ───────────────────────────────────────────

def test_videoref_command_sets_awaiting_flag():
    bot = _get_bot_module()
    with patch.object(bot, "send"):
        bot.handle_command("123", "/videoref", "", {})
    assert 123 in bot._VIDEOREF_AWAITING


# ── happy path: armed video → validate → download → slice → reply ────────────

def test_video_with_flag_sliced_and_flag_cleared():
    bot = _get_bot_module()
    bot._VIDEOREF_AWAITING.add(123)
    frames = [Path("frame_000.jpg"), Path("frame_001.jpg"), Path("frame_002.jpg")]
    with patch.object(bot, "_download_telegram_file", return_value="/tmp/ref.mp4") as dl, \
         patch.object(vf, "slice_video_to_frames", return_value=frames) as sl, \
         patch.object(bot, "send_with_keyboard") as skb, \
         patch.object(bot, "send"):
        consumed = bot._videoref_intercept("123", _video_msg())
    assert consumed is True
    dl.assert_called_once()
    sl.assert_called_once()
    assert 123 not in bot._VIDEOREF_AWAITING  # flag cleared after success
    # Веха C: the "Нарезано N" receipt now carries the opt-in motion button.
    assert "3" in skb.call_args.args[1]  # "Нарезано 3 кадров"


# ── CRITICAL: a video without the flag must not be hijacked ──────────────────

def test_video_without_flag_not_intercepted():
    """Any video without /videoref armed falls through untouched.

    Guards /video_face_swap and every other video flow from being hijacked.
    """
    bot = _get_bot_module()
    with patch.object(bot, "_download_telegram_file") as dl, \
         patch.object(vf, "slice_video_to_frames") as sl, \
         patch.object(bot, "send") as snd:
        consumed = bot._videoref_intercept("123", _video_msg())
    assert consumed is False
    dl.assert_not_called()
    sl.assert_not_called()
    snd.assert_not_called()


# ── limit fails BEFORE download, flag still cleared ──────────────────────────

def test_oversize_video_refused_without_download():
    bot = _get_bot_module()
    bot._VIDEOREF_AWAITING.add(123)
    with patch.object(bot, "_download_telegram_file") as dl, \
         patch.object(vf, "slice_video_to_frames") as sl, \
         patch.object(bot, "send") as snd:
        consumed = bot._videoref_intercept(
            "123", _video_msg(file_size=25 * 1024 * 1024, duration=5)
        )
    assert consumed is True
    dl.assert_not_called()          # never fetch oversize
    sl.assert_not_called()
    assert 123 not in bot._VIDEOREF_AWAITING  # flag cleared even on refusal
    assert "20" in snd.call_args[0][1]


def test_too_long_video_refused_without_download():
    bot = _get_bot_module()
    bot._VIDEOREF_AWAITING.add(123)
    with patch.object(bot, "_download_telegram_file") as dl, \
         patch.object(vf, "slice_video_to_frames") as sl, \
         patch.object(bot, "send") as snd:
        consumed = bot._videoref_intercept("123", _video_msg(duration=20))
    assert consumed is True
    dl.assert_not_called()
    sl.assert_not_called()
    assert 123 not in bot._VIDEOREF_AWAITING
    assert "15" in snd.call_args[0][1]


# ── friend gate ──────────────────────────────────────────────────────────────

def test_videoref_allowed_for_friend():
    bot = _get_bot_module()
    assert "/videoref" in bot.FRIEND_ALLOWED_COMMANDS
