from __future__ import annotations

from app.prompts.video_prompt_builder import default_negative_prompt
from app.services.block_m2_video.prompt_assembly import (
    DEFAULT_MOTION,
    REALISM_SUFFIX,
    WARDROBE_MODES,
    assemble_animate_prompt,
    clamp_prompt,
)


def test_user_motion_leads_and_realism_appended():
    motion = "she slowly turns toward the camera"
    prompt, negative = assemble_animate_prompt(
        motion, add_realism=True, add_negative=True, wardrobe="safe"
    )
    assert prompt.startswith(motion)
    assert REALISM_SUFFIX in prompt
    # safe-mode negative composition contains the anti-cartoon negative as a prefix
    assert negative.startswith(default_negative_prompt())


def test_empty_motion_uses_default_no_double_realism():
    prompt, _ = assemble_animate_prompt(
        "", add_realism=True, add_negative=False, wardrobe="safe"
    )
    assert prompt == DEFAULT_MOTION
    # REALISM_SUFFIX must NOT be double-appended (DEFAULT_MOTION already has photorealistic)
    assert prompt.count(REALISM_SUFFIX) == 0


def test_flags_off_spicy_returns_motion_and_empty_negative():
    result = assemble_animate_prompt(
        "танец", add_realism=False, add_negative=False, wardrobe="spicy"
    )
    assert result == ("танец", "")


def test_safe_default_negative_has_clothing_terms_no_anchor():
    prompt, negative = assemble_animate_prompt(
        "walking", add_realism=False, add_negative=False, wardrobe="safe"
    )
    assert "bra" in negative
    assert "undressing" in negative
    # safe has no positive clothing anchor
    assert "keeping original clothing" not in prompt
    assert prompt == "walking"


def test_preserve_mode_has_anchor_and_negative():
    prompt, negative = assemble_animate_prompt(
        "walking", add_realism=False, add_negative=False, wardrobe="preserve"
    )
    assert "keeping original clothing" in prompt
    assert "bra" in negative


def test_invalid_wardrobe_falls_back_to_safe():
    _, negative = assemble_animate_prompt(
        "walking", add_realism=False, add_negative=False, wardrobe="bogus"
    )
    assert "bra" in negative


def test_add_negative_combines_anticartoon_and_clothing():
    _, negative = assemble_animate_prompt(
        "walking", add_realism=False, add_negative=True, wardrobe="safe"
    )
    assert "anime" in negative or "cartoon" in negative
    assert "bra" in negative


def test_wardrobe_modes_tuple():
    assert WARDROBE_MODES == ("preserve", "safe", "spicy")


def test_default_motion_favors_smoothness():
    # Default fallback motion is tuned for smoothness (slow, minimal, locked camera)
    # because high motion-per-frame reads jerky at the engines' fixed fps.
    low = DEFAULT_MOTION.lower()
    assert "slow" in low
    assert "static camera" in low
    assert "minimal" in low


def test_clamp_truncates_on_word_boundary():
    text = "the quick brown fox jumps"
    cut, flag = clamp_prompt(text, 12)
    assert flag is True
    assert cut == "the quick"
    assert not cut.endswith(" ")


def test_clamp_noop_within_cap():
    text = "short"
    cut, flag = clamp_prompt(text, 100)
    assert cut == "short"
    assert flag is False
