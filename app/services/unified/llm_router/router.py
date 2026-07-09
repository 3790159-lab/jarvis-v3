# -*- coding: utf-8 -*-
"""The Claude tool_use router — Phase-4 natural-language orchestrator.

:class:`LLMRouter` takes a free-form user message, asks Claude (with the
registry's tools attached) what to do, executes any tools it calls, feeds the
results back, and loops until Claude returns a plain-text answer. The outcome
is a :class:`RouterResponse` carrying the final text, any media the tools
produced, the tools invoked, token usage, and the computed USD cost.

The Anthropic SDK client is synchronous; :meth:`route_message` is async and
runs each API call in a worker thread so it never blocks the event loop. Cost
and audit recording are injected (defaulting to the Day-8 ``cost_tracker`` /
``audit_logger`` modules) so the agentic loop stays unit-testable without
touching real ledgers.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.services.unified.llm_router.llm_client import DEFAULT_MODEL, compute_cost
from app.services.unified.llm_router.tool_registry import (
    ToolContext,
    ToolRegistry,
    ToolResult,
)

# ``compute_cost`` and the pricing table now live in ``llm_client``; it is
# re-exported here for backward compatibility with earlier import sites.

_DEFAULT_SYSTEM_PROMPT = (
    "Ты — Jarvis, персональный AI-ассистент Daniil в Telegram. Твоя "
    "специализация — face-swap (замена лиц на фото и в видео), анимация фото в "
    "видео и AI-персоны. Пользователь пишет на естественном языке, чаще всего "
    "по-русски (в том числе голосом — см. ниже).\n"
    "\n"
    "ЧТО ТЫ РЕАЛЬНО УМЕЕШЬ (РАБОТАЕТ, через инструменты):\n"
    "1. Пакетный face-swap. Сценарий: сначала собрать исходные фото-лица "
    "(swap_batch_start_source), затем целевые фото, куда подставляются лица "
    "(swap_batch_start_targets), потом запустить обработку (swap_batch_run_swap). "
    "Это именно ПАКЕТНЫЙ (batch) режим — много фото за один прогон; на один "
    "прогон одновременно может идти только одна генерация. Текущий пакет можно "
    "отменить (cancel_current_batch).\n"
    "2. Замена лица в ГОТОВОМ видео (video_face_swap). Пользователь присылает "
    "видеоролик и фото с лицом — бот подставляет это лицо во все кадры и "
    "отдаёт новое видео (со звуком). Лимиты: до 60 секунд и до 1080p. Это НЕ "
    "анимация фото — нужен именно готовый видеофайл-источник. После вызова бот "
    "сам попросит прислать видео и фото-лицо.\n"
    "3. Анимация результатов swap в видео (swap_batch_run_animation): режим "
    "'yes' — дефолтное кинематографичное движение, 'custom' — своё описание "
    "движения, 'no' — без анимации. Качество видео настраивается отдельно "
    "(swap_batch_set_quality): длительность в секундах и FPS (кадров в секунду). "
    "Анимация одного видео занимает заметное время (минуты).\n"
    "4. Статистика расходов пользователя (get_user_stats): сколько потрачено "
    "сегодня, за месяц и всего.\n"
    "5. Интернет-ресёрч (web_research): найти и проанализировать актуальную "
    "информацию, сравнить варианты, ответить по свежим данным. Используй на "
    "запросы «найди», «изучи», «сделай ресёрч», «сравни текстом» (если же "
    "просят сравнение именно таблицей — это build_table, см. ниже).\n"
    "6. Сравнительная таблица (build_table): построить таблицу-сравнение/"
    "рейтинг по теме из интернета и прислать файлом XLSX. Используй на «сделай "
    "таблицу», «таблицу топ …», «сравни … таблицей».\n"
    "7. Генерация изображения (generate_image): создать картинку по текстовому "
    "описанию. Используй на «сгенери картинку», «нарисуй …». По умолчанию одна "
    "картинка.\n"
    "8. Вопросы по загруженному файлу (answer_about_file): суммировать/извлечь "
    "данные/ответить по последнему присланному файлу (PDF/DOCX/XLSX). О наличии "
    "файла ты узнаёшь из системного контекста (если файл загружен, там будет "
    "пометка с его именем). Если по контексту файл есть — на вопросы о нём "
    "(«что в файле», «суммируй документ», «какая сумма в договоре», «этот "
    "файл/документ») ВЫЗЫВАЙ answer_about_file и не утверждай сам, что файла "
    "нет. Если же файла действительно нет — это сообщит сам инструмент, тогда и "
    "попроси прислать.\n"
    "\n"
    "ГОЛОСОВЫЕ СООБЩЕНИЯ: входящие голосовые автоматически распознаются "
    "(Whisper) и приходят к тебе уже как обычный текст — это происходит ДО "
    "тебя, отдельного инструмента для распознавания нет и вызывать ничего не "
    "нужно. Отвечать голосом ты МОЖЕШЬ по запросу: если пользователь просит "
    "озвучить/ответить голосом («ответь голосом», «озвучь», «скажи голосом») — "
    "вызови reply_with_voice и передай в text сам текст ответа; он озвучит "
    "именно этот ответ, независимо от глобальной настройки автоозвучивания "
    "(она отдельная и по умолчанию выключена). Без явной просьбы голосом не "
    "отвечай.\n"
    "\n"
    "ЧТО ПОКА НЕ ПОДКЛЮЧЕНО (заглушки — не выдавай за рабочее):\n"
    "- Генерация фото AI-персон (generate_persona_photo): FLUX-бэкенд персон "
    "ещё НЕ подключён в текущем окружении, вызов вернёт ошибку. Если "
    "пользователь просит сгенерировать фото персоны — честно скажи, что эта "
    "возможность пока не подключена, не делай вид, что получилось.\n"
    "\n"
    "ЧЕСТНОСТЬ ПРО ВОЗМОЖНОСТИ:\n"
    "- Глубокий анализ/планирование (/brain) и инженерный разбор (/engineer) "
    "пока работают ТОЛЬКО как команды и дают ШАБЛОННЫЙ план + краткий "
    "веб-ресёрч — не выдавай это за полноценный автономный анализ. Если "
    "пользователь просит именно это, предложи команду /brain или /engineer.\n"
    "- Многошаговые сценарии без инструментов (персоны, обучение LoRA, Photo "
    "Studio) запускаются командами — направляй пользователя на нужную команду, "
    "не пытайся выполнить их инструментом. (Пакетный swap — наоборот, это "
    "инструменты swap_batch_*, см. пункт 1.)\n"
    "\n"
    "КАК СЕБЯ ВЕСТИ:\n"
    "- На понятный запрос — выбери и вызови подходящий инструмент, затем дай "
    "короткое подтверждение того, что сделано.\n"
    "- На непонятный, бессмысленный или слишком короткий ввод (например «раз», "
    "«ы», случайные символы) НЕ угадывай инструмент и НЕ выдумывай намерение — "
    "вежливо уточни, что именно нужно сделать, и при необходимости коротко "
    "подскажи, что ты умеешь.\n"
    "- Если запрос не требует инструмента (приветствие, вопрос о тебе) — просто "
    "ответь текстом.\n"
    "\n"
    "ТОН: кратко, дружелюбно, по-русски, без лишних повторов и канцелярита. "
    "Без воды — одно-два предложения, если задача не требует большего."
)


@dataclass
class RouterResponse:
    """Outcome of :meth:`LLMRouter.route_message`."""

    text: str = ""
    media: List[ToolResult] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str = ""
    pending_paid: Optional[Dict[str, Any]] = None   # {name, params, est_usd} — awaiting confirm


# Injected recorders: keep signatures matching the Day-8 modules.
CostRecorder = Callable[[Optional[int], Optional[str], float], None]
AuditRecorder = Callable[..., None]

# HTTP statuses that are safe to retry (rate limit, request-timeout/conflict,
# and every 5xx). Anthropic's RateLimitError carries 429; InternalServerError /
# OverloadedError carry 5xx.
_TRANSIENT_STATUS_CODES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
# Network-level anthropic errors that carry no status_code — classified by the
# exception class name so the retry policy works without importing the SDK
# (and so tests can exercise it with lightweight doubles).
_TRANSIENT_ERROR_NAMES = frozenset(
    {
        "RateLimitError",
        "APITimeoutError",
        "APIConnectionError",
        "InternalServerError",
        "OverloadedError",
        "ServiceUnavailableError",
    }
)


def _is_transient_error(exc: BaseException) -> bool:
    """True if ``exc`` is worth retrying (rate limit / timeout / 5xx).

    4xx client errors (bad request, auth, not-found) and unrecognised
    exceptions are treated as permanent — retrying them only wastes calls.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in _TRANSIENT_STATUS_CODES or status >= 500
    return type(exc).__name__ in _TRANSIENT_ERROR_NAMES


