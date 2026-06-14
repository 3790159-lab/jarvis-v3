# -*- coding: utf-8 -*-
"""``video_face_swap`` tool — start the "swap a face into a whole video" flow.

Like the swap-batch tools, this is a thin wrapper over a legacy bot dispatcher
(injected as ``dispatch_fn``) so this package never imports the 5k-line bot
module. Calling it asks the bot to enter the video-face-swap intake state; the
actual video + face photo arrive as Telegram media and are handled there. The
heavy lifting (RunPod ComfyUI ReActor over video) lives in
``block_m2_face_swap.video_face_swap_engine``.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from app.services.unified.llm_router.tool_registry import (
    Tool,
    ToolContext,
    ToolResult,
)

# dispatch_fn: (chat_id_int) -> None  (the legacy bot entry point)
DispatchFn = Callable[[int], Any]


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def build_video_face_swap_tool(*, dispatch_fn: Optional[DispatchFn] = None) -> Tool:
    """Build the ``video_face_swap`` :class:`Tool`."""

    async def handler(params: dict, context: ToolContext) -> ToolResult:
        if dispatch_fn is None:
            return ToolResult.fail("Замена лица в видео не подключена.")
        try:
            chat = int(context.chat_id)
        except (TypeError, ValueError):
            return ToolResult.fail("Не удалось определить chat_id.")
        try:
            await _maybe_await(dispatch_fn(chat))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"Ошибка запуска видео-свапа: {exc}")
        return ToolResult.ok_text(
            "Готов заменить лицо в видео. Пришли видео и фото с лицом."
        )

    return Tool(
        name="video_face_swap",
        description=(
            "Заменить лицо в готовом видео: пользователь присылает видеоролик "
            "и фото с лицом, бот подставляет это лицо во все кадры и отдаёт "
            "новое видео (со звуком). Когда использовать: просьбы вроде "
            "«поменяй лицо в этом видео», «сделай ролик с моим лицом», "
            "«замени лицо в видеоклипе». Это НЕ анимация фото — нужен именно "
            "готовый видеофайл-источник. Лимиты: до 60 секунд и до 1080p "
            "(длиннее/больше — отклоняется или ужимается для экономии). После "
            "вызова бот попросит прислать видео и фото-лицо."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )
