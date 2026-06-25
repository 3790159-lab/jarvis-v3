"""Tests for AI motion-prompt generation (Task 1: the generator).

generate_motion_prompt(image_path) wraps Claude Vision (vision.analyze_image)
with the MOTION_VISION_QUESTION and post-processes the result:
  - returns None when vision is unsupported / falls back to placeholder
  - strips a leading preamble ("Here is ...:") the model may emit
  - strips surrounding quotes
  - clamps under WAVESPEED_PROMPT_MAX_CHARS
"""
from __future__ import annotations

from app.services import vision
from app.services.block_m2_video import motion_prompt_ai
from app.services.block_m2_video.motion_prompt_ai import (
    MOTION_VISION_QUESTION,
    generate_motion_prompt,
)


def test_returns_clean_prompt_and_passes_motion_question(monkeypatch):
    captured = {}

    def fake_analyze(path, question=""):
        captured["path"] = path
        captured["question"] = question
        return "slow gentle head turn, soft blinking, locked static camera, photorealistic"

    monkeypatch.setattr(vision, "is_vision_supported", lambda: True)
    monkeypatch.setattr(vision, "analyze_image", fake_analyze)

    result = generate_motion_prompt("/tmp/face.jpg")

    assert result == (
        "slow gentle head turn, soft blinking, locked static camera, photorealistic"
    )
    # the dedicated motion question (not the default vision prompt) must be used
    assert captured["question"] == MOTION_VISION_QUESTION
    assert captured["path"] == "/tmp/face.jpg"


def test_returns_none_when_vision_unsupported(monkeypatch):
    monkeypatch.setattr(vision, "is_vision_supported", lambda: False)
    # analyze_image must NOT be called when vision is unsupported
    def boom(*a, **k):
        raise AssertionError("analyze_image must not be called without a key")

    monkeypatch.setattr(vision, "analyze_image", boom)

    assert generate_motion_prompt("/tmp/face.jpg") is None


def test_returns_none_on_placeholder_fallback(monkeypatch):
    # key is present but the API failed -> analyze_image returns the placeholder
    placeholder = vision.analyze_image_placeholder(__file__)
    monkeypatch.setattr(vision, "is_vision_supported", lambda: True)
    monkeypatch.setattr(vision, "analyze_image", lambda p, q="": placeholder)

    assert generate_motion_prompt("/tmp/face.jpg") is None


def test_strips_leading_preamble(monkeypatch):
    monkeypatch.setattr(vision, "is_vision_supported", lambda: True)
    monkeypatch.setattr(
        vision,
        "analyze_image",
        lambda p, q="": "Here is the motion prompt:\nslow gentle head turn, soft blinking",
    )

    result = generate_motion_prompt("/tmp/face.jpg")

    assert result == "slow gentle head turn, soft blinking"
    assert "Here is" not in result


def test_strips_surrounding_quotes(monkeypatch):
    monkeypatch.setattr(vision, "is_vision_supported", lambda: True)
    monkeypatch.setattr(
        vision,
        "analyze_image",
        lambda p, q="": '"slow gentle head turn, locked static camera"',
    )

    result = generate_motion_prompt("/tmp/face.jpg")

    assert result == "slow gentle head turn, locked static camera"


def test_clamps_to_wavespeed_max_chars(monkeypatch):
    monkeypatch.setenv("WAVESPEED_PROMPT_MAX_CHARS", "50")
    long_prompt = "slow gentle head turn, " * 20  # ~460 chars
    monkeypatch.setattr(vision, "is_vision_supported", lambda: True)
    monkeypatch.setattr(vision, "analyze_image", lambda p, q="": long_prompt)

    result = generate_motion_prompt("/tmp/face.jpg")

    assert result is not None
    assert len(result) <= 50


def test_returns_none_on_empty_result(monkeypatch):
    monkeypatch.setattr(vision, "is_vision_supported", lambda: True)
    monkeypatch.setattr(vision, "analyze_image", lambda p, q="": "   \n  ")

    assert generate_motion_prompt("/tmp/face.jpg") is None


def test_motion_question_is_constant_string():
    # guardrail: the vision question must be a non-empty constant
    assert isinstance(MOTION_VISION_QUESTION, str)
    assert MOTION_VISION_QUESTION.strip()
