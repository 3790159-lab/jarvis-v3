"""/videoref wardrobe toggle ("Не раздевать") — TDD spy-teeth.

Root cause of the live-test exposure bug: /videoref never passed a `wardrobe` arg
anywhere, so assemble_animate_prompt silently fell back to its own internal default
wardrobe="safe" (negative-only, unreliably enforced). Fix: default the pending state to
wardrobe_mode="preserve" (positive clothing anchor + strong negative — /swapbatch's own
default) and thread it through as a fourth toggle row (vref:ward:<mode>), mirroring the
existing engine_mode/vref:eng: pattern exactly.

CRITICAL teeth: (1) default-without-tap now assembles a preserve-mode prompt (the fix
itself), (2) preserve adds a POSITIVE anchor, not just a negative (distinguishes it from
the old accidental safe fallback), (3) engine_mode and wardrobe_mode are independent axes.

All heavy boundaries (limit gate, swap engine, animate engine, cost ledger, telegram) are
patched except where a test explicitly needs the REAL assemble_animate_prompt output
(prompt-content teeth use a real FaceSwapHandler + BatchOrchestrator, no engine call).
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
        "jarvis_tg_ctrl_vrefward",
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
    """Identical helper to test_videoref_second_engine.py, duplicated so this file is
    independently runnable."""
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


def _make_real_handler(tmp_path):
    """Real FaceSwapHandler + BatchOrchestrator (no session for the chat used) — for
    teeth that must assert on the ACTUAL assembled prompt text, not a mocked call."""
    from app.handlers.face_swap_handler import FaceSwapHandler
    from app.services.block_m2_face_swap.batch_orchestrator import BatchOrchestrator
    orch = BatchOrchestrator(state_root=tmp_path / "batches", validator=MagicMock())
    return FaceSwapHandler(orchestrator=orch)


# ── Tooth 1: default without any tap -> preserve, not the old accidental safe ──


def test_handoff_defaults_wardrobe_mode_to_preserve(tmp_path):
    bot = _get_bot_module()
    _run_handoff(bot, tmp_path / "ward_default", 10)
    assert bot._VIDEOREF_SWAP_PENDING[123]["wardrobe_mode"] == "preserve"


def test_build_single_animate_request_default_wardrobe_assembles_preserve_prompt(tmp_path):
    """THE FIX: no wardrobe passed -> real assembled prompt carries the preserve
    positive anchor, NOT the old accidental 'safe' (negative-only, unreliable) fallback."""
    handler = _make_real_handler(tmp_path)
    req = handler.build_single_animate_request(
        999, image_path="x.jpg", motion="turns head",
    )
    assert "keeping original clothing" in req.prompt


# ── Tooth 2: tap vref:ward:spicy flips state and the assembled prompt changes ──


def test_wardrobe_toggle_switches_mode_and_redraws():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "spicy", "wardrobe_mode": "preserve",
    }
    with patch.object(bot, "send_with_keyboard") as skb, patch.object(bot, "send"):
        bot._videoref_wardrobe_toggle("123", "spicy")
    pend = bot._VIDEOREF_SWAP_PENDING[123]
    assert pend["wardrobe_mode"] == "spicy"
    skb.assert_called_once()


def test_wardrobe_toggle_back_to_preserve():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "wardrobe_mode": "spicy",
    }
    with patch.object(bot, "send_with_keyboard"), patch.object(bot, "send"):
        bot._videoref_wardrobe_toggle("123", "preserve")
    assert bot._VIDEOREF_SWAP_PENDING[123]["wardrobe_mode"] == "preserve"


def test_wardrobe_toggle_without_pending_is_soft_and_does_not_crash():
    bot = _get_bot_module()
    assert 123 not in bot._VIDEOREF_SWAP_PENDING
    with patch.object(bot, "send") as snd:
        bot._videoref_wardrobe_toggle("123", "spicy")
    assert 123 not in bot._VIDEOREF_SWAP_PENDING
    assert snd.called


def test_wardrobe_toggle_invalid_mode_falls_back_to_preserve_and_does_not_raise():
    """Invalid input never corrupts pending -> safe fallback is preserve (NOT spicy —
    the safe direction for an unrecognized value is the protective one)."""
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "wardrobe_mode": "spicy",
    }
    with patch.object(bot, "send_with_keyboard") as skb, patch.object(bot, "send"):
        bot._videoref_wardrobe_toggle("123", "not_a_real_mode")
    assert bot._VIDEOREF_SWAP_PENDING[123]["wardrobe_mode"] == "preserve"
    skb.assert_called_once()


def test_dispatch_vref_ward_routes_to_toggle():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "wardrobe_mode": "preserve",
    }
    cq = {
        "id": "cqid", "data": "vref:ward:spicy",
        "message": {"chat": {"id": 123}, "message_id": 1},
        "from": {"id": 123},
    }
    with patch.object(bot, "answer_callback_query") as ack, \
         patch.object(bot, "_videoref_wardrobe_toggle") as toggle, \
         patch.object(bot, "_is_admin_id", return_value=True):
        bot.handle_callback_query(cq, {})
    ack.assert_called_once()
    toggle.assert_called_once_with("123", "spicy")


def test_keyboard_gains_fourth_wardrobe_row(tmp_path):
    bot = _get_bot_module()
    _run_handoff(bot, tmp_path / "ward_row", 10)
    rows = bot._videoref_duration_keyboard(123)
    assert len(rows) == 4
    ward_row = rows[3][0]
    assert "раздевать" in ward_row["text"].lower()
    assert ward_row["callback_data"] == "vref:ward:spicy"


def test_keyboard_spicy_wardrobe_row_offers_preserve_back():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "spicy", "wardrobe_mode": "spicy",
    }
    rows = bot._videoref_duration_keyboard(123)
    ward_row = rows[3][0]
    assert ward_row["callback_data"] == "vref:ward:preserve"


# ── Tooth 3: preserve = positive anchor + negative, spicy = neither ────────────


def test_preserve_wardrobe_prompt_has_positive_anchor_not_just_negative(tmp_path):
    handler = _make_real_handler(tmp_path)
    req = handler.build_single_animate_request(
        999, image_path="x.jpg", motion="turns head", wardrobe="preserve",
    )
    assert "keeping original clothing" in req.prompt
    assert "exposed chest" in req.negative_prompt


def test_spicy_wardrobe_prompt_has_no_clothing_anchor_or_negative(tmp_path):
    handler = _make_real_handler(tmp_path)
    req = handler.build_single_animate_request(
        999, image_path="x.jpg", motion="turns head", wardrobe="spicy",
    )
    assert "keeping original clothing" not in req.prompt
    assert "undressing" not in req.negative_prompt


# ── Tooth 4: engine_mode + wardrobe_mode independent, both thread through ──────


def test_wardrobe_toggle_does_not_touch_engine_mode():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "seedance", "wardrobe_mode": "preserve",
    }
    with patch.object(bot, "send_with_keyboard"), patch.object(bot, "send"):
        bot._videoref_wardrobe_toggle("123", "spicy")
    pend = bot._VIDEOREF_SWAP_PENDING[123]
    assert pend["engine_mode"] == "seedance"      # untouched by wardrobe toggle
    assert pend["wardrobe_mode"] == "spicy"


def test_engine_toggle_does_not_touch_wardrobe_mode():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "spicy", "wardrobe_mode": "spicy",
    }
    with patch.object(bot, "send_with_keyboard"), patch.object(bot, "send"):
        bot._videoref_engine_toggle("123", "seedance")
    pend = bot._VIDEOREF_SWAP_PENDING[123]
    assert pend["wardrobe_mode"] == "spicy"        # untouched by engine toggle
    assert pend["engine_mode"] == "seedance"


def test_do_animate_threads_both_engine_mode_and_wardrobe_mode(monkeypatch):
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
        123, handler, Path("/tmp/s.jpg"), "m",
        seconds=10, engine_mode="seedance", wardrobe_mode="spicy",
    )
    assert fake_select.mode == "seedance"
    kw = handler.build_single_animate_request.call_args.kwargs
    assert kw["engine_mode"] == "seedance"
    assert kw["wardrobe"] == "spicy"


def test_do_animate_default_wardrobe_mode_is_preserve_when_omitted(monkeypatch):
    """CRITICAL: no wardrobe_mode passed to _videoref_do_animate -> still 'preserve'
    (the function's own default), not the old accidental 'safe'."""
    bot = _get_bot_module()
    import app.services.block_m2_video.engines.router as router_mod
    import app.services.block_m2_video.batch_animate as ba_mod
    handler = MagicMock()
    handler.build_single_animate_request.return_value = "REQ"

    async def fake_select(mode):
        return "ENGINE"

    async def fake_animate(engine, reqs, concurrency=1):
        return [Path("/tmp/video.mp4")]

    class FakeRouter:
        def select(self, mode):
            return fake_select(mode)

    monkeypatch.setattr(router_mod, "EngineRouter", FakeRouter)
    monkeypatch.setattr(ba_mod, "animate_batch", fake_animate)

    bot._videoref_do_animate(123, handler, Path("/tmp/s.jpg"), "m")
    kw = handler.build_single_animate_request.call_args.kwargs
    assert kw["wardrobe"] == "preserve"


def test_stages_reads_wardrobe_mode_from_pending_and_threads_to_do_animate():
    bot = _get_bot_module()
    handler = MagicMock()
    pend = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "spicy", "wardrobe_mode": "spicy",
    }
    with patch.object(bot, "_videoref_do_swap", return_value=Path("/tmp/s.jpg")), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_videoref_do_animate", return_value=Path("/tmp/v.mp4")) as an, \
         patch.object(bot._cost, "record_cost"), \
         patch.object(bot, "_send_local_video"), patch.object(bot, "send"):
        bot._videoref_swapanim_stages(
            "123", "/tmp/face.jpg", pend, bot._videoref_swapanim_est(10)
        )
    assert an.call_args.kwargs.get("wardrobe_mode") == "spicy"


