# -*- coding: utf-8 -*-
"""Per-engine capability descriptors.

The single source of truth the UI, cost gate, and request validation read from.
Each engine offers exactly what it can do; the engine implementations also use
their own caps to snap duration/resolution and compute cost.

Seedance pricing is APPROXIMATE (Replicate token billing) — refine from the
first real billing. WaveSpeed pricing is verified.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EngineCapabilities:
    engine_mode: str
    display_name: str
    allowed_durations: tuple[int, ...]
    allowed_resolutions: tuple[str, ...]
    default_duration: int
    default_resolution: str
    native_fps: int
    censored: bool
    pricing: dict           # {(resolution, seconds): usd}
    gen_seconds_table: dict  # {seconds: rough wall-clock seconds per clip}

    def snap_duration(self, seconds: int) -> int:
        return min(self.allowed_durations, key=lambda d: (abs(d - seconds), d))

    def snap_resolution(self, resolution: str) -> str:
        return resolution if resolution in self.allowed_resolutions else self.default_resolution

    def cost_for(self, seconds: int, resolution: str) -> float:
        return self.pricing[(self.snap_resolution(resolution), self.snap_duration(seconds))]

    def gen_seconds(self, seconds: int) -> int:
        return self.gen_seconds_table[self.snap_duration(seconds)]


WAVESPEED_CAPS = EngineCapabilities(
    engine_mode="spicy",
    display_name="WaveSpeed (без цензуры)",
    allowed_durations=(5, 10, 15),
    allowed_resolutions=("720p", "1080p"),
    default_duration=10,
    default_resolution="720p",
    native_fps=30,
    censored=False,
    pricing={
        ("720p", 5): 0.50, ("720p", 10): 1.00, ("720p", 15): 1.50,
        ("1080p", 5): 0.75, ("1080p", 10): 1.50, ("1080p", 15): 2.25,
    },
    gen_seconds_table={5: 40, 10: 60, 15: 90},
)

SEEDANCE_CAPS = EngineCapabilities(
    engine_mode="seedance",
    display_name="Seedance (дёшево, 10с/1080p)",
    allowed_durations=(5, 10),
    allowed_resolutions=("480p", "720p", "1080p"),
    default_duration=5,
    default_resolution="1080p",
    native_fps=24,
    censored=True,
    pricing={  # APPROX — refine from first real Replicate billing
        ("480p", 5): 0.05, ("480p", 10): 0.10,
        ("720p", 5): 0.11, ("720p", 10): 0.22,
        ("1080p", 5): 0.25, ("1080p", 10): 0.55,
    },
    gen_seconds_table={5: 90, 10: 150},
)

CAPS_BY_MODE = {"spicy": WAVESPEED_CAPS, "seedance": SEEDANCE_CAPS}


def caps_for(mode: str) -> EngineCapabilities:
    """Return the capability descriptor for an engine mode, else KeyError."""
    return CAPS_BY_MODE[mode]
