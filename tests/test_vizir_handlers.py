# -*- coding: utf-8 -*-
"""Vizir Phase 1 — pluggable StepHandler registry (mirrors SwapEngine). TDD."""
from app.services.vizir.handlers import HandlerRegistry, HandlerResult


def test_handler_result_defaults_ok_free():
    res = HandlerResult()
    assert res.ok is True
    assert res.cost_usd == 0.0
    assert res.result is None
    assert res.error is None


def test_register_and_get_handler_by_kind():
    reg = HandlerRegistry()

    async def noop(step, ctx):
        return HandlerResult(result="ok")

    reg.register("noop", noop)
    assert reg.has("noop") is True
    assert reg.get("noop") is noop


def test_unknown_kind_is_absent():
    reg = HandlerRegistry()
    assert reg.has("missing") is False
    assert reg.get("missing") is None
