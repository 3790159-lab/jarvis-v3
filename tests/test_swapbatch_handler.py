# -*- coding: utf-8 -*-
"""Tests for :class:`FaceSwapHandler`. The orchestrator is real (in-memory),
the validator is mocked."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.handlers.face_swap_handler import FaceSwapHandler, HandlerReply
from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator,
    STATE_AWAITING_CUSTOM_PROMPTS,
    STATE_AWAITING_CUSTOM_PROMPTS_CONFIRM,
    STATE_EXPECTING_SOURCE,
    STATE_EXPECTING_TARGETS,
    STATE_SWAP_DONE,
    STATE_TARGETS_RECEIVED,
)


def _make_photo(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    return p


def _make_validator(face_count: int = 1) -> MagicMock:
    v = MagicMock()
    v.count_faces.return_value = face_count
    return v


def _make_handler(tmp_path: Path, face_count: int = 1) -> tuple[FaceSwapHandler, BatchOrchestrator]:
    orch = BatchOrchestrator(
        state_root=tmp_path / "batches",
        validator=_make_validator(face_count=face_count),
    )
    return FaceSwapHandler(orchestrator=orch), orch


# ── help / status ────────────────────────────────────────────────────────────


def test_help_lists_all_commands(tmp_path):
    handler, _ = _make_handler(tmp_path)
    r = handler.handle_help()
    for cmd in (
        "/swapbatch_source", "/swapbatch_batch", "/swapbatch_go",
        "/swapbatch_animate_yes", "/swapbatch_animate_no",
        "/swapbatch_cancel", "/swapbatch_status",
    ):
        assert cmd in r.text


def test_status_with_no_session(tmp_path):
    handler, _ = _make_handler(tmp_path)
    r = handler.handle_status(42)
    assert "Нет активного" in r.text


# ── happy flow ───────────────────────────────────────────────────────────────


def test_full_setup_flow_through_targets(tmp_path):
    handler, orch = _make_handler(tmp_path)
    src = _make_photo(tmp_path, "src.jpg")
    t1 = _make_photo(tmp_path, "t1.jpg")
    t2 = _make_photo(tmp_path, "t2.jpg")

    assert "лицом" in handler.handle_source_intent(42).text
    r = handler.consume_source(42, src)
    assert r.consumed is True
    assert "Source принят" in r.text

    assert handler.handle_batch_intent(42).consumed is True
    r = handler.consume_targets_album(42, [t1, t2])
    assert "📊" in r.text  # cost report header
    assert orch.status(42) == STATE_TARGETS_RECEIVED


def test_consume_source_returns_not_consumed_when_no_session(tmp_path):
    handler, _ = _make_handler(tmp_path)
    r = handler.consume_source(42, _make_photo(tmp_path, "x.jpg"))
    assert r.consumed is False
    assert r.text is None


def test_consume_targets_returns_not_consumed_when_state_wrong(tmp_path):
    handler, _ = _make_handler(tmp_path)
    handler.handle_source_intent(42)
    # We're in EXPECTING_SOURCE — album consume should NOT fire.
    r = handler.consume_targets_album(42, [_make_photo(tmp_path, "t.jpg")])
    assert r.consumed is False


# ── error flows ──────────────────────────────────────────────────────────────


def test_consume_source_no_face_keeps_state(tmp_path):
    handler, orch = _make_handler(tmp_path, face_count=0)
    handler.handle_source_intent(42)
    r = handler.consume_source(42, _make_photo(tmp_path, "src.jpg"))
    assert r.consumed is True
    assert "⚠️" in r.text
    assert orch.status(42) == STATE_EXPECTING_SOURCE  # still waiting


def test_cancel_when_no_session(tmp_path):
    handler, _ = _make_handler(tmp_path)
    r = handler.handle_cancel(42)
    assert "Нет активного" in r.text


def test_cancel_in_setup_clears_session(tmp_path):
    handler, orch = _make_handler(tmp_path)
    handler.handle_source_intent(42)
    r = handler.handle_cancel(42)
    assert "отмена" in r.text.lower()
    assert orch.get(42) is None


# ── animation phases ────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_run_swap_phase_delivers_photos_when_done(tmp_path):
    handler, orch = _make_handler(tmp_path)
    src = _make_photo(tmp_path, "src.jpg")
    t1 = _make_photo(tmp_path, "t1.jpg")
    handler.handle_source_intent(42)
    handler.consume_source(42, src)
    handler.handle_batch_intent(42)
    handler.consume_targets_album(42, [t1])

    swapped = _make_photo(tmp_path, "sw.png")

    async def swap_fn(source, targets, cc):
        return [swapped]

    r = await handler.run_swap_phase(42, swap_fn)
    assert "Swap завершён" in r.text
    assert r.photos == [swapped]


# ── post-swap menu (Task A) ──────────────────────────────────────────────────


async def _swap_reply(handler, orch, tmp_path, n=1):
    """Drive a chat through swap and return the run_swap_phase reply."""
    src = _make_photo(tmp_path, "src.jpg")
    targets = [_make_photo(tmp_path, f"t{i}.jpg") for i in range(n)]
    handler.handle_source_intent(42)
    handler.consume_source(42, src)
    handler.handle_batch_intent(42)
    handler.consume_targets_album(42, targets)
    swapped = [_make_photo(tmp_path, f"sw{i}.png") for i in range(n)]

    async def swap_fn(source, tgts, cc):
        return swapped

    return await handler.run_swap_phase(42, swap_fn)


@pytest.mark.anyio
async def test_post_swap_menu_lists_animate_custom(tmp_path, monkeypatch):
    # These four tests exercise the animate-ENABLED path explicitly.
    # SWAPBATCH_ANIMATE_ENABLED defaults to "0" (FIX C); pin it to "1" here so
    # the tests keep covering the enabled menu without relying on env leakage.
    monkeypatch.setenv("SWAPBATCH_ANIMATE_ENABLED", "1")
    handler, orch = _make_handler(tmp_path)
    r = await _swap_reply(handler, orch, tmp_path)
    assert "/swapbatch_animate_custom" in r.text


@pytest.mark.anyio
async def test_post_swap_menu_lists_set_quality(tmp_path, monkeypatch):
    monkeypatch.setenv("SWAPBATCH_ANIMATE_ENABLED", "1")
    handler, orch = _make_handler(tmp_path)
    r = await _swap_reply(handler, orch, tmp_path)
    assert "/swapbatch_set_quality" in r.text


@pytest.mark.anyio
async def test_post_swap_menu_lists_no(tmp_path, monkeypatch):
    monkeypatch.setenv("SWAPBATCH_ANIMATE_ENABLED", "1")
    handler, orch = _make_handler(tmp_path)
    r = await _swap_reply(handler, orch, tmp_path)
    assert "/swapbatch_no" in r.text


@pytest.mark.anyio
async def test_post_swap_menu_still_lists_animate_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("SWAPBATCH_ANIMATE_ENABLED", "1")
    handler, orch = _make_handler(tmp_path)
    r = await _swap_reply(handler, orch, tmp_path)
    assert "/swapbatch_animate_yes" in r.text


def test_handle_animate_no_terminates(tmp_path):
    handler, orch = _make_handler(tmp_path)
    src = _make_photo(tmp_path, "src.jpg")
    t1 = _make_photo(tmp_path, "t1.jpg")
    handler.handle_source_intent(42)
    handler.consume_source(42, src)
    handler.handle_batch_intent(42)
    handler.consume_targets_album(42, [t1])
    sess = orch.get(42)
    sess.status = "SWAP_DONE"
    sess.targets[0].swap_result_path = str(_make_photo(tmp_path, "sw.png"))

    r = handler.handle_animate_no(42)
    assert "Готово" in r.text
    assert orch.get(42) is None  # pruned


@pytest.mark.anyio
async def test_run_animate_phase_collects_videos(tmp_path):
    handler, orch = _make_handler(tmp_path)
    src = _make_photo(tmp_path, "src.jpg")
    t1 = _make_photo(tmp_path, "t1.jpg")
    handler.handle_source_intent(42)
    handler.consume_source(42, src)
    handler.handle_batch_intent(42)
    handler.consume_targets_album(42, [t1])
    sess = orch.get(42)
    sess.status = "SWAP_DONE"
    swapped = _make_photo(tmp_path, "sw.png")
    sess.targets[0].swap_result_path = str(swapped)

    video = _make_photo(tmp_path, "v.mp4")

    async def animate_fn(swapped_path, idx, cc):
        return video

    r = await handler.run_animate_phase(42, animate_fn)
    assert "Animate завершён" in r.text
    assert r.videos == [video]
    assert orch.get(42) is None  # pruned after success


@pytest.mark.anyio
async def test_run_animate_phase_records_cost_for_successful_videos(
    tmp_path, monkeypatch
):
    """Completing animation bills the user once per successful video (swap +
    animate), and not at all for failures."""
    from app.handlers import face_swap_handler as fsh

    calls: list[tuple] = []
    monkeypatch.setattr(
        fsh._cost, "record_cost",
        lambda uid, uname, amount, **kw: calls.append((uid, uname, amount)),
    )

    handler, orch = _make_handler(tmp_path)
    src = _make_photo(tmp_path, "src.jpg")
    targets = [_make_photo(tmp_path, f"t{i}.jpg") for i in range(2)]
    handler.handle_source_intent(42)
    handler.consume_source(42, src)
    handler.handle_batch_intent(42)
    handler.consume_targets_album(42, targets)
    sess = orch.get(42)
    sess.status = "SWAP_DONE"
    for i, t in enumerate(sess.targets):
        t.swap_result_path = str(_make_photo(tmp_path, f"sw{i}.png"))

    video = _make_photo(tmp_path, "v.mp4")

    async def animate_fn(swapped_path, idx, cc):
        # Second photo "fails" → only one successful video billed.
        if idx == 1:
            raise RuntimeError("animate boom")
        return video

    await handler.run_animate_phase(42, animate_fn, user_id=42, username="vasya")

    assert len(calls) == 1
    uid, uname, amount = calls[0]
    assert uid == 42
    assert uname == "vasya"
    # 1 successful video × (swap 0.02 + animate 0.27) = 0.29
    assert amount == pytest.approx(0.29)


# ── custom-prompts flow (Day 6) ──────────────────────────────────────────────


def _seed_swap_done(handler, orch, tmp_path, n=2):
    src = _make_photo(tmp_path, "src.jpg")
    targets = [_make_photo(tmp_path, f"t{i}.jpg") for i in range(n)]
    handler.handle_source_intent(42)
    handler.consume_source(42, src)
    handler.handle_batch_intent(42)
    handler.consume_targets_album(42, targets)
    sess = orch.get(42)
    sess.status = STATE_SWAP_DONE
    for i, t in enumerate(sess.targets):
        t.swap_result_path = str(_make_photo(tmp_path, f"sw{i}.png"))
    return sess


def test_handle_animate_custom_redisplays_numbered_photos(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=2)
    r = handler.handle_animate_custom(42)
    assert orch.status(42) == STATE_AWAITING_CUSTOM_PROMPTS
    assert r.numbered_photos is not None
    assert len(r.numbered_photos) == 2
    # Instructions must show the expected numbered format.
    assert "1." in r.text


def test_consume_custom_prompts_text_valid_shows_preview(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=2)
    handler.handle_animate_custom(42)
    r = handler.consume_custom_prompts_text(42, "1. walking\n2. dancing")
    assert r.consumed is True
    assert orch.status(42) == STATE_AWAITING_CUSTOM_PROMPTS_CONFIRM
    assert "walking" in r.text and "dancing" in r.text
    assert "/swapbatch_confirm" in r.text


def test_consume_custom_prompts_text_default_marker(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=2)
    handler.handle_animate_custom(42)
    r = handler.consume_custom_prompts_text(42, "1. walking\n2. /skip")
    assert "walking" in r.text
    assert "по умолчанию" in r.text  # default marker for photo 2


def test_consume_custom_prompts_text_parse_error_stays(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=2)
    handler.handle_animate_custom(42)
    r = handler.consume_custom_prompts_text(42, "no numbers at all")
    assert r.consumed is True
    assert "⚠️" in r.text
    assert orch.status(42) == STATE_AWAITING_CUSTOM_PROMPTS  # unchanged


def test_consume_custom_prompts_text_not_consumed_when_wrong_state(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=2)
    # Still in SWAP_DONE — text must not be consumed by the custom flow.
    r = handler.consume_custom_prompts_text(42, "1. walking\n2. dancing")
    assert r.consumed is False


def test_consume_custom_prompts_too_few_offers_partial(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=3)
    handler.handle_animate_custom(42)
    r = handler.consume_custom_prompts_text(42, "1. a\n2. b")
    assert "/swapbatch_apply_partial" in r.text


def test_consume_custom_prompts_too_many_offers_first(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=2)
    handler.handle_animate_custom(42)
    r = handler.consume_custom_prompts_text(42, "1. a\n2. b\n3. c\n4. d")
    assert "/swapbatch_apply_first" in r.text


def test_handle_no_exits_without_animation(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=2)
    r = handler.handle_no(42)
    assert "без анимации" in r.text
    assert orch.get(42) is None  # session closed


def test_handle_retry_returns_to_awaiting(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=2)
    handler.handle_animate_custom(42)
    handler.consume_custom_prompts_text(42, "1. a\n2. b")
    r = handler.handle_retry(42)
    assert orch.status(42) == STATE_AWAITING_CUSTOM_PROMPTS
    assert r.numbered_photos is not None and len(r.numbered_photos) == 2


@pytest.mark.anyio
async def test_run_custom_animate_phase_passes_prompts(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=2)
    handler.handle_animate_custom(42)
    handler.consume_custom_prompts_text(42, "1. walking\n2. /skip")

    calls = []
    video = _make_photo(tmp_path, "v.mp4")

    async def animate_fn(swapped, idx, prompt, cc):
        calls.append((idx, prompt))
        return video

    r = await handler.run_custom_animate_phase(42, animate_fn)
    assert calls == [(0, "walking"), (1, None)]
    assert "Animate завершён" in r.text
    assert r.videos == [video, video]
    assert orch.get(42) is None  # pruned after success


# ── quality settings (Task C) ────────────────────────────────────────────────


def test_set_quality_duration_only(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=1)
    r = handler.handle_set_quality(42, "duration=10")
    assert "10" in r.text
    assert orch.get(42).duration_sec == 10
    assert orch.get(42).fps == 21


def test_set_quality_no_args_shows_current(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=1)
    r = handler.handle_set_quality(42, "")
    assert "5" in r.text and "21" in r.text


def test_set_quality_no_session(tmp_path):
    handler, _ = _make_handler(tmp_path)
    r = handler.handle_set_quality(42, "duration=10")
    assert "⚠️" in r.text


def test_set_quality_duration_out_of_range_unchanged(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=1)
    r = handler.handle_set_quality(42, "duration=99")
    assert "⚠️" in r.text
    assert orch.get(42).duration_sec == 5  # unchanged


def test_set_quality_fps_boost_rejected_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.delenv("ENABLE_FPS_INTERPOLATION", raising=False)
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=1)
    r = handler.handle_set_quality(42, "fps=42")
    assert "not yet verified" in r.text
    assert orch.get(42).fps == 21  # unchanged


def test_set_quality_fps_boost_allowed_when_flag_on(tmp_path, monkeypatch):
    # fps=30 is a non-multiple of 21 — exercises the expanded [21,60] range
    # backed by exact-fps RIFE interpolation.
    monkeypatch.setenv("ENABLE_FPS_INTERPOLATION", "1")
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=1)
    r = handler.handle_set_quality(42, "duration=8 fps=30")
    assert orch.get(42).fps == 30
    assert orch.get(42).duration_sec == 8


def test_set_quality_long_duration_warning(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=1)
    r = handler.handle_set_quality(42, "duration=15")
    assert "Wan 2.2" in r.text


# ── set_prompt clamp ─────────────────────────────────────────────────────────


def test_set_prompt_within_cap_unchanged(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=1)
    r = handler.handle_set_prompt(42, "walking on the beach")
    assert orch.get(42).motion_prompt == "walking on the beach"
    assert "обрез" not in r.text


def test_set_prompt_overlong_is_clamped_and_warns(tmp_path, monkeypatch):
    monkeypatch.setenv("WAVESPEED_PROMPT_MAX_CHARS", "50")
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=1)
    long = "word " * 40  # 200 chars
    r = handler.handle_set_prompt(42, long)
    assert len(orch.get(42).motion_prompt) <= 50
    assert "обрез" in r.text


def test_set_prompt_no_session(tmp_path):
    handler, _ = _make_handler(tmp_path)
    r = handler.handle_set_prompt(42, "anything")
    assert "⚠️" in r.text


# ── set_wardrobe ─────────────────────────────────────────────────────────────


def test_set_wardrobe_preserve(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=1)
    r = handler.handle_set_wardrobe(42, "preserve")
    assert orch.get(42).wardrobe_mode == "preserve"
    assert "preserve" in r.text


def test_set_wardrobe_invalid_keeps_mode_and_shows_help(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=1)
    handler.handle_set_wardrobe(42, "preserve")
    r = handler.handle_set_wardrobe(42, "xxx")
    # Invalid mode does not change the stored value.
    assert orch.get(42).wardrobe_mode == "preserve"
    assert "preserve | safe | spicy" in r.text


def test_set_wardrobe_no_session(tmp_path):
    handler, _ = _make_handler(tmp_path)
    r = handler.handle_set_wardrobe(42, "preserve")
    assert "⚠️" in r.text


# ── engine choice (Task 9) ───────────────────────────────────────────────────


def test_engine_keyboard_lists_both_engines_with_seedance_label(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=1)
    kb = handler.build_engine_keyboard()
    flat = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
    labels = " ".join(b["text"] for row in kb["inline_keyboard"] for b in row)
    assert "sbeng:spicy" in flat
    assert "sbeng:seedance" in flat
    assert "sbeng:none" in flat
    assert "censored" in labels.lower() or "sfw" in labels.lower()  # пометка


def test_set_engine_updates_session_and_snaps_quality(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=1)
    reply = handler.handle_set_engine(42, "seedance")
    sess = orch.get(42)
    assert sess.video_engine == "seedance"
    # 15с недоступно у Seedance → дефолтная длительность снапается в допустимую
    assert sess.duration_sec in (5, 10)
    assert sess.resolution in ("480p", "720p", "1080p")
