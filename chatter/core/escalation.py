"""Эскалация: детерминированный слой + оживление воронки (арка 3B).

ЧИСТЫЙ модуль — ноль сети, ноль LLM, ноль Telethon. Классификатор (LLM) живёт
отдельно в classifier.py. Здесь только бесплатные детерминированные триггеры,
которые работают, даже если классификатор/сеть лежат (спека §4, слой 1), и
тонкая обёртка над мёртвым `conversation.next_state` (§5).

ШОВ (§8): этот модуль ВЫЗЫВАЕТ `disclosure.is_bot_question`,
`guardrails.contains_unbacked_claim` и `conversation.next_state`, но НЕ правит
ни один из пяти core-файлов.
"""
from __future__ import annotations

from dataclasses import dataclass

# Заголовки секции ключевых слов по языкам (settings.language). Значение
# заголовка не важно для парсинга по существу — важно найти начало списка.
_KEYWORD_HEADINGS = (
    "ключевые слова эскалации",   # ru
    "escalation keywords",        # en
    "ключові слова ескалації",    # uk
)


def parse_escalation_keywords(playbook: str) -> list[str]:
    """Достаёт детерминированные ключевые слова из playbook.md (§4: «Всё из
    playbook.md, не хардкод»).

    Конвенция: секция `## <один из _KEYWORD_HEADINGS>`, дальше пункты списка
    `- слово`, до следующего заголовка (`#`). Отсутствие секции → `[]` (слой
    ключевых слов просто выключен, это не ошибка). Слова casefold'ятся, чтобы
    матч был регистронезависимым.
    """
    lines = (playbook or "").splitlines()
    out: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().casefold()
            in_section = heading in _KEYWORD_HEADINGS
            continue
        if not in_section:
            continue
        if stripped.startswith("- "):
            word = stripped[2:].strip()
            if word:
                out.append(word.casefold())
    return out
