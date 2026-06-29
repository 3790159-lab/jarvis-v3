# -*- coding: utf-8 -*-
"""Vizir Phase 2 — invariant: the core is driver-agnostic. The SAME core run
yields an identical Report whether driven by CCDriver or called directly with a
no-op event sink. Proves the driver only renders; it holds no logic. This is the
seam a JarvisDriver will later occupy unchanged. TDD (invariant)."""
import asyncio

from app.services.vizir.models import Task
from app.services.vizir.coordinator import Coordinator
from app.services.vizir.drivers.cc_driver import CCDriver
from app.services.vizir.demo.log_triage import build_log_triage


def _run(coro):
    return asyncio.run(coro)


def _fixture(tmp_path):
    log = tmp_path / "bot.log"
    log.write_text(
        "2026-06-24 18:09:18 | INFO     | root     | a\n"
        "2026-06-24 18:09:19 | ERROR    | engine   | b\n"
        "2026-06-24 18:09:20 | INFO     | root     | c\n",
        encoding="utf-8",
    )
    return str(log)


def test_same_report_with_driver_and_without(tmp_path):
    path = _fixture(tmp_path)

    reg_a, plan_a = build_log_triage(path)
    report_core = _run(Coordinator(reg_a).run(Task("t", "g"), plan_a))

    reg_b, plan_b = build_log_triage(path)
    report_drv = _run(CCDriver(reg_b).run(Task("t", "g"), plan_b))

    assert report_core.status == report_drv.status == "completed"
    assert report_core.total_cost_usd == report_drv.total_cost_usd == 0.0
    assert ([s.status.value for s in report_core.steps]
            == [s.status.value for s in report_drv.steps])
    assert report_core.steps[-1].result == report_drv.steps[-1].result
