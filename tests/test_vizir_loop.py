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


def test_reserve_before_attempt_stops_budget_without_running_handler():
    calls = {"n": 0}
    async def handler(step, ctx):
        calls["n"] += 1
        return HandlerResult(ok=True, result={"stopped_reason": "completed"}, cost_usd=0.01)
    coord = _coord_with(handler)
    accept_fn = lambda d: AcceptanceResult(accepted=False, reasons=["still bad"])
    cfg = LoopConfig(max_attempts=5, min_attempt_usd=0.015)  # need >= $0.015 to start
    loop = _loop(coord, accept_fn, cfg=cfg)
    task = Task("t", "g", budget_usd=0.02, actor="admin")
    rep = _run(loop.run(task, "base"))

    # attempt1: remaining 0.02 >= 0.015 -> runs, spends 0.01. attempt2: remaining
    # 0.01 < 0.015 -> reserve REFUSES to start (handler not called again).
    assert calls["n"] == 1
    assert rep.stopped_reason == "stopped_budget"
    assert rep.attempts == 1
    assert rep.loop_spent_usd <= task.budget_usd
    assert rep.needs_escalation is True


def test_cost_cap_breach_in_attempt_stops_loop_immediately_no_retry():
    # handler overspends via report_cost beyond the step cap -> Coordinator
    # returns stopped_cost_cap (proven mechanism, see test_vizir_cost_cap.py).
    async def overspender(step, ctx):
        for c in [0.01, 0.01, 0.01, 0.01]:   # 4 x 0.01 vs max_usd 0.03
            ctx["report_cost"](c)             # 4th raises StepBudgetExceeded
        return HandlerResult(ok=True, result={"stopped_reason": "completed"}, cost_usd=0.04)
    coord = _coord_with(overspender)

    def build_plan(prompt):
        return Plan(steps=[Step(kind="gen", params={"prompt": prompt},
                                estimated_usd=0.03, max_usd=0.03)])

    accept_fn = lambda d: AcceptanceResult(accepted=True, reasons=[])  # would accept if reached
    loop = _loop(coord, accept_fn, cfg=LoopConfig(max_attempts=3), build_plan=build_plan)
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "base"))

    assert rep.attempts == 1                       # did NOT retry a breached attempt
    assert rep.stopped_reason == "stopped_cost_cap"
    assert rep.accepted is False
    assert rep.needs_escalation is True
    assert abs(rep.loop_spent_usd - 0.03) < 1e-9   # partial charge (step_spent), not full


def test_identical_reasons_two_attempts_stops_stalled_before_max():
    async def handler(step, ctx):
        return HandlerResult(ok=True, result={"stopped_reason": "completed"}, cost_usd=0.0)
    coord = _coord_with(handler)
    # SAME reasons every attempt -> not converging
    accept_fn = lambda d: AcceptanceResult(accepted=False, reasons=["no dark theme background"])
    loop = _loop(coord, accept_fn, cfg=LoopConfig(max_attempts=5))
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "base"))

    assert rep.attempts == 2                     # stalled at 2, NOT run to max_attempts=5
    assert rep.stopped_reason == "stopped_stalled"
    assert rep.needs_escalation is True


def test_loop_deadline_stops_timeout_between_attempts():
    async def handler(step, ctx):
        return HandlerResult(ok=True, result={"stopped_reason": "completed"}, cost_usd=0.0)
    coord = _coord_with(handler)
    accept_fn = lambda d: AcceptanceResult(accepted=False, reasons=["reason-%s" % id(d)])

    # fake monotonic clock: 0 at start, then jumps past the 10s deadline
    ticks = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    now_fn = lambda: next(ticks)
    cfg = LoopConfig(max_attempts=10, loop_deadline_s=10.0)
    loop = _loop(coord, accept_fn, cfg=cfg, now_fn=now_fn)
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "base"))

    assert rep.stopped_reason == "stopped_timeout"
    assert rep.attempts == 1                     # 1 attempt ran, deadline hit before attempt 2
    assert rep.needs_escalation is True


