"""Веха D — swap+animate bridge on the /videoref output (TDD D1–D6).

The FINAL stitch: Веха C output (best frame + motion prompt) → button → source
face → swap (batch-of-1) → animate (WaveSpeed spicy) → video. Money with TEETH:
a single check_limit on the FULL est BEFORE any spend; record_cost per stage,
only on that stage's success; quoted == charged from one source helper.

All heavy boundaries (limit gate, swap engine, animate engine, cost ledger,
telegram) are patched — no paid call runs here.
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

from app.services.block_m2_video.motion_prompt_ai import VideoMotionResult  # noqa: E402


def _get_bot_module():
    spec = importlib.util.spec_from_file_location(
        "jarvis_tg_ctrl_vehad",
        ROOT / "tools" / "jarvis_smart_telegram_control.py",
    )
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_CHAT_ID": "123",
    }):
        spec.loader.exec_module(mod)
    return mod


def _arm_motion_pending(bot, tmp_path, chat_id: int = 123, n: int = 3) -> Path:
    """Stash a frames-dir in pending the way _videoref_intercept would."""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (frames_dir / f"frame_{i:03d}.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    bot._VIDEOREF_PENDING[chat_id] = {"frames_dir": frames_dir}
    return frames_dir


# ── D1: handoff — stash {best, motion_prompt} + button instead of stub ────────


def test_swapanim_est_is_single_source_swap_plus_spicy_animate():
    """The quote == charge: est = swap + spicy animate, one helper, one number."""
    bot = _get_bot_module()
    from app.services.block_m2_video.engines.capabilities import caps_for
    caps = caps_for("spicy")
    expected = bot.VIDEOREF_SWAP_USD + caps.cost_for(
        bot.VIDEOREF_ANIM_SECONDS, bot.VIDEOREF_ANIM_RESOLUTION
    )
    assert bot._videoref_swapanim_est() == expected


def test_motion_success_stashes_swap_pending_and_shows_button(tmp_path):
    """After Веха C success: best+prompt stashed, '🎭 Свап + анимация' button shown."""
    bot = _get_bot_module()
    _arm_motion_pending(bot, tmp_path)
    prompt = "turns head slowly left, locked static camera, photorealistic"
    ok = VideoMotionResult(prompt=prompt, usage={"t": 1}, cost_usd=0.31)
    best = Path("frame_001.jpg")
    bot._USERNAME_BY_CHAT["123"] = "frienduser"
    with patch.object(bot, "_check_limit", return_value=(True, "")), \
         patch.object(bot, "select_best_frame", return_value=best), \
         patch.object(bot, "generate_video_motion_prompt", return_value=ok), \
         patch.object(bot._cost, "record_cost"), \
         patch.object(bot, "_send_local_photo") as photo, \
         patch.object(bot, "send_with_keyboard") as skb, \
         patch.object(bot, "send"):
        bot._videoref_motion_run("123")

    # frame photo + motion prompt caption still shown (Веха C output intact)
    photo.assert_called_once()
    # handoff: best frame + motion prompt stashed for the swap+animate step
    assert 123 in bot._VIDEOREF_SWAP_PENDING
    pend = bot._VIDEOREF_SWAP_PENDING[123]
    assert pend["best_frame"] == best
    assert pend["motion_prompt"] == prompt
    # button '🎭 Свап + анимация (~$est)' with callback vref:swapanim
    skb.assert_called_once()
    kb = skb.call_args.args[2]
    btn = kb[0][0]
    assert btn["callback_data"] == "vref:swapanim"
    assert f"{bot._videoref_swapanim_est():.2f}" in btn["text"]


def test_motion_refusal_does_not_stash_swap_pending(tmp_path):
    """No usable prompt → nothing to swap → no swap-pending, no button."""
    bot = _get_bot_module()
    _arm_motion_pending(bot, tmp_path)
    refusal = VideoMotionResult(prompt=None, usage={"t": 1}, cost_usd=0.28)
    with patch.object(bot, "_check_limit", return_value=(True, "")), \
         patch.object(bot, "select_best_frame", return_value=Path("frame_001.jpg")), \
         patch.object(bot, "generate_video_motion_prompt", return_value=refusal), \
         patch.object(bot._cost, "record_cost"), \
         patch.object(bot._whitelist, "load_admin_user_id", return_value=999), \
         patch.object(bot, "send_with_keyboard") as skb, \
         patch.object(bot, "send"):
        bot._videoref_motion_run("123")

    assert 123 not in bot._VIDEOREF_SWAP_PENDING
    skb.assert_not_called()
