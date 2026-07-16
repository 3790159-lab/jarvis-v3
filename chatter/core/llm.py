from __future__ import annotations
from abc import ABC, abstractmethod


class LLMClient(ABC):
    @abstractmethod
    def complete(self, system: str, messages: list[dict], *, max_tokens: int) -> str:
        ...


class FakeLLM(LLMClient):
    """Deterministic, offline LLM for tests and no-key demo runs."""

    def __init__(self, scripted: list[str] | None = None):
        self._scripted = list(scripted) if scripted else []
        self._i = 0
        self.calls: list[dict] = []

    def complete(self, system: str, messages: list[dict], *, max_tokens: int) -> str:
        self.calls.append({"system": system, "messages": messages, "max_tokens": max_tokens})
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

    def complete(self, system: str, messages: list[dict], *, max_tokens: int) -> str:
        resp = self._client.messages.create(
            model=self._model, max_tokens=max_tokens, system=system, messages=messages,
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
