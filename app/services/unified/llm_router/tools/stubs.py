# -*- coding: utf-8 -*-
"""Skeleton tools for future unified-Jarvis phases.

Each builder returns a fully-formed :class:`Tool` whose handler fails
gracefully with a "not yet implemented" message. They are NOT registered by
``register_default_tools`` (we don't want Claude calling a no-op) — they exist
as the concrete shape Steps 2+ will flesh out:

- ``computer_use``    → Phase B (Computer Use)
- ``office_file``     → Phase C (Office files)
- ``live_face_swap``  → Phase D (live face swap)
"""
from __future__ import annotations

from typing import List

from app.services.unified.llm_router.tool_registry import (
    Tool,
    ToolContext,
    ToolResult,
)


def _stub(name: str, description: str, phase: str, input_schema: dict) -> Tool:
    async def handler(params: dict, context: ToolContext) -> ToolResult:
        return ToolResult.fail(
            f"Инструмент '{name}' ещё не реализован ({phase})."
        )

    return Tool(
        name=name,
        description=description,
        input_schema=input_schema,
        handler=handler,
    )


def build_stub_tools() -> List[Tool]:
    """Build the future-phase skeleton tools (not auto-registered)."""
    return [
        _stub(
            name="computer_use",
            description=(
                "[Phase B] Управление компьютером: клики, ввод, скриншоты. "
                "Пока не реализовано."
            ),
            phase="Phase B",
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "Что сделать."}
                },
            },
        ),
        _stub(
            name="office_file",
            description=(
                "[Phase C] Работа с офисными файлами (docx/xlsx/pptx): открыть, "
                "прочитать, отредактировать. Пока не реализовано."
            ),
            phase="Phase C",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Путь к файлу."},
                    "operation": {"type": "string", "description": "Операция."},
                },
            },
        ),
        _stub(
            name="live_face_swap",
            description=(
                "[Phase D] Live face swap (видеопоток в реальном времени). "
                "Пока не реализовано."
            ),
            phase="Phase D",
            input_schema={
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "Источник лица."}
                },
            },
        ),
    ]
