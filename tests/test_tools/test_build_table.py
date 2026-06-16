# -*- coding: utf-8 -*-
"""Unit tests for the build_table router tool (wraps /telegram-tools/internet-table)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.tool_registry import ToolContext
from app.services.unified.llm_router.tools.build_table import build_table_tool


def _ctx() -> ToolContext:
    return ToolContext(user_id=1, username="u", chat_id="1")


def test_schema_requires_query():
    tool = build_table_tool(table_fn=lambda q: {"rows_count": 1, "table_path": "t.xlsx"})
    assert tool.name == "build_table"
    block = tool.to_anthropic()
    assert block["input_schema"]["required"] == ["query"]


def test_reports_rows_and_telegram_delivery():
    seen = []

    def fake(q):
        seen.append(q)
        return {"rows_count": 10, "table_path": "C:/x/top_ai.xlsx", "telegram_send": {"ok": True}}

    tool = build_table_tool(table_fn=fake)
    result = asyncio.run(tool.handler({"query": "топ AI сервисов"}, _ctx()))
    assert seen == ["топ AI сервисов"]
    assert not result.is_error
    assert "10" in result.text
    assert "Telegram" in result.text


def test_backend_error_becomes_failure():
    tool = build_table_tool(table_fn=lambda q: {"_error": "Tavily limit"})
    result = asyncio.run(tool.handler({"query": "x"}, _ctx()))
    assert result.is_error
    assert "Tavily" in result.error


def test_empty_query_fails_without_calling_backend():
    called = []
    tool = build_table_tool(table_fn=lambda q: called.append(q) or {})
    result = asyncio.run(tool.handler({"query": ""}, _ctx()))
    assert result.is_error
    assert called == []


def test_unwired_backend_degrades():
    tool = build_table_tool(table_fn=None)
    result = asyncio.run(tool.handler({"query": "x"}, _ctx()))
    assert result.is_error


def test_exception_is_caught():
    def boom(q):
        raise RuntimeError("backend down")

    tool = build_table_tool(table_fn=boom)
    result = asyncio.run(tool.handler({"query": "x"}, _ctx()))
    assert result.is_error
    assert "backend down" in result.error
