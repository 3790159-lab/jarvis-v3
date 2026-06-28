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
    # I2.2: three duration buttons (no ref duration → propose 5с, starred)
    skb.assert_called_once()
    btns = skb.call_args.args[2][0]
    assert [b["callback_data"] for b in btns] == ["vref:sa:5", "vref:sa:10", "vref:sa:15"]
    assert "⭐" in btns[0]["text"]                       # proposed 5с
    assert f"{bot._videoref_swapanim_est(5):.2f}" in btns[0]["text"]


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


# ── D5: the bridge — real swap+animate with PER-STAGE billing ─────────────────

_PEND = {"best_frame": Path("frame_001.jpg"), "motion_prompt": "turns head left"}


def _spicy_anim_cost(bot):
    from app.services.block_m2_video.engines.capabilities import caps_for
    return caps_for("spicy").cost_for(bot.VIDEOREF_ANIM_SECONDS, bot.VIDEOREF_ANIM_RESOLUTION)


def test_both_stages_succeed_charges_per_stage_sum_equals_est_and_sends_video():
    """swap ok + animate ok → record $0.02 then $0.50, sum == est, video sent."""
    bot = _get_bot_module()
    bot._USERNAME_BY_CHAT["123"] = "frienduser"
    handler = MagicMock()
    est = bot._videoref_swapanim_est()
    with patch.object(bot, "_videoref_do_swap", return_value=Path("/tmp/swapped.jpg")) as sw, \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_videoref_do_animate", return_value=Path("/tmp/video.mp4")) as an, \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "_send_local_video") as vid, \
         patch.object(bot, "send"):
        bot._videoref_swapanim_stages("123", "/tmp/face.jpg", _PEND, est)

    sw.assert_called_once()
    an.assert_called_once()
    amounts = [c.args[2] for c in rec.call_args_list]
    assert amounts == [bot.VIDEOREF_SWAP_USD, _spicy_anim_cost(bot)]
    assert round(sum(amounts), 6) == round(est, 6)      # quoted == charged, closed loop
    vid.assert_called_once()
    assert "video.mp4" in str(vid.call_args.args[1])


def test_swap_ok_animate_fail_charges_only_swap_and_sends_no_video():
    """swap ok + animate fail → record ONLY $0.02 (never for the failed animation)."""
    bot = _get_bot_module()
    handler = MagicMock()
    with patch.object(bot, "_videoref_do_swap", return_value=Path("/tmp/swapped.jpg")), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_videoref_do_animate", return_value=None), \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "_send_local_video") as vid, \
         patch.object(bot, "send") as snd:
        bot._videoref_swapanim_stages("123", "/tmp/face.jpg", _PEND, bot._videoref_swapanim_est())

    assert rec.call_count == 1
    assert rec.call_args_list[0].args[2] == bot.VIDEOREF_SWAP_USD    # swap only
    vid.assert_not_called()
    assert any("❌" in c.args[1] for c in snd.call_args_list)


def test_swap_fail_charges_nothing_and_never_animates():
    """swap fail → $0 charged, animate never attempted, no video."""
    bot = _get_bot_module()
    with patch.object(bot, "_videoref_do_swap", return_value=None), \
         patch.object(bot, "_videoref_do_animate") as an, \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "_send_local_video") as vid, \
         patch.object(bot, "send") as snd:
        bot._videoref_swapanim_stages("123", "/tmp/face.jpg", _PEND, bot._videoref_swapanim_est())

    rec.assert_not_called()
    an.assert_not_called()      # never animate a failed swap
    vid.assert_not_called()
    assert any("❌" in c.args[1] for c in snd.call_args_list)


def test_do_swap_reuses_swap_batch_as_batch_of_one(monkeypatch):
    """Reuse 1:1: _videoref_do_swap calls get_swap_engine().swap_batch([best])."""
    bot = _get_bot_module()
    import app.services.block_m2_face_swap.engines.factory as fac

    async def fake_swap(source, targets):
        fake_swap.seen = (source, targets)
        return [Path("/tmp/swapped.jpg")]

    engine = MagicMock()
    engine.swap_batch = fake_swap
    monkeypatch.setattr(fac, "get_swap_engine", lambda: engine)

    out = bot._videoref_do_swap("/tmp/face.jpg", Path("frame_001.jpg"))
    assert out == Path("/tmp/swapped.jpg")
    _src, targets = fake_swap.seen
    assert len(targets) == 1        # batch-of-1


