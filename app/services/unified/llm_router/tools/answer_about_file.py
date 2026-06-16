# -*- coding: utf-8 -*-
"""``answer_about_file`` tool — Q&A over the user's last uploaded file (PDF/DOCX/XLSX).

The bot keeps the most recently uploaded file in per-chat state; ``file_qa_fn(chat_id,
question) -> dict`` (with ``answer`` / ``_error`` / ``no_file``) is injected and reuses
the existing file-analysis path.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from app.services.unified.llm_router.tool_registry import Tool, ToolContext, ToolResult

FileQaFn = Callable[[str, str], Any]  # (chat_id, question) -> dict; sync or async


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def build_answer_about_file_tool(*, file_qa_fn: Optional[FileQaFn] = None) -> Tool:
    async def handler(params: dict, context: ToolContext) -> ToolResult:
        question = (params.get("question") or "").strip()
        if not question:
            return ToolResult.fail("Пустой вопрос по файлу.")
        if file_qa_fn is None:
            return ToolResult.fail("Бэкенд анализа файлов не подключён.")
        try:
            data = await _maybe_await(file_qa_fn(context.chat_id, question))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"Не удалось обработать файл: {exc}")
        if not isinstance(data, dict):
            return ToolResult.fail("Бэкенд анализа файлов вернул неожиданный ответ.")
        if data.get("no_file"):
            return ToolResult.ok_text(
                "Сначала пришли файл (PDF/DOCX/XLSX), потом задай по нему вопрос."
            )
        if data.get("_error"):
            return ToolResult.fail(str(data["_error"]))
        answer = (data.get("answer") or "").strip()
        return ToolResult.ok_text(answer or "По файлу ничего не нашлось.")

    return Tool(
        name="answer_about_file",
        description=(
            "Ответить на вопрос по последнему загруженному пользователем файлу "
            "(PDF/DOCX/XLSX): суммировать, извлечь данные (суммы, даты, "
            "контрагентов), ответить на вопрос по содержимому. Когда использовать: "
            "пользователь спрашивает про присланный файл (например «что в файле», "
            "«суммируй документ», «какая сумма в договоре»). Если файла нет, "
            "инструмент попросит сначала прислать файл."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Вопрос/инструкция по содержимому файла.",
                }
            },
            "required": ["question"],
        },
        handler=handler,
    )
