# -*- coding: utf-8 -*-
"""``build_table`` tool — research-backed comparison table → XLSX, wraps /internet-table.

The backend (Tavily+Perplexity+LLM) builds the file AND sends it to Telegram itself,
so this tool reports the outcome as text. ``table_fn(query) -> dict`` is injected.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from app.services.unified.llm_router.tool_registry import Tool, ToolContext, ToolResult

TableFn = Callable[[str], Any]  # (query) -> dict; sync or async


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def build_table_tool(*, table_fn: Optional[TableFn] = None) -> Tool:
    async def handler(params: dict, context: ToolContext) -> ToolResult:
        query = (params.get("query") or "").strip()
        if not query:
            return ToolResult.fail("Пустой запрос для таблицы.")
        if table_fn is None:
            return ToolResult.fail("Бэкенд таблиц не подключён.")
        try:
            data = await _maybe_await(table_fn(query))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"Таблица не построена: {exc}")
        if not isinstance(data, dict):
            return ToolResult.fail("Бэкенд таблиц вернул неожиданный ответ.")
        if data.get("_error"):
            return ToolResult.fail(str(data["_error"]))
        rows = data.get("rows_count")
        path = data.get("table_path") or "?"
        sent = (data.get("telegram_send") or {}).get("ok")
        text = f"Таблица готова. Строк: {rows}. Файл: {path}."
        text += " Отправлен в Telegram." if sent else " (файл создан; отправка могла не пройти)."
        return ToolResult.ok_text(text)

    return Tool(
        name="build_table",
        description=(
            "Построить сравнительную таблицу по теме из интернета (поиск Tavily + "
            "анализ Perplexity, результат — файл Excel/XLSX, который отправляется "
            "пользователю в Telegram). Когда использовать: пользователь просит "
            "таблицу, сравнение в виде таблицы, рейтинг/топ в табличном виде "
            "(например «сделай таблицу топ AI сервисов», «таблицу сравнения X и Y»). "
            "Эквивалент /table."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Тема таблицы на естественном языке.",
                }
            },
            "required": ["query"],
        },
        handler=handler,
    )
