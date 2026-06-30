# -*- coding: utf-8 -*-
"""Vizir Hermes mid-flight integration — Phase M (mocks, $0, TDD).

Drives the Hermes handler under the REAL Coordinator to prove the frame holds
around a multi-step agent:
  * within cap  -> completes, streams cost_progress, charges actual (charge-after)
  * over cap    -> report_cost raises mid-flight -> StepBudgetExceeded propagates
                   out of Hermes -> step BLOCKED, partial charge, run STOPS
                   (stopped_cost_cap)
  * SPY         -> a bridge that never reports cost lets the same run overspend
                   undetected, proving the cap test above has TEETH.
No real money — Hermes is a fake ``run_fn`` streaming cumulative cost."""
import asyncio

from app.services.vizir.models import Task, Step, Plan, StepStatus
from app.services.vizir.handlers import HandlerRegistry
from app.services.vizir.coordinator import Coordinator
from app.services.vizir.handlers_hermes import make_hermes_handler


def _run(coro):
    return asyncio.run(coro)


def _charge_sink(sink):
    async def charge(actor, op, amount):
        sink.append((actor, op, amount))
    return charge


def _streaming_run(cumulatives, final_response="<html></html>", stopped="completed"):
    """Fake Hermes: emits each cumulative cost via on_step (which may RAISE and
    abort the loop, exactly as a propagated StepBudgetExceeded stops Hermes)."""
    def run_fn(prompt, *, model, enabled_toolsets, disabled_toolsets,
               max_iterations, on_step):
        n = 0
        for cum in cumulatives:
            on_step(cum, note=f"iter {n}")
            n += 1
        return dict(final_response=final_response, cost_usd=cumulatives[-1],
                    iterations=n, stopped_reason=stopped, tokens=100 * n)
    return run_fn


def test_within_cap_completes_streams_progress_and_charges_actual():
    reg = HandlerRegistry()
    reg.register("hermes", make_hermes_handler(
        run_fn=_streaming_run([0.01, 0.02, 0.03])))
    events, charges = [], []
    coord = Coordinator(reg, on_event=events.append, charge_logger=_charge_sink(charges))
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    plan = Plan(steps=[Step(kind="hermes", params={"prompt": "x"},
                            estimated_usd=0.10, max_usd=0.10)])
    report = _run(coord.run(task, plan))

    assert report.steps[0].status is StepStatus.DONE
    assert abs(report.total_cost_usd - 0.03) < 1e-9          # charge-after = actual total
    assert charges == [("admin", "hermes", 0.03)]
    types = [e["type"] for e in events]
    assert "cost_progress" in types and "progress" in types  # mid-flight visibility


def test_cost_cap_breach_blocks_charges_partial_and_stops_run():
    reg = HandlerRegistry()
    # cumulative 0.02,0.04,0.06,0.08 -> deltas 0.02 each; cap 0.05 -> 3rd delta raises
    reg.register("hermes", make_hermes_handler(
        run_fn=_streaming_run([0.02, 0.04, 0.06, 0.08])))
    charges = []
    coord = Coordinator(reg, charge_logger=_charge_sink(charges))
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    plan = Plan(steps=[
        Step(kind="hermes", params={"prompt": "x"}, estimated_usd=0.05, max_usd=0.05),
        Step(kind="hermes", params={"prompt": "y"}, estimated_usd=0.05, max_usd=0.05),
    ])
    report = _run(coord.run(task, plan))

    assert report.steps[0].status is StepStatus.BLOCKED
    assert abs(report.steps[0].cost_usd - 0.04) < 1e-9       # approved deltas only
    assert report.status == "stopped_cost_cap"               # money breach -> STOP
    assert report.steps[1].status is StepStatus.SKIPPED      # run stopped, 2nd not run
    assert charges == [("admin", "hermes", 0.04)]            # PARTIAL charge of actual


def test_spy_bridge_without_report_cost_overspends_proving_teeth():
    # SABOTAGE: a run that NEVER calls on_step (broken bridge) -> coordinator never
    # meters the spend -> the cap cannot fire mid-flight.
    def silent_run(prompt, *, model, enabled_toolsets, disabled_toolsets,
                   max_iterations, on_step):
        return dict(final_response="<html></html>", cost_usd=0.08,
                    iterations=4, stopped_reason="completed", tokens=400)

    reg = HandlerRegistry()
    reg.register("hermes", make_hermes_handler(run_fn=silent_run))
    coord = Coordinator(reg)
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    plan = Plan(steps=[Step(kind="hermes", params={"prompt": "x"},
                            estimated_usd=0.05, max_usd=0.05)])
    report = _run(coord.run(task, plan))

    # broken bridge -> $0.08 spent through a $0.05 cap, NOT blocked
    assert report.steps[0].status is StepStatus.DONE
    assert abs(report.total_cost_usd - 0.08) < 1e-9
    # therefore the real cap test (BLOCKED, stopped_cost_cap) would FAIL -> it has teeth
    cap_assertion_holds = (report.steps[0].status is StepStatus.BLOCKED
                           and report.status == "stopped_cost_cap")
    assert not cap_assertion_holds
