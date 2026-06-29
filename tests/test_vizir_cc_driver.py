# -*- coding: utf-8 -*-
"""Vizir Phase 2 — thin CC-driver renders core events as journal lines.
The driver only formats/wires; all logic stays in the core. ASCII tokens keep
it Windows-console safe. TDD."""
from app.services.vizir.drivers.cc_driver import CCDriver
from app.services.vizir.handlers import HandlerRegistry


def test_driver_renders_each_event_type_as_a_readable_line():
    d = CCDriver(HandlerRegistry())
    d.render({"type": "step_started", "kind": "alpha"})
    d.render({"type": "gate_checked", "kind": "alpha", "spent": 0.0,
              "estimated": 0.01, "budget": 0.05})
    d.render({"type": "charged", "kind": "alpha", "cost_usd": 0.01})
    d.render({"type": "step_done", "kind": "alpha", "cost_usd": 0.01})
    d.render({"type": "run_completed", "total_cost_usd": 0.01})

    text = "\n".join(d.journal)
    assert "step: alpha" in text
    assert "gate: alpha" in text
    assert "charged: alpha" in text
    assert "done: alpha" in text
    assert "completed" in text


def test_driver_renders_block_and_approval_and_stop():
    d = CCDriver(HandlerRegistry())
    d.render({"type": "step_blocked", "kind": "b", "reason": "budget would be exceeded"})
    d.render({"type": "step_needs_approval", "kind": "c"})
    d.render({"type": "run_stopped", "status": "stopped_budget"})

    text = "\n".join(d.journal)
    assert "blocked: b" in text and "budget would be exceeded" in text
    assert "needs-approval: c" in text
    assert "stopped: stopped_budget" in text
