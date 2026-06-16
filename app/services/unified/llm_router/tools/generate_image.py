# -*- coding: utf-8 -*-
"""``generate_image`` tool — text-to-image (Replicate FLUX), wraps /image/generate.

Returns the first generated image as a photo ToolResult (one-shot NL). The
backend ``image_fn(prompt, num_images) -> dict`` (with ``urls`` or ``_error``)
is injected; the bot wires it to ``backend_post``.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from app.services.unified.llm_router.tool_registry import Tool, ToolContext, ToolResult

ImageFn = Callable[[str, int], Any]  # (prompt, num_images) -> dict; sync or async


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def build_generate_image_tool(*, image_fn: Optional[ImageFn] = None) -> Tool:
    async def handler(params: dict, context: ToolContext) -> ToolResult:
        prompt = (params.get("prompt") or "").strip()
        if not prompt:
            return ToolResult.fail("Пустой prompt для генерации изображения.")
        try:
            num_images = int(params.get("num_images") or 1)
        except (TypeError, ValueError):
            num_images = 1
        num_images = max(1, min(num_images, 4))
        if image_fn is None:
            return ToolResult.fail("Бэкенд генерации изображений не подключён.")
        try:
            data = await _maybe_await(image_fn(prompt, num_images))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"Генерация не выполнена: {exc}")
        if not isinstance(data, dict):
            return ToolResult.fail("Бэкенд генерации вернул неожиданный ответ.")
        if data.get("_error"):
            return ToolResult.fail(str(data["_error"]))
        urls = data.get("urls") or []
        if not urls:
            return ToolResult.fail("Провайдер вернул пустой список изображений.")
        return ToolResult.photo(urls[0], caption=prompt[:80])

    return Tool(
        name="generate_image",
        description=(
            "Сгенерировать изображение по текстовому описанию (Replicate FLUX). "
            "Когда использовать: пользователь просит создать/нарисовать/"
            "сгенерировать картинку или фото по описанию (например «сгенери "
            "картинку кота в очках», «нарисуй закат над морем»). По умолчанию одно "
            "изображение. Эквивалент /gen."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Описание желаемого изображения.",
                },
                "num_images": {
                    "type": "integer",
                    "description": "Сколько изображений (1–4), по умолчанию 1.",
                },
            },
            "required": ["prompt"],
        },
        handler=handler,
    )