def test_do_animate_reuses_pattern_with_spicy_5s_720p(monkeypatch):
    """Reuse 1:1 + engine_mode='spicy' explicit: build_single_animate_request +
    animate_batch via EngineRouter, spicy/5s/720p, motion passed through."""
    bot = _get_bot_module()
    import app.services.block_m2_video.engines.router as router_mod
    import app.services.block_m2_video.batch_animate as ba_mod

    handler = MagicMock()
    handler.build_single_animate_request.return_value = "REQ"

    async def fake_select(mode):
        fake_select.mode = mode
        return "ENGINE"

    async def fake_animate(engine, reqs, concurrency=1):
        fake_animate.seen = (engine, reqs, concurrency)
        return [Path("/tmp/video.mp4")]

    class FakeRouter:
        def select(self, mode):
            return fake_select(mode)

    monkeypatch.setattr(router_mod, "EngineRouter", FakeRouter)
    monkeypatch.setattr(ba_mod, "animate_batch", fake_animate)

    out = bot._videoref_do_animate(123, handler, Path("/tmp/swapped.jpg"), "motion text")

    assert out == Path("/tmp/video.mp4")
    kw = handler.build_single_animate_request.call_args.kwargs
    assert kw["engine_mode"] == "spicy"
    assert kw["seconds"] == bot.VIDEOREF_ANIM_SECONDS
    assert kw["resolution"] == bot.VIDEOREF_ANIM_RESOLUTION
    assert kw["motion"] == "motion text"
    assert fake_select.mode == "spicy"
    assert fake_animate.seen[2] == 1        # concurrency=1


# ── D6: anti-double-click — pending consumed (pop) before any spend ───────────


def test_first_run_pops_pending_and_runs_stages():
    """First trigger → pending consumed (popped) before spend, stages run."""
    bot = _get_bot_module()
    _arm_swap_pending(bot)
    with patch.object(bot, "_check_limit", return_value=(True, "")), \
         patch.object(bot, "_videoref_swapanim_stages") as stages, \
         patch.object(bot, "send"):
        bot._videoref_swapanim_run("123", "/tmp/face.jpg")

    assert 123 not in bot._VIDEOREF_SWAP_PENDING     # consumed before stages
    stages.assert_called_once()


def test_double_run_second_finds_no_pending_and_spends_nothing():
    """Double-click / repeated photo → second run finds empty pending → 0 swap,
    0 animate, 0 charge ($0.52 never billed twice)."""
    bot = _get_bot_module()
    _arm_swap_pending(bot)
    with patch.object(bot, "_check_limit", return_value=(True, "")) as cl, \
         patch.object(bot, "_videoref_swapanim_stages") as stages, \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "send"):
        bot._videoref_swapanim_run("123", "/tmp/face.jpg")   # first: pop + run
        bot._videoref_swapanim_run("123", "/tmp/face.jpg")   # second: empty

    assert stages.call_count == 1     # only the first trigger reached the stages
    assert cl.call_count == 1         # second run bailed BEFORE the gate
    rec.assert_not_called()


# ── I2.1: duration capture + est(seconds) — price becomes a function of length ─


def test_est_scales_with_duration():
    """est = swap $0.02 + spicy caps: 5с=$0.52, 10с=$1.02, 15с=$1.52."""
    bot = _get_bot_module()
    assert round(bot._videoref_swapanim_est(5), 2) == 0.52
    assert round(bot._videoref_swapanim_est(10), 2) == 1.02
    assert round(bot._videoref_swapanim_est(15), 2) == 1.52


def test_intercept_captures_reference_duration_into_pending():
    import app.services.block_m2_video.video_frames as vf
    bot = _get_bot_module()
    bot._VIDEOREF_AWAITING.add(123)
    frames = [Path("frame_000.jpg"), Path("frame_001.jpg")]
    with patch.object(bot, "_download_telegram_file", return_value="/tmp/ref.mp4"), \
         patch.object(vf, "slice_video_to_frames", return_value=frames), \
         patch.object(bot, "send_with_keyboard"), \
         patch.object(bot, "send"):
        bot._videoref_intercept(
            "123", {"video": {"file_id": "v", "file_size": 1_000_000, "duration": 8}}
        )
    assert bot._VIDEOREF_PENDING[123].get("duration") == 8


