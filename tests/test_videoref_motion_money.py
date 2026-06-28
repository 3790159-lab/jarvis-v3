"""Money-gate + wiring for the 🎬 videoref motion-analysis button (Веха C / Task 4).

The FIRST real paid videoref flow. Spy tests WITH TEETH: they assert the exact
order and count of the money calls (check_limit before any paid/scoring work,
record_cost exactly once and only on success, never on refusal/over-limit/
no-face). The heavy boundaries (limit gate, frame scoring, Grok call, cost
ledger, telegram sends) are all patched — no paid call runs here.
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

import app.services.block_m2_video.video_frames as vf  # noqa: E402
from app.services.block_m2_video.motion_prompt_ai import VideoMotionResult  # noqa: E402


def _get_bot_module():
    spec = importlib.util.spec_from_file_location(
        "jarvis_tg_ctrl_vrefmoney",
        ROOT / "tools" / "jarvis_smart_telegram_control.py",
    )
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_CHAT_ID": "123",
    }):
        spec.loader.exec_module(mod)
    return mod


def _arm_pending(bot, tmp_path, chat_id: int = 123, n: int = 3) -> Path:
    """Stash a frames-dir in pending the way _videoref_intercept would."""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (frames_dir / f"frame_{i:03d}.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    bot._VIDEOREF_PENDING[chat_id] = {"frames_dir": frames_dir}
    return frames_dir


# ── 1. over-limit → check_limit blocks, Grok 0, record_cost 0 ─────────────────


def test_over_limit_blocks_before_grok_and_charge(tmp_path):
    bot = _get_bot_module()
    _arm_pending(bot, tmp_path)
    grok = MagicMock()
    best = MagicMock(return_value=Path("frame_001.jpg"))
    with patch.object(bot, "_check_limit", return_value=(False, "Дневной лимит исчерпан")), \
         patch.object(bot, "select_best_frame", best), \
         patch.object(bot, "generate_video_motion_prompt", grok), \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "send") as snd:
        bot._videoref_motion_run("123")

    grok.assert_not_called()            # never pay when over limit
    rec.assert_not_called()             # nothing charged
    best.assert_not_called()            # not even local scoring
    assert any("🚫" in c.args[1] for c in snd.call_args_list)


# ── 2. no face → select_best_frame None, Grok 0, record_cost 0 ────────────────


def test_no_face_refuses_without_grok_or_charge(tmp_path):
    bot = _get_bot_module()
    _arm_pending(bot, tmp_path)
    grok = MagicMock()
    with patch.object(bot, "_check_limit", return_value=(True, "")), \
         patch.object(bot, "select_best_frame", return_value=None), \
         patch.object(bot, "generate_video_motion_prompt", grok), \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "send") as snd:
        bot._videoref_motion_run("123")

    grok.assert_not_called()            # no face -> nothing to swap -> no paid call
    rec.assert_not_called()
    assert any("❌" in c.args[1] for c in snd.call_args_list)


# ── 3. refusal → record_cost 0 (user not charged), cost logged, admin notified ─


def test_refusal_does_not_charge_user_but_notifies_admin(tmp_path):
    bot = _get_bot_module()
    _arm_pending(bot, tmp_path)
    refusal = VideoMotionResult(prompt=None, usage={"t": 1}, cost_usd=0.28)
    with patch.object(bot, "_check_limit", return_value=(True, "")), \
         patch.object(bot, "select_best_frame", return_value=Path("frame_001.jpg")), \
         patch.object(bot, "generate_video_motion_prompt", return_value=refusal), \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot._whitelist, "load_admin_user_id", return_value=999), \
         patch.object(bot, "_send_local_photo") as photo, \
         patch.object(bot, "send") as snd:
        bot._videoref_motion_run("123")

    rec.assert_not_called()             # xAI billed US, not the user
    photo.assert_not_called()           # no prompt -> nothing to show
    # admin gets a visibility ping mentioning the real xAI cost
    admin_calls = [c for c in snd.call_args_list if c.args[0] == "999"]
    assert admin_calls, "admin must be notified of the unbilled refusal cost"
    assert any("0.28" in c.args[1] for c in admin_calls)
    # user gets a soft error (not the admin ping)
    assert any(str(c.args[0]) == "123" and "❌" in c.args[1] for c in snd.call_args_list)


# ── 4. success → record_cost exactly once == $0.35, frame+prompt shown ────────


def test_success_charges_once_and_shows_frame_and_prompt(tmp_path):
    bot = _get_bot_module()
    _arm_pending(bot, tmp_path)
    prompt = "turns head slowly left, raises right hand, locked static camera, photorealistic"
    ok = VideoMotionResult(prompt=prompt, usage={"t": 1}, cost_usd=0.31)
    best = Path("frame_001.jpg")
    bot._USERNAME_BY_CHAT["123"] = "frienduser"
    with patch.object(bot, "_check_limit", return_value=(True, "")), \
         patch.object(bot, "select_best_frame", return_value=best), \
         patch.object(bot, "generate_video_motion_prompt", return_value=ok), \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "_send_local_photo") as photo, \
         patch.object(bot, "send_with_keyboard") as skb, \
         patch.object(bot, "send") as snd:
        bot._videoref_motion_run("123")

    # charged exactly once, quoted == charged ($0.35 from the single source)
    rec.assert_called_once()
    args = rec.call_args.args
    assert args[0] == 123                       # chat/user id
    assert args[2] == bot.VIDEOREF_MOTION_USD   # amount
    assert bot.VIDEOREF_MOTION_USD == 0.35
    # frame photo shown with the motion prompt as caption
    photo.assert_called_once()
    assert str(best) in str(photo.call_args.args[1])
    assert prompt in (photo.call_args.kwargs.get("caption", "") or "".join(map(str, photo.call_args.args)))
    # Веха D handoff: duration-choice buttons offered (replaces the old stub).
    # No ref duration in this test → proposed 5с is the first button.
    skb.assert_called_once()
    assert skb.call_args.args[2][0][0]["callback_data"] == "vref:sa:5"


# ── 5. friend gate: vref: allowed for friends ────────────────────────────────


def test_vref_in_friend_callback_allowlist():
    bot = _get_bot_module()
    assert "vref:" in bot.FRIEND_ALLOWED_CALLBACK_PREFIXES


# ── 6. double-click → second run has no pending → 0 Grok, 0 charge ────────────


def test_double_click_second_run_no_pending_no_charge(tmp_path):
    bot = _get_bot_module()
    _arm_pending(bot, tmp_path)
    ok = VideoMotionResult(prompt="turns head left, locked static camera", cost_usd=0.31)
    grok = MagicMock(return_value=ok)
    with patch.object(bot, "_check_limit", return_value=(True, "")), \
         patch.object(bot, "select_best_frame", return_value=Path("frame_001.jpg")), \
         patch.object(bot, "generate_video_motion_prompt", grok), \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "_send_local_photo"), \
         patch.object(bot, "send"):
        bot._videoref_motion_run("123")   # first click consumes pending
        bot._videoref_motion_run("123")   # second click: nothing pending

    assert grok.call_count == 1           # only the first click paid
    assert rec.call_count == 1            # charged exactly once, not twice


# ── 7. button rendered with price BEFORE click (opt-in) ──────────────────────


def test_intercept_shows_motion_button_with_price(tmp_path):
    bot = _get_bot_module()
    bot._VIDEOREF_AWAITING.add(123)
    frames = [Path("frame_000.jpg"), Path("frame_001.jpg")]
    with patch.object(bot, "_download_telegram_file", return_value="/tmp/ref.mp4"), \
         patch.object(vf, "slice_video_to_frames", return_value=frames), \
         patch.object(bot, "send_with_keyboard") as skb, \
         patch.object(bot, "send"):
        consumed = bot._videoref_intercept(
            "123", {"video": {"file_id": "v", "file_size": 1_000_000, "duration": 5}}
        )

    assert consumed is True
    skb.assert_called_once()
    # button label carries the price; callback is vref:motion
    kb = skb.call_args.args[2]
    btn = kb[0][0]
    assert "0.35" in btn["text"]
    assert btn["callback_data"] == "vref:motion"
    # frames-dir stashed for the callback
    assert 123 in bot._VIDEOREF_PENDING
