from __future__ import annotations

import os
import time
from dataclasses import dataclass

from .ambiguity_resolver import AmbiguityResolver
from .input_normalizer import NormalizedInput
from .intent_gate import FastIntentGate
from .llm import FOLLOWUP_SYSTEM, GENERAL_SYSTEM, VISION_SYSTEM, HybridLLM
from .memory_state import MemoryState
from .response_formatter import ResponseFormatter
from .tool_executor import ToolExecutor
from .tool_router import ToolRouter


@dataclass
class OrchestratorConfig:
    project_root: str
    allowed_roots: list[str]


class ConversationOrchestrator:
    def __init__(self, config: OrchestratorConfig) -> None:
        self.config = config
        self.memory = MemoryState()
        self.gate = FastIntentGate()
        self.resolver = AmbiguityResolver()
        self.router = ToolRouter()
        self.tools = ToolExecutor(config.allowed_roots)
        self.llm = HybridLLM()
        self.formatter = ResponseFormatter()

    def handle_input(self, item: NormalizedInput) -> list[str]:
        started = time.monotonic()
        state = self.memory.get(item.chat_id)

        if item.media and item.media.bytes_data:
            self.memory.remember_media(
                item.chat_id,
                {
                    "kind": item.media.kind,
                    "caption": item.media.caption,
                    "bytes_data": item.media.bytes_data,
                },
            )

        decision = self.gate.classify(item.combined_text or item.text or item.caption, has_photo=item.has_photo, reply_text=item.reply_text)
        decision = self.resolver.resolve(item.combined_text or item.text or item.caption, decision)
        plan = self.router.plan(decision.intent)

        backend_used = "none"
        tools_used = list(plan.tools)
        note = ""
        out_text = ""

        if plan.route in {"trace", "math_first", "facts_first", "local_first"}:
            trace_text = self.memory.render_last_trace(item.chat_id) if plan.route == "trace" else ""
            result = self.tools.run(plan.route, item.combined_text or item.text or item.caption, project_root=self.config.project_root, trace_text=trace_text)
            if result.ok:
                out_text = result.summary + (f"\n\n{result.details}" if result.details else "")
                backend_used = "none"
                mode = "verified_answer" if plan.verified_answer else "project_operator"
                self.memory.remember_result(
                    item.chat_id,
                    item.combined_text,
                    out_text,
                    intent=decision.intent,
                    route=plan.route,
                    mode=mode,
                    tool_output=result.facts,
                )
            else:
                note = result.summary
                if decision.intent == "current_facts":
                    out_text = result.summary
                else:
                    out_text = result.summary

        elif plan.route == "vision_first":
            media = item.media.bytes_data if item.media and item.media.bytes_data else state.last_media.get("bytes_data")
            if media:
                llm_result = self.llm.generate(
                    system_prompt=VISION_SYSTEM,
                    user_prompt=(item.combined_text or "Опиши, что на изображении, и дай краткий вывод."),
                    preferred="openai",
                    image_bytes=media,
                )
                if llm_result.ok:
                    out_text = llm_result.text
                    backend_used = llm_result.backend
                    self.memory.remember_result(
                        item.chat_id,
                        item.combined_text,
                        out_text,
                        intent=decision.intent,
                        route=plan.route,
                        mode="assistant_explainer",
                        tool_output={},
                    )
                else:
                    out_text = (
                        "Фото получено, но vision-модуль сейчас не смог обработать изображение. "
                        "Проверь OpenAI ключ или пришли ещё и текстовое описание."
                    )
                    note = llm_result.error
            else:
                out_text = "Фото не найдено в текущем контексте."

        elif plan.route == "followup":
            context = state.last_result.strip()
            media = state.last_media.get("bytes_data")
            prompt = (
                f"Предыдущий ответ:\n{context or 'нет'}\n\n"
                f"Новый вопрос пользователя:\n{item.combined_text or item.text}\n\n"
                "Дай полезное продолжение, опираясь на прошлый результат. "
                "Если прошлый результат был ошибочным и пользователь просит перепроверку, признай ошибку и исправь её."
            )
            llm_result = self.llm.generate(
                system_prompt=FOLLOWUP_SYSTEM,
                user_prompt=prompt,
                preferred="openai",
                image_bytes=media if ("фото" in (item.combined_text or "").lower() or state.last_media) else None,
            )
            if llm_result.ok:
                out_text = llm_result.text
                backend_used = llm_result.backend
                self.memory.remember_result(
                    item.chat_id,
                    item.combined_text,
                    out_text,
                    intent=decision.intent,
                    route=plan.route,
                    mode="assistant_explainer",
                    tool_output=state.last_tool_output,
                )
            else:
                out_text = (
                    "Я увидел, что это уточнение к прошлому ответу, но сейчас не смог корректно собрать продолжение. "
                    "Попробуй сформулировать уточнение чуть подробнее."
                )
                note = llm_result.error

        else:
            preferred = "openai" if self.llm.has_openai() else "ollama"
            llm_result = self.llm.generate(
                system_prompt=GENERAL_SYSTEM,
                user_prompt=item.combined_text or item.text or item.caption,
                preferred=preferred,
            )
            if llm_result.ok:
                out_text = llm_result.text
                backend_used = llm_result.backend
                self.memory.remember_result(
                    item.chat_id,
                    item.combined_text,
                    out_text,
                    intent=decision.intent,
                    route=plan.route,
                    mode="assistant_explainer",
                    tool_output={},
                )
            else:
                out_text = "Сейчас у меня нет доступного language backend для общего ответа. Проверь OpenAI или Ollama."
                note = llm_result.error

        latency_ms = int((time.monotonic() - started) * 1000)
        self.memory.add_trace(
            item.chat_id,
            intent=decision.intent,
            route=plan.route,
            tools_used=tools_used,
            backend_used=backend_used,
            latency_ms=latency_ms,
            status="ok" if out_text else "error",
            note=note,
        )

        formatted = self.formatter.finalize(
            out_text,
            mode="verified_answer" if plan.verified_answer else "assistant_explainer",
        )
        return self.formatter.split_for_telegram(formatted.text)