def test_loop_emits_structured_events():
    events = []
    n = {"i": 0}
    async def handler(step, ctx):
        n["i"] += 1
        return HandlerResult(ok=True, result={"n": n["i"], "stopped_reason": "completed"}, cost_usd=0.0)
    coord = _coord_with(handler)
    def accept_fn(d):
        return AcceptanceResult(accepted=(d.get("n", 0) >= 2),
                                reasons=[] if d.get("n", 0) >= 2 else ["bad-%d" % d.get("n", 0)])
    loop = _loop(coord, accept_fn, on_event=events.append)
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "base"))

    types = [e["type"] for e in events]
    assert rep.accepted is True and rep.attempts == 2
    assert types.count("loop_attempt_started") == 2
    assert types.count("loop_attempt_rejected") == 1
    assert types[-1] == "loop_stopped"
    stopped = events[-1]
    assert stopped["reason"] == "completed" and stopped["attempts"] == 2
    rejected = [e for e in events if e["type"] == "loop_attempt_rejected"][0]
    assert rejected["reasons"] == ["bad-1"]


def test_spy_broken_reserve_lets_loop_overspend_proving_budget_teeth():
    async def handler(step, ctx):
        return HandlerResult(ok=True, result={"stopped_reason": "completed"}, cost_usd=0.01)
    coord = _coord_with(handler)
    accept_fn = lambda d: AcceptanceResult(accepted=False, reasons=["x-%s" % id(object())])
    cfg = LoopConfig(max_attempts=10, min_attempt_usd=0.015)
    loop = _loop(coord, accept_fn, cfg=cfg)
    # SABOTAGE reserve: always allow starting an attempt
    loop._reserve_allows = lambda remaining, c: True
    task = Task("t", "g", budget_usd=0.02, actor="admin")
    rep = _run(loop.run(task, "base"))
    # broken reserve -> the per-attempt Task budget backstop (Coordinator) still
    # bounds it, but the LOOP no longer stops with stopped_budget at attempt 1.
    # The real budget test asserts (attempts==1, stopped_budget); a broken reserve
    # violates it -> that assertion has teeth.
    real_budget_assertion_holds = (rep.attempts == 1 and rep.stopped_reason == "stopped_budget")
    assert not real_budget_assertion_holds


def test_spy_broken_stall_runs_to_max_proving_stall_teeth():
    async def handler(step, ctx):
        return HandlerResult(ok=True, result={"stopped_reason": "completed"}, cost_usd=0.0)
    coord = _coord_with(handler)
    accept_fn = lambda d: AcceptanceResult(accepted=False, reasons=["same reason"])
    loop = _loop(coord, accept_fn, cfg=LoopConfig(max_attempts=4))
    # SABOTAGE stall detector: never stalled
    loop._is_stalled = lambda reasons, prev: False
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "base"))
    # identical reasons no longer stop at 2 -> runs to max_attempts=4
    assert rep.attempts == 4 and rep.stopped_reason == "stopped_max_attempts"
    real_stall_assertion_holds = (rep.attempts == 2 and rep.stopped_reason == "stopped_stalled")
    assert not real_stall_assertion_holds


def test_spy_broken_hardstop_retries_breach_proving_costcap_teeth():
    async def overspender(step, ctx):
        for c in [0.01, 0.01, 0.01, 0.01]:
            ctx["report_cost"](c)
        return HandlerResult(ok=True, result={"stopped_reason": "completed"}, cost_usd=0.04)
    coord = _coord_with(overspender)
    def build_plan(prompt):
        return Plan(steps=[Step(kind="gen", params={"prompt": prompt},
                                estimated_usd=0.03, max_usd=0.03)])
    accept_fn = lambda d: AcceptanceResult(accepted=False, reasons=["r-%s" % id(object())])
    loop = _loop(coord, accept_fn, cfg=LoopConfig(max_attempts=3), build_plan=build_plan)
    # SABOTAGE hard-stop: pretend every run "completed"
    loop._is_hard_stop = lambda status: False
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    rep = _run(loop.run(task, "base"))
    # broken hard-stop -> a breached attempt is treated as completed and RETRIED
    assert rep.attempts > 1
    real_costcap_assertion_holds = (rep.attempts == 1 and rep.stopped_reason == "stopped_cost_cap")
    assert not real_costcap_assertion_holds


