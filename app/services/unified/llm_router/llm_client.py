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

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

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

    The returned client is wrapped so ``messages.create`` fails closed once
    the account's credit balance is depleted (see :func:`is_balance_depleted`)
    — every caller gets this protection without needing to know about it.
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
        return _GuardedAnthropicClient(mod.Anthropic(api_key=key))
    except Exception:  # noqa: BLE001 - construction failure → graceful None
        return None


# ── credit_balance_too_low fail-closed guard ─────────────────────────────────
#
# Anthropic returns HTTP 400 with "credit balance is too low" once the account
# runs out of credit. That is a permanent condition (not worth retrying), so
# every subsequent paid call should refuse immediately instead of spending a
# real HTTP round-trip on a call that is guaranteed to fail. The flag is a
# plain in-process global (mirrors the existing ``_ROUTER_BUILD_FAILED`` latch
# pattern in ``jarvis_smart_telegram_control.py``): it resets on bot restart,
# or via ``reset_balance_flag`` (the admin ``/reset_balance_flag`` command).

CREDIT_BALANCE_LOW_MARKER = "credit balance is too low"


class CreditBalanceDepletedError(RuntimeError):
    """The Anthropic account is out of credit — paid calls fail closed."""


_balance_state: Dict[str, bool] = {"depleted": False, "owner_alerted": False}


def is_credit_balance_error(exc: BaseException) -> bool:
    """True if ``exc`` is Anthropic's 400 "credit balance is too low"."""
    if isinstance(exc, CreditBalanceDepletedError):
        return True
    status = getattr(exc, "status_code", None)
    return status == 400 and CREDIT_BALANCE_LOW_MARKER in str(exc).lower()


def is_balance_depleted() -> bool:
    """True once a credit-balance-too-low error has been observed this episode."""
    return _balance_state["depleted"]


def mark_balance_depleted() -> bool:
    """Flip the depleted flag on. Returns ``True`` only the first time (episode
    start), so the caller can log/alert exactly once."""
    first = not _balance_state["depleted"]
    _balance_state["depleted"] = True
    if first:
        logger.warning(
            "Anthropic credit balance too low — failing closed for paid calls "
            "until reset (bot restart or /reset_balance_flag)"
        )
    return first


def should_alert_owner() -> bool:
    """True (and marks alerted) exactly once per episode — dedupes owner pings."""
    if _balance_state["owner_alerted"]:
        return False
    _balance_state["owner_alerted"] = True
    return True


def reset_balance_flag() -> None:
    """Clear the depleted/alerted flags — bot restart or ``/reset_balance_flag``."""
    _balance_state["depleted"] = False
    _balance_state["owner_alerted"] = False


class _GuardedMessages:
    """Proxies ``client.messages`` so ``.create`` fails closed once depleted."""

    def __init__(self, messages: Any) -> None:
        self._messages = messages

    def create(self, **kwargs: Any) -> Any:
        if is_balance_depleted():
            raise CreditBalanceDepletedError(
                "Anthropic credit balance is too low (fail-closed, no API call made)"
            )
        try:
            return self._messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - re-classified below
            if is_credit_balance_error(exc):
                mark_balance_depleted()
                raise CreditBalanceDepletedError(str(exc)) from exc
            raise


class _GuardedAnthropicClient:
    """Transparent proxy around an ``anthropic.Anthropic`` client — only
    ``messages`` is intercepted; every other attribute passes through."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.messages = _GuardedMessages(client.messages)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
