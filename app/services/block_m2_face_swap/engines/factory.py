# -*- coding: utf-8 -*-
"""Select the active SwapEngine from the SWAP_ENGINE env var.

Cost lookups (used by the cost estimator before any engine is built) map the
engine name to constants WITHOUT instantiating — so a token-less environment
can still compute an estimate.
"""
from __future__ import annotations

import os

from .base import SwapEngine
from .lucataco_engine import (
    COLD_START_USD as _LUCATACO_COLD_START,
    COST_PER_SWAP_USD as _LUCATACO_COST,
)

_DEFAULT = "lucataco"

# Cost-only metadata (no class construction). RunPod values mirror the legacy
# SWAPBATCH_* env defaults; lucataco is managed (no cold start).
_COST_PER_PHOTO = {"lucataco": _LUCATACO_COST, "runpod": 0.02}
_COLD_START = {"lucataco": _LUCATACO_COLD_START, "runpod": 0.05}


def _name() -> str:
    return (os.getenv("SWAP_ENGINE", _DEFAULT) or _DEFAULT).strip().lower()


def get_swap_cost_per_photo() -> float:
    return _COST_PER_PHOTO.get(_name(), 0.02)


def get_swap_cold_start_usd() -> float:
    return _COLD_START.get(_name(), 0.05)


def get_swap_engine() -> SwapEngine:
    name = _name()
    if name == "lucataco":
        from .lucataco_engine import LucatacoSwapEngine
        return LucatacoSwapEngine()
    if name == "runpod":
        # Frozen RunPod path — kept for rollback, NOT the default.
        from ..face_swap_engine import FaceSwapEngine
        return FaceSwapEngine()
    raise ValueError(f"Unknown SWAP_ENGINE={name!r} (use lucataco|runpod)")
