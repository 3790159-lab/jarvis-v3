# -*- coding: utf-8 -*-
"""Vizir mid-flight T-B (Feature 1) — per-step cost cap ENFORCEMENT.

report_cost is reserve-before-spend: if step_spent+chunk would exceed Step.max_usd
(or the task budget / actor limit), it RAISES StepBudgetExceeded so the chunk is
never spent and the agent unwinds. The coordinator catches it -> step BLOCKED,
charges step_spent (the approved=actually-spent chunks, partial, never the full
estimate, never zero) -> stops the run (stopped_cost_cap). TDD on mocks ($0)."""
import asyncio

from app.services.vizir.models import Task, Step, Plan, StepStatus
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.coordinator import Coordinator, StepBudgetExceeded


def _run(coro):
    return asyncio.run(coro)


def _agent_reg(chunks, approved, kind="streamer"):
    """Agent reports each chunk via report_cost (may raise), counting approved."""
    reg = HandlerRegistry()

    async def agent(step, ctx):
        for c in chunks:
            ctx["report_cost"](c)          # reserve-before-spend; may raise
            approved.append(c)             # only approved (actually-spent) chunks
        return HandlerResult(ok=True, result="done", cost_usd=sum(chunks))

    reg.register(kind, agent)
    return reg


def _charge_sink(sink):
    async def charge(actor, op, amount):
        sink.append((actor, op, amount))
    return charge


def test_within_cap_passes_and_charges_full():
    approved = []
    reg = _agent_reg([0.01, 0.02], approved)
    charges = []
    coord = Coordinator(reg, charge_logger=_charge_sink(charges))
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    plan = Plan(steps=[Step(kind="streamer", estimated_usd=0.05, max_usd=0.05)])
    report = _run(coord.run(task, plan))

    assert approved == [0.01, 0.02]                     # all chunks within cap
    assert report.steps[0].status is StepStatus.DONE
    assert abs(report.total_cost_usd - 0.03) < 1e-9
    assert charges == [("admin", "streamer", 0.03)]     # full charge


def test_max_usd_breach_blocks_and_charges_partial_step_spent():
    approved = []
    reg = _agent_reg([0.01, 0.01, 0.01, 0.01, 0.01, 0.01], approved)  # 6x $0.01
    charges = []
    coord = Coordinator(reg, charge_logger=_charge_sink(charges))
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    plan = Plan(steps=[Step(kind="streamer", estimated_usd=0.03, max_usd=0.03)])
    report = _run(coord.run(task, plan))

    assert len(approved) == 3                            # 4th chunk raised, not spent
    assert report.steps[0].status is StepStatus.BLOCKED
    assert abs(report.steps[0].cost_usd - 0.03) < 1e-9   # step_spent == cap
    assert abs(report.total_cost_usd - 0.03) < 1e-9
    assert report.status == "stopped_cost_cap"
    assert charges == [("admin", "streamer", 0.03)]      # PARTIAL charge of actual


def test_task_budget_breach_midflight_also_stops():
    approved = []
    reg = _agent_reg([0.01] * 6, approved)
    coord = Coordinator(reg)
    task = Task("t", "g", budget_usd=0.03, actor="admin")  # no per-step cap
    plan = Plan(steps=[Step(kind="streamer", estimated_usd=0.0, max_usd=0.0)])
    report = _run(coord.run(task, plan))

    assert len(approved) == 3                            # budget stops mid-flight
    assert report.steps[0].status is StepStatus.BLOCKED
    assert report.status == "stopped_cost_cap"
    assert abs(report.total_cost_usd - 0.03) < 1e-9


def test_backward_compat_step_without_max_usd_behaves_like_before():
    charges = []
    reg = HandlerRegistry()

    async def old_paid(step, ctx):                       # never calls report_cost
        return HandlerResult(ok=True, result="p", cost_usd=0.01)

    reg.register("old_paid", old_paid)
    coord = Coordinator(reg, charge_logger=_charge_sink(charges))
    task = Task("t", "g", budget_usd=1.0, actor="admin")
    report = _run(coord.run(task, Plan(steps=[Step(kind="old_paid", estimated_usd=0.01)])))

    assert report.steps[0].status is StepStatus.DONE
    assert report.status == "completed"
    assert charges == [("admin", "old_paid", 0.01)]


def test_spy_broken_cap_lets_agent_overspend_proving_teeth():
    approved = []
    reg = _agent_reg([0.01] * 6, approved)
    coord = Coordinator(reg)
    # SABOTAGE the cap: every chunk allowed regardless.
    coord._chunk_allowed = lambda task, total_cost, step_spent, amount, step: (True, "")

    task = Task("t", "g", budget_usd=1.0, actor="admin")
    plan = Plan(steps=[Step(kind="streamer", estimated_usd=0.03, max_usd=0.03)])
    report = _run(coord.run(task, plan))

    # broken cap -> all 6 chunks spent ($0.06 > $0.03 cap), step NOT blocked
    assert len(approved) == 6
    assert report.steps[0].status is StepStatus.DONE
    # therefore the real cap test (approved==3, BLOCKED) would FAIL -> it has teeth
    cap_assertion_holds = (len(approved) == 3 and report.steps[0].status is StepStatus.BLOCKED)
    assert not cap_assertion_holds


def test_step_budget_exceeded_is_importable_and_raised():
    # the exception agents can catch for cleanup is part of the public surface
    assert issubclass(StepBudgetExceeded, Exception)
