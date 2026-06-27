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
from dataclasses import dataclass

from app.services import grok_vision, vision
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
    "turn, soft blinking, subtle breathing, minimal body movement) that suits "
    "THIS specific photo. Do NOT describe the person's appearance, clothing, "
    "face or background. Use comma-separated english phrases, max ~40 words. "
    "Always include \"locked static camera\" and \"photorealistic\". "
    "Do NOT repeat the example below verbatim — it only shows the FORMAT; your "
    "prompt must describe the movement that fits this particular image.\n"
    "Format example (do NOT copy these words): slow gentle head turn, soft "
    "blinking, subtle breathing, minimal body movement, locked static camera, "
    "soft cinematic lighting, photorealistic"
)

# Video-motion question (Веха C / Задача 3). UNLIKE MOTION_VISION_QUESTION (which
# asks for slow/subtle motion on ONE static photo), this asks Grok to read an
# ORDERED sequence of frames and re-create the movement happening ACROSS them —
# the action and its direction/sequence. Still output-only + comma-list so the
# engine-agnostic _refusal_layer applies unchanged. "do NOT copy the example"
# guards against the echo failure mode learned in Веха A.
VIDEO_MOTION_VISION_QUESTION = (
    "You are given an ORDERED sequence of frames sampled from one short video, "
    "first to last. Write ONE motion prompt for an image-to-video model that "
    "RE-CREATES the movement happening ACROSS these frames over time — the "
    "action and its direction/sequence (e.g. \"turns head left, then raises "
    "right hand, leans forward\"). Output ONLY the motion prompt — no preamble, "
    "no quotes, no explanation. Do NOT describe the person's appearance, "
    "clothing, face, or background — movement only. Use comma-separated english "
    "phrases, max ~50 words. State the camera explicitly (\"locked static "
    "camera\" if the framing does not move, otherwise the camera motion you "
    "observe). Do NOT copy the example; it only shows the FORMAT.\n"
    "Format example (do NOT copy these words): turns head slowly to the left, "
    "raises right hand toward face, gentle forward lean, hair sways, locked "
    "static camera, photorealistic"
)

# Sentinel that marks vision's not-configured / error fallback placeholder.
_PLACEHOLDER_SENTINEL = "Анализ изображений пока не поддерживается"


@dataclass
class VideoMotionResult:
    """Result of one Grok multi-image motion-prompt call (Веха C / Задача 3).

    ``prompt`` is ``None`` on refusal / empty / no-key — the money-gate Задача 4
    checks to decide whether to charge. ``usage``/``cost_usd`` come from the SAME
    call (no second request) so Задача 4's ledger logs the real cost even when
    ``prompt`` is ``None`` (a refusal is still a billable xAI call).
    """

    prompt: str | None
    usage: dict | None = None
    cost_usd: float | None = None

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


def _engine_chain() -> list[str]:
    """Ordered vision engines from ``MOTION_PROMPT_VISION_ENGINES``.

    Default ``"claude,grok"``: cheap, SFW-friendly Claude first, uncensored Grok
    as an AUTOMATIC fallback when Claude refuses (no manual per-press switch).
    Set ``"grok"`` / ``"claude"`` to force a single engine.
    """
    raw = os.getenv("MOTION_PROMPT_VISION_ENGINES", "claude,grok")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


def _run_engine(engine: str, image_path: str) -> str | None:
    """Call one vision engine; return its RAW reply, or ``None`` when the engine
    is unavailable (no key) or the call errored. Never raises."""
    if engine == "claude":
        if not vision.is_vision_supported():
            return None
        try:
            return vision.analyze_image(image_path, MOTION_VISION_QUESTION)
        except Exception:  # noqa: BLE001 - degrade, let the chain continue
            return None
    if engine == "grok":
        if not grok_vision.is_grok_vision_supported():
            return None
        try:
            return grok_vision.analyze_image(image_path, MOTION_VISION_QUESTION)
        except Exception:  # noqa: BLE001 - degrade, let the chain continue
            return None
    logger.warning("motion-prompt: unknown vision engine %r — skipping", engine)
    return None


def generate_motion_prompt(image_path: str) -> str | None:
    """Generate a motion prompt via the configured vision-engine chain.

    Engines are tried in ``MOTION_PROMPT_VISION_ENGINES`` order (default
    ``claude,grok``). ``_refusal_layer`` is applied to EVERY engine identically
    (engine-agnostic), so a refusal from one engine auto-falls-back to the next.
    Returns the first usable prompt, or ``None`` when every engine is
    unavailable / empty / refuses. Money (check_limit/record_cost) is the
    caller's job — this stays pure so a fallback never double-charges.
    """
    cap = int(os.getenv("WAVESPEED_PROMPT_MAX_CHARS", "1500"))
    for engine in _engine_chain():
        raw = _run_engine(engine, image_path)
        if not raw or _PLACEHOLDER_SENTINEL in raw:
            continue  # unavailable / empty / placeholder -> try next engine
        # Detect refusals on the CLEANED text (before clamp — clamp could cut a marker).
        candidate = _clean(raw)
        layer = _refusal_layer(candidate)
        if layer is not None:
            # Diagnostic only (file/console log, never the user chat): raw reply +
            # which engine and which layer caught it, so we can tell a real refusal
            # (layer 1/2) from a layer-3 false-positive on a comma-less prompt.
            logger.info(
                "motion-prompt refusal: engine=%s layer=%d raw=%r",
                engine, layer, raw[:_LOG_RAW_MAX],
            )
            continue  # this engine refused -> fall back to the next engine
        cleaned, _ = clamp_prompt(candidate, cap)
        result = cleaned.strip()
        if result:
            return result
    return None


def generate_video_motion_prompt(frame_paths) -> VideoMotionResult:
    """Write a motion prompt that re-creates movement across video frames.

    Веха C / Задача 3 — the first PAID task. One Grok multi-image call (Claude is
    skipped: it censors spicy by design, so it would only waste a call). Returns
    a :class:`VideoMotionResult`; ``prompt`` is ``None`` on refusal / empty /
    no-key, while ``usage``/``cost_usd`` from the same call are always surfaced.

    PURE generation: NO ``check_limit`` / ``record_cost`` here — billing and the
    money-gate are Задача 4. An empty ``frame_paths`` short-circuits BEFORE any
    paid call (never pay to analyse nothing).
    """
    frames = list(frame_paths)
    if not frames:
        return VideoMotionResult(prompt=None)

    res = grok_vision.analyze_images_detailed(frames, VIDEO_MOTION_VISION_QUESTION)
    raw = res.text or ""
    if not raw or _PLACEHOLDER_SENTINEL in raw:
        return VideoMotionResult(prompt=None, usage=res.usage, cost_usd=res.cost_usd)

    candidate = _clean(raw)
    layer = _refusal_layer(candidate)
    if layer is not None:
        # Diagnostic only (never the user chat): which layer caught the refusal,
        # so a real content refusal (layer 1/2) is distinguishable from a layer-3
        # false-positive on a comma-less reply. cost stays in the result.
        logger.info(
            "video-motion refusal: layer=%d raw=%r", layer, raw[:_LOG_RAW_MAX]
        )
        return VideoMotionResult(prompt=None, usage=res.usage, cost_usd=res.cost_usd)

    cap = int(os.getenv("WAVESPEED_PROMPT_MAX_CHARS", "1500"))
    cleaned, _ = clamp_prompt(candidate, cap)
    prompt = cleaned.strip() or None
    return VideoMotionResult(prompt=prompt, usage=res.usage, cost_usd=res.cost_usd)
