# -*- coding: utf-8 -*-
"""Vizir paid StepHandler — Grok motion-prompt for ONE static photo.

Reuses the proven money-aware Grok path (run #2): ``grok_vision
.analyze_images_detailed`` returns text + the real reported cost from the SAME
call. The SINGLE-image motion question (``MOTION_VISION_QUESTION``) is the right
tool for one static photo — the video-motion question expects multiple frames
and unreliably refuses a single image. Refusal detection reuses the proven
``motion_prompt_ai`` heuristics.

Money rule mirrored: a refusal / empty reply returns ``ok=False`` so the
Coordinator does NOT charge it ('refusal не списан'). The Coordinator owns the
budget gate (check-before) and the charge-after — this handler only does work.

``analyze_fn`` / ``question`` are injectable for $0 unit tests; defaults bind the
real path lazily so importing this module never pulls heavy deps until used.
"""
from __future__ import annotations

from .coordinator import StepBudgetExceeded
from .handlers import HandlerResult
from .models import Step


def _default_analyze(image_paths, question):
    from app.services.grok_vision import analyze_images_detailed
    return analyze_images_detailed(image_paths, question)


def _default_question():
    from app.services.block_m2_video.motion_prompt_ai import MOTION_VISION_QUESTION
    return MOTION_VISION_QUESTION


def _clean_and_check(text: str):
    """Return (cleaned_prompt, is_refusal) using the proven motion_prompt_ai logic."""
    from app.services.block_m2_video.motion_prompt_ai import _clean, _looks_like_refusal
    cleaned = _clean(text)
    return cleaned, _looks_like_refusal(cleaned)


def make_grok_motion_handler(analyze_fn=None, question=None):
    """Return an async StepHandler turning one image into a motion prompt via Grok.

    ``analyze_fn(image_paths, question) -> obj`` with ``.text`` (str) and
    ``.cost_usd`` (float|None). Defaults to the real proven single-image path."""
    afn = analyze_fn or _default_analyze
    q = question if question is not None else _default_question()

    async def grok_motion(step: Step, ctx: dict) -> HandlerResult:
        image = step.params["image_path"]
        # Mid-flight contract (optional): use the ctx callbacks when a coordinator
        # provides them; absent (direct unit calls) -> behaves exactly as before.
        report_progress = ctx.get("report_progress")
        report_cost = ctx.get("report_cost")
        if report_progress:
            report_progress("grok: requesting motion prompt")

        res = afn([image], q)
        cost = float(getattr(res, "cost_usd", None) or 0.0)
        text = (getattr(res, "text", "") or "").strip()
        if not text:
            return HandlerResult(ok=False, error="grok empty/no-key", cost_usd=cost)
        cleaned, is_refusal = _clean_and_check(text)
        if is_refusal:
            return HandlerResult(ok=False, error="grok refusal", cost_usd=cost)

        # Grok is a SINGLE atomic call — the money is already spent, so a cap
        # breach here cannot un-spend it. Report for visibility but swallow
        # StepBudgetExceeded; the Coordinator still charges via charge-after
        # (result.cost_usd) so an already-incurred cost is never lost.
        if report_cost and cost > 0:
            try:
                report_cost(cost)
            except StepBudgetExceeded:
                pass
        return HandlerResult(ok=True, result=cleaned, cost_usd=cost)

    return grok_motion