def _run_handoff(bot, frames_root, duration):
    """Run a successful motion analysis with a given reference duration stashed."""
    frames_dir = frames_root / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    (frames_dir / "frame_000.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    pend = {"frames_dir": frames_dir}
    if duration is not None:
        pend["duration"] = duration
    bot._VIDEOREF_PENDING[123] = pend
    ok = VideoMotionResult(prompt="turns head left", cost_usd=0.31)
    with patch.object(bot, "_check_limit", return_value=(True, "")), \
         patch.object(bot, "select_best_frame", return_value=Path("frame_001.jpg")), \
         patch.object(bot, "generate_video_motion_prompt", return_value=ok), \
         patch.object(bot._cost, "record_cost"), \
         patch.object(bot, "_send_local_photo"), \
         patch.object(bot, "send_with_keyboard") as skb, \
         patch.object(bot, "send"):
        bot._videoref_motion_run("123")
    return skb


def test_handoff_proposes_snapped_duration(tmp_path):
    """Proposed length = caps.snap_duration(ref): 7->5, 8->10, 13->15."""
    for ref, proposed in [(7, 5), (8, 10), (13, 15)]:
        bot = _get_bot_module()
        _run_handoff(bot, tmp_path / f"r{ref}", ref)
        assert bot._VIDEOREF_SWAP_PENDING[123]["seconds"] == proposed, f"ref={ref}"


def test_handoff_no_duration_falls_back_to_5s(tmp_path):
    bot = _get_bot_module()
    _run_handoff(bot, tmp_path / "nodur", None)
    assert bot._VIDEOREF_SWAP_PENDING[123]["seconds"] == 5   # money-safe cheapest


def test_handoff_button_label_uses_proposed_duration_est(tmp_path):
    bot = _get_bot_module()
    skb = _run_handoff(bot, tmp_path / "d10", 10)      # ref 10 -> propose 10
    btns = skb.call_args.args[2][0]
    proposed = [b for b in btns if "⭐" in b["text"]][0]
    assert proposed["callback_data"] == "vref:sa:10"
    assert f"{bot._videoref_swapanim_est(10):.2f}" in proposed["text"]   # 1.02


def test_gate_reads_est_of_pending_seconds_not_hardcoded():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
    }
    with patch.object(bot, "_check_limit", return_value=(True, "")) as cl, \
         patch.object(bot, "_videoref_swapanim_stages"), \
         patch.object(bot, "send"):
        bot._videoref_swapanim_run("123", "/tmp/face.jpg")
    assert cl.call_args.kwargs["estimated_usd"] == bot._videoref_swapanim_est(10)


def test_animate_record_is_caps_cost_for_pending_seconds():
    bot = _get_bot_module()
    handler = MagicMock()
    pend = {"best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10}
    with patch.object(bot, "_videoref_do_swap", return_value=Path("/tmp/s.jpg")), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_videoref_do_animate", return_value=Path("/tmp/v.mp4")) as an, \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "_send_local_video"), patch.object(bot, "send"):
        bot._videoref_swapanim_stages(
            "123", "/tmp/face.jpg", pend, bot._videoref_swapanim_est(10)
        )
    from app.services.block_m2_video.engines.capabilities import caps_for
    amounts = [c.args[2] for c in rec.call_args_list]
    assert amounts[0] == bot.VIDEOREF_SWAP_USD                   # swap flat
    assert amounts[1] == caps_for("spicy").cost_for(10, "720p")  # animate scales
    assert an.call_args.args[4] == 10        # pending seconds threaded into animate


def test_quoted_equals_charged_for_seconds_10():
    """TEETH: gate est == sum of per-stage records == est(10) for a 10s clip."""
    bot = _get_bot_module()
    handler = MagicMock()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
    }
    gate = {}

    def _cl(uid, *, estimated_usd):
        gate["v"] = estimated_usd
        return (True, "")

    with patch.object(bot, "_check_limit", side_effect=_cl), \
         patch.object(bot, "_videoref_do_swap", return_value=Path("/tmp/s.jpg")), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_videoref_do_animate", return_value=Path("/tmp/v.mp4")), \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "_send_local_video"), patch.object(bot, "send"):
        bot._videoref_swapanim_run("123", "/tmp/face.jpg")

    charged = sum(c.args[2] for c in rec.call_args_list)
    assert gate["v"] == bot._videoref_swapanim_est(10)   # quoted (the gate)
    assert gate["v"] == charged                          # == charged (stage sum)


# ── I2.2: duration-choice buttons (one-tap) ───────────────────────────────────


