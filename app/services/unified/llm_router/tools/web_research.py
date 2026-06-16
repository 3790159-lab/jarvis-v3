# -*- coding: utf-8 -*-
"""``web_research`` tool — internet research/analysis (Perplexity), wraps /research.

The backend is injected (``research_fn(query) -> dict`` with an ``answer`` key, or
``_error`` on failure; sync or async). The bot wires it to ``backend_post`` against
``/api/jarvis/tools/internet/research``; unit tests inject a fake.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from app.services.unified.llm_router.tool_registry import Tool, ToolContext, ToolResult

ResearchFn = Callable[[str], Any]  # (query) -> dict; sync or async


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def build_web_research_tool(*, research_fn: Optional[ResearchFn] = None) -> Tool:
    async def handler(params: dict, context: ToolContext) -> ToolResult:
        query = (params.get("query") or "").strip()
        if not query:
            return ToolResult.fail("Пустой запрос для ресёрча.")
        if research_fn is None:
            return ToolResult.fail("Бэкенд ресёрча не подключён.")
        try:
            data = await _maybe_await(research_fn(query))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"Ресёрч не выполнен: {exc}")
        if not isinstance(data, dict):
            return ToolResult.fail("Бэкенд ресёрча вернул неожиданный ответ.")
        if data.get("_error"):
            return ToolResult.fail(str(data["_error"]))
        answer = (data.get("answer") or "").strip()
        return ToolResult.ok_text(answer or "Пустой результат ресёрча.")

    return Tool(
        name="web_research",
        description=(
            "Найти и проанализировать актуальную информацию в интернете "
            "(поиск + анализ через Perplexity) и вернуть текстовый ответ. Когда "
            "использовать: пользователь просит изучить вопрос, найти информацию, "
            "сделать ресёрч, сравнить варианты, узнать что-то актуальное "
            "(например «сделай ресёрч по X», «найди инфу про Y», «сравни A и B»). "
            "Эквивалент /research."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Поисковый запрос/вопрос на естественном языке.",
                }
            },
            "required": ["query"],
        },
        handler=handler,
    )
