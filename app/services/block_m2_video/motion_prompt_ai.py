"""AI motion-prompt generation from a photo (Block M2 video).

A thin wrapper over Claude Vision (``vision.analyze_image``) that asks the model
to write a *motion* prompt in the house style (slow/subtle motion, locked static
camera, photorealistic) and post-processes the reply so it is ready to feed the
animation engine: leading preamble stripped, surrounding quotes removed, clamped
under ``WAVESPEED_PROMPT_MAX_CHARS``.

Returns ``None`` when vision is unavailable (no key / API fell back to the
placeholder) so callers can show a soft error instead of billing a bad prompt.
"""
from __future__ import annotations

import os
import re

from app.services import vision
from app.services.block_m2_video.prompt_assembly import clamp_prompt

# Vision question, tuned for the DEFAULT_MOTION style: slow/subtle motion, no
# description of the person's appearance, comma-style english, output-only.
MOTION_VISION_QUESTION = (
    "You write motion prompts for an image-to-video model that animates this "
    "photo. Output ONLY the motion prompt — no preamble, no quotes, no "
    "explanation. Describe SLOW, SUBTLE, natural movement only (gentle head "
    "turn, soft blinking, subtle breathing, minimal body movement). Do NOT "
    "describe the person's appearance, clothing, face or background. Use "
    "comma-separated english phrases, max ~40 words. Always include "
    "\"locked static camera\" and \"photorealistic\".\n"
    "Example: slow gentle head turn, soft blinking, subtle breathing, minimal "
    "body movement, locked static camera, soft cinematic lighting, photorealistic"
)

# Sentinel that marks vision's not-configured / error fallback placeholder.
_PLACEHOLDER_SENTINEL = "Анализ изображений пока не поддерживается"

# Leading preamble the model may emit despite "output-only" instructions, e.g.
# "Here is the motion prompt:" / "Sure, here's the prompt:".
_PREAMBLE_RE = re.compile(r"^\s*(here|sure|okay|certainly)\b[^\n:]*:\s*", re.IGNORECASE)

# Matching quote pairs to peel off a fully-wrapped reply.
_QUOTE_PAIRS = {'"': '"', "'": "'", "«": "»", "“": "”", "`": "`"}


def _postprocess(raw: str) -> str:
    text = raw.strip()
    text = _PREAMBLE_RE.sub("", text, count=1).strip()
    if text and text[0] in _QUOTE_PAIRS and text.endswith(_QUOTE_PAIRS[text[0]]):
        text = text[1:-1].strip()
    cap = int(os.getenv("WAVESPEED_PROMPT_MAX_CHARS", "1500"))
    text, _ = clamp_prompt(text, cap)
    return text.strip()


def generate_motion_prompt(image_path: str) -> str | None:
    """Generate a motion prompt for ``image_path`` via Claude Vision.

    Returns the cleaned prompt, or ``None`` when vision is unavailable or the
    reply is empty/placeholder.
    """
    if not vision.is_vision_supported():
        return None
    try:
        raw = vision.analyze_image(image_path, MOTION_VISION_QUESTION)
    except Exception:
        return None
    if not raw or _PLACEHOLDER_SENTINEL in raw:
        return None
    cleaned = _postprocess(raw)
    return cleaned or None
