"""Tests for AI motion-prompt generation (Task 1: the generator).

generate_motion_prompt(image_path) wraps Claude Vision (vision.analyze_image)
with the MOTION_VISION_QUESTION and post-processes the result:
  - returns None when vision is unsupported / falls back to placeholder
  - strips a leading preamble ("Here is ...:") the model may emit
  - strips surrounding quotes
  - clamps under WAVESPEED_PROMPT_MAX_CHARS
"""
from __future__ import annotations

import logging

import pytest

from app.services import vision
from app.services.block_m2_video import motion_prompt_ai
from app.services.block_m2_video.motion_prompt_ai import (
    MOTION_VISION_QUESTION,
    generate_motion_prompt,
)
from app.services.block_m2_video.motion_prompt_ai import (
    _looks_like_refusal,
    _refusal_layer,
)

_MODULE_LOGGER = "app.services.block_m2_video.motion_prompt_ai"


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


# ── Content-refusal detection (Claude Vision is censored and refuses spicy) ───

def _gen_with(monkeypatch, raw_reply):
    monkeypatch.setattr(vision, "is_vision_supported", lambda: True)
    monkeypatch.setattr(vision, "analyze_image", lambda p, q="": raw_reply)
    return generate_motion_prompt("/tmp/face.jpg")


def test_refusal_exact_screenshot_returns_none(monkeypatch):
    # the actual live failure: model refusal leaked into motion_prompt
    refusal = (
        "I can't create motion prompts for this image. The pose and framing "
        "are inappropriate for me to work with"
    )
    assert _gen_with(monkeypatch, refusal) is None


@pytest.mark.parametrize("refusal", [
    "I cannot generate a motion prompt for this photo.",
    "I'm not able to animate this image.",
    "I'm unable to help with that.",
    "Unfortunately, I can't assist with this request.",
    "Sorry, I can't do that.",
    "As an AI, I won't create content like this.",
    "I'm not comfortable describing this image.",
])
def test_refusal_variants_return_none(monkeypatch, refusal):
    assert _gen_with(monkeypatch, refusal) is None


def test_valid_prompt_with_can_substrings_not_refused(monkeypatch):
    # "camera"/"candle" contain "can" but are NOT refusals — must pass through
    prompt = "slow camera push, candle flicker, hair sway, locked static camera, photorealistic"
    assert _gen_with(monkeypatch, prompt) == prompt


def test_valid_prompt_with_scan_not_refused(monkeypatch):
    prompt = "slow scan across the face, subtle breathing, locked static camera, photorealistic"
    assert _gen_with(monkeypatch, prompt) == prompt


def test_valid_prompt_subject_opener_not_refused(monkeypatch):
    # starts with "she" (allowed), not "I" — must not trip the opener layer
    prompt = "she slowly turns toward the camera, soft blinking, photorealistic"
    assert _gen_with(monkeypatch, prompt) == prompt


def test_layer3_prose_without_comma_returns_none(monkeypatch):
    # a prose sentence with no comma is not our comma-list format -> refusal
    prose = "The subject remains completely still in this photograph"
    assert _gen_with(monkeypatch, prose) is None


def test_looks_like_refusal_unit():
    # layer 1 (opener), layer 2 (phrase), layer 3 (no comma)
    assert _looks_like_refusal("I can't do this, sorry") is True
    assert _looks_like_refusal("Sorry, not happening") is True
    assert _looks_like_refusal("this pose is inappropriate, no") is True
    assert _looks_like_refusal("slow head turn no commas here at all") is True
    # valid comma-list motion prompt
    assert _looks_like_refusal(
        "slow gentle head turn, soft blinking, photorealistic"
    ) is False


# ── Refusal layer classification + diagnostic logging ────────────────────────

def test_refusal_layer_unit():
    # None == valid prompt; 1 opener, 2 phrase, 3 no-comma, 0 empty
    assert _refusal_layer("I can't, sorry") == 1
    assert _refusal_layer("this is inappropriate, no") == 2
    assert _refusal_layer("no commas at all here") == 3
    assert _refusal_layer("") == 0
    assert _refusal_layer("   \n ") == 0
    assert _refusal_layer("slow head turn, soft blinking, photorealistic") is None


def test_layer1_refusal_logs_raw_and_layer(monkeypatch, caplog):
    refusal = "I can't create motion prompts for this image, it's inappropriate"
    monkeypatch.setattr(vision, "is_vision_supported", lambda: True)
    monkeypatch.setattr(vision, "analyze_image", lambda p, q="": refusal)

    with caplog.at_level(logging.INFO, logger=_MODULE_LOGGER):
        result = generate_motion_prompt("/tmp/face.jpg")

    assert result is None
    recs = [r for r in caplog.records if r.name == _MODULE_LOGGER]
    assert len(recs) == 1
    blob = recs[0].getMessage()
    assert "layer=1" in blob                         # the layer is parseable
    assert "I can't create motion prompts" in blob   # the RAW reply is logged


def test_layer2_refusal_logs_layer2(monkeypatch, caplog):
    # no opener, but an embedded phrase marker -> layer 2
    refusal = "this content is inappropriate, I won't describe it"
    monkeypatch.setattr(vision, "is_vision_supported", lambda: True)
    monkeypatch.setattr(vision, "analyze_image", lambda p, q="": refusal)

    with caplog.at_level(logging.INFO, logger=_MODULE_LOGGER):
        assert generate_motion_prompt("/tmp/face.jpg") is None

    recs = [r for r in caplog.records if r.name == _MODULE_LOGGER]
    assert len(recs) == 1
    assert "layer=2" in recs[0].getMessage()


def test_layer3_no_comma_logs_layer3(monkeypatch, caplog):
    # a valid-looking but comma-less reply -> layer 3 (the false-positive suspect)
    prose = "the subject remains completely still in this photograph"
    monkeypatch.setattr(vision, "is_vision_supported", lambda: True)
    monkeypatch.setattr(vision, "analyze_image", lambda p, q="": prose)

    with caplog.at_level(logging.INFO, logger=_MODULE_LOGGER):
        assert generate_motion_prompt("/tmp/face.jpg") is None

    recs = [r for r in caplog.records if r.name == _MODULE_LOGGER]
    assert len(recs) == 1
    blob = recs[0].getMessage()
    assert "layer=3" in blob
    assert "remains completely still" in blob        # raw reply present


def test_successful_prompt_does_not_log(monkeypatch, caplog):
    prompt = "slow gentle head turn, soft blinking, locked static camera, photorealistic"
    monkeypatch.setattr(vision, "is_vision_supported", lambda: True)
    monkeypatch.setattr(vision, "analyze_image", lambda p, q="": prompt)

    with caplog.at_level(logging.DEBUG, logger=_MODULE_LOGGER):
        result = generate_motion_prompt("/tmp/face.jpg")

    assert result == prompt
    # the success path must stay silent (no log spam, no content leak)
    recs = [r for r in caplog.records if r.name == _MODULE_LOGGER]
    assert recs == []
