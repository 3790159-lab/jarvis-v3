# -*- coding: utf-8 -*-
"""Unit tests for the web_research router tool (wraps /api/jarvis/tools/internet/research)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.tool_registry import ToolContext
from app.services.unified.llm_router.tools.web_research import build_web_research_tool


def _ctx() -> ToolContext:
    return ToolContext(user_id=1, username="u", chat_id="1")


def test_schema_requires_query():
    tool = build_web_research_tool(research_fn=lambda q: {"answer": "x"})
    assert tool.name == "web_research"
    block = tool.to_anthropic()
    assert block["input_schema"]["type"] == "object"
    assert "query" in block["input_schema"]["properties"]
    assert block["input_schema"]["required"] == ["query"]


def test_forwards_query_and_returns_answer():
    seen = []

    def fake(q):
        seen.append(q)
        return {"answer": "FAL дешевле на батчах."}

    tool = build_web_research_tool(research_fn=fake)
    result = asyncio.run(tool.handler({"query": "сравни FAL и Replicate"}, _ctx()))
    assert seen == ["сравни FAL и Replicate"]
    assert not result.is_error
    assert result.text == "FAL дешевле на батчах."


def test_backend_error_becomes_tool_failure():
    tool = build_web_research_tool(research_fn=lambda q: {"_error": "PERPLEXITY_API_KEY is empty"})
    result = asyncio.run(tool.handler({"query": "x"}, _ctx()))
    assert result.is_error
    assert "PERPLEXITY" in result.error


def test_empty_query_fails_without_calling_backend():
    called = []
    tool = build_web_research_tool(research_fn=lambda q: called.append(q) or {"answer": "y"})
    result = asyncio.run(tool.handler({"query": "  "}, _ctx()))
    assert result.is_error
    assert called == []


def test_unwired_backend_degrades_gracefully():
    tool = build_web_research_tool(research_fn=None)
    result = asyncio.run(tool.handler({"query": "x"}, _ctx()))
    assert result.is_error


def test_exception_is_caught():
    def boom(q):
        raise RuntimeError("network down")

    tool = build_web_research_tool(research_fn=boom)
    result = asyncio.run(tool.handler({"query": "x"}, _ctx()))
    assert result.is_error
    assert "network down" in result.error


def test_async_backend_awaited():
    async def fake(q):
        return {"answer": "async ok"}

    tool = build_web_research_tool(research_fn=fake)
    result = asyncio.run(tool.handler({"query": "x"}, _ctx()))
    assert result.text == "async ok"
