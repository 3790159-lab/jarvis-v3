# -*- coding: utf-8 -*-
"""Unit tests for the Phase-4 unified LLM router (Claude tool_use orchestrator).

The router lives at ``app/services/unified/llm_router``. These tests drive a
*fake* Anthropic client (real network calls are never made) so the agentic
tool-use loop, cost accounting, and graceful-failure behaviour are exercised
deterministically. The only thing mocked is the external API boundary — tool
handlers, the registry, and the router itself are the real code under test.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.router import LLMRouter, RouterResponse
from app.services.unified.llm_router.tool_registry import (
    Tool,
    ToolContext,
    ToolRegistry,
    ToolResult,
)


# ── fakes mimicking the anthropic SDK Message surface ────────────────────────


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeText:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeToolUse:
    type = "tool_use"

    def __init__(self, id: str, name: str, input: dict) -> None:
        self.id = id
        self.name = name
        self.input = input


class _FakeMessage:
    def __init__(self, content, stop_reason, usage: _FakeUsage) -> None:
        self.content = content
        self.stop_reason = stop_reason
        self.usage = usage


class _FakeMessages:
    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeClient:
    """Stand-in for ``anthropic.Anthropic`` — yields a scripted response queue."""

    def __init__(self, responses) -> None:
        self.messages = _FakeMessages(responses)


def _ctx() -> ToolContext:
    return ToolContext(user_id=222, username="vasya", chat_id="222", conversation_state={})


def _text_msg(text: str, in_tok: int = 10, out_tok: int = 5) -> _FakeMessage:
    return _FakeMessage([_FakeText(text)], "end_turn", _FakeUsage(in_tok, out_tok))


def _tool_msg(tool_id: str, name: str, inp: dict, in_tok: int = 10, out_tok: int = 7) -> _FakeMessage:
    return _FakeMessage(
        [_FakeToolUse(tool_id, name, inp)], "tool_use", _FakeUsage(in_tok, out_tok)
    )


def _echo_tool(recorder: list) -> Tool:
    async def handler(params: dict, context: ToolContext) -> ToolResult:
        recorder.append((params, context))
        return ToolResult.ok_text(f"echoed:{params.get('value', '')}")

    return Tool(
        name="echo",
        description="Эхо-инструмент: возвращает переданное значение.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        handler=handler,
    )


# ── ToolRegistry ─────────────────────────────────────────────────────────────


def test_tool_registry_register_and_lookup():
    reg = ToolRegistry()
    tool = _echo_tool([])
    reg.register(tool)

    assert reg.get("echo") is tool
    assert reg.get("nonexistent") is None
    assert "echo" in reg.names()

    anthropic_tools = reg.to_anthropic_tools()
    assert anthropic_tools == [
        {
            "name": "echo",
            "description": "Эхо-инструмент: возвращает переданное значение.",
            "input_schema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        }
    ]


# ── route_message ──────────────────────────────────────────────────────────


def test_route_message_with_single_tool_call():
    calls: list = []
    reg = ToolRegistry()
    reg.register(_echo_tool(calls))
    client = _FakeClient([
        _tool_msg("toolu_1", "echo", {"value": "hi"}),
        _text_msg("Готово: hi"),
    ])
    router = LLMRouter(client, reg, model="claude-sonnet-4-6")

    resp = asyncio.run(router.route_message("повтори hi", _ctx()))

    assert isinstance(resp, RouterResponse)
    assert resp.text == "Готово: hi"
    assert resp.tools_used == ["echo"]
    assert calls and calls[0][0] == {"value": "hi"}
    # Both API turns happened (tool_use → result → final text).
    assert len(client.messages.calls) == 2
    # Tools were offered to the model on the first turn.
    assert client.messages.calls[0]["tools"][0]["name"] == "echo"


def test_route_message_with_multi_step_chain():
    order: list = []

    def make(name: str) -> Tool:
        async def handler(params, context):
            order.append(name)
            return ToolResult.ok_text(f"{name}-ok")
        return Tool(name=name, description=f"шаг {name}", input_schema={"type": "object", "properties": {}}, handler=handler)

    reg = ToolRegistry()
    reg.register(make("step_a"))
    reg.register(make("step_b"))
    client = _FakeClient([
        _tool_msg("t1", "step_a", {}),
        _tool_msg("t2", "step_b", {}),
        _text_msg("Цепочка завершена"),
    ])
    router = LLMRouter(client, reg, model="claude-sonnet-4-6")

    resp = asyncio.run(router.route_message("сделай a потом b", _ctx()))

    assert order == ["step_a", "step_b"]
    assert resp.tools_used == ["step_a", "step_b"]
    assert resp.text == "Цепочка завершена"
    assert len(client.messages.calls) == 3


def test_route_message_plain_response_no_tools():
    reg = ToolRegistry()
    reg.register(_echo_tool([]))
    client = _FakeClient([_text_msg("Привет! Чем помочь?")])
    router = LLMRouter(client, reg, model="claude-sonnet-4-6")

    resp = asyncio.run(router.route_message("привет", _ctx()))

    assert resp.text == "Привет! Чем помочь?"
    assert resp.tools_used == []
    assert resp.media == []
    assert len(client.messages.calls) == 1


def test_route_message_handles_tool_failure_gracefully():
    async def boom(params, context):
        raise RuntimeError("tool exploded")

    reg = ToolRegistry()
    reg.register(Tool(name="boom", description="падает", input_schema={"type": "object", "properties": {}}, handler=boom))
    client = _FakeClient([
        _tool_msg("t1", "boom", {}),
        _text_msg("Извините, инструмент не сработал."),
    ])
    router = LLMRouter(client, reg, model="claude-sonnet-4-6")

    # Must not raise — the failure is fed back to the model as an error result.
    resp = asyncio.run(router.route_message("запусти boom", _ctx()))

    assert resp.tools_used == ["boom"]
    assert resp.text == "Извините, инструмент не сработал."
    # The second turn carried an is_error tool_result back to the model.
    second_turn_msgs = client.messages.calls[1]["messages"]
    tool_result = second_turn_msgs[-1]["content"][0]
    assert tool_result["is_error"] is True


def test_route_message_respects_disabled_flag():
    reg = ToolRegistry()
    reg.register(_echo_tool([]))
    client = _FakeClient([_text_msg("should not be returned")])
    router = LLMRouter(client, reg, model="claude-sonnet-4-6", enabled=False)

    resp = asyncio.run(router.route_message("привет", _ctx()))

    assert resp.error == "router_disabled"
    assert resp.tools_used == []
    # The API was never called when the router is disabled.
    assert client.messages.calls == []


def test_router_records_cost_per_call():
    recorded: list = []

    def spy_record_cost(user_id, username, amount_usd):
        recorded.append((user_id, username, amount_usd))

    reg = ToolRegistry()
    client = _FakeClient([_text_msg("ответ", in_tok=1000, out_tok=500)])
    router = LLMRouter(
        client, reg, model="claude-sonnet-4-6", record_cost=spy_record_cost
    )

    resp = asyncio.run(router.route_message("вопрос", _ctx()))

    assert len(recorded) == 1
    uid, uname, amount = recorded[0]
    assert uid == 222 and uname == "vasya"
    # sonnet-4-6 = $3/1M in + $15/1M out → 1000*3e-6 + 500*15e-6 = 0.0105
    assert amount == pytest.approx(0.0105, rel=1e-6)
    assert resp.cost_usd == pytest.approx(0.0105, rel=1e-6)
    assert resp.input_tokens == 1000 and resp.output_tokens == 500
