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


def test_progress_maps_attempts_and_rejection_reasons(tmp_path):
    # attempt 1 truncated (retry), attempt 2 completes -> we should SEE:
    # attempt_started x2, attempt_rejected with the reason, then accepted.
    seq = [("<html>x</html>", "max_iterations"), ("<html>ok</html>", "completed")]
    it = iter(seq)
    async def hermes(step, ctx):
        rc = ctx.get("report_cost")
        if rc:
            rc(0.05)
        fr, sr = next(it)
        return HandlerResult(ok=True, cost_usd=0.05,
                             result={"final_response": fr, "stopped_reason": sr})
    stages = []
    h, _ = _handler(tmp_path, hermes)
    _run(h.run_task_phase(chat_id=237616472, base_prompt="make page",
                          progress_cb=lambda s, p: stages.append((s, p)),
                          user_id=237616472, username="daniil"))
    kinds = [s for s, _ in stages]
    assert kinds.count("attempt_started") == 2
    rej = [p for s, p in stages if s == "attempt_rejected"]
    assert len(rej) == 1
    assert any("did not complete" in r for r in rej[0]["reasons"])  # feedback shown


def test_accepted_writes_and_returns_artifact(tmp_path):
    html = "<html><body>hello jarvis</body></html>"
    hermes = _mock_hermes(final_response=html, cost=0.08)
    h, _ = _handler(tmp_path, hermes)
    rep = _run(h.run_task_phase(chat_id=237616472, base_prompt="make page",
                                progress_cb=lambda s, p: None,
                                user_id=237616472, username="daniil"))
    assert rep.escalated is False
    assert rep.document_path is not None and rep.document_path.exists()
    assert rep.document_path.read_text(encoding="utf-8") == html
    assert "Готово" in rep.text and "$" in rep.text


def test_escalation_carries_honest_refusal_reason(tmp_path):
    hermes = _mock_hermes(ok=False, error="Claude отказался: content policy", cost=0.0)
    h, _ = _handler(tmp_path, hermes, max_attempts=1)
    rep = _run(h.run_task_phase(chat_id=237616472, base_prompt="spicy thing",
                                progress_cb=lambda s, p: None,
                                user_id=237616472, username="daniil"))
    assert rep.escalated is True
    assert "content policy" in rep.text                 # FIX 1 honest reason surfaced
    assert "не завершена" in rep.text or "не завершен" in rep.text
    assert "$" in rep.text                               # spent + cap shown


def test_generic_acceptance_completed_accepts_first_try(tmp_path):
    hermes = _mock_hermes(final_response="<html>done</html>", stopped_reason="completed", cost=0.05)
    h, _ = _handler(tmp_path, hermes)
    rep = _run(h.run_task_phase(chat_id=1, base_prompt="anything",
                                progress_cb=lambda s, p: None, user_id=1, username="daniil"))
    assert rep.escalated is False and rep.document_path is not None


def test_task_acceptance_rejects_hallucination_and_feeds_back(tmp_path):
    # build-task, Hermes returns description+path WITHOUT code twice -> reject ->
    # escalation, and the "сам артефакт" feedback is injected into retry
    # (visible in attempt_rejected).
    hallu = "Готово! Игра создана по адресу C:\\Users\\Admin\\Desktop\\ttt\\index.html"
    hermes = _mock_hermes(final_response=hallu, stopped_reason="completed", cost=0.05)
    stages = []
    h, _ = _handler(tmp_path, hermes, max_attempts=2)
    rep = _run(h.run_task_phase(chat_id=1, base_prompt="сделай веб игру крестики нолики",
                                progress_cb=lambda s, p: stages.append((s, p)),
                                user_id=1, username="daniil"))
    assert rep.escalated is True
    assert rep.document_path is None                    # NO false artifact
    rej = [p for s, p in stages if s == "attempt_rejected"]
    assert rej and any("артефакт" in r for pr in rej for r in pr["reasons"])


def test_generic_acceptance_always_truncated_retries_then_escalates(tmp_path):
    hermes = _mock_hermes(final_response="<html>x</html>", stopped_reason="max_iterations", cost=0.05)
    h, _ = _handler(tmp_path, hermes, max_attempts=2)
    rep = _run(h.run_task_phase(chat_id=1, base_prompt="anything",
                                progress_cb=lambda s, p: None, user_id=1, username="daniil"))
    assert rep.escalated is True
    assert rep.stopped_reason if hasattr(rep, "stopped_reason") else True  # reply text form
    assert "did not complete" in rep.text


# =====================================================================
# Variant B money-ledger spy-teeth (CP-1 RED phase). See
# docs/specs/2026-07-02-vizir-bot-integration-design.md.
# TARGET: record to the visibility ledger ONCE, only on an ACCEPTED loop,
# amount == rep.loop_spent_usd (the sum shown in Telegram). A rejected /
# escalated loop records NOTHING.
# =====================================================================

from app.services.vizir.hermes_acceptance import AcceptanceResult


def _accept_all(v):
    return AcceptanceResult(accepted=True, reasons=[])


