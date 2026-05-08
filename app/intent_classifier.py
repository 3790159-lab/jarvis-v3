from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class IntentResult:
    intent: str
    needs_tools: bool = False
    needs_local_context: bool = False
    is_followup: bool = False
    wants_long_answer: bool = False
    use_dual: bool = False


class IntentClassifier:
    FOLLOWUP_WORDS = (
        "объясни подробнее",
        "объясни детальнее",
        "подробнее",
        "детальнее",
        "а почему",
        "что это значит",
        "сравни",
        "продолжи",
    )
    REALTIME_WORDS = (
        "сейчас",
        "сегодня",
        "завтра",
        "на выходных",
        "погода",
        "прогноз",
        "ветер",
        "температур",
        "сколько градусов",
    )
    LOCAL_WORDS = (
        "проект",
        "файл",
        "папк",
        "git",
        "readme",
        ".py",
        ".env",
        "агент",
        "код",
        "orchestrator",
        "telegram_bot",
        "worker",
        "найди",
        "покажи",
        "список",
    )

    def classify(self, text: str, last_result: str = "") -> IntentResult:
        t = (text or "").strip().lower()

        if any(x in t for x in self.FOLLOWUP_WORDS) and last_result.strip():
            return IntentResult(intent="followup", is_followup=True)

        if any(x in t for x in self.REALTIME_WORDS):
            return IntentResult(intent="realtime", needs_tools=True)

        if self._looks_like_local_request(t):
            use_dual = any(x in t for x in ("проанализируй", "архитектур", "деталь", "объясни суть", "в целом"))
            return IntentResult(
                intent="local_project",
                needs_tools=True,
                needs_local_context=True,
                use_dual=use_dual,
            )

        wants_long = any(x in t for x in ("подробно", "детально", "с примерами", "распиши"))
        return IntentResult(intent="general", wants_long_answer=wants_long)

    def _looks_like_local_request(self, text: str) -> bool:
        return any(x in text for x in self.LOCAL_WORDS)
