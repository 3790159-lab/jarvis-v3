# -*- coding: utf-8 -*-
"""Tests for ``generate_video_motion_prompt`` (Веха C / Задача 3).

The FIRST paid task of Веха C: one Grok multi-image call writes a motion prompt
RE-CREATING the movement across the ordered frames. Pure generation — billing /
money-gate is Задача 4. ``analyze_images_detailed`` is mocked so no paid call
runs here.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from app.services.block_m2_video import motion_prompt_ai
from app.services.block_m2_video.motion_prompt_ai import (
    MOTION_VISION_QUESTION,
    VIDEO_MOTION_VISION_QUESTION,
    VideoMotionResult,
    generate_video_motion_prompt,
)
from app.services.grok_vision import GrokVisionResult


def _frames(n: int = 3) -> list[str]:
    return [f"frame_{i:03d}.jpg" for i in range(n)]


# ── valid reply ──────────────────────────────────────────────────────────────


def test_valid_reply_returns_cleaned_prompt_with_usage_cost(monkeypatch):
    raw = (
        "Here is the motion prompt: turns head slowly to the left, raises right "
        "hand toward face, gentle forward lean, locked static camera, photorealistic"
    )
    fake = MagicMock(
        return_value=GrokVisionResult(
            text=raw, usage={"prompt_tokens": 1200}, cost_usd=0.31
        )
    )
    monkeypatch.setattr(motion_prompt_ai.grok_vision, "analyze_images_detailed", fake)

    result = generate_video_motion_prompt(_frames())

    assert isinstance(result, VideoMotionResult)
    # preamble stripped by _clean
    assert result.prompt is not None
    assert result.prompt.startswith("turns head slowly to the left")
    assert "Here is the motion prompt" not in result.prompt
    # usage/cost surfaced from the SAME call for the Задача 4 ledger
    assert result.usage == {"prompt_tokens": 1200}
    assert result.cost_usd == 0.31
    # called once, with the VIDEO question (not the static single-photo one)
    fake.assert_called_once()
    args, _ = fake.call_args
    assert args[0] == _frames()
    assert args[1] == VIDEO_MOTION_VISION_QUESTION


def test_long_reply_is_clamped(monkeypatch):
    monkeypatch.setenv("WAVESPEED_PROMPT_MAX_CHARS", "40")
    long = "turns head left, " * 20 + "locked static camera, photorealistic"
    fake = MagicMock(return_value=GrokVisionResult(text=long, usage={}, cost_usd=0.3))
    monkeypatch.setattr(motion_prompt_ai.grok_vision, "analyze_images_detailed", fake)

    result = generate_video_motion_prompt(_frames())
    assert result.prompt is not None
    assert len(result.prompt) <= 40


# ── refusals → prompt=None but cost visible ──────────────────────────────────


def test_refusal_opener_layer1_returns_none_prompt_keeps_cost(monkeypatch):
    fake = MagicMock(
        return_value=GrokVisionResult(
            text="I'm sorry, I can't help with that request.",
            usage={"prompt_tokens": 900},
            cost_usd=0.28,
        )
    )
    monkeypatch.setattr(motion_prompt_ai.grok_vision, "analyze_images_detailed", fake)

    result = generate_video_motion_prompt(_frames())
    assert result.prompt is None
    # xAI billed the refusal call — cost stays visible for Задача 4 to log
    assert result.cost_usd == 0.28
    assert result.usage == {"prompt_tokens": 900}


def test_refusal_phrase_layer2_returns_none_prompt(monkeypatch):
    fake = MagicMock(
        return_value=GrokVisionResult(
            text="This content is inappropriate, so here, no prompt.",
            cost_usd=0.2,
        )
    )
    monkeypatch.setattr(motion_prompt_ai.grok_vision, "analyze_images_detailed", fake)
    assert generate_video_motion_prompt(_frames()).prompt is None


def test_no_comma_layer3_returns_none_prompt(monkeypatch):
    fake = MagicMock(return_value=GrokVisionResult(text="nope", cost_usd=0.1))
    monkeypatch.setattr(motion_prompt_ai.grok_vision, "analyze_images_detailed", fake)
    assert generate_video_motion_prompt(_frames()).prompt is None


# ── empty / no key ───────────────────────────────────────────────────────────


def test_empty_reply_returns_none_prompt(monkeypatch):
    """No key / API error degrade to text='' inside analyze_images_detailed."""
    fake = MagicMock(return_value=GrokVisionResult(text=""))
    monkeypatch.setattr(motion_prompt_ai.grok_vision, "analyze_images_detailed", fake)
    result = generate_video_motion_prompt(_frames())
    assert result.prompt is None


# ── empty frame list → NO paid call ──────────────────────────────────────────


def test_empty_frame_list_does_not_call_grok(monkeypatch):
    fake = MagicMock(return_value=GrokVisionResult(text="x, y"))
    monkeypatch.setattr(motion_prompt_ai.grok_vision, "analyze_images_detailed", fake)

    result = generate_video_motion_prompt([])
    assert result.prompt is None
    fake.assert_not_called()  # money: never pay to analyze nothing


# ── question is distinct from the static single-photo one ─────────────────────


def test_video_question_differs_from_static_question():
    assert VIDEO_MOTION_VISION_QUESTION != MOTION_VISION_QUESTION
    # it must be about motion ACROSS frames, not a single photo
    assert "frames" in VIDEO_MOTION_VISION_QUESTION.lower()


# ── $0 purity: no billing calls inside the generator ─────────────────────────


def test_generator_does_not_touch_billing(monkeypatch):
    """Pure generation: it must not call check_limit / record_cost."""
    fake = MagicMock(
        return_value=GrokVisionResult(text="turns head left, locked static camera", cost_usd=0.3)
    )
    monkeypatch.setattr(motion_prompt_ai.grok_vision, "analyze_images_detailed", fake)

    # If the module ever imports a cost tracker, these spies would catch a call.
    called = {"check": 0, "record": 0}
    if hasattr(motion_prompt_ai, "check_limit"):
        monkeypatch.setattr(
            motion_prompt_ai, "check_limit",
            lambda *a, **k: called.__setitem__("check", called["check"] + 1),
        )
    if hasattr(motion_prompt_ai, "record_cost"):
        monkeypatch.setattr(
            motion_prompt_ai, "record_cost",
            lambda *a, **k: called.__setitem__("record", called["record"] + 1),
        )

    generate_video_motion_prompt(_frames())
    assert called == {"check": 0, "record": 0}
