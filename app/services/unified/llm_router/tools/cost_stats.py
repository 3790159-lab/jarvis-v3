# -*- coding: utf-8 -*-
"""``get_user_stats`` tool — wrap ``/my_stats`` as a router tool.

Returns the caller's own spend table (today / month / all-time). The stats
backend is injected; it defaults to the Day-8 ``cost_tracker`` formatter so the
text matches exactly what ``/my_stats`` produces.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from app.services.unified.llm_router.tool_registry import (
    Tool,
    ToolContext,
    ToolResult,
)

# stats_fn: (user_id, username) -> str  (the rendered stats message); sync or async.
StatsFn = Callable[[Optional[int], Optional[str]], Any]


def _default_stats(user_id: Optional[int], username: Optional[str]) -> str:
    from app.services.audit import cost_tracker

    return cost_tracker.format_my_stats_message(user_id, username)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def build_cost_stats_tool(*, stats_fn: Optional[StatsFn] = None) -> Tool:
    """Build the ``get_user_stats`` :class:`Tool`."""
    fn = stats_fn or _default_stats

    async def handler(params: dict, context: ToolContext) -> ToolResult:
        try:
            text = await _maybe_await(fn(context.user_id, context.username))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"Не удалось получить статистику: {exc}")
        return ToolResult.ok_text(text or "Статистика недоступна.")

    return Tool(
        name="get_user_stats",
        description=(
            "Показать статистику расходов пользователя (сегодня / месяц / всего). "
            "Используй, когда пользователь спрашивает про свои траты или статистику. "
            "Эквивалент /my_stats."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )
