# -*- coding: utf-8 -*-
"""Unit tests for the ``get_user_stats`` router tool (wraps ``/my_stats``).

The stats backend is injected so these tests never touch the real cost ledger.
We verify the Anthropic schema, that the caller's identity is forwarded to the
backend, and that a failing/empty backend degrades gracefully instead of
raising into the router loop.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.tool_registry import ToolContext
from app.services.unified.llm_router.tools.cost_stats import build_cost_stats_tool


def _ctx() -> ToolContext:
    return ToolContext(user_id=222, username="vasya", chat_id="222")


def test_cost_stats_tool_schema_valid():
    tool = build_cost_stats_tool(stats_fn=lambda uid, uname: "x")
    assert tool.name == "get_user_stats"
    assert tool.description
    block = tool.to_anthropic()
    assert block["name"] == "get_user_stats"
    assert block["input_schema"]["type"] == "object"
    assert block["input_schema"]["properties"] == {}


def test_cost_stats_tool_forwards_identity_and_returns_text():
    seen: list = []

    def fake_stats(user_id, username):
        seen.append((user_id, username))
        return "Сегодня: $0.10\nМесяц: $1.20"

    tool = build_cost_stats_tool(stats_fn=fake_stats)
    result = asyncio.run(tool.handler({}, _ctx()))

    assert seen == [(222, "vasya")]
    assert not result.is_error
    assert result.text == "Сегодня: $0.10\nМесяц: $1.20"


def test_cost_stats_tool_handles_backend_failure():
    def boom(user_id, username):
        raise RuntimeError("ledger unreadable")

    tool = build_cost_stats_tool(stats_fn=boom)
    result = asyncio.run(tool.handler({}, _ctx()))
    assert result.is_error
    assert "статистику" in result.error.lower()


def test_cost_stats_tool_handles_empty_result():
    tool = build_cost_stats_tool(stats_fn=lambda uid, uname: "")
    result = asyncio.run(tool.handler({}, _ctx()))
    assert not result.is_error
    assert result.text == "Статистика недоступна."


def test_cost_stats_tool_awaits_async_backend():
    async def async_stats(user_id, username):
        return f"stats for {username}"

    tool = build_cost_stats_tool(stats_fn=async_stats)
    result = asyncio.run(tool.handler({}, _ctx()))
    assert not result.is_error
    assert result.text == "stats for vasya"
