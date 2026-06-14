# -*- coding: utf-8 -*-
"""Swap-batch tools — wrap the existing ``/swapbatch_*`` flow as router tools.

Each tool drives the legacy synchronous dispatcher
(``tools.jarvis_smart_telegram_control._swapbatch_dispatch``) which already
sends its own Telegram replies. The tools therefore return a short
confirmation :class:`ToolResult`; the user-visible progress comes from the
existing flow. ``dispatch_fn`` / ``set_quality_fn`` are injected so the tools
are testable and so this package never imports the 5k-line bot module.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, List, Optional

from app.services.unified.llm_router.tool_registry import (
    Tool,
    ToolContext,
    ToolResult,
)

# dispatch_fn: (chat_id_int, command_str) -> None  (the legacy _swapbatch_dispatch)
DispatchFn = Callable[[int, str], Any]
# set_quality_fn: (chat_id_int, args_str) -> None
SetQualityFn = Callable[[int, str], Any]


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _chat_int(context: ToolContext) -> Optional[int]:
    try:
        return int(context.chat_id)
    except (TypeError, ValueError):
        return None


def _make_dispatch_tool(
    *,
    name: str,
    description: str,
    command: str,
    dispatch_fn: Optional[DispatchFn],
    confirmation: str,
) -> Tool:
    async def handler(params: dict, context: ToolContext) -> ToolResult:
        if dispatch_fn is None:
            return ToolResult.fail("Модуль face-swap не подключён.")
        chat = _chat_int(context)
        if chat is None:
            return ToolResult.fail("Не удалось определить chat_id.")
        try:
            await _maybe_await(dispatch_fn(chat, command))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"Ошибка swap-batch ({command}): {exc}")
        return ToolResult.ok_text(confirmation)

    return Tool(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": {}},
        handler=handler,
    )


def build_swap_batch_tools(
    *,
    dispatch_fn: Optional[DispatchFn] = None,
    set_quality_fn: Optional[SetQualityFn] = None,
) -> List[Tool]:
    """Build the swap-batch tool set wrapping the legacy ``/swapbatch_*`` flow."""
    tools: List[Tool] = [
        _make_dispatch_tool(
            name="swap_batch_start_source",
            description=(
                "Шаг 1 пакетного face-swap: начать сбор исходных фото — лиц, "
                "которые будут подставляться. После вызова бот ждёт, пока "
                "пользователь пришлёт фото-источники. Когда использовать: "
                "пользователь хочет начать замену лиц / новый батч swap "
                "(например «давай поменяем лица», «начнём свап», «загружу лицо»). "
                "Эквивалент команды /swapbatch_source."
            ),
            command="source",
            dispatch_fn=dispatch_fn,
            confirmation="Жду исходные фото (источник лиц).",
        ),
        _make_dispatch_tool(
            name="swap_batch_start_targets",
            description=(
                "Шаг 2 пакетного face-swap: начать приём целевых фото — кадров, "
                "в которые подставляются лица-источники. Можно загрузить сразу "
                "несколько (это пакетный режим). Когда использовать: источник уже "
                "выбран и пользователь готов прислать фото для обработки "
                "(например «вот фото, куда вставить», «целевые готовы»). "
                "Эквивалент /swapbatch_batch."
            ),
            command="batch",
            dispatch_fn=dispatch_fn,
            confirmation="Жду целевые фото для подстановки.",
        ),
        _make_dispatch_tool(
            name="swap_batch_run_swap",
            description=(
                "Шаг 3 пакетного face-swap: запустить саму обработку — подставить "
                "исходные лица во все целевые фото пакета. Занимает несколько "
                "минут; одновременно может идти только один прогон. Когда "
                "использовать: источник и целевые фото уже загружены и "
                "пользователь говорит начинать (например «запускай», «поехали», "
                "«делай свап»). Эквивалент /swapbatch_go."
            ),
            command="go",
            dispatch_fn=dispatch_fn,
            confirmation="Запускаю пакетный face-swap.",
        ),
        _make_dispatch_tool(
            name="cancel_current_batch",
            description=(
                "Отменить текущий пакет face-swap и сбросить его состояние. "
                "Когда использовать: пользователь передумал или хочет начать "
                "заново (например «отмена», «стоп», «забудь этот батч»). "
                "Эквивалент /swapbatch_cancel."
            ),
            command="cancel",
            dispatch_fn=dispatch_fn,
            confirmation="Отменяю текущий пакет.",
        ),
    ]

    # Animation tool: mode → command mapping (yes / custom / no).
    _ANIM = {
        "yes": "animate_yes",
        "custom": "animate_custom",
        "no": "animate_no",
    }

    async def animation_handler(params: dict, context: ToolContext) -> ToolResult:
        if dispatch_fn is None:
            return ToolResult.fail("Модуль face-swap не подключён.")
        mode = str(params.get("mode") or "yes").strip().lower()
        command = _ANIM.get(mode)
        if command is None:
            return ToolResult.fail(f"Неизвестный режим анимации: {mode}")
        chat = _chat_int(context)
        if chat is None:
            return ToolResult.fail("Не удалось определить chat_id.")
        try:
            await _maybe_await(dispatch_fn(chat, command))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"Ошибка анимации: {exc}")
        return ToolResult.ok_text(f"Запускаю анимацию (режим: {mode}).")

    tools.append(
        Tool(
            name="swap_batch_run_animation",
            description=(
                "Анимировать готовые результаты face-swap, превратив фото в "
                "короткое видео. Режимы (mode): 'yes' — дефолтное "
                "кинематографичное движение, 'custom' — пользователь задаёт своё "
                "описание движения, 'no' — пропустить анимацию. Длительность "
                "(секунды) и FPS (кадры в секунду) берутся из настроек качества "
                "(см. swap_batch_set_quality); анимация одного видео занимает "
                "минуты. Когда использовать: swap уже сделан и пользователь хочет "
                "оживить кадры (например «анимируй», «сделай видео из этого»). "
                "Эквивалент /swapbatch_animate_*."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["yes", "custom", "no"],
                        "description": "Режим анимации (по умолчанию yes).",
                    }
                },
            },
            handler=animation_handler,
        )
    )

    async def set_quality_handler(params: dict, context: ToolContext) -> ToolResult:
        if set_quality_fn is None:
            return ToolResult.fail("Настройка качества недоступна.")
        chat = _chat_int(context)
        if chat is None:
            return ToolResult.fail("Не удалось определить chat_id.")
        duration = params.get("duration_sec")
        fps = params.get("fps")
        args = " ".join(
            str(x) for x in (duration, fps) if x is not None
        ).strip()
        try:
            await _maybe_await(set_quality_fn(chat, args))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"Ошибка настройки качества: {exc}")
        return ToolResult.ok_text(
            f"Установил качество анимации: {args or '(по умолчанию)'}."
        )

    tools.append(
        Tool(
            name="swap_batch_set_quality",
            description=(
                "Задать качество будущей анимации: длительность видео в секундах "
                "(duration_sec) и частоту кадров FPS (fps, кадров в секунду). "
                "Влияет на плавность и время генерации — больше секунд/FPS "
                "означает дольше и дороже. Когда использовать: пользователь "
                "просит изменить длину или плавность видео перед анимацией "
                "(например «сделай 8 секунд», «хочу 30 fps»). "
                "Эквивалент /swapbatch_set_quality."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "duration_sec": {
                        "type": "number",
                        "description": "Длительность видео в секундах.",
                    },
                    "fps": {
                        "type": "integer",
                        "description": "Кадров в секунду.",
                    },
                },
            },
            handler=set_quality_handler,
        )
    )

    return tools
