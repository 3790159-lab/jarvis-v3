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
    "Ты — Jarvis, ассистент в Telegram. Пользователь пишет на естественном "
    "языке (часто по-русски). Реши, какие инструменты вызвать, чтобы выполнить "
    "запрос, и вызови их. Если инструмент не нужен — ответь обычным текстом. "
    "Отвечай кратко и по-русски. Когда задача выполнена, дай короткое "
    "подтверждение того, что сделано."
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


# Injected recorders: keep signatures matching the Day-8 modules.
CostRecorder = Callable[[Optional[int], Optional[str], float], None]
AuditRecorder = Callable[..., None]


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
        self._record_cost = record_cost
        self._audit = audit

    async def route_message(
        self,
        text: str,
        context: ToolContext,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> RouterResponse:
        """Route ``text``: call Claude, run tools, loop to a plain-text answer."""
        if not self._enabled:
            return RouterResponse(error="router_disabled")

        messages: List[Dict[str, Any]] = list(conversation_history or [])
        messages.append({"role": "user", "content": text})

        tools_payload = self._registry.to_anthropic_tools()
        tools_used: List[str] = []
        media: List[ToolResult] = []
        input_tokens = 0
        output_tokens = 0
        final_text = ""

        for _ in range(self._max_iterations):
            kwargs: Dict[str, Any] = {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "system": self._system_prompt,
                "messages": messages,
            }
            if tools_payload:
                kwargs["tools"] = tools_payload

            resp = await asyncio.to_thread(self._client.messages.create, **kwargs)

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
