from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Callable

log = logging.getLogger("chatter.core.llm")


class LLMClient(ABC):
    # no_thinking: явно заглушить расширенное мышление модели. Нужен служебным
    # вызовам с маленьким max_tokens (классификатор): у sonnet-5 thinking включён
    # ПО УМОЛЧАНИЮ при опущенном параметре и молча съедает весь бюджет токенов —
    # инцидент volska 2026-07-22 (пустой ответ классификатора на каждом
    # сообщении). Ответы персоне дефолт модели не трогают.
    #
    # uncached_suffix: волатильный хвост системного промпта (разовая заметка
    # brain'а). Идёт ОТДЕЛЬНЫМ system-блоком ПОСЛЕ cache_control-breakpoint'а —
    # иначе каждая заметка инвалидировала бы кэш всей системы (спека
    # 2026-07-23-chatter-prompt-caching).
    #
    # tag: метка вызывающего ("brain"/"classifier") для usage-логгера.
    @abstractmethod
    def complete(self, system: str, messages: list[dict], *,
                 max_tokens: int, no_thinking: bool = False,
                 uncached_suffix: str | None = None, tag: str = "") -> str:
        ...


class FakeLLM(LLMClient):
    """Deterministic, offline LLM for tests and no-key demo runs."""

    def __init__(self, scripted: list[str] | None = None):
        self._scripted = list(scripted) if scripted else []
        self._i = 0
        self.calls: list[dict] = []

    def complete(self, system: str, messages: list[dict], *,
                 max_tokens: int, no_thinking: bool = False,
                 uncached_suffix: str | None = None, tag: str = "") -> str:
        # Наблюдаемый контракт для тестов прежний: suffix — часть system
        # (в реальном запросе он второй system-блок), плюс новые поля отдельно.
        recorded_system = (f"{system}\n\n{uncached_suffix}"
                           if uncached_suffix else system)
        self.calls.append({"system": recorded_system, "messages": messages,
                           "max_tokens": max_tokens, "no_thinking": no_thinking,
                           "uncached_suffix": uncached_suffix, "tag": tag})
        if self._i < len(self._scripted):
            out = self._scripted[self._i]
            self._i += 1
            return out
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return f"Поняла вас про «{last_user}». Расскажите чуть подробнее, что именно ищете?"


class AnthropicLLM(LLMClient):
    """Production client — model id from config. Reads ANTHROPIC_API_KEY from
    env. The `anthropic` SDK is imported lazily inside __init__ so importing
    this module (and using FakeLLM) never requires the SDK or a network/key.

    Prompt-caching: стабильная система уходит system-блоком с
    `cache_control: ephemeral` (TTL 5 мин, write ×1.25 / read ×0.1).
    Порог включения кэша зависит от модели (haiku-4-5: 4096 токенов) —
    короткий промпт молча не кэшируется, факт виден по
    usage.cache_creation_input_tokens == 0.

    usage_sink: колбэк с полями usage каждого вызова (для llm_usage в БД).
    Сбой sink НЕ роняет ответ, но кричит в лог (DEV-18: не молча)."""

    def __init__(self, model: str,
                 usage_sink: Callable[[dict], None] | None = None):
        import anthropic  # imported lazily so tests never need the SDK/key
        self._client = anthropic.Anthropic()
        self._model = model
        self._usage_sink = usage_sink

    def complete(self, system: str, messages: list[dict], *,
                 max_tokens: int, no_thinking: bool = False,
                 uncached_suffix: str | None = None, tag: str = "") -> str:
        kwargs = {}
        if no_thinking:
            # sonnet-5 и haiku-4-5 оба принимают disabled (проверено живьём
            # 2026-07-22). НЕ слать thinking по умолчанию: адаптивный дефолт
            # модели для ответов персоны — осознанный выбор.
            # Кэшу системы это не мешает: thinking-инвалидация бьёт только
            # messages-tier, system-tier живёт.
            kwargs["thinking"] = {"type": "disabled"}
        system_blocks = [{"type": "text", "text": system,
                          "cache_control": {"type": "ephemeral"}}]
        if uncached_suffix:
            system_blocks.append({"type": "text", "text": uncached_suffix})
        resp = self._client.messages.create(
            model=self._model, max_tokens=max_tokens, system=system_blocks,
            messages=messages, **kwargs,
        )
        self._record_usage(resp, tag)
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()

    def _record_usage(self, resp, tag: str) -> None:
        if self._usage_sink is None:
            return
        try:
            u = resp.usage
            self._usage_sink({
                "tag": tag, "model": self._model,
                "input_tokens": int(getattr(u, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(u, "output_tokens", 0) or 0),
                "cache_read_input_tokens":
                    int(getattr(u, "cache_read_input_tokens", 0) or 0),
                "cache_creation_input_tokens":
                    int(getattr(u, "cache_creation_input_tokens", 0) or 0),
            })
        except Exception:
            # Логгер — наблюдаемость, не бизнес-путь: ответ лиду важнее записи
            # метрики. Но тихо глотать нельзя (DEV-18).
            log.warning("usage-sink упал — запись usage потеряна, ответ не тронут",
                        exc_info=True)
