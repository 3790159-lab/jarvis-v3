from __future__ import annotations

from app.prompts.video_prompt_builder import default_negative_prompt
from app.services.block_m2_video.prompt_assembly import (
    DEFAULT_MOTION,
    REALISM_SUFFIX,
    WARDROBE_MODES,
    assemble_animate_prompt,
    assemble_custom_animate_prompts,
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


# ── Задача 2: per-photo custom prompt assembly (pure, no engine/billing) ───────
# Variant A's ONLY difference from the default path is the prompt SOURCE: each
# swapped photo gets its own motion prompt from sess.custom_prompts (1-based
# display position), falling back to DEFAULT_MOTION when absent/empty. Order of
# the returned list must track the photo order exactly (result[i-1] == photo i).


def _photos(n):
    # the helper only needs the count/order; contents are irrelevant
    return [f"photo_{i}.png" for i in range(n)]


def test_custom_all_prompts_each_gets_its_own():
    custom = {1: "walking", 2: "dancing", 3: "jumping"}
    reqs = assemble_custom_animate_prompts(
        _photos(3), custom, add_realism=False, add_negative=False, wardrobe="spicy"
    )
    assert len(reqs) == 3
    assert reqs[0][0] == "walking"
    assert reqs[1][0] == "dancing"
    assert reqs[2][0] == "jumping"


def test_custom_empty_entry_falls_back_to_default():
    # photo 2 has no prompt (None) → DEFAULT_MOTION; others keep their own.
    custom = {1: "walking", 2: None, 3: "jumping"}
    reqs = assemble_custom_animate_prompts(
        _photos(3), custom, add_realism=False, add_negative=False, wardrobe="spicy"
    )
    assert reqs[0][0] == "walking"
    assert reqs[1][0] == DEFAULT_MOTION   # fallback, not duplicated
    assert reqs[2][0] == "jumping"


def test_custom_no_prompts_all_default():
    # No custom prompts at all → every photo animates with DEFAULT_MOTION,
    # identical to the shared-default behaviour.
    reqs = assemble_custom_animate_prompts(
        _photos(3), {}, add_realism=False, add_negative=False, wardrobe="spicy"
    )
    assert [r[0] for r in reqs] == [DEFAULT_MOTION, DEFAULT_MOTION, DEFAULT_MOTION]


def test_custom_none_map_all_default():
    # custom_prompts may be None entirely (never submitted) — still safe.
    reqs = assemble_custom_animate_prompts(
        _photos(2), None, add_realism=False, add_negative=False, wardrobe="spicy"
    )
    assert [r[0] for r in reqs] == [DEFAULT_MOTION, DEFAULT_MOTION]


def test_custom_order_does_not_drift():
    # The prompt for photo #2 must land at reqs[1], never shifted.
    custom = {2: "ONLY_SECOND"}
    reqs = assemble_custom_animate_prompts(
        _photos(3), custom, add_realism=False, add_negative=False, wardrobe="spicy"
    )
    assert reqs[0][0] == DEFAULT_MOTION
    assert reqs[1][0] == "ONLY_SECOND"
    assert reqs[2][0] == DEFAULT_MOTION


def test_custom_wardrobe_realism_negative_applied_per_req():
    custom = {1: "walking", 2: "dancing"}
    reqs = assemble_custom_animate_prompts(
        _photos(2), custom, add_realism=True, add_negative=True, wardrobe="preserve"
    )
    for prompt, negative in reqs:
        # realism appended (custom motion present) ...
        assert REALISM_SUFFIX in prompt
        # ... wardrobe anchor + negative applied uniformly to every photo
        assert "keeping original clothing" in prompt
        assert default_negative_prompt() in negative
        assert "bra" in negative


# ── I1: stronger realism (shared suffix + negative → videoref AND swapbatch) ───


def test_realism_suffix_strengthened_with_new_terms():
    """REALISM_SUFFIX gains lifelike/lighting/true-to-life motion; existing kept."""
    low = REALISM_SUFFIX.lower()
    # new realism terms (video came out anime — strengthen the existing suffix)
    assert "lifelike" in low
    assert "realistic lighting" in low
    assert "true-to-life motion" in low
    # existing terms preserved (NOT lost, NOT duplicated)
    assert "photorealistic" in low
    assert "natural skin texture" in low


def test_negative_prompt_strengthened_anti_stylization():
    """default_negative_prompt gains cgi/render/stylized/illustration; anti-anime kept."""
    neg = default_negative_prompt().lower()
    # new anti-stylization terms
    assert "cgi" in neg
    assert "render" in neg
    assert "stylized" in neg
    assert "illustration" in neg
    # existing anti-anime preserved
    assert "anime" in neg
    assert "cartoon" in neg
    assert "3d" in neg


def test_strengthened_realism_flows_through_shared_stack():
    """The shared stack carries the strengthened suffix + negative into the
    assembled prompt (covers both videoref and swapbatch animation)."""
    prompt, negative = assemble_animate_prompt(
        "turns head left", add_realism=True, add_negative=True, wardrobe="safe"
    )
    assert "lifelike" in prompt.lower()
    assert "cgi" in negative.lower()
