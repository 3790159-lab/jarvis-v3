# -*- coding: utf-8 -*-
"""Vizir Phase 2 — a REAL free 3-step coordination task driven end-to-end by the
CC-driver: read_log -> analyze_levels -> summarize. $0, no paid calls. TDD."""
import asyncio

from app.services.vizir.models import Task
from app.services.vizir.drivers.cc_driver import CCDriver
from app.services.vizir.demo.log_triage import build_log_triage


def _run(coro):
    return asyncio.run(coro)


def test_log_triage_three_step_free_task_runs_e2e(tmp_path):
    log = tmp_path / "bot.log"
    log.write_text(
        "2026-06-24 18:09:18 | INFO     | root      | started\n"
        "2026-06-24 18:09:19 | WARNING  | __main__  | slow\n"
        "2026-06-24 18:09:20 | ERROR    | engine    | boom\n"
        "2026-06-24 18:09:21 | INFO     | root      | ok\n",
        encoding="utf-8",
    )
    registry, plan = build_log_triage(str(log))
    driver = CCDriver(registry)
    task = Task(task_id="triage", goal="triage bot log", budget_usd=0.0)

    report = _run(driver.run(task, plan))

    assert report.status == "completed"
    assert report.total_cost_usd == 0.0               # Phase 2 is free
    assert [s.status.value for s in report.steps] == ["done", "done", "done"]

    summary = report.steps[-1].result
    assert "lines=4" in summary
    assert "INFO=2" in summary and "WARNING=1" in summary and "ERROR=1" in summary

    text = "\n".join(driver.journal)
    for kind in ("read_log", "analyze_levels", "summarize"):
        assert f"step: {kind}" in text
    assert "completed" in text
