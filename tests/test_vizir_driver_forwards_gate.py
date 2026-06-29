# -*- coding: utf-8 -*-
"""Vizir Phase 3 — the thin driver forwards gate wiring (charge_logger,
check_limit_fn) to its Coordinator, so a real (and later Jarvis) driver run is
fully gated. Driver stays thin: it forwards, holds no logic. TDD ($0)."""
import asyncio

from app.services.vizir.models import Task, Step, Plan, StepStatus
from app.services.vizir.handlers import HandlerRegistry, HandlerResult
from app.services.vizir.drivers.cc_driver import CCDriver


def _run(coro):
    return asyncio.run(coro)


def test_driver_forwards_check_limit_and_charge_logger():
    charges = []

    async def charge(actor, op, amount):
        charges.append((actor, op, amount))

    def deny_friend(actor, estimated):
        return (actor != "friend", "" if actor != "friend" else "limit reached")

    reg = HandlerRegistry()

    async def paid(step, ctx):
        return HandlerResult(ok=True, result="p", cost_usd=0.01)

    reg.register("paid", paid)

    # friend -> denied by forwarded check_limit_fn
    d1 = CCDriver(reg, charge_logger=charge, check_limit_fn=deny_friend)
    r1 = _run(d1.run(Task("t1", "g", budget_usd=1.0, actor="friend"),
                     Plan(steps=[Step(kind="paid", estimated_usd=0.01)])))
    assert r1.steps[0].status is StepStatus.BLOCKED
    assert charges == []                       # blocked -> not charged

    # admin -> allowed, charge forwarded
    d2 = CCDriver(reg, charge_logger=charge, check_limit_fn=deny_friend)
    r2 = _run(d2.run(Task("t2", "g", budget_usd=1.0, actor="admin"),
                     Plan(steps=[Step(kind="paid", estimated_usd=0.01)])))
    assert r2.status == "completed"
    assert charges == [("admin", "paid", 0.01)]
