"""/videoref second engine (movable engine toggle) — TDD spy-teeth.

Second engine choice (spicy/seedance) becomes a third toggle row on the existing
swap+animate keyboard. engine_mode threads through est -> gate -> stages -> do_animate,
replacing four hardcoded "spicy" occurrences. CRITICAL: every read site must default
to "spicy" when pending has no "engine_mode" key (old-shape pending / no tap) so
existing /videoref behavior stays byte-identical.

All heavy boundaries (limit gate, swap engine, animate engine, cost ledger, telegram)
are patched — no paid call runs here. Mirrors tests/test_veha_d_swapanim.py conventions.
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
        "jarvis_tg_ctrl_vref2eng",
        ROOT / "tools" / "jarvis_smart_telegram_control.py",
    )
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_CHAT_ID": "123",
    }):
        spec.loader.exec_module(mod)
    return mod


def _run_handoff(bot, frames_root, duration):
    """Run a successful motion analysis with a given reference duration stashed
    (identical helper to test_veha_d_swapanim.py, duplicated here so this file is
    independently runnable)."""
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


# ── engine_mode threads through _videoref_swapanim_est ────────────────────────


def test_est_default_engine_mode_is_spicy_byte_for_byte():
    """CRITICAL regression: no engine_mode arg -> identical number to today."""
    bot = _get_bot_module()
    from app.services.block_m2_video.engines.capabilities import caps_for
    expected = bot.VIDEOREF_SWAP_USD + caps_for("spicy").cost_for(5, "720p")
    assert bot._videoref_swapanim_est() == expected
    assert bot._videoref_swapanim_est(engine_mode="spicy") == bot._videoref_swapanim_est()


def test_est_engine_mode_seedance_uses_seedance_cost():
    bot = _get_bot_module()
    from app.services.block_m2_video.engines.capabilities import caps_for
    expected = bot.VIDEOREF_SWAP_USD + caps_for("seedance").cost_for(10, "720p")
    assert bot._videoref_swapanim_est(10, engine_mode="seedance") == expected


# ── handoff stashes engine_mode default ────────────────────────────────────────


def test_handoff_defaults_engine_mode_to_spicy(tmp_path):
    bot = _get_bot_module()
    _run_handoff(bot, tmp_path / "eng_default", 10)
    assert bot._VIDEOREF_SWAP_PENDING[123]["engine_mode"] == "spicy"


# ── _videoref_duration_keyboard becomes engine-aware ───────────────────────────


def test_keyboard_regression_no_tap_identical_durations_and_prices(tmp_path):
    """CRITICAL regression: default engine_mode -> byte-identical to the
    pre-second-engine keyboard (same 3 buttons, same callback_data, same prices)."""
    bot = _get_bot_module()
    _run_handoff(bot, tmp_path / "regress", 10)
    rows = bot._videoref_duration_keyboard(123)
    dur_row = rows[0]
    assert [b["callback_data"] for b in dur_row] == ["vref:sa:5", "vref:sa:10", "vref:sa:15"]
    for sec, btn in zip((5, 10, 15), dur_row):
        assert f"{bot._videoref_swapanim_est(sec):.2f}" in btn["text"]
    assert rows[1][0]["callback_data"] in ("vref:smooth:on", "vref:smooth:off")


def test_keyboard_default_engine_row_offers_seedance(tmp_path):
    bot = _get_bot_module()
    _run_handoff(bot, tmp_path / "eng_row", 10)
    rows = bot._videoref_duration_keyboard(123)
    assert len(rows) == 3
    eng_row = rows[2][0]
    assert "WaveSpeed" in eng_row["text"]
    assert eng_row["callback_data"] == "vref:eng:seedance"


def test_keyboard_seedance_mode_uses_seedance_durations_and_prices(tmp_path):
    bot = _get_bot_module()
    _run_handoff(bot, tmp_path / "eng_sd", 10)
    bot._VIDEOREF_SWAP_PENDING[123]["engine_mode"] = "seedance"
    rows = bot._videoref_duration_keyboard(123)
    dur_row = rows[0]
    assert [b["callback_data"] for b in dur_row] == ["vref:sa:5", "vref:sa:10"]
    for sec, btn in zip((5, 10), dur_row):
        assert f"{bot._videoref_swapanim_est(sec, engine_mode='seedance'):.2f}" in btn["text"]
    eng_row = rows[2][0]
    assert "Seedance" in eng_row["text"]
    assert eng_row["callback_data"] == "vref:eng:spicy"


# ── _videoref_engine_toggle: switch + snap + redraw ────────────────────────────


def test_engine_toggle_switches_mode_and_snaps_duration():
    """Toggle to seedance while at 15с (WaveSpeed-only length) -> snaps to 10с
    (Seedance's max, allowed_durations=(5, 10))."""
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 15,
        "smooth": False, "engine_mode": "spicy",
    }
    with patch.object(bot, "send_with_keyboard") as skb, patch.object(bot, "send"):
        bot._videoref_engine_toggle("123", "seedance")
    pend = bot._VIDEOREF_SWAP_PENDING[123]
    assert pend["engine_mode"] == "seedance"
    assert pend["seconds"] == 10
    skb.assert_called_once()


