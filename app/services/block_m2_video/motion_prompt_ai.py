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

import logging
import os
import re

from app.services import vision
from app.services.block_m2_video.prompt_assembly import clamp_prompt

logger = logging.getLogger(__name__)

# Cap on how much of the raw Claude reply we mirror into the diagnostic log: the
# reply is the model's own short text (a motion prompt or a refusal, ~40 words),
# never the user's photo or PII, but we truncate defensively to avoid log spam.
_LOG_RAW_MAX = 500

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

# Claude Vision is safety-aligned and REFUSES suggestive/NSFW photos with a
# natural-language refusal (e.g. "I can't create motion prompts… inappropriate").
# That refusal is a valid string, so without this guard it leaks into the paid
# animation as the motion prompt. Three layers, refusal-first, high precision:
#
#   Layer 1 — refusal openers anchored to the START. A compliant motion prompt is
#     a comma-style description of movement; it NEVER opens with "I"/"Sorry"/etc.
#   Layer 2 — distinctive refusal phrases anywhere (embedded refusals).
#   Layer 3 — no comma at all => not our mandated comma-list format. Tiny false-
#     positive risk (a terse valid prompt without commas), but the cost is benign
#     (user re-clicks, nothing billed) vs. a refusal reaching a paid render.
_REFUSAL_OPENERS_RE = re.compile(
    r"^(i\b|i'm|i'd|i'll|i've|i am|sorry|unfortunately|apolog|my apologies|"
    r"as an\b|as a language)",
    re.IGNORECASE,
)
_REFUSAL_PHRASES = (
    "can't", "cannot", "can not", "unable", "not able to",
    "inappropriate", "not comfortable", "won't", "will not",
)


def _clean(raw: str) -> str:
    """Strip whitespace, a leading preamble, and surrounding quotes (no clamp)."""
    text = raw.strip()
    text = _PREAMBLE_RE.sub("", text, count=1).strip()
    if text and text[0] in _QUOTE_PAIRS and text.endswith(_QUOTE_PAIRS[text[0]]):
        text = text[1:-1].strip()
    return text


def _refusal_layer(text: str) -> int | None:
    """Classify a cleaned reply: which guard layer (if any) marks it a refusal.

    Returns the firing layer so the caller can log not just THAT a reply was
    rejected but WHICH heuristic caught it (key for telling a genuine content
    refusal apart from a layer-3 false-positive on a comma-less valid prompt):

      ``None`` — a usable motion prompt (no layer fired)
      ``0``    — empty / whitespace-only reply
      ``1``    — refusal opener anchored to the start
      ``2``    — distinctive refusal phrase anywhere
      ``3``    — no comma at all (not our mandated comma-list format)
    """
    low = text.strip().lower()
    if not low:
        return 0
    if _REFUSAL_OPENERS_RE.match(low):           # layer 1: opener
        return 1
    if any(p in low for p in _REFUSAL_PHRASES):  # layer 2: phrase
        return 2
    if "," not in low:                           # layer 3: not comma-list format
        return 3
    return None


def _looks_like_refusal(text: str) -> bool:
    """True when the cleaned reply is a model refusal, not a usable motion prompt."""
    return _refusal_layer(text) is not None


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
    # Detect refusals on the CLEANED text (before clamp — clamp could cut a marker).
    candidate = _clean(raw)
    layer = _refusal_layer(candidate)
    if layer is not None:
        # Diagnostic only (file/console log, never the user chat): record the raw
        # reply + which layer caught it, so we can later tell a real Claude refusal
        # (layer 1/2) from a layer-3 false-positive on a comma-less valid prompt.
        logger.info(
            "motion-prompt refusal: layer=%d raw=%r", layer, raw[:_LOG_RAW_MAX]
        )
        return None
    cap = int(os.getenv("WAVESPEED_PROMPT_MAX_CHARS", "1500"))
    cleaned, _ = clamp_prompt(candidate, cap)
    return cleaned.strip() or None