def test_handoff_shows_three_duration_buttons_with_per_length_prices(tmp_path):
    bot = _get_bot_module()
    skb = _run_handoff(bot, tmp_path / "d10", 10)        # ref 10 -> propose 10
    btns = skb.call_args.args[2][0]
    assert [b["callback_data"] for b in btns] == ["vref:sa:5", "vref:sa:10", "vref:sa:15"]
    # each button quotes est(its own length)
    assert f"{bot._videoref_swapanim_est(5):.2f}" in btns[0]["text"]
    assert f"{bot._videoref_swapanim_est(10):.2f}" in btns[1]["text"]
    assert f"{bot._videoref_swapanim_est(15):.2f}" in btns[2]["text"]
    # only the proposed (10) is starred
    assert "⭐" in btns[1]["text"]
    assert "⭐" not in btns[0]["text"]
    assert "⭐" not in btns[2]["text"]


def test_choose_duration_sets_pending_seconds_and_arms_face():
    """One-tap: vref:sa:15 → pending seconds=15 + face-awaiting armed."""
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
    }
    with patch.object(bot, "send") as snd:
        bot._videoref_swapanim_arm("123", 15)

    assert bot._VIDEOREF_SWAP_PENDING[123]["seconds"] == 15   # chosen overrides proposed
    assert 123 in bot._VIDEOREF_FACE_AWAITING
    assert any("лицо" in c.args[1].lower() for c in snd.call_args_list)


def test_choose_without_pending_is_soft_and_does_not_arm():
    bot = _get_bot_module()
    with patch.object(bot, "send") as snd:
        bot._videoref_swapanim_arm("123", 10)
    assert 123 not in bot._VIDEOREF_FACE_AWAITING
    assert snd.called


def test_chosen_length_not_proposed_flows_into_billing():
    """TEETH: proposed 10 but user picks 15 → gate/charge use est(15)=$1.52,
    not est(10). The chosen length must win end-to-end."""
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
    }
    with patch.object(bot, "send"):
        bot._videoref_swapanim_arm("123", 15)            # change 10 -> 15
    with patch.object(bot, "_check_limit", return_value=(True, "")) as cl, \
         patch.object(bot, "_videoref_swapanim_stages"), \
         patch.object(bot, "send"):
        bot._videoref_swapanim_run("123", "/tmp/face.jpg")

    assert cl.call_args.kwargs["estimated_usd"] == bot._videoref_swapanim_est(15)
    assert cl.call_args.kwargs["estimated_usd"] != bot._videoref_swapanim_est(10)


# ── I3.1: smooth est + toggle (the RIFE call itself is I3.2) ───────────────────


def _rife(bot, seconds):
    from app.handlers.face_swap_handler import rife_surcharge_usd
    return rife_surcharge_usd(1, seconds)


def test_est_includes_rife_surcharge_only_when_smooth():
    bot = _get_bot_module()
    # smooth=False is the same as the I2 price (no RIFE)
    assert bot._videoref_swapanim_est(5, smooth=False) == bot._videoref_swapanim_est(5)
    # smooth=True adds the RIFE surcharge for that length
    assert round(bot._videoref_swapanim_est(5, smooth=True), 4) == round(0.52 + _rife(bot, 5), 4)
    from app.services.block_m2_video.engines.capabilities import caps_for
    expected10 = (bot.VIDEOREF_SWAP_USD + caps_for("spicy").cost_for(10, "720p")
                  + _rife(bot, 10))
    assert bot._videoref_swapanim_est(10, smooth=True) == expected10


def test_handoff_default_smooth_off_with_toggle_button(tmp_path):
    bot = _get_bot_module()
    skb = _run_handoff(bot, tmp_path / "d10", 10)
    assert bot._VIDEOREF_SWAP_PENDING[123]["smooth"] is False    # money-safe default
    rows = skb.call_args.args[2]
    toggle = rows[1][0]                                          # row under durations
    assert "Плавность" in toggle["text"]
    assert toggle["callback_data"] == "vref:smooth:on"          # currently off → offers on


def test_smooth_toggle_writes_pending_flag():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10, "smooth": False,
    }
    with patch.object(bot, "send_with_keyboard"), patch.object(bot, "send"):
        bot._videoref_smooth_toggle("123", True)
    assert bot._VIDEOREF_SWAP_PENDING[123]["smooth"] is True
    with patch.object(bot, "send_with_keyboard"), patch.object(bot, "send"):
        bot._videoref_smooth_toggle("123", False)
    assert bot._VIDEOREF_SWAP_PENDING[123]["smooth"] is False


