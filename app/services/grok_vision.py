"""Grok (xAI) vision client — uncensored motion-prompt engine (Веха A / Этап 1).

xAI is OpenAI-compatible, so this is a thin wrapper over the ``openai`` SDK
pointed at ``https://api.x.ai/v1``. It mirrors ``vision.analyze_image`` so it is
a drop-in motion-prompt engine for the ✨ button (Этап 2), and adds multi-image
support for the future video-frame arc (xAI accepts several images per request).

Design notes:
  - Key comes from ``XAI_API_KEY`` (env only, never hard-coded, never logged).
  - Failures (no key / API error / 4xx) degrade to an empty string — the caller
    treats a falsy reply as "no result" and shows a soft error. Never crashes.
  - Refusal detection lives one level up in ``generate_motion_prompt``
    (``_refusal_layer`` is engine-agnostic); this client returns RAW text.
  - The house-style MOTION question stays in ``motion_prompt_ai`` and is passed
    in as ``question`` — not duplicated here.
"""
from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

XAI_BASE_URL = "https://api.x.ai/v1"
GROK_VISION_MODEL = os.getenv("GROK_VISION_MODEL", "grok-4.3")
_MAX_TOKENS = int(os.getenv("GROK_VISION_MAX_TOKENS", "500"))

# xAI reports per-call cost as ``cost_in_usd_ticks`` where 1e9 ticks == $1
# (nano-USD). Used for the visibility ledger only; the actual money-safe charge
# in Этап 2 uses a fixed estimate, not this reported value.
_USD_TICKS_PER_USD = 1e9


@dataclass
class GrokVisionResult:
    """Raw reply + usage/cost surface for the ledger (Этап 2)."""
    text: str
    usage: dict | None = None
    cost_usd: float | None = None


def is_grok_vision_supported() -> bool:
    """True when an xAI key is configured (mirror of ``vision.is_vision_supported``)."""
    return bool(os.getenv("XAI_API_KEY", "").strip())


def _build_client():
    """Construct the OpenAI-compatible client for xAI. Patched out in tests."""
    from openai import OpenAI

    return OpenAI(api_key=os.getenv("XAI_API_KEY", "").strip(), base_url=XAI_BASE_URL)


def _media_type(path: Path) -> str:
    mapping = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp",
    }
    return mapping.get(path.suffix.lower(), "image/jpeg")


def _data_uri(path: Path) -> str:
    b64 = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{_media_type(path)};base64,{b64}"


def _usage_to_dict(usage) -> dict | None:
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        try:
            return dict(usage.model_dump())
        except Exception:  # noqa: BLE001 - defensive, fall through
            pass
    return {k: v for k, v in vars(usage).items() if not k.startswith("_")}


def _cost_usd(usage) -> float | None:
    ticks = getattr(usage, "cost_in_usd_ticks", None)
    if ticks is None and isinstance(usage, dict):
        ticks = usage.get("cost_in_usd_ticks")
    if ticks is None:
        return None
    try:
        return float(ticks) / _USD_TICKS_PER_USD
    except (TypeError, ValueError):
        return None


def analyze_images_detailed(
    image_paths: list[str], question: str = ""
) -> GrokVisionResult:
    """Core call: send all images in ONE request, return text + usage/cost.

    Returns ``GrokVisionResult(text="")`` (no usage) on no-key or API error —
    never raises for those. Raises ``ValueError`` only for a missing file
    (caller bug; mirrors ``vision.analyze_image``).
    """
    paths = [Path(p) for p in image_paths]
    for p in paths:
        if not p.exists():
            raise ValueError(f"Image file not found: {p}")

    if not is_grok_vision_supported():
        logger.warning("XAI_API_KEY not set — Grok vision unavailable")
        return GrokVisionResult(text="")

    content: list[dict] = [{"type": "text", "text": question}]
    for p in paths:
        content.append(
            {"type": "image_url", "image_url": {"url": _data_uri(p)}}
        )

    try:
        client = _build_client()
        resp = client.chat.completions.create(
            model=GROK_VISION_MODEL,
            messages=[{"role": "user", "content": content}],
            max_tokens=_MAX_TOKENS,
        )
        text = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        return GrokVisionResult(
            text=text, usage=_usage_to_dict(usage), cost_usd=_cost_usd(usage)
        )
    except Exception as exc:  # noqa: BLE001 - degrade, never crash the caller
        logger.warning("Grok vision call failed: %s", exc)
        return GrokVisionResult(text="")


def analyze_images(image_paths: list[str], question: str = "") -> str:
    """Multi-image convenience wrapper returning just the raw reply text."""
    return analyze_images_detailed(image_paths, question).text


def analyze_image(image_path: str, question: str = "") -> str:
    """Single-image, drop-in mirror of ``vision.analyze_image`` (text only)."""
    return analyze_images([image_path], question)
