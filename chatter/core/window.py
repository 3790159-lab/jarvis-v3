"""Окно истории ПО ТОКЕНАМ (арка «память+стоимость», условие 2 Даниила).

Бюджет в токенах, а не в сообщениях: N=24 коротких реплик и N=24 длинных —
разный объём, а бюджет промпта один. Fallback на количество (max_messages)
страхует от моря микро-реплик, тащащего старьё.

Зафиксированное поведение для гигантского сообщения: последнее сообщение
включается ВСЕГДА и целиком, даже если оно само больше бюджета — резать
реплику лида посреди нельзя, бюджет в этом случае осознанно превышен.
"""
from __future__ import annotations

# ~3 символа/токен для кириллицы: калибровка по замеру 2026-07-23
# (~140 ток на ~420 симв обмена в llm_usage).
_CHARS_PER_TOKEN = 3


def estimate_tokens(text: str) -> int:
    """Локальная оценка без токенайзера (сеть здесь недопустима)."""
    return len(text) // _CHARS_PER_TOKEN + 1


def select_window(history: list[dict], *, budget_tokens: int,
                  max_messages: int) -> list[dict]:
    """Хвост истории, влезающий в бюджет токенов И в лимит сообщений.
    Порядок сохранён; возвращается непрерывный суффикс history."""
    if not history:
        return []
    picked = 0
    used = 0
    for m in reversed(history):
        cost = estimate_tokens(m["text"])
        if picked > 0 and (used + cost > budget_tokens
                           or picked + 1 > max_messages):
            break
        picked += 1
        used += cost
        if used > budget_tokens:  # первое (последнее по времени) превысило само
            break
    return history[-picked:]