class LLMRouter:
    """Routes a natural-language message through Claude + the tool registry."""

    def __init__(
        self,
        anthropic_client: Any,
        tool_registry: ToolRegistry,
        model: str = DEFAULT_MODEL,
        *,
        enabled: bool = True,
        system_prompt: Optional[str] = None,
        max_iterations: int = 6,
        max_tokens: int = 1024,
        max_retries: int = 3,
        retry_backoff: float = 0.5,
        sleep_fn: Optional[Callable[[float], None]] = None,
        record_cost: Optional[CostRecorder] = None,
        audit: Optional[AuditRecorder] = None,
    ) -> None:
        self._client = anthropic_client
        self._registry = tool_registry
        self._model = model
        self._enabled = enabled
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT
        self._max_iterations = max_iterations
        self._max_tokens = max_tokens
        self._max_retries = max(1, int(max_retries))
        self._retry_backoff = retry_backoff
        self._sleep = sleep_fn or time.sleep
        self._record_cost = record_cost
        self._audit = audit

    async def route_message(
        self,
        text: str,
        context: ToolContext,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        extra_context: Optional[str] = None,
    ) -> RouterResponse:
        """Route ``text``: call Claude, run tools, loop to a plain-text answer.

        ``extra_context`` is a per-message hint (e.g. "a file is uploaded")
        appended to the system prompt for THIS call only — it is not persisted
        into the system prompt nor the conversation history, so it always
        reflects the current turn's state.
        """
        if not self._enabled:
            return RouterResponse(error="router_disabled")

        system_prompt = self._system_prompt
        if extra_context:
            system_prompt = f"{system_prompt}\n\n{extra_context}"

        messages: List[Dict[str, Any]] = list(conversation_history or [])
        messages.append({"role": "user", "content": text})

        tools_payload = self._registry.to_anthropic_tools()
        tools_used: List[str] = []
        media: List[ToolResult] = []
        input_tokens = 0
        output_tokens = 0
        final_text = ""

        try:
            for _ in range(self._max_iterations):
                kwargs: Dict[str, Any] = {
                    "model": self._model,
                    "max_tokens": self._max_tokens,
                    "system": system_prompt,
                    "messages": messages,
                }
                if tools_payload:
                    kwargs["tools"] = tools_payload

                resp = await asyncio.to_thread(self._create_message, **kwargs)

                usage = getattr(resp, "usage", None)
                if usage is not None:
                    input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
                    output_tokens += int(getattr(usage, "output_tokens", 0) or 0)

                content = getattr(resp, "content", []) or []
                tool_uses = [b for b in content if getattr(b, "type", None) == "tool_use"]

                if not tool_uses:
                    final_text = "".join(
                        getattr(b, "text", "")
                        for b in content
                        if getattr(b, "type", None) == "text"
                    )
                    break

                # Echo the assistant turn (with its tool_use blocks) back verbatim.
                messages.append({"role": "assistant", "content": content})

                # Money gate: if Claude selected any PAID tool, DO NOT execute
                # anything in this batch. Halt and surface a pending action for the
                # caller to confirm. The trigger is registry metadata (tool.paid),
                # never the message text — so no phrase can bypass this.
                paid_tu = next(
                    (tu for tu in tool_uses
                     if getattr(self._registry.get(tu.name), "paid", False)),
                    None,
                )
                if paid_tu is not None:
                    tool = self._registry.get(paid_tu.name)
                    # Money-leak fix: this halt is an un-executed turn (awaiting a
                    # confirm tap). Do NOT write the routing tokens to the user's
                    # ledger — before the tap /costs must not move at all. The
                    # tokens are still surfaced on the response for transparency,
                    # but nothing is billed.
                    cost = compute_cost(self._model, input_tokens, output_tokens)
                    return RouterResponse(
                        tools_used=tools_used,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_usd=cost,
                        pending_paid={
                            "name": paid_tu.name,
                            "params": paid_tu.input or {},
                            "est_usd": float(getattr(tool, "est_usd", 0.0) or 0.0),
                        },
                    )

                results: List[Dict[str, Any]] = []
                for tu in tool_uses:
                    tools_used.append(tu.name)
                    result = await self._execute_tool(tu.name, tu.input or {}, context)
                    if result.is_media:
                        media.append(result)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": result.to_tool_content(),
                            "is_error": result.is_error,
                        }
                    )
                messages.append({"role": "user", "content": results})
            else:
                # Loop exhausted without a plain-text turn.
                final_text = "Не удалось завершить запрос за отведённое число шагов."
        except Exception as exc:  # noqa: BLE001 - API failure must degrade, not crash
            # Retries are exhausted (transient) or the error is permanent (4xx).
            # Return a graceful error result so the caller can fall back to the
            # legacy dispatcher instead of the message crashing the bot loop.
            cost = compute_cost(self._model, input_tokens, output_tokens)
            self._record(context, tools_used, input_tokens + output_tokens, cost)
            return RouterResponse(
                error=f"{type(exc).__name__}: {exc}",
                tools_used=tools_used,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
            )

        cost = compute_cost(self._model, input_tokens, output_tokens)
        self._record(context, tools_used, input_tokens + output_tokens, cost)

        return RouterResponse(
            text=final_text,
            media=media,
            tools_used=tools_used,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )

    # ── internals ──────────────────────────────────────────────────────────
    def _create_message(self, **kwargs: Any) -> Any:
        """Call ``messages.create`` with bounded exponential-backoff retry.

        Transient errors (rate limit / timeout / 5xx) are retried up to
        ``max_retries`` total attempts; permanent errors (4xx/auth) raise on the
        first failure. The last transient error is re-raised once attempts are
        exhausted so the caller can degrade gracefully.
        """
        last_exc: Optional[BaseException] = None
        for attempt in range(self._max_retries):
            try:
                return self._client.messages.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - classified below
                if not _is_transient_error(exc):
                    raise
                last_exc = exc
                if attempt + 1 >= self._max_retries:
                    break
                self._sleep(self._retry_backoff * (2 ** attempt))
        assert last_exc is not None  # only reached after a transient failure
        raise last_exc

    async def _execute_tool(
        self, name: str, params: Dict[str, Any], context: ToolContext
    ) -> ToolResult:
        tool = self._registry.get(name)
        if tool is None:
            return ToolResult.fail(f"Неизвестный инструмент: {name}")
        try:
            return await tool.handler(params, context)
        except Exception as exc:  # noqa: BLE001 - tool failure must not crash the loop
            return ToolResult.fail(f"{type(exc).__name__}: {exc}")

    def _record(
        self,
        context: ToolContext,
        tools_used: List[str],
        total_tokens: int,
        cost: float,
    ) -> None:
        if self._record_cost is not None:
            try:
                self._record_cost(context.user_id, context.username, cost)
            except Exception:  # noqa: BLE001 - accounting must not break routing
                pass
        if self._audit is not None:
            try:
                self._audit(
                    context.user_id,
                    context.username,
                    context.chat_id,
                    "router_call",
                    {
                        "tool_used": tools_used,
                        "tokens_used": total_tokens,
                        "cost_usd": round(cost, 6),
                    },
                )
            except Exception:  # noqa: BLE001
                pass
