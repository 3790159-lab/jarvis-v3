# -*- coding: utf-8 -*-
"""Vizir Phase 4 — THE seam proof: CCDriver and JarvisDriver drive the SAME core
and yield IDENTICAL Reports + identical gate-wiring effects, differing ONLY in
rendering. This is what lets the live bot later swap CCDriver -> JarvisDriver
without touching the core. TDD ($0)."""
import asyncio

from app.services.vizir.models import Task, Step, Plan, StepStatus
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.drivers.cc_driver import CCDriver
from app.services.vizir.drivers.jarvis_driver import JarvisDriver


def _run(coro):
    return asyncio.run(coro)


def _scenario():
    reg = HandlerRegistry()

    async def free(step, ctx):
        return HandlerResult(result="f")

    async def paid(step, ctx):
        return HandlerResult(ok=True, result="p", cost_usd=0.01)

    reg.register("free", free)
    reg.register("paid", paid)
    plan = Plan(steps=[
        Step(kind="free"),
        Step(kind="paid", estimated_usd=0.01),
        Step(kind="paid", estimated_usd=0.01),
    ])
    return reg, plan


def _charge_sink(sink):
    async def charge(actor, op, amount):
        sink.append((actor, op, amount))
    return charge


def _drive(driver_cls, budget, charges):
    reg, plan = _scenario()
    d = driver_cls(reg, charge_logger=_charge_sink(charges))
    task = Task("t", "g", budget_usd=budget, actor="admin")
    return d, _run(d.run(task, plan))


def _assert_identical(r1, r2):
    assert r1.status == r2.status
    assert r1.total_cost_usd == r2.total_cost_usd
    assert [s.status.value for s in r1.steps] == [s.status.value for s in r2.steps]
    assert [s.result for s in r1.steps] == [s.result for s in r2.steps]


def test_both_drivers_identical_when_completed():
    cc_charges, jv_charges = [], []
    cc, r_cc = _drive(CCDriver, 0.05, cc_charges)      # budget allows both paid
    jv, r_jv = _drive(JarvisDriver, 0.05, jv_charges)

    assert r_cc.status == "completed"
    _assert_identical(r_cc, r_jv)
    assert cc_charges == jv_charges == [("admin", "paid", 0.01), ("admin", "paid", 0.01)]
    assert cc.journal != jv.messages                   # rendering differs


def test_both_drivers_identical_when_budget_blocks():
    cc_charges, jv_charges = [], []
    cc, r_cc = _drive(CCDriver, 0.015, cc_charges)     # 2nd paid step blocked
    jv, r_jv = _drive(JarvisDriver, 0.015, jv_charges)

    assert r_cc.status == "stopped_budget"
    assert [s.status.value for s in r_cc.steps] == ["done", "done", "blocked"]
    _assert_identical(r_cc, r_jv)
    assert cc_charges == jv_charges == [("admin", "paid", 0.01)]   # 2nd never charged
    assert cc.journal != jv.messages