def test_toggle_redraws_duration_buttons_with_smooth_price():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10, "smooth": False,
    }
    with patch.object(bot, "send_with_keyboard") as skb, patch.object(bot, "send"):
        bot._videoref_smooth_toggle("123", True)
    rows = skb.call_args.args[2]
    b10 = [b for b in rows[0] if b["callback_data"] == "vref:sa:10"][0]
    assert f"{bot._videoref_swapanim_est(10, smooth=True):.2f}" in b10["text"]   # RIFE-aware
    assert rows[1][0]["callback_data"] == "vref:smooth:off"                      # now offers off


def test_gate_reads_est_with_both_seconds_and_smooth():
    """TEETH: pending smooth=True → gate quotes est(seconds, smooth=True)."""
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10, "smooth": True,
    }
    with patch.object(bot, "_check_limit", return_value=(True, "")) as cl, \
         patch.object(bot, "_videoref_swapanim_stages"), patch.object(bot, "send"):
        bot._videoref_swapanim_run("123", "/tmp/face.jpg")
    assert cl.call_args.kwargs["estimated_usd"] == bot._videoref_swapanim_est(10, smooth=True)
    assert cl.call_args.kwargs["estimated_usd"] != bot._videoref_swapanim_est(10, smooth=False)


# ── I3.2: real RIFE call in the worker, money-safe (closes the smooth axis) ────


def _smooth_pend(seconds=10):
    return {"best_frame": Path("f.jpg"), "motion_prompt": "x",
            "seconds": seconds, "smooth": True}


def test_smooth_on_success_charges_rife_and_delivers_smoothed():
    """smooth ok → record swap+anim+RIFE == est(smooth=True); smoothed delivered."""
    bot = _get_bot_module()
    handler = MagicMock()
    from app.services.block_m2_video.engines.capabilities import caps_for
    with patch.object(bot, "_videoref_do_swap", return_value=Path("/tmp/s.jpg")), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_videoref_do_animate", return_value=Path("/tmp/v.mp4")), \
         patch.object(bot, "_videoref_do_smooth", return_value=Path("/tmp/v48.mp4")) as sm, \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "_send_local_video") as vid, patch.object(bot, "send"):
        bot._videoref_swapanim_stages(
            "123", "/tmp/face.jpg", _smooth_pend(10),
            bot._videoref_swapanim_est(10, smooth=True),
        )
    sm.assert_called_once()
    amounts = [c.args[2] for c in rec.call_args_list]
    assert amounts == [bot.VIDEOREF_SWAP_USD,
                       caps_for("spicy").cost_for(10, "720p"),
                       _rife(bot, 10)]
    assert round(sum(amounts), 6) == round(bot._videoref_swapanim_est(10, smooth=True), 6)
    assert "v48.mp4" in str(vid.call_args.args[1])     # smoothed delivered


def test_smooth_on_rife_fail_delivers_original_and_no_rife_charge():
    """TEETH: RIFE fail → ORIGINAL video delivered (never lost), RIFE NOT charged."""
    bot = _get_bot_module()
    handler = MagicMock()
    from app.services.block_m2_video.engines.capabilities import caps_for
    with patch.object(bot, "_videoref_do_swap", return_value=Path("/tmp/s.jpg")), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_videoref_do_animate", return_value=Path("/tmp/v.mp4")), \
         patch.object(bot, "_videoref_do_smooth", side_effect=RuntimeError("RIFE 500")), \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "_send_local_video") as vid, patch.object(bot, "send"):
        bot._videoref_swapanim_stages(
            "123", "/tmp/face.jpg", _smooth_pend(10),
            bot._videoref_swapanim_est(10, smooth=True),
        )
    amounts = [c.args[2] for c in rec.call_args_list]
    assert amounts == [bot.VIDEOREF_SWAP_USD, caps_for("spicy").cost_for(10, "720p")]  # no RIFE
    vid.assert_called_once()
    assert "v.mp4" in str(vid.call_args.args[1])       # ORIGINAL delivered, not lost


def test_smooth_off_never_calls_rife():
    bot = _get_bot_module()
    handler = MagicMock()
    pend = {"best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10, "smooth": False}
    with patch.object(bot, "_videoref_do_swap", return_value=Path("/tmp/s.jpg")), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_videoref_do_animate", return_value=Path("/tmp/v.mp4")), \
         patch.object(bot, "_videoref_do_smooth") as sm, \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "_send_local_video") as vid, patch.object(bot, "send"):
        bot._videoref_swapanim_stages(
            "123", "/tmp/face.jpg", pend, bot._videoref_swapanim_est(10, smooth=False),
        )
    sm.assert_not_called()
    assert len(rec.call_args_list) == 2                 # swap + anim only
    assert "v.mp4" in str(vid.call_args.args[1])
