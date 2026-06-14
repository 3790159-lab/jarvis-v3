# -*- coding: utf-8 -*-
"""``generate_persona_photo`` tool — produce a persona photo via FLUX.

The image-generation backend (``generate_fn``) and persona-existence check
(``persona_exists_fn``) are injected. In production the bot bridge supplies a
``generate_fn`` wired to the existing ``block_m1_persona`` FLUX pipeline; when
no backend is wired the handler fails gracefully (it never raises into the
router loop).
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, List, Optional

from app.services.unified.llm_router.tool_registry import (
    Tool,
    ToolContext,
    ToolResult,
)

# generate_fn: (persona_id, prompt, count) -> list[str]  (urls or local paths);
# may be sync or async.
GenerateFn = Callable[[str, str, int], Any]
PersonaExistsFn = Callable[[str], bool]


def _default_generate(persona_id: str, prompt: str, count: int) -> List[str]:
    """Placeholder backend: real FLUX wiring is supplied by the bot bridge."""
    raise RuntimeError(
        "Генерация фото персон не подключена в этом окружении "
        "(нужен FLUX-бэкенд block_m1_persona)."
    )


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def build_persona_photo_tool(
    *,
    generate_fn: Optional[GenerateFn] = None,
    persona_exists_fn: Optional[PersonaExistsFn] = None,
) -> Tool:
    """Build the ``generate_persona_photo`` :class:`Tool`."""
    gen = generate_fn or _default_generate

    async def handler(params: dict, context: ToolContext) -> ToolResult:
        persona_id = str(params.get("persona_id") or "").strip()
        prompt = str(params.get("prompt") or "").strip()
        try:
            count = int(params.get("count", 1) or 1)
        except (TypeError, ValueError):
            count = 1
        count = max(1, min(count, 4))

        if not persona_id:
            return ToolResult.fail("Не указан persona_id.")
        if persona_exists_fn is not None and not persona_exists_fn(persona_id):
            return ToolResult.fail(f"Персона не найдена: {persona_id}")

        try:
            images = await _maybe_await(gen(persona_id, prompt, count))
        except Exception as exc:  # noqa: BLE001 - surface as graceful tool error
            return ToolResult.fail(f"Генерация не удалась: {exc}")

        images = list(images or [])
        if not images:
            return ToolResult.fail("Бэкенд не вернул ни одного изображения.")

        if len(images) > 1:
            caption = f"Фото персоны {persona_id} (1 из {len(images)})"
        else:
            caption = f"Фото персоны {persona_id}"
        return ToolResult.photo(images[0], caption)

    return Tool(
        name="generate_persona_photo",
        description=(
            "Сгенерировать фотографию AI-персоны (по её LoRA) через FLUX по "
            "текстовому описанию сцены. ВНИМАНИЕ: в текущем окружении функция "
            "ещё НЕ подключена — FLUX-бэкенд персон отсутствует, это заглушка, "
            "вызов вернёт ошибку «не подключено». Поэтому, если пользователь "
            "просит фото персоны, честно предупреди, что возможность пока не "
            "подключена, а не делай вид, что фото готово. Когда (в будущем) "
            "использовать: пользователь просит создать кадр конкретной персоны "
            "(например «сделай фото персоны anna на пляже»)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "persona_id": {
                    "type": "string",
                    "description": "Идентификатор персоны (LoRA).",
                },
                "prompt": {
                    "type": "string",
                    "description": "Описание сцены/кадра на естественном языке.",
                },
                "count": {
                    "type": "integer",
                    "description": "Сколько фото сгенерировать (1–4, по умолчанию 1).",
                    "minimum": 1,
                    "maximum": 4,
                },
            },
            "required": ["persona_id", "prompt"],
        },
        handler=handler,
    )