def test_ledger_tooth1_accepted_records_once_with_loop_total(tmp_path):
    # Mutation: never calling record_cost on rep.accepted (or recording the wrong
    # amount/uid/username) turns this red.
    # ANCHOR: a single accepted attempt records once under BOTH per-step (current)
    # and Variant-B semantics; it pins uid/username/amount, not B-vs-A.
    hermes = _mock_hermes(final_response="<html>ok</html>", stopped_reason="completed", cost=0.10)
    h, calls = _handler(tmp_path, hermes, accept_fn=_accept_all)
    _run(h.run_task_phase(chat_id=237616472, base_prompt="make a page",
                          progress_cb=lambda s, p: None, user_id=237616472, username="daniil"))
    assert len(calls["record_cost"]) == 1
    uid, username, amount = calls["record_cost"][0]
    assert uid == 237616472
    assert username == "daniil"
    assert abs(amount - 0.10) < 1e-9


def test_ledger_tooth2_refusal_records_nothing(tmp_path):
    # Mutation: recording on a non-accepted loop (or charging a refusal) turns this red.
    # ANCHOR: a refusal is unpaid, so per-step (current) also records nothing.
    hermes = _mock_hermes(ok=False, error="content policy", cost=0.0)
    h, calls = _handler(tmp_path, hermes, max_attempts=1)
    rep = _run(h.run_task_phase(chat_id=237616472, base_prompt="spicy thing",
                                progress_cb=lambda s, p: None, user_id=237616472, username="daniil"))
    assert calls["record_cost"] == []
    assert rep.escalated is True


def test_ledger_tooth3_check_limit_denial_blocks_before_spend(tmp_path):
    # Mutation: recording despite a denied gate — or removing the pre-spend
    # check_limit wiring — turns this red.
    hermes = _mock_hermes(cost=0.10)
    h, calls = _handler(tmp_path, hermes,
                        check_limit=lambda u, estimated_usd: (False, "Дневной лимит исчерпан"))
    rep = _run(h.run_task_phase(chat_id=999, base_prompt="x",
                                progress_cb=lambda s, p: None, user_id=999, username="artem"))
    # gate consulted BEFORE any spend, denied -> $0 recorded, escalated
    assert calls["check_limit"], "check_limit must be consulted before spending"
    assert calls["record_cost"] == []
    assert rep.escalated is True


def test_ledger_tooth4_multiattempt_accepted_records_sum_once(tmp_path):
    # CRITICAL B-vs-A discriminator.
    # Mutation: reverting to per-step charge_logger makes this red (would be called
    # twice — once per paid attempt — instead of once with the loop total).
    # attempt 1 PAID (0.10) but REJECTED by acceptance; attempt 2 PAID (0.06) ACCEPTED.
    # Variant B records ONCE with the sum (0.16 == rep.loop_spent_usd); per-step
    # (current) records TWICE (0.10, then 0.06).
    seq = [("<html>a</html>", "completed", 0.10), ("<html>b</html>", "completed", 0.06)]
    it = iter(seq)
    async def hermes(step, ctx):
        fr, sr, cost = next(it)
        rc = ctx.get("report_cost")
        if rc:
            rc(cost)  # reserve-before-spend
        return HandlerResult(ok=True, cost_usd=cost,
                             result={"final_response": fr, "stopped_reason": sr})
    accept_state = {"n": 0}
    def accept_fn(v):
        accept_state["n"] += 1
        if accept_state["n"] == 1:
            return AcceptanceResult(accepted=False, reasons=["retry once"])
        return AcceptanceResult(accepted=True, reasons=[])
    h, calls = _handler(tmp_path, hermes, accept_fn=accept_fn)
    _run(h.run_task_phase(chat_id=237616472, base_prompt="make a page",
                          progress_cb=lambda s, p: None, user_id=237616472, username="daniil"))
    assert len(calls["record_cost"]) == 1, calls["record_cost"]   # NOT two per-attempt charges
    uid, username, amount = calls["record_cost"][0]
    assert uid == 237616472 and username == "daniil"
    assert abs(amount - 0.16) < 1e-9                               # the SUM == loop_spent_usd


def test_ledger_tooth5_paid_but_rejected_loop_records_nothing(tmp_path):
    # Regression / additive-boundary tooth: a loop that SPENDS real money but is
    # ultimately REJECTED (escalated) records NOTHING (Variant B — record only on
    # rep.accepted). Mutation: per-step charge_logger makes this red (two paid
    # attempts -> two ledger records on an escalated loop).
    #
    # Regression note: the existing videoref/swapbatch `_cost.record_cost` callers
    # are untouched by this change — the /task ledger record is ADDITIVE and fires
    # only inside run_task_phase on an accepted loop, never wrapping/intercepting
    # other callers. Their true coverage is the videoref/swapbatch suites
    # (run: python -m pytest tests/ -q -k "videoref or swapbatch").
    seq = [("<html>a</html>", "completed", 0.10), ("<html>b</html>", "completed", 0.10)]
    it = iter(seq)
    async def hermes(step, ctx):
        fr, sr, cost = next(it)
        rc = ctx.get("report_cost")
        if rc:
            rc(cost)
        return HandlerResult(ok=True, cost_usd=cost,
                             result={"final_response": fr, "stopped_reason": sr})
    reject_state = {"n": 0}
    def accept_fn(v):
        reject_state["n"] += 1
        return AcceptanceResult(accepted=False, reasons=["reason-%d" % reject_state["n"]])
    h, calls = _handler(tmp_path, hermes, accept_fn=accept_fn, max_attempts=2)
    rep = _run(h.run_task_phase(chat_id=237616472, base_prompt="make a page",
                                progress_cb=lambda s, p: None, user_id=237616472, username="daniil"))
    assert rep.escalated is True                                   # loop rejected/escalated
    assert calls["record_cost"] == []                             # paid, but nothing recorded