def test_engine_toggle_back_to_spicy_does_not_resnap_valid_length():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "seedance",
    }
    with patch.object(bot, "send_with_keyboard"), patch.object(bot, "send"):
        bot._videoref_engine_toggle("123", "spicy")
    pend = bot._VIDEOREF_SWAP_PENDING[123]
    assert pend["engine_mode"] == "spicy"
    assert pend["seconds"] == 10          # 10с valid for WaveSpeed too -> unchanged


def test_engine_toggle_without_pending_is_soft_and_does_not_crash():
    bot = _get_bot_module()
    assert 123 not in bot._VIDEOREF_SWAP_PENDING
    with patch.object(bot, "send") as snd:
        bot._videoref_engine_toggle("123", "seedance")
    assert 123 not in bot._VIDEOREF_SWAP_PENDING
    assert snd.called


def test_engine_toggle_invalid_mode_falls_back_to_spicy_and_does_not_raise():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "spicy",
    }
    with patch.object(bot, "send_with_keyboard") as skb, patch.object(bot, "send"):
        bot._videoref_engine_toggle("123", "not_a_real_engine")
    pend = bot._VIDEOREF_SWAP_PENDING[123]
    assert pend["engine_mode"] == "spicy"       # invalid input never corrupts pending
    assert pend["seconds"] == 10                # spicy's own durations include 10 -> unchanged
    skb.assert_called_once()


# ── vref:eng: dispatch — isolated from sbeng:/anim: ────────────────────────────


def test_dispatch_vref_eng_seedance_routes_to_toggle():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 15,
        "engine_mode": "spicy",
    }
    cq = {
        "id": "cqid", "data": "vref:eng:seedance",
        "message": {"chat": {"id": 123}, "message_id": 1},
        "from": {"id": 123},
    }
    with patch.object(bot, "answer_callback_query") as ack, \
         patch.object(bot, "_videoref_engine_toggle") as toggle, \
         patch.object(bot, "_is_admin_id", return_value=True):
        bot.handle_callback_query(cq, {})
    ack.assert_called_once()
    toggle.assert_called_once_with("123", "seedance")


def test_dispatch_sbeng_does_not_touch_videoref_engine_toggle():
    """Isolation teeth: sbeng: (swapbatch) never reaches the videoref toggle."""
    bot = _get_bot_module()
    handler = MagicMock()
    handler.handle_set_engine.return_value = "ok"
    handler.build_quality_keyboard.return_value = {"inline_keyboard": []}
    cq = {
        "id": "cqid", "data": "sbeng:seedance",
        "message": {"chat": {"id": 123}, "message_id": 1},
        "from": {"id": 123},
    }
    with patch.object(bot, "answer_callback_query"), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_swapbatch_apply_reply"), \
         patch.object(bot, "send_with_keyboard"), \
         patch.object(bot, "_videoref_engine_toggle") as veng_toggle, \
         patch.object(bot, "_is_admin_id", return_value=True):
        bot.handle_callback_query(cq, {})
    veng_toggle.assert_not_called()


def test_vref_eng_prefix_covered_by_existing_friend_allowlist():
    """vref: prefix already friend-allowed (unchanged) -> vref:eng: needs no new
    allowlist entry."""
    bot = _get_bot_module()
    assert "vref:" in bot.FRIEND_ALLOWED_CALLBACK_PREFIXES
    assert "sbeng:" in bot.FRIEND_ALLOWED_CALLBACK_PREFIXES
    assert "anim:" in bot.FRIEND_ALLOWED_CALLBACK_PREFIXES


# ── _videoref_do_animate: engine_mode replaces hardcoded "spicy" ───────────────


