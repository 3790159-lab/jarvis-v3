# -*- coding: utf-8 -*-
"""Vizir autonomy LOOP — приёмко-гейтед retry over the Coordinator. $0 (mocks)."""
import asyncio

from app.services.vizir.loop import LoopConfig, LoopReport


def _run(coro):
    return asyncio.run(coro)


def test_loopconfig_defaults():
    cfg = LoopConfig()
    assert cfg.max_attempts == 3
    assert cfg.min_attempt_usd == 0.0
    assert cfg.loop_deadline_s == 0.0


def test_loopreport_fields():
    r = LoopReport(
        task_id="t", goal="g", actor="admin", accepted=True,
        stopped_reason="completed", attempts=1, loop_spent_usd=0.0,
        last_result={"x": 1}, reasons=[], reasons_history=[], needs_escalation=False,
    )
    assert r.accepted is True and r.stopped_reason == "completed"
    assert r.needs_escalation is False and r.attempts == 1
