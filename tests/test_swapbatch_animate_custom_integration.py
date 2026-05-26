# -*- coding: utf-8 -*-
"""Integration smoke tests for the custom-prompts animation flow (Day 6).

Exercises parser → orchestrator → handler end-to-end with a real in-memory
``BatchOrchestrator`` and a mocked animation engine (the ``animate_fn``).
The bot-wiring layer (``tools/jarvis_smart_telegram_control.py``) is exercised
only for its default-prompt substitution rule, replicated here in the test's
``animate_fn`` so the "all defaults == /swapbatch_animate_yes" claim is verified.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.handlers.face_swap_handler import FaceSwapHandler
from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator,
    STATE_AWAITING_CUSTOM_PROMPTS,
    STATE_AWAITING_CUSTOM_PROMPTS_CONFIRM,
    STATE_SWAP_DONE,
)

# Mirrors the bot wiring's default in jarvis_smart_telegram_control.py: a
# custom-flow photo with a None (default) prompt animates with this string,
# which is exactly what /swapbatch_animate_yes always uses.
_DEFAULT_MOTION_PROMPT = "a cinematic portrait, soft natural light"


def _make_photo(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    return p


def _make_handler(tmp_path: Path):
    validator = MagicMock()
    validator.count_faces.return_value = 1
    orch = BatchOrchestrator(
        state_root=tmp_path / "batches", validator=validator
    )
    return FaceSwapHandler(orchestrator=orch), orch


def _seed_swap_done(handler, orch, tmp_path, n=3):
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


def _capturing_animate_fn(tmp_path):
    """Returns (animate_fn, calls) where calls records effective prompts."""
    calls: list[tuple[int, str]] = []
    video = _make_photo(tmp_path, "v.mp4")

    async def animate_fn(swapped, idx, prompt, cancel_check):
        effective = prompt or _DEFAULT_MOTION_PROMPT
        calls.append((idx, effective))
        return video

    return animate_fn, calls


@pytest.mark.anyio
async def test_full_custom_flow_confirm(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=3)

    r = handler.handle_animate_custom(42)
    assert orch.status(42) == STATE_AWAITING_CUSTOM_PROMPTS
    assert len(r.numbered_photos) == 3

    r = handler.consume_custom_prompts_text(
        42, "1. walking\n2. dancing\n3. jumping"
    )
    assert orch.status(42) == STATE_AWAITING_CUSTOM_PROMPTS_CONFIRM
    assert "/swapbatch_confirm" in r.text

    animate_fn, calls = _capturing_animate_fn(tmp_path)
    r = await handler.run_custom_animate_phase(42, animate_fn)
    assert calls == [(0, "walking"), (1, "dancing"), (2, "jumping")]
    assert "Animate завершён" in r.text
    assert orch.get(42) is None  # pruned


@pytest.mark.anyio
async def test_all_defaults_equivalent_to_animate_yes(tmp_path):
    """Empty/skip everywhere → every photo animates with the default prompt,
    exactly as /swapbatch_animate_yes would."""
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=2)
    handler.handle_animate_custom(42)
    handler.consume_custom_prompts_text(42, "1. /skip\n2. /skip")

    animate_fn, calls = _capturing_animate_fn(tmp_path)
    await handler.run_custom_animate_phase(42, animate_fn)
    assert calls == [
        (0, _DEFAULT_MOTION_PROMPT),
        (1, _DEFAULT_MOTION_PROMPT),
    ]


@pytest.mark.anyio
async def test_apply_partial_fills_defaults(tmp_path):
    """Too few prompts: custom for the ones given, default for the rest."""
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=3)
    handler.handle_animate_custom(42)
    r = handler.consume_custom_prompts_text(42, "1. walking")
    assert "/swapbatch_apply_partial" in r.text

    # /swapbatch_apply_partial routes to the same confirm worker.
    animate_fn, calls = _capturing_animate_fn(tmp_path)
    await handler.run_custom_animate_phase(42, animate_fn)
    assert calls == [
        (0, "walking"),
        (1, _DEFAULT_MOTION_PROMPT),
        (2, _DEFAULT_MOTION_PROMPT),
    ]


def test_parse_error_then_valid(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=2)
    handler.handle_animate_custom(42)

    r = handler.consume_custom_prompts_text(42, "no numbers here")
    assert "⚠️" in r.text
    assert orch.status(42) == STATE_AWAITING_CUSTOM_PROMPTS  # still awaiting

    r = handler.consume_custom_prompts_text(42, "1. a\n2. b")
    assert orch.status(42) == STATE_AWAITING_CUSTOM_PROMPTS_CONFIRM


@pytest.mark.anyio
async def test_retry_path(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=2)
    handler.handle_animate_custom(42)
    handler.consume_custom_prompts_text(42, "1. wrong\n2. wrong")

    r = handler.handle_retry(42)
    assert orch.status(42) == STATE_AWAITING_CUSTOM_PROMPTS
    assert len(r.numbered_photos) == 2

    handler.consume_custom_prompts_text(42, "1. right\n2. right")
    animate_fn, calls = _capturing_animate_fn(tmp_path)
    await handler.run_custom_animate_phase(42, animate_fn)
    assert calls == [(0, "right"), (1, "right")]


def test_cancel_path_closes_session(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=2)
    handler.handle_animate_custom(42)
    r = handler.handle_no(42)
    assert "без анимации" in r.text
    assert orch.get(42) is None


@pytest.mark.anyio
async def test_per_photo_failure_does_not_halt_batch(tmp_path):
    handler, orch = _make_handler(tmp_path)
    _seed_swap_done(handler, orch, tmp_path, n=3)
    handler.handle_animate_custom(42)
    handler.consume_custom_prompts_text(42, "1. a\n2. b\n3. c")

    video = _make_photo(tmp_path, "v.mp4")

    async def animate_fn(swapped, idx, prompt, cancel_check):
        if idx == 1:
            raise RuntimeError("pod timeout")
        return video

    r = await handler.run_custom_animate_phase(42, animate_fn)
    # 2 of 3 succeeded; the batch finished despite the middle failure.
    assert "2 видео" in r.text
    assert "1 не удалось" in r.text
