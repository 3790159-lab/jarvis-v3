# -*- coding: utf-8 -*-
"""Vizir Phase 3 — grok_motion paid StepHandler (mocked, $0).
Wraps the proven Grok motion-prompt path. success -> ok + cost; refusal/empty ->
ok=False (NOT charged, mirrors 'refusal не списан'). TDD on mocks first."""
import asyncio
from types import SimpleNamespace

from app.services.vizir.models import Step
from app.services.vizir.handlers_grok import make_grok_motion_handler


def _run(coro):
    return asyncio.run(coro)


def test_grok_motion_success_returns_ok_with_cost():
    def fake_motion(frames):
        return SimpleNamespace(prompt="slow head turn, locked static camera", cost_usd=0.0123)

    handler = make_grok_motion_handler(motion_fn=fake_motion)
    step = Step(kind="grok_motion", params={"image_path": "x.jpg"}, estimated_usd=0.01)
    res = _run(handler(step, {"task": None, "results": {}}))

    assert res.ok is True
    assert "locked static camera" in res.result
    assert abs(res.cost_usd - 0.0123) < 1e-9


def test_grok_motion_refusal_is_not_charged():
    def fake_refusal(frames):
        # refusal: prompt None, but the xAI call still reported a cost
        return SimpleNamespace(prompt=None, cost_usd=0.0098)

    handler = make_grok_motion_handler(motion_fn=fake_refusal)
    step = Step(kind="grok_motion", params={"image_path": "x.jpg"}, estimated_usd=0.01)
    res = _run(handler(step, {"task": None, "results": {}}))

    assert res.ok is False                  # -> coordinator will NOT charge it
    assert res.error