def test_spy_broken_compose_drops_base_proving_goaldrift_teeth():
    seen = []
    def build_plan(prompt):
        seen.append(prompt)
        return Plan(steps=[Step(kind="gen", params={"prompt": prompt}, estimated_usd=0.0)])
    n = {"i": 0}
    async def handler(step, ctx):
        n["i"] += 1
        return HandlerResult(ok=True, result={"n": n["i"], "stopped_reason": "completed"}, cost_usd=0.0)
    coord = _coord_with(handler)
    accept_fn = lambda d: AcceptanceResult(accepted=(d.get("n", 0) >= 2),
                                           reasons=[] if d.get("n", 0) >= 2 else ["fix me"])
    loop = _loop(coord, accept_fn, build_plan=build_plan)
    # SABOTAGE compose: overwrite the goal with only the feedback (drift)
    loop._compose_prompt = lambda base, reasons: ("; ".join(reasons) if reasons else base)
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    _run(loop.run(task, "BASE_GOAL"))
    # broken compose -> attempt 2 prompt LOST the base goal
    assert "BASE_GOAL" not in seen[1]
    real_immutable_assertion_holds = ("BASE_GOAL" in seen[1])
    assert not real_immutable_assertion_holds


_GOOD_HTML = (
    "<html><head><style>body{background:#0a0a0a;} .a{color:#00ffff;}</style></head>"
    "<body><div id=\"messages\" class=\"chat\"></div>"
    "<input id=\"msg\"><button onclick=\"send()\">Send</button>"
    "<span id=\"status\">online</span>"
    "<script>const API_URL=\"http://localhost:8010\";"
    "function send(){fetch(API_URL,{method:\"POST\"});}</script></body></html>"
)
# same but neon accent removed -> acceptance rejects with "no neon cyan/blue accent"
_BAD_HTML = _GOOD_HTML.replace("#00ffff", "#ffffff")


def test_make_loop_with_real_acceptance_retries_then_accepts():
    from app.services.vizir.loop import make_loop
    seen = []
    n = {"i": 0}
    async def hermes_fake(step, ctx):
        seen.append(step.params["prompt"])
        n["i"] += 1
        html = _BAD_HTML if n["i"] == 1 else _GOOD_HTML
        return HandlerResult(ok=True,
                             result={"final_response": html, "stopped_reason": "completed"},
                             cost_usd=0.0)
    reg = HandlerRegistry()
    reg.register("hermes", hermes_fake)
    coord = Coordinator(reg)
    loop = make_loop(coord, kind="hermes", estimated_usd=0.0, max_usd=0.0, max_attempts=3)
    task = Task("t", "make jarvis chat", budget_usd=0.50, actor="admin")
    rep = _run(loop.run(task, "Generate a Jarvis dark-neon web chat."))

    assert rep.accepted is True and rep.stopped_reason == "completed"
    assert rep.attempts == 2
    # attempt 2 prompt carried the real acceptance reason from attempt 1
    assert "neon" in seen[1]


def test_make_loop_truncated_run_is_rejected_up_front():
    from app.services.vizir.loop import make_loop
    async def hermes_fake(step, ctx):
        # good HTML but run did NOT complete (hit max_iterations) -> acceptance rejects
        return HandlerResult(ok=True,
                             result={"final_response": _GOOD_HTML, "stopped_reason": "max_iterations"},
                             cost_usd=0.0)
    reg = HandlerRegistry()
    reg.register("hermes", hermes_fake)
    coord = Coordinator(reg)
    loop = make_loop(coord, kind="hermes", estimated_usd=0.0, max_usd=0.0, max_attempts=2)
    task = Task("t", "g", budget_usd=0.50, actor="admin")
    rep = _run(loop.run(task, "base"))

    assert rep.accepted is False
    assert rep.stopped_reason in ("stopped_stalled", "stopped_max_attempts")
    assert any("did not complete" in r for r in rep.reasons)
