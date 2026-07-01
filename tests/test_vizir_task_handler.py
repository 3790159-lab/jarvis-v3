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


from app.handlers.vizir_task_handler import VizirTaskHandler
from app.services.vizir.handlers import HandlerResult


def _mock_hermes(final_response="<html>ok</html>", stopped_reason="completed",
                 ok=True, cost=0.10, error=None):
    async def handler(step, ctx):
        rc = ctx.get("report_cost")
        if rc and cost:
            rc(cost)  # reserve-before-spend, like the real adapter
        if not ok:
            return HandlerResult(ok=False, error=error or "refused", cost_usd=0.0)
        return HandlerResult(ok=True, cost_usd=cost, result={
            "final_response": final_response, "stopped_reason": stopped_reason})
    return handler


def _handler(tmp_path, hermes, check_limit=None, record_cost=None, **cfg):
    calls = {"check_limit": [], "record_cost": []}
    def _cl(uid, *, estimated_usd):
        calls["check_limit"].append((uid, estimated_usd))
        return (check_limit or (lambda u, estimated_usd: (True, "")))(uid, estimated_usd=estimated_usd)
    def _rc(uid, username, amount):
        calls["record_cost"].append((uid, username, amount))
        if record_cost:
            record_cost(uid, username, amount)
    h = VizirTaskHandler(
        hermes_handler=hermes, check_limit=_cl, record_cost=_rc,
        artifact_dir=tmp_path, budget_usd=cfg.get("budget_usd", 0.90),
        min_attempt_usd=cfg.get("min_attempt_usd", 0.20),
        max_usd=cfg.get("max_usd", 0.40), estimated_per_attempt_usd=0.15,
        max_attempts=cfg.get("max_attempts", 3), loop_deadline_s=cfg.get("loop_deadline_s", 600.0),
        accept_fn=cfg.get("accept_fn"),
    )
    return h, calls


def test_money_wiring_check_limit_before_and_record_cost_after_success(tmp_path):
    hermes = _mock_hermes(cost=0.10)
    h, calls = _handler(tmp_path, hermes)
    _run(h.run_task_phase(chat_id=237616472, base_prompt="make a page",
                          progress_cb=lambda s, p: None, user_id=237616472, username="daniil"))
    # check_limit called with the actor (chat_id as int) BEFORE spending
    assert calls["check_limit"], "check_limit must be called before an attempt"
    assert calls["check_limit"][0][0] == 237616472
    # record_cost called AFTER the successful step with the real cost
    assert calls["record_cost"], "record_cost must be called after a successful step"
    assert calls["record_cost"][0][0] == 237616472
    assert abs(calls["record_cost"][0][2] - 0.10) < 1e-9


def test_money_wiring_refusal_not_charged(tmp_path):
    hermes = _mock_hermes(ok=False, error="content policy", cost=0.0)
    h, calls = _handler(tmp_path, hermes, max_attempts=1)
    _run(h.run_task_phase(chat_id=237616472, base_prompt="x",
                          progress_cb=lambda s, p: None, user_id=237616472, username="daniil"))
    # a refusal (ok=False) is never charged
    assert calls["record_cost"] == []


def test_money_wiring_check_limit_denial_blocks_spend(tmp_path):
    hermes = _mock_hermes(cost=0.10)
    h, calls = _handler(tmp_path, hermes,
                        check_limit=lambda u, estimated_usd: (False, "Дневной лимит исчерпан"))
    rep = _run(h.run_task_phase(chat_id=999, base_prompt="x",
                                progress_cb=lambda s, p: None, user_id=999, username="artem"))
    # denied by the limit gate -> nothing charged, escalated
    assert calls["record_cost"] == []
    assert rep.escalated is True
