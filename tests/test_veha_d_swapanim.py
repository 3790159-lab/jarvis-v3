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


# ── D2: button vref:swapanim → arm face-awaiting + ask for the source face ────


def test_swapanim_button_with_pending_arms_and_asks_face():
    """Click with a live swap-pending → face-awaiting armed, bot asks for a face."""
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("frame_001.jpg"), "motion_prompt": "turns head left",
    }
    with patch.object(bot, "send") as snd:
        bot._videoref_swapanim_arm("123")

    assert 123 in bot._VIDEOREF_FACE_AWAITING
    assert any("лицо" in c.args[1].lower() for c in snd.call_args_list)


def test_swapanim_button_without_pending_is_soft_and_does_not_arm():
    """Stale button / double-click (no pending) → soft message, NOT armed."""
    bot = _get_bot_module()
    assert 123 not in bot._VIDEOREF_SWAP_PENDING
    with patch.object(bot, "send") as snd:
        bot._videoref_swapanim_arm("123")

    assert 123 not in bot._VIDEOREF_FACE_AWAITING   # never arm without pending
    assert snd.called                                # but the user still hears back


def test_vref_swapanim_in_friend_callback_allowlist():
    """vref: prefix already friend-allowed → vref:swapanim covered for friends."""
    bot = _get_bot_module()
    assert "vref:" in bot.FRIEND_ALLOWED_CALLBACK_PREFIXES


# ── D3: source-face intercept — strict flag-gate isolation (the riskiest) ─────

_FACE_MSG = {"photo": [{"file_id": "p", "file_size": 1000, "file_unique_id": "u"}]}


def test_face_intercept_armed_downloads_disarms_and_spawns_worker():
    """Armed + photo → True, download called, flag cleared, worker spawned."""
    bot = _get_bot_module()
    bot._VIDEOREF_FACE_AWAITING.add(123)
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("frame_001.jpg"), "motion_prompt": "x",
    }
    with patch.object(bot, "_download_telegram_file", return_value="/tmp/face.jpg") as dl, \
         patch("threading.Thread") as Thr, \
         patch.object(bot, "_videoref_swapanim_run") as worker, \
         patch.object(bot, "send"):
        consumed = bot._videoref_face_intercept("123", _FACE_MSG)

    assert consumed is True
    dl.assert_called_once()
    assert 123 not in bot._VIDEOREF_FACE_AWAITING        # disarmed after capture
    Thr.assert_called_once()                              # worker spawned
    assert Thr.call_args.kwargs["target"] is worker
    assert Thr.call_args.kwargs["args"] == ("123", "/tmp/face.jpg")
    Thr.return_value.start.assert_called_once()


def test_face_intercept_not_armed_returns_false_and_no_download():
    """CRITICAL: no face-awaiting flag → False, no download — foreign photo
    falls straight through to swapbatch / /animate / normal photo flow."""
    bot = _get_bot_module()
    assert 123 not in bot._VIDEOREF_FACE_AWAITING
    with patch.object(bot, "_download_telegram_file") as dl, \
         patch("threading.Thread") as Thr, \
         patch.object(bot, "_videoref_swapanim_run") as worker, \
         patch.object(bot, "send") as snd:
        consumed = bot._videoref_face_intercept("123", _FACE_MSG)

    assert consumed is False     # not ours → caller continues the chain
    dl.assert_not_called()       # never touch a foreign photo
    Thr.assert_not_called()
    worker.assert_not_called()
    snd.assert_not_called()


def test_face_intercept_disarms_so_next_photo_falls_through():
    """Flag cleared after handling → a following photo is NOT hijacked
    (e.g. the next swapbatch photo)."""
    bot = _get_bot_module()
    bot._VIDEOREF_FACE_AWAITING.add(123)
    bot._VIDEOREF_SWAP_PENDING[123] = {"best_frame": Path("f.jpg"), "motion_prompt": "x"}
    msg2 = {"photo": [{"file_id": "q", "file_size": 1000, "file_unique_id": "v"}]}
    with patch.object(bot, "_download_telegram_file", return_value="/tmp/face.jpg"), \
         patch("threading.Thread"), \
         patch.object(bot, "_videoref_swapanim_run"), \
         patch.object(bot, "send"):
        first = bot._videoref_face_intercept("123", _FACE_MSG)
        second = bot._videoref_face_intercept("123", msg2)

    assert first is True
    assert second is False        # disarmed → second photo not intercepted


