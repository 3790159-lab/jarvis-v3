# -*- coding: utf-8 -*-
"""``reply_with_voice`` tool — speak a specific reply on request.

Most of the time Jarvis answers in text. When the user explicitly asks for a
spoken/audio answer ("ответь голосом", "озвучь"), Claude calls this tool with
the text to voice; the tool turns it into a Telegram voice note via the
existing TTS pipeline (``synthesize_speech``) and delivers it.

This is independent of the global ``JARVIS_VOICE_REPLY_ENABLED`` flag: that flag
voices *every* reply, whereas this tool voices *this one* because it was asked
for. ``synthesize_fn`` / ``send_fn`` are injected so the tool is unit-testable
without OpenAI or Telegram, and so a missing/invalid key surfaces as an honest
tool error instead of a silent no-op.
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from app.services.unified.llm_router.tool_registry import (
    Tool,
    ToolContext,
    ToolResult,
)

# synthesize_fn: (text) -> SynthesisResult-like (.is_error/.error/.audio/.audio_format/.cost_usd)
SynthesizeFn = Callable[[str], Any]
# send_fn: (context, result) -> None  (delivers the voice note + records its cost)
SendFn = Callable[[ToolContext, Any], Any]


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _default_synthesize(text: str) -> Any:
    """Reuse the unified TTS synthesiser (no reinvented backend)."""
    from app.services.unified.voice.synthesize import synthesize_speech

    return synthesize_speech(text)


def build_voice_reply_tool(
    *,
    synthesize_fn: Optional[SynthesizeFn] = None,
    send_fn: Optional[SendFn] = None,
) -> Tool:
    """Build the ``reply_with_voice`` :class:`Tool`."""
    synth = synthesize_fn or _default_synthesize

    async def handler(params: dict, context: ToolContext) -> ToolResult:
        text = str(params.get("text") or "").strip()
        if not text:
            return ToolResult.fail("Нет текста для озвучивания.")
        if send_fn is None:
            return ToolResult.fail(
                "Голосовые ответы не подключены в этом окружении."
            )
        try:
            result = await _maybe_await(synth(text))
        except Exception as exc:  # noqa: BLE001 - surface as graceful tool error
            return ToolResult.fail(f"Ошибка синтеза речи: {exc}")
        if getattr(result, "is_error", False) or not getattr(result, "audio", b""):
            return ToolResult.fail(
                getattr(result, "error", "") or "Не удалось синтезировать речь."
            )
        try:
            await _maybe_await(send_fn(context, result))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"Не удалось отправить голосовое: {exc}")
        return ToolResult.ok_text("Озвучил ответ и отправил голосовым сообщением.")

    return Tool(
        name="reply_with_voice",
        description=(
            "Озвучить ответ и отправить его голосовым сообщением (TTS). В "
            "параметре text передай именно тот текст, который нужно произнести "
            "вслух. Когда использовать: пользователь ЯВНО просит голосовой/аудио "
            "ответ (например «ответь голосом», «озвучь», «скажи голосом», «пришли "
            "аудио»). Работает по запросу и не зависит от глобальной настройки "
            "озвучивания. Не дублируй голосом обычные текстовые ответы — только "
            "когда об этом просят. Если синтез недоступен (нет/невалиден ключ "
            "OpenAI) — вернётся честная ошибка, не выдавай её за успех."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Текст, который нужно произнести вслух.",
                }
            },
            "required": ["text"],
        },
        handler=handler,
    )
