from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class IntentDecision:
    intent: str
    confidence: float
    reason: str


class FastIntentGate:
    MATH_PATTERNS = [
        r"\bпосчитай\b",
        r"\bвычисли\b",
        r"\bсколько будет\b",
        r"\bумнож(ь|ить)\b",
        r"\bподел(и|ить)\b",
        r"\bслож(и|ить)\b",
        r"\bвыч(ти|есть)\b",
        r"\bнайди площадь\b",
        r"\bплощадь треугольника\b",
        r"\bпроцент(ов|а)?\b",
        r"\bреши уравнение\b",
        r"\bнайди корень уравнения\b",
    ]
    WEATHER_PATTERNS = [
        r"\bпогода\b",
        r"\bпрогноз\b",
        r"\bтемператур(а|ы)\b",
        r"\bветер\b",
        r"\bградус(ов|а)?\b",
        r"\bна выходных\b",
        r"\bсейчас\b.*\bпогод",
    ]
    LOCAL_PATTERNS = [
        r"\bпокажи файлы\b",
        r"\bпрочитай\b",
        r"\bнайди\b.*(\.py|readme|env|файл)",
        r"\bкакие агенты\b",
        r"\bgit status\b",
        r"\bпроанализируй проект\b",
        r"\bструктур[ау] проекта\b",
        r"\bкод\b",
        r"\bпапк[ау]\b",
    ]
    FOLLOWUP_PATTERNS = [
        r"\bобъясни подробнее\b",
        r"\bперепроверь\b",
        r"\bэто точно\b",
        r"\bчто думаешь\b",
        r"\bпочему\b",
        r"\bчто это значит\b",
        r"\bраспиши\b",
        r"\bсравни\b",
    ]
    CURRENT_FACTS_PATTERNS = [
        r"\bкто сейчас\b",
        r"\bновости\b",
        r"\bкурс\b",
        r"\bсегодня\b",
        r"\bзавтра\b",
        r"\bсейчас\b",
    ]

    def classify(self, text: str, *, has_photo: bool = False, reply_text: str = "") -> IntentDecision:
        raw = (text or "").strip()
        lower = raw.lower()

        if has_photo:
            return IntentDecision("photo_vision", 0.98, "message_has_photo")
        if lower == "/trace_last":
            return IntentDecision("trace", 1.0, "debug_command")

        for pattern in self.LOCAL_PATTERNS:
            if re.search(pattern, lower):
                return IntentDecision("local_project", 0.93, pattern)

        for pattern in self.MATH_PATTERNS:
            if re.search(pattern, lower):
                return IntentDecision("math_exact", 0.97, pattern)

        for pattern in self.WEATHER_PATTERNS:
            if re.search(pattern, lower):
                return IntentDecision("current_facts", 0.94, pattern)

        for pattern in self.FOLLOWUP_PATTERNS:
            if re.search(pattern, lower):
                return IntentDecision("followup", 0.9, pattern)

        if reply_text.strip():
            return IntentDecision("followup", 0.72, "reply_to_previous_message")

        for pattern in self.CURRENT_FACTS_PATTERNS:
            if re.search(pattern, lower):
                return IntentDecision("current_facts", 0.62, pattern)

        if len(lower.split()) <= 2:
            return IntentDecision("general", 0.45, "too_short_general_guess")

        return IntentDecision("general", 0.7, "default_general")
