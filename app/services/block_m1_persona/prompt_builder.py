# -*- coding: utf-8 -*-
"""Diverse FLUX prompt generator for AI persona seed photo creation."""
from __future__ import annotations

_ANGLES = [
    "front view",
    "slight three-quarter view",
    "side profile",
    "slightly elevated angle",
    "low angle looking up",
]

_LIGHTING = [
    "natural window light",
    "golden hour sunlight",
    "studio softbox lighting",
    "dramatic side lighting",
    "soft diffused overcast light",
]

_BACKGROUNDS = [
    "plain white background",
    "blurred urban street",
    "minimal modern interior",
    "blurred lush garden",
    "neutral grey studio backdrop",
]

_EMOTIONS = [
    "natural relaxed smile",
    "serious confident expression",
    "candid genuine laugh",
    "thoughtful gaze",
    "warm friendly look",
]

_COMPOSITIONS = [
    "head and shoulders portrait",
    "full body shot",
    "waist-up composition",
    "close-up face crop",
    "environmental portrait",
]

_QUALITY_SUFFIX = (
    ", photorealistic, 8k resolution, professional photography, "
    "sharp focus, high detail, cinematic"
)


class PersonaPromptBuilder:
    """Generates diverse FLUX prompts for persona seed photo generation."""

    @staticmethod
    def build_seed_prompts(description: str, style: str, count: int = 20) -> list[str]:
        """Build a list of diverse generation prompts for a persona.

        Args:
            description: Physical description of the persona.
            style: Content style tag (e.g. "fashion", "lifestyle").
            count: Number of prompts to generate.

        Returns:
            List of ``count`` unique prompt strings.
        """
        pools = [_ANGLES, _LIGHTING, _BACKGROUNDS, _EMOTIONS, _COMPOSITIONS]
        pool_sizes = [len(p) for p in pools]
        total_combinations = 1
        for s in pool_sizes:
            total_combinations *= s

        prompts: list[str] = []
        seen: set[str] = set()

        for i in range(max(count, total_combinations)):
            if len(prompts) >= count:
                break
            angle = _ANGLES[i % len(_ANGLES)]
            lighting = _LIGHTING[(i // len(_ANGLES)) % len(_LIGHTING)]
            background = _BACKGROUNDS[(i // (len(_ANGLES) * len(_LIGHTING))) % len(_BACKGROUNDS)]
            emotion = _EMOTIONS[(i // (len(_ANGLES) * len(_LIGHTING) * len(_BACKGROUNDS))) % len(_EMOTIONS)]
            composition = _COMPOSITIONS[
                (i // (len(_ANGLES) * len(_LIGHTING) * len(_BACKGROUNDS) * len(_EMOTIONS)))
                % len(_COMPOSITIONS)
            ]

            prompt = (
                f"A professional {style} photo of a person: {description}. "
                f"{composition}, {angle}, {lighting}, {background}, {emotion}"
                f"{_QUALITY_SUFFIX}"
            )
            if prompt not in seen:
                seen.add(prompt)
                prompts.append(prompt)

        # Fill remaining with style variations if count exceeds combinations
        extra_styles = [
            "editorial fashion shoot",
            "lifestyle brand campaign",
            "beauty photography",
            "corporate headshot",
            "social media content",
        ]
        idx = 0
        while len(prompts) < count:
            variation = extra_styles[idx % len(extra_styles)]
            prompt = (
                f"A {variation} featuring a person: {description}. "
                f"{style} style, varied composition{_QUALITY_SUFFIX}"
            )
            unique_prompt = f"{prompt} (v{len(prompts)})"
            prompts.append(unique_prompt)
            idx += 1

        return prompts[:count]
