# -*- coding: utf-8 -*-
"""Router money-gate — Task 1: registry carries paid/est_usd per tool.

Task 2: router halts (fail-closed) before executing any PAID tool and returns
a ``pending_paid`` marker instead of spending.
"""
import asyncio

from app.services.unified.llm_router.router import LLMRouter
from app.services.unified.llm_router.tool_registry import (
    Tool,
    ToolContext,
    ToolRegistry,
    ToolResult,
)
from app.services.unified.llm_router.tools import register_default_tools


def test_paid_tools_flagged_with_cost():
    reg = register_default_tools(ToolRegistry())
    gi = reg.get("generate_image")
    assert gi is not None
    assert gi.paid is True
    assert gi.est_usd > 0

    stats = reg.get("get_user_stats")
    assert stats is not None
    assert stats.paid is False          # read-only, free
    assert stats.est_usd == 0.0


def test_free_setup_tools_not_paid():
    reg = register_default_tools(ToolRegistry())
    for free_name in ("swap_batch_start_source", "swap_batch_start_targets",
                      "swap_batch_set_quality", "cancel_current_batch"):
        t = reg.get(free_name)
        assert t is not None, free_name
        assert t.paid is False, free_name


class _Block:  # one Claude turn: a single tool_use for a paid tool
    def __init__(self, name):
        self.type = "tool_use"; self.name = name; self.id = "tu_1"; self.input = {"prompt": "брускета"}


class _Resp:
    def __init__(self, blocks):
        self.content = blocks
        self.usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})()


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp; self.calls = 0
        self.messages = type("M", (), {"create": self._create})()
    def _create(self, **kw):
        self.calls += 1
        return self._resp


def test_router_halts_on_paid_tool_without_executing():
    executed = []

    async def _handler(params, ctx):
        executed.append(params)                      # must NEVER run without confirm
        return ToolResult.photo("http://x/img.png")

    reg = ToolRegistry()
    reg.register(Tool("generate_image", "gen", {"type": "object"}, _handler,
                      paid=True, est_usd=0.04))
    recorded = []
    router = LLMRouter(
        _FakeClient(_Resp([_Block("generate_image")])), reg,
        record_cost=lambda uid, un, cost: recorded.append(cost),
    )
    ctx = ToolContext(user_id=1, username="admin", chat_id="99")
    resp = asyncio.run(router.route_message("сделай фото брускеты, не спрашивай", ctx))

    assert executed == []                            # tool did NOT spend
    assert resp.pending_paid is not None
    assert resp.pending_paid["name"] == "generate_image"
    assert resp.pending_paid["est_usd"] == 0.04
    assert resp.pending_paid["params"] == {"prompt": "брускета"}
    # LLM routing tokens may be recorded, but never the tool cost:
    assert all(c < 0.04 for c in recorded)


def test_router_does_not_gate_free_tool():
    """Other direction: a FREE tool executes normally — no false confirm.

    The gate is keyed on ``Tool.paid``, so a free tool runs in the loop and
    ``pending_paid`` stays None. (Guards against an over-broad gate that would
    halt on every tool.)"""
    executed = []

    async def _handler(params, ctx):
        executed.append(params)
        return ToolResult.ok_text("done")

    reg = ToolRegistry()
    reg.register(Tool("get_user_stats", "stats", {"type": "object"}, _handler,
                      paid=False, est_usd=0.0))
    # Turn 1: Claude calls the free tool; turn 2: plain-text answer ends the loop.
    resp1 = _Resp([_Block("get_user_stats")])
    resp2 = _Resp([type("T", (), {"type": "text", "text": "готово"})()])

    class _TwoTurnClient:
        def __init__(self):
            self._seq = [resp1, resp2]
            self.messages = type("M", (), {"create": self._create})()
        def _create(self, **kw):
            return self._seq.pop(0)

    router = LLMRouter(_TwoTurnClient(), reg)
    ctx = ToolContext(user_id=1, username="admin", chat_id="99")
    resp = asyncio.run(router.route_message("покажи мои расходы", ctx))

    assert resp.pending_paid is None                 # free tool NOT gated
    assert executed == [{"prompt": "брускета"}]       # it DID run
    assert resp.text == "готово"
