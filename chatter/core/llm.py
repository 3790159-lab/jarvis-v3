from __future__ import annotations
from abc import ABC, abstractmethod


class LLMClient(ABC):
    # no_thinking: явно заглушить расширенное мышление модели. Нужен служебным
    # вызовам с маленьким max_tokens (классификатор): у sonnet-5 thinking включён
    # ПО УМОЛЧАНИЮ при опущенном параметре и молча съедает весь бюджет токенов —
    # инцидент volska 2026-07-22 (пустой ответ классификатора на каждом
    # сообщении). Ответы персоне дефолт модели не трогают.
    @abstractmethod
    def complete(self, system: str, messages: list[dict], *,
                 max_tokens: int, no_thinking: bool = False) -> str:
        ...


class FakeLLM(LLMClient):
    """Deterministic, offline LLM for tests and no-key demo runs."""

    def __init__(self, scripted: list[str] | None = None):
        self._scripted = list(scripted) if scripted else []
        self._i = 0
        self.calls: list[dict] = []

    def complete(self, system: str, messages: list[dict], *,
                 max_tokens: int, no_thinking: bool = False) -> str:
        self.calls.append({"system": system, "messages": messages,
                           "max_tokens": max_tokens, "no_thinking": no_thinking})
        if self._i < len(self._scripted):
            out = self._scripted[self._i]
            self._i += 1
            return out
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return f"Поняла вас про «{last_user}». Расскажите чуть подробнее, что именно ищете?"


class AnthropicLLM(LLMClient):
    """Production client — Claude Haiku 4.5 by default (model id from config).
    Reads ANTHROPIC_API_KEY from env. The `anthropic` SDK is imported lazily
    inside __init__ so importing this module (and using FakeLLM) never
    requires the SDK or a network/key."""

    def __init__(self, model: str):
        import anthropic  # imported lazily so tests never need the SDK/key
        self._client = anthropic.Anthropic()
        self._model = model

    def complete(self, system: str, messages: list[dict], *,
                 max_tokens: int, no_thinking: bool = False) -> str:
        kwargs = {}
        if no_thinking:
            # sonnet-5 и haiku-4-5 оба принимают disabled (проверено живьём
            # 2026-07-22). НЕ слать thinking по умолчанию: адаптивный дефолт
            # модели для ответов персоны — осознанный выбор.
            kwargs["thinking"] = {"type": "disabled"}
        resp = self._client.messages.create(
            model=self._model, max_tokens=max_tokens, system=system,
            messages=messages, **kwargs,
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
