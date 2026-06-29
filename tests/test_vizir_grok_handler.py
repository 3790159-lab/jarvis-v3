# -*- coding: utf-8 -*-
"""Vizir Phase 3 — grok_motion paid StepHandler (mocked, $0).

Uses the SINGLE-image motion question via grok_vision.analyze_images_detailed
(correct tool for one static photo; the video-motion question is for multi-frame
and unreliably refuses a single image). success -> ok + cost; refusal/empty ->
ok=False (NOT charged, 'refusal не списан'). analyze_fn injected for $0 tests."""
import asyncio
from types import SimpleNamespace

from app.services.vizir.models import Step
from app.services.vizir.handlers_grok import make_grok_motion_handler


def _run(coro):
    return asyncio.run(coro)


def test_grok_motion_success_returns_ok_with_cost_and_clean_prompt():
    def fake_analyze(paths, question):
        return SimpleNamespace(
            text="slow gentle head turn, soft blinking, locked static camera, photorealistic",
            cost_usd=0.0205,
        )

    handler = make_grok_motion_handler(analyze_fn=fake_analyze)
    step = Step(kind="grok_motion", params={"image_path": "x.jpg"}, estimated_usd=0.025)
    res = _run(handler(step, {"task": None, "results": {}}))

    assert res.ok is True
    assert "locked static camera" in res.result
    assert abs(res.cost_usd - 0.0205) < 1e-9


def test_grok_motion_refusal_is_not_charged():
    def fake_refusal(paths, question):
        # a natural-language refusal (opener "I can't") -> refusal layer fires
        return SimpleNamespace(text="I can't help with that request.", cost_usd=0.0102)

    handler = make_grok_motion_handler(analyze_fn=fake_refusal)
    step = Step(kind="grok_motion", params={"image_path": "x.jpg"}, estimated_usd=0.025)
    res = _run(handler(step, {"task": None, "results": {}}))

    assert res.ok is False
    assert res.error


def test_grok_motion_empty_is_not_charged():
    def fake_empty(paths, question):
        return SimpleNamespace(text="", cost_usd=0.0)

    handler = make_grok_motion_handler(analyze_fn=fake_empty)
    step = Step(kind="grok_motion", params={"image_path": "x.jpg"}, estimated_usd=0.025)
    res = _run(handler(step, {"task": None, "results": {}}))

    assert res.ok is False
