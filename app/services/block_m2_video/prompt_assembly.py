from __future__ import annotations

from app.prompts.video_prompt_builder import default_negative_prompt

DEFAULT_MOTION = (
    "slow gentle head turn, soft blinking, subtle breathing, minimal body movement, "
    "locked static camera, smooth continuous slow motion, "
    "soft cinematic lighting, photorealistic"
)
REALISM_SUFFIX = (
    "photorealistic, realistic, cinematic, natural skin texture, detailed skin"
)
WARDROBE_MODES = ("preserve", "safe", "spicy")

# Wardrobe table: each mode maps to a positive clothing anchor and a negative.
# - preserve: positive anchor + strong anti-undress negative
# - safe:     no positive anchor, mild anti-undress negative (default)
# - spicy:    no constraints at all (uncensored freedom)
_WARDROBE_TABLE: dict[str, dict[str, str]] = {
    "preserve": {
        "prompt": "keeping original clothing, same outfit, fully dressed, clothing unchanged",
        "negative": (
            "undressing, removing clothes, taking off clothing, lingerie, bra, "
            "underwear, nudity, topless, exposed chest"
        ),
    },
    "safe": {
        "prompt": "",
        "negative": "undressing, removing clothes, lingerie, bra, underwear, nudity, topless",
    },
    "spicy": {
        "prompt": "",
        "negative": "",
    },
}


def assemble_animate_prompt(
    user_motion: str,
    *,
    add_realism: bool,
    add_negative: bool,
    wardrobe: str = "safe",
) -> tuple[str, str]:
    motion = (user_motion or "").strip()

    if motion:
        prompt_parts = [motion]
        if add_realism:
            prompt_parts.append(REALISM_SUFFIX)
    else:
        # DEFAULT_MOTION already contains photorealistic; do NOT append REALISM_SUFFIX.
        prompt_parts = [DEFAULT_MOTION]

    if wardrobe not in WARDROBE_MODES:
        wardrobe = "safe"
    mode = _WARDROBE_TABLE[wardrobe]

    if mode["prompt"]:
        prompt_parts.append(mode["prompt"])

    negative_parts: list[str] = []
    if add_negative:
        negative_parts.append(default_negative_prompt())
    if mode["negative"]:
        negative_parts.append(mode["negative"])

    prompt = ", ".join(prompt_parts)
    negative = ", ".join(negative_parts)
    return (prompt, negative)


def assemble_custom_animate_prompts(
    photos,
    custom_prompts,
    *,
    add_realism: bool,
    add_negative: bool,
    wardrobe: str = "safe",
) -> list[tuple[str, str]]:
    """Per-photo ``(prompt, negative)`` for the custom-prompts flow (Variant A).

    The ONLY difference from the shared-default path is the prompt source: each
    swapped photo gets its own motion prompt from ``custom_prompts`` keyed by
    1-based display position. A missing/None/empty entry falls back to
    ``DEFAULT_MOTION`` via :func:`assemble_animate_prompt` (the fallback lives
    there — it is not duplicated here).

    ``result[i - 1]`` corresponds to photo ``i`` in ``photos`` order, so the
    per-photo prompt can never drift relative to the swapped-photo list. Pure:
    no engine, no billing, no I/O — just prompt assembly.
    """
    custom = custom_prompts or {}
    return [
        assemble_animate_prompt(
            custom.get(pos) or "",
            add_realism=add_realism,
            add_negative=add_negative,
            wardrobe=wardrobe,
        )
        for pos, _photo in enumerate(photos, start=1)
    ]


def clamp_prompt(text: str, cap: int) -> tuple[str, bool]:
    if len(text) <= cap:
        return (text, False)
    cut = text[:cap].rstrip()
    if " " in cut:
        cut = cut[: cut.rfind(" ")].rstrip()
    return (cut, True)
