# -*- coding: utf-8 -*-
"""Anthropic client factory + model/pricing config for the unified router.

Centralises two concerns that were previously inlined:

- **Client construction** — :func:`build_anthropic_client` reads
  ``ANTHROPIC_API_KEY`` and returns an ``anthropic.Anthropic`` instance, or
  ``None`` when the SDK or key is absent (or construction fails) so the caller
  can transparently fall back to the legacy dispatcher. The SDK module is
  injectable for testing.
- **Pricing** — :data:`DEFAULT_MODEL`, :data:`MODEL_PRICING`, and
  :func:`compute_cost` live here; ``router.py`` re-imports them.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

# The model the router uses unless ``JARVIS_ROUTER_MODEL`` overrides it.
DEFAULT_MODEL = "claude-sonnet-4-6"

# USD per *single* token, keyed by model id: (input_price, output_price).
MODEL_PRICING: Dict[str, Tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0 / 1_000_000, 15.0 / 1_000_000),
    "claude-opus-4-8": (5.0 / 1_000_000, 25.0 / 1_000_000),
    "claude-haiku-4-5": (1.0 / 1_000_000, 5.0 / 1_000_000),
}
# Fallback price if the model id is unknown — assume Sonnet-tier.
DEFAULT_PRICING: Tuple[float, float] = (3.0 / 1_000_000, 15.0 / 1_000_000)


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """USD cost for ``input_tokens``/``output_tokens`` on ``model``."""
    price_in, price_out = MODEL_PRICING.get(model, DEFAULT_PRICING)
    return input_tokens * price_in + output_tokens * price_out


def resolve_model() -> str:
    """The configured router model: ``JARVIS_ROUTER_MODEL`` or the default."""
    return (os.getenv("JARVIS_ROUTER_MODEL", "") or "").strip() or DEFAULT_MODEL


def build_anthropic_client(
    api_key: Optional[str] = None,
    *,
    anthropic_module: Any = None,
) -> Optional[Any]:
    """Build an Anthropic SDK client, or ``None`` if unavailable.

    Returns ``None`` (rather than raising) when no API key is configured, the
    ``anthropic`` package is not installed, or client construction fails — so a
    caller can fall back to the legacy dispatcher. ``anthropic_module`` may be
    injected in tests to avoid importing the real SDK.
    """
    key = (api_key if api_key is not None else os.getenv("ANTHROPIC_API_KEY", "")).strip()
    if not key:
        return None

    mod = anthropic_module
    if mod is None:
        try:
            import anthropic as mod  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001 - missing SDK → graceful None
            return None

    try:
        return mod.Anthropic(api_key=key)
    except Exception:  # noqa: BLE001 - construction failure → graceful None
        return None
