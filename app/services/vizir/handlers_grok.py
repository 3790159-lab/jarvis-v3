# -*- coding: utf-8 -*-
"""Vizir paid StepHandler — Grok motion-prompt.

Reuses the PROVEN money-aware Grok path (run #2 / Веха C):
``motion_prompt_ai.generate_video_motion_prompt`` returns a prompt (or None on
refusal/empty/no-key) plus the real reported cost from the SAME call.

Money rule mirrored: a refusal (prompt is None) returns ``ok=False`` so the
Coordinator does NOT charge it ('refusal не списан'). The Coordinator owns the
budget gate (check-before) and the charge-after — this handler only does work.

``motion_fn`` is injectable for $0 unit tests; the default binds the real path
lazily so importing this module never pulls heavy video deps until used.
"""
from __future__ import annotations

from .handlers import HandlerResult
from .models import Step


def _default_motion_fn(frames):
    from app.services.block_m2_video.motion_prompt_ai import generate_video_motion_prompt
    return generate_video_motion_prompt(frames)


def make_grok_motion_handler(motion_fn=None):
    """Return an async StepHandler that turns one image into a motion prompt via
    Grok. ``motion_fn(frames) -> obj`` with ``.prompt`` (str|None) and
    ``.cost_usd`` (float|None). Defaults to the real proven path."""
    fn = motion_fn or _default_motion_fn

    async def grok_motion(step: Step, ctx: dict) -> HandlerResult:
        image = step.params["image_path"]
        res = fn([image])
        cost = float(getattr(res, "cost_usd", None) or 0.0)
        if getattr(res, "prompt", None) is None:
            # Refusal / empty / no-key: not usable -> ok=False -> NOT charged.
            return HandlerResult(ok=False, error="grok refusal/empty", cost_usd=cost)
        return HandlerResult(ok=True, result=res.prompt, cost_usd=cost)

    return grok_motion
