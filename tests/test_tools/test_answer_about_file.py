# -*- coding: utf-8 -*-
"""Unit tests for the answer_about_file router tool (Q&A over the last uploaded file)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.llm_router.tool_registry import ToolContext
from app.services.unified.llm_router.tools.answer_about_file import build_answer_about_file_tool


def _ctx() -> ToolContext:
    return ToolContext(user_id=1, username="u", chat_id="777")


def test_schema_requires_question():
    tool = build_answer_about_file_tool(file_qa_fn=lambda c, q: {"answer": "x"})
    assert tool.name == "answer_about_file"
    block = tool.to_anthropic()
    assert block["input_schema"]["required"] == ["question"]


def test_forwards_chat_and_question_and_returns_answer():
    seen = []

    def fake(chat_id, question):
        seen.append((chat_id, question))
        return {"answer": "В договоре сумма 50000 руб."}

    tool = build_answer_about_file_tool(file_qa_fn=fake)
    result = asyncio.run(tool.handler({"question": "какая сумма?"}, _ctx()))
    assert seen == [("777", "какая сумма?")]
    assert not result.is_error
    assert "50000" in result.text


def test_no_file_is_friendly_text_not_error():
    tool = build_answer_about_file_tool(file_qa_fn=lambda c, q: {"no_file": True})
    result = asyncio.run(tool.handler({"question": "что тут?"}, _ctx()))
    assert not result.is_error
    assert "файл" in result.text.lower()


def test_backend_error_becomes_failure():
    tool = build_answer_about_file_tool(file_qa_fn=lambda c, q: {"_error": "parse failed"})
    result = asyncio.run(tool.handler({"question": "x"}, _ctx()))
    assert result.is_error
    assert "parse failed" in result.error


def test_empty_question_fails_without_backend():
    called = []
    tool = build_answer_about_file_tool(file_qa_fn=lambda c, q: called.append(q) or {"answer": "y"})
    result = asyncio.run(tool.handler({"question": ""}, _ctx()))
    assert result.is_error
    assert called == []


def test_unwired_backend_degrades():
    tool = build_answer_about_file_tool(file_qa_fn=None)
    result = asyncio.run(tool.handler({"question": "x"}, _ctx()))
    assert result.is_error


def test_exception_is_caught():
    def boom(c, q):
        raise RuntimeError("reader crash")

    tool = build_answer_about_file_tool(file_qa_fn=boom)
    result = asyncio.run(tool.handler({"question": "x"}, _ctx()))
    assert result.is_error
    assert "reader crash" in result.error