def test_do_animate_default_engine_mode_is_spicy_byte_for_byte(monkeypatch):
    """CRITICAL regression: no engine_mode passed -> identical selection to today."""
    bot = _get_bot_module()
    import app.services.block_m2_video.engines.router as router_mod
    import app.services.block_m2_video.batch_animate as ba_mod
    handler = MagicMock()
    handler.build_single_animate_request.return_value = "REQ"

    async def fake_select(mode):
        fake_select.mode = mode
        return "ENGINE"

    async def fake_animate(engine, reqs, concurrency=1):
        return [Path("/tmp/video.mp4")]

    class FakeRouter:
        def select(self, mode):
            return fake_select(mode)

    monkeypatch.setattr(router_mod, "EngineRouter", FakeRouter)
    monkeypatch.setattr(ba_mod, "animate_batch", fake_animate)

    bot._videoref_do_animate(123, handler, Path("/tmp/s.jpg"), "m")
    assert fake_select.mode == "spicy"
    assert handler.build_single_animate_request.call_args.kwargs["engine_mode"] == "spicy"


def test_do_animate_seedance_engine_mode_threads_through(monkeypatch):
    bot = _get_bot_module()
    import app.services.block_m2_video.engines.router as router_mod
    import app.services.block_m2_video.batch_animate as ba_mod
    handler = MagicMock()
    handler.build_single_animate_request.return_value = "REQ"

    async def fake_select(mode):
        fake_select.mode = mode
        return "ENGINE"

    async def fake_animate(engine, reqs, concurrency=1):
        return [Path("/tmp/video.mp4")]

    class FakeRouter:
        def select(self, mode):
            return fake_select(mode)

    monkeypatch.setattr(router_mod, "EngineRouter", FakeRouter)
    monkeypatch.setattr(ba_mod, "animate_batch", fake_animate)

    bot._videoref_do_animate(
        123, handler, Path("/tmp/s.jpg"), "m", seconds=10, engine_mode="seedance"
    )
    assert fake_select.mode == "seedance"
    assert handler.build_single_animate_request.call_args.kwargs["engine_mode"] == "seedance"


# ── money gate reads engine_mode from pending ──────────────────────────────────


def test_gate_regression_no_engine_mode_defaults_to_spicy_est():
    """CRITICAL regression: pend without 'engine_mode' key -> gate est identical
    to pre-second-engine behavior."""
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
    }
    with patch.object(bot, "_check_limit", return_value=(True, "")) as cl, \
         patch.object(bot, "_videoref_swapanim_stages"), patch.object(bot, "send"):
        bot._videoref_swapanim_run("123", "/tmp/face.jpg")
    assert cl.call_args.kwargs["estimated_usd"] == bot._videoref_swapanim_est(10)


def test_gate_reads_est_with_engine_mode_seedance():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "seedance",
    }
    with patch.object(bot, "_check_limit", return_value=(True, "")) as cl, \
         patch.object(bot, "_videoref_swapanim_stages"), patch.object(bot, "send"):
        bot._videoref_swapanim_run("123", "/tmp/face.jpg")
    assert cl.call_args.kwargs["estimated_usd"] == bot._videoref_swapanim_est(
        10, engine_mode="seedance"
    )
    assert cl.call_args.kwargs["estimated_usd"] != bot._videoref_swapanim_est(
        10, engine_mode="spicy"
    )


# ── per-stage billing reads engine_mode from pending ───────────────────────────


def test_stages_regression_no_engine_mode_defaults_spicy_byte_for_byte():
    """CRITICAL: pend without 'engine_mode' key -> do_animate called with
    engine_mode='spicy' and billed at spicy cost, identical to pre-arc behavior."""
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
    assert an.call_args.kwargs.get("engine_mode", "spicy") == "spicy"
    amounts = [c.args[2] for c in rec.call_args_list]
    assert amounts[1] == caps_for("spicy").cost_for(10, "720p")


def test_stages_seedance_charges_seedance_cost_and_selects_seedance_engine():
    bot = _get_bot_module()
    handler = MagicMock()
    pend = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "seedance",
    }
    with patch.object(bot, "_videoref_do_swap", return_value=Path("/tmp/s.jpg")), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_videoref_do_animate", return_value=Path("/tmp/v.mp4")) as an, \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "_send_local_video"), patch.object(bot, "send"):
        bot._videoref_swapanim_stages(
            "123", "/tmp/face.jpg", pend,
            bot._videoref_swapanim_est(10, engine_mode="seedance"),
        )
    from app.services.block_m2_video.engines.capabilities import caps_for
    assert an.call_args.kwargs.get("engine_mode") == "seedance"
    amounts = [c.args[2] for c in rec.call_args_list]
    assert amounts[1] == caps_for("seedance").cost_for(10, "720p")


def test_quoted_equals_charged_for_seedance_path():
    """TEETH: seedance gate est == sum of per-stage records — quoted == charged
    holds on the new engine path exactly as it does on spicy."""
    bot = _get_bot_module()
    handler = MagicMock()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "seedance",
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
    assert gate["v"] == bot._videoref_swapanim_est(10, engine_mode="seedance")
    assert gate["v"] == charged
