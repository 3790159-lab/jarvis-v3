# tests/test_vizir_task_handler.py
# -*- coding: utf-8 -*-
"""Vizir /task handler — autonomous loop over the bot. $0 (real Coordinator + mock Hermes)."""
import asyncio
from pathlib import Path

from app.handlers.vizir_task_handler import VizirTaskReply, accept_generic


def _run(coro):
    return asyncio.run(coro)


def test_generic_acceptance_completed_nonempty_accepted():
    acc = accept_generic({"final_response": "anything non-empty", "stopped_reason": "completed"})
    assert acc.accepted is True
    assert acc.reasons == []


def test_generic_acceptance_not_completed_rejected_with_reason():
    acc = accept_generic({"final_response": "x", "stopped_reason": "max_iterations"})
    assert acc.accepted is False
    assert any("did not complete" in r for r in acc.reasons)


def test_generic_acceptance_empty_output_rejected():
    acc = accept_generic({"final_response": "", "stopped_reason": "completed"})
    assert acc.accepted is False
    assert any("empty output" in r for r in acc.reasons)


def test_vizirtaskreply_fields():
    r = VizirTaskReply(text="ok", document_path=Path("a.html"), escalated=False)
    assert r.text == "ok" and r.escalated is False and r.document_path == Path("a.html")
