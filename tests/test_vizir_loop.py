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


from app.services.vizir.models import Task, Step, Plan
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.coordinator import Coordinator
from app.services.vizir.loop import LoopController, LoopConfig
from app.services.vizir.hermes_acceptance import AcceptanceResult


def _coord_with(handler, kind="gen", **coord_kw):
    reg = HandlerRegistry()
    reg.register(kind, handler)
    return Coordinator(reg, **coord_kw)


def _gen_handler(result_dict, cost=0.0, kind="gen"):
    """A mock generation handler returning a fixed result dict, charging `cost`."""
    async def handler(step, ctx):
        return HandlerResult(ok=True, result=result_dict, cost_usd=cost)
    return handler


def _build_plan_gen(prompt):
    # single-step plan, kind "gen"; estimated_usd=0 so pre-check treats it free
    return Plan(steps=[Step(kind="gen", params={"prompt": prompt}, estimated_usd=0.0)])


def _loop(coord, accept_fn, cfg=None, on_event=None, now_fn=None, build_plan=_build_plan_gen):
    return LoopController(coord, build_plan=build_plan, accept_fn=accept_fn,
                          config=cfg or LoopConfig(), on_event=on_event, now_fn=now_fn)


def test_completed_on_first_attempt_stops_and_not_escalated():
    coord = _coord_with(_gen_handler({"final_response": "<html></html>",
                                      "stopped_reason": "completed"}))
    accept_fn = lambda d: AcceptanceResult(accepted=True, reasons=[])
    loop = _loop(coord, accept_fn)
    task = Task("t", "make chat", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "base goal"))

    assert rep.accepted is True
    assert rep.stopped_reason == "completed"
    assert rep.attempts == 1
    assert rep.needs_escalation is False


def test_never_accepted_stops_at_max_attempts_and_escalates():
    n = {"i": 0}
    async def handler(step, ctx):
        n["i"] += 1
        return HandlerResult(ok=True, result={"n": n["i"], "stopped_reason": "completed"}, cost_usd=0.0)
    coord = _coord_with(handler)
    # different reasons each attempt (no stall), never accepted
    accept_fn = lambda d: AcceptanceResult(accepted=False, reasons=["reason-%d" % d.get("n", 0)])
    loop = _loop(coord, accept_fn, cfg=LoopConfig(max_attempts=3))
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "base"))

    assert rep.attempts == 3
    assert rep.stopped_reason == "stopped_max_attempts"
    assert rep.accepted is False
    assert rep.needs_escalation is True
    assert len(rep.reasons_history) == 3


def test_feedback_reasons_injected_into_next_prompt_base_preserved():
    seen_prompts = []
    def build_plan(prompt):
        seen_prompts.append(prompt)
        return Plan(steps=[Step(kind="gen", params={"prompt": prompt}, estimated_usd=0.0)])

    n = {"i": 0}
    async def handler(step, ctx):
        n["i"] += 1
        return HandlerResult(ok=True, result={"n": n["i"], "stopped_reason": "completed"}, cost_usd=0.0)
    coord = _coord_with(handler)
    # attempt 1 rejected with a specific reason; attempt 2 accepted
    def accept_fn(d):
        if d.get("n", 0) >= 2:
            return AcceptanceResult(accepted=True, reasons=[])
        return AcceptanceResult(accepted=False, reasons=["no neon cyan/blue accent"])
    loop = _loop(coord, accept_fn, build_plan=build_plan)
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "BASE_GOAL"))

    assert rep.accepted is True and rep.attempts == 2
    # attempt 1 prompt = base only; attempt 2 prompt = base + injected reason
    assert seen_prompts[0] == "BASE_GOAL"
    assert "BASE_GOAL" in seen_prompts[1]                     # base preserved (immutable)
    assert "no neon cyan/blue accent" in seen_prompts[1]      # reason injected
    assert rep.reasons_history == [["no neon cyan/blue accent"]]