def test_face_intercept_download_failure_still_disarms():
    """Download error → flag still cleared (never stuck armed), no worker."""
    bot = _get_bot_module()
    bot._VIDEOREF_FACE_AWAITING.add(123)
    bot._VIDEOREF_SWAP_PENDING[123] = {"best_frame": Path("f.jpg"), "motion_prompt": "x"}
    with patch.object(bot, "_download_telegram_file", return_value=None), \
         patch("threading.Thread") as Thr, \
         patch.object(bot, "_videoref_swapanim_run") as worker, \
         patch.object(bot, "send") as snd:
        consumed = bot._videoref_face_intercept("123", _FACE_MSG)

    assert consumed is True
    assert 123 not in bot._VIDEOREF_FACE_AWAITING        # not stuck armed
    Thr.assert_not_called()                               # no worker on failure
    worker.assert_not_called()
    assert snd.called                                     # user told it failed


def test_face_intercept_armed_but_no_photo_keeps_flag_and_returns_false():
    """Armed but message has no photo (e.g. text) → False, flag KEPT so the
    real face photo arriving next is still caught (mirror of video intercept)."""
    bot = _get_bot_module()
    bot._VIDEOREF_FACE_AWAITING.add(123)
    with patch.object(bot, "_download_telegram_file") as dl, \
         patch("threading.Thread") as Thr, \
         patch.object(bot, "send"):
        consumed = bot._videoref_face_intercept("123", {"text": "hi"})

    assert consumed is False
    assert 123 in bot._VIDEOREF_FACE_AWAITING            # still waiting for the face
    dl.assert_not_called()
    Thr.assert_not_called()


# ── D4: money gate with TEETH — single check_limit on the FULL est ────────────


def _arm_swap_pending(bot, chat_id: int = 123):
    bot._VIDEOREF_SWAP_PENDING[chat_id] = {
        "best_frame": Path("frame_001.jpg"), "motion_prompt": "turns head left",
    }


def test_over_limit_blocks_all_paid_stages_and_charges_nothing():
    """CULMINATION teeth: friend over limit → check_limit False → NO swap, NO
    animate (stages seam 0), NO record_cost; soft refusal + admin ping."""
    bot = _get_bot_module()
    _arm_swap_pending(bot)
    with patch.object(bot, "_check_limit",
                      return_value=(False, "Дневной лимит $5.00 исчерпан")) as cl, \
         patch.object(bot, "_videoref_swapanim_stages") as stages, \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot._whitelist, "load_admin_user_id", return_value=999), \
         patch.object(bot, "send") as snd:
        bot._videoref_swapanim_run("123", "/tmp/face.jpg")

    cl.assert_called_once()
    stages.assert_not_called()      # no swap, no animate — gate blocked everything
    rec.assert_not_called()         # nothing charged
    # honest refusal to the friend (BEFORE any swap) + admin visibility ping
    assert any(str(c.args[0]) == "123" and "🚫" in c.args[1] for c in snd.call_args_list)
    assert any(c.args[0] == "999" for c in snd.call_args_list), "admin must be notified"


def test_under_limit_passes_gate_into_paid_stages():
    """Under limit → check_limit True → proceeds into the paid stages (D5)."""
    bot = _get_bot_module()
    _arm_swap_pending(bot)
    with patch.object(bot, "_check_limit", return_value=(True, "")), \
         patch.object(bot, "_videoref_swapanim_stages") as stages, \
         patch.object(bot, "send"):
        bot._videoref_swapanim_run("123", "/tmp/face.jpg")

    stages.assert_called_once()     # gate open → swap+animate stages run


def test_gate_est_is_single_source_quoted_equals_charged():
    """The gate estimate is _videoref_swapanim_est() — the SAME number the button
    quoted (no hardcoded $0.52 in the gate)."""
    bot = _get_bot_module()
    _arm_swap_pending(bot)
    with patch.object(bot, "_check_limit", return_value=(True, "")) as cl, \
         patch.object(bot, "_videoref_swapanim_stages"), \
         patch.object(bot, "send"):
        bot._videoref_swapanim_run("123", "/tmp/face.jpg")

    assert cl.call_args.kwargs["estimated_usd"] == bot._videoref_swapanim_est()


def test_no_pending_stale_worker_refuses_before_gate():
    """Stale worker (no swap-pending) → refuse before even quoting the limit."""
    bot = _get_bot_module()
    assert 123 not in bot._VIDEOREF_SWAP_PENDING
    with patch.object(bot, "_check_limit") as cl, \
         patch.object(bot, "_videoref_swapanim_stages") as stages, \
         patch.object(bot, "send") as snd:
        bot._videoref_swapanim_run("123", "/tmp/face.jpg")

    cl.assert_not_called()
    stages.assert_not_called()
    assert snd.called
