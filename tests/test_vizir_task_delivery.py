# tests/test_vizir_task_delivery.py
# -*- coding: utf-8 -*-
"""Спай-зубы output-contract + доставки /task. $0 (real Coordinator + mock Hermes)."""
import asyncio
from pathlib import Path

from app.handlers.vizir_task_handler import VizirTaskHandler
from app.services.vizir.handlers import HandlerResult


def _run(coro):
    return asyncio.run(coro)


def _capturing_hermes(final_response, captured, *, stopped_reason="completed", cost=0.10):
    """Mock Hermes that records the prompt it received (to inspect the preamble)."""
    async def handler(step, ctx):
        captured["prompt"] = step.params["prompt"]
        rc = ctx.get("report_cost")
        if rc and cost:
            rc(cost)
        return HandlerResult(ok=True, cost_usd=cost, result={
            "final_response": final_response, "stopped_reason": stopped_reason})
    return handler


def _mock_text(final_response, cost=0.10):
    async def handler(step, ctx):
        rc = ctx.get("report_cost")
        if rc and cost:
            rc(cost)
        return HandlerResult(ok=True, cost_usd=cost, result={
            "final_response": final_response, "stopped_reason": "completed"})
    return handler


def _mk(tmp_path, hermes, **cfg):
    return VizirTaskHandler(
        hermes_handler=hermes, artifact_dir=tmp_path,
        budget_usd=cfg.get("budget_usd", 0.90), min_attempt_usd=0.20,
        max_usd=0.40, estimated_per_attempt_usd=0.15,
        max_attempts=cfg.get("max_attempts", 2), loop_deadline_s=600.0)


# --- Зуб 1: output-contract преамбула реально уходит в Hermes-промт ---
def test_tooth1_preamble_injected_into_hermes_prompt(tmp_path):
    cap = {}
    hermes = _capturing_hermes("<html><body>ok</body></html>", cap)
    h = _mk(tmp_path, hermes)
    _run(h.run_task_phase(chat_id=1, base_prompt="сделай веб игру крестики нолики",
                          progress_cb=lambda s, p: None, user_id=1, username="d"))
    assert "prompt" in cap
    assert "не можешь создавать файлы" in cap["prompt"].lower()
    assert "инлайн" in cap["prompt"].lower()
    assert "крестики" in cap["prompt"]


# --- Зуб 4a (goal-чистота): текст-задача "что умеешь" НЕ ложно-reject как build-task ---
def test_tooth4_text_task_accepted_not_false_build(tmp_path):
    answer = "Я — Claude Code. Умею: писать код, отвечать на вопросы, работать с файлами."
    hermes = _mock_text(answer)
    h = _mk(tmp_path, hermes)
    rep = _run(h.run_task_phase(chat_id=1, base_prompt="что ты умеешь?",
                                progress_cb=lambda s, p: None, user_id=1, username="d"))
    assert rep.escalated is False, rep.text
