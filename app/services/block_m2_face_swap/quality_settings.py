# -*- coding: utf-8 -*-
"""Per-batch video quality settings (duration + fps) for Block M.2.5 (Task C).

Pure parsing/validation — no I/O, no engine, no Telegram — so it is trivially
unit-testable. The orchestrator stores the resulting :class:`QualitySettings`
on the session; the engine turns them into workflow parameters (native frame
count from ``duration_sec`` and RIFE interpolation from ``fps``).

**fps model:** the Wan 2.2 i2v workflow generates natively at 21 fps. Higher
fps is achieved by inserting a RIFE interpolation node with an *integer*
multiplier, so ``fps`` is restricted to multiples of 21 (21/42/63/84). The
multiplier is ``fps / 21``.

**Feature gate:** fps > 21 needs the RIFE node, which is cloned by the pod
bootstrap but not yet verified on a live pod. Until verified, ``fps_enabled``
is ``False`` and any fps > 21 is rejected. See ``fps_interpolation_enabled``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = [
    "DURATION_MIN",
    "DURATION_MAX",
    "FPS_NATIVE",
    "FPS_ALLOWED",
    "QualityError",
    "QualitySettings",
    "parse_quality_args",
    "validate_quality",
    "interpolation_multiplier",
    "fps_interpolation_enabled",
]

DURATION_MIN = 3
DURATION_MAX = 15
FPS_NATIVE = 21
FPS_ALLOWED = (21, 42, 63, 84)  # multipliers 1×/2×/3×/4× of the native 21 fps

_KNOWN_KEYS = {"duration", "fps"}

# Exact wording requested for the gated-off rejection (Day 7+ smoke test).
_FPS_GATED_MSG = (
    "FPS boost not yet verified on this pod — pending Day 7+ smoke test "
    "(пока доступно только fps=21)"
)


class QualityError(ValueError):
    """Raised when quality arguments are malformed or out of range."""


@dataclass
class QualitySettings:
    """Validated per-batch quality settings."""

    duration_sec: int
    fps: int = FPS_NATIVE


def parse_quality_args(text: str) -> dict:
    """Parse ``duration=10 fps=42`` into ``{"duration": 10, "fps": 42}``.

    Empty input → ``{}`` (the caller treats that as "show current settings").

    Raises:
        QualityError: unknown key, missing ``=``, or non-integer value.
    """
    out: dict = {}
    for token in text.split():
        if "=" not in token:
            raise QualityError(
                f"непонятный аргумент {token!r} — нужен формат key=value"
            )
        key, _, raw = token.partition("=")
        key = key.strip().lower()
        if key not in _KNOWN_KEYS:
            raise QualityError(
                f"неизвестный параметр {key!r}; доступны: "
                f"{', '.join(sorted(_KNOWN_KEYS))}"
            )
        try:
            out[key] = int(raw.strip())
        except ValueError as exc:
            raise QualityError(
                f"значение {key}={raw!r} должно быть целым числом"
            ) from exc
    return out


def validate_quality(
    duration: int, fps: int, *, fps_enabled: bool
) -> QualitySettings:
    """Range-check duration and fps, applying the fps feature gate.

    Raises:
        QualityError: duration out of [DURATION_MIN, DURATION_MAX], fps not in
            FPS_ALLOWED, or fps > native while ``fps_enabled`` is False.
    """
    if not DURATION_MIN <= duration <= DURATION_MAX:
        raise QualityError(
            f"длительность должна быть {DURATION_MIN}–{DURATION_MAX} сек, "
            f"получено {duration}"
        )
    if fps not in FPS_ALLOWED:
        raise QualityError(
            f"fps должен быть одним из {', '.join(map(str, FPS_ALLOWED))}, "
            f"получено {fps}"
        )
    if fps > FPS_NATIVE and not fps_enabled:
        raise QualityError(_FPS_GATED_MSG)
    return QualitySettings(duration_sec=duration, fps=fps)


def interpolation_multiplier(fps: int) -> int:
    """Integer RIFE multiplier for ``fps`` relative to the native 21 fps."""
    return max(1, round(fps / FPS_NATIVE))


def fps_interpolation_enabled() -> bool:
    """Read the ``ENABLE_FPS_INTERPOLATION`` feature flag (default: off)."""
    return os.environ.get("ENABLE_FPS_INTERPOLATION", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