def test_stages_regression_no_wardrobe_mode_key_defaults_to_preserve():
    """Old-shape pending (no wardrobe_mode key at all) -> stages still passes
    'preserve' explicitly to do_animate (fix applies even to stale pending dicts)."""
    bot = _get_bot_module()
    handler = MagicMock()
    pend = {"best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10}
    with patch.object(bot, "_videoref_do_swap", return_value=Path("/tmp/s.jpg")), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_videoref_do_animate", return_value=Path("/tmp/v.mp4")) as an, \
         patch.object(bot._cost, "record_cost"), \
         patch.object(bot, "_send_local_video"), patch.object(bot, "send"):
        bot._videoref_swapanim_stages(
            "123", "/tmp/face.jpg", pend, bot._videoref_swapanim_est(10)
        )
    assert an.call_args.kwargs.get("wardrobe_mode", "preserve") == "preserve"


# ── Tooth 5: vref:ward: never collides with sbward:, /swapbatch untouched ──────


def test_dispatch_sbward_does_not_touch_videoref_wardrobe_toggle():
    bot = _get_bot_module()
    handler = MagicMock()
    handler.handle_wardrobe_button.return_value = {"inline_keyboard": []}
    cq = {
        "id": "cqid", "data": "sbward:on",
        "message": {"chat": {"id": 123}, "message_id": 1},
        "from": {"id": 123},
    }
    with patch.object(bot, "answer_callback_query"), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_swapbatch_apply_reply"), \
         patch.object(bot, "send_with_keyboard"), \
         patch.object(bot, "_videoref_wardrobe_toggle") as vward_toggle, \
         patch.object(bot, "_is_admin_id", return_value=True):
        bot.handle_callback_query(cq, {})
    vward_toggle.assert_not_called()


def test_vref_ward_prefix_covered_by_existing_friend_allowlist():
    bot = _get_bot_module()
    assert "vref:" in bot.FRIEND_ALLOWED_CALLBACK_PREFIXES
    assert "sbward:" in bot.FRIEND_ALLOWED_CALLBACK_PREFIXES


# ── Tooth 6: wardrobe never enters the cost formula ─────────────────────────────


def test_est_signature_unaffected_by_wardrobe_no_param_exists():
    """_videoref_swapanim_est has no wardrobe parameter at all — wardrobe is
    prompt-only, never priced."""
    bot = _get_bot_module()
    import inspect
    sig = inspect.signature(bot._videoref_swapanim_est)
    assert "wardrobe" not in sig.parameters
    assert "wardrobe_mode" not in sig.parameters


def test_quoted_equals_charged_unaffected_by_wardrobe_mode():
    """TEETH: gate est == sum of per-stage records regardless of wardrobe_mode —
    quoted == charged holds independent of the wardrobe axis."""
    bot = _get_bot_module()
    handler = MagicMock()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "spicy", "wardrobe_mode": "spicy",
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
    assert gate["v"] == bot._videoref_swapanim_est(10, engine_mode="spicy")
    assert gate["v"] == charged
