from __future__ import annotations
import re
from chatter.storage.db import Store

_NUMBER = re.compile(r"\d[\d\s.,]*\d|\d")

_CURRENCY = r"(?:руб\w*|₽|\$|€|грн\w*|тыс\w*|евро\w*|доллар\w*)"
_PRICE_WORD = r"(?:цен\w*|сто[ий]\w*|стоимост\w*|скидк\w*|предоплат\w*)"
_TIME_UNIT = (
    r"(?:дн(?:я|ей|ь)?\w*|недел\w*|час\w*|месяц\w*|"
    r"январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]\w*|июн\w*|июл\w*|"
    r"август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)"
)
_WEEKDAY = (
    r"(?:понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресен\w*)"
)

# Stems used to check whether a *bare* (number-less) temporal claim is backed
# by knowledge.md, sorted longest-first so a specific stem wins over a
# shorter one that happens to be a substring of it.
_TIME_UNIT_STEMS = sorted(
    [
        "недел", "месяц", "январ", "феврал", "август", "сентябр", "октябр",
        "ноябр", "декабр", "апрел", "март", "июн", "июл", "час", "дн", "ма",
    ],
    key=len, reverse=True,
)
_WEEKDAY_STEMS = [
    "понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскресен",
]

_PRICE_CONTEXT = re.compile(
    rf"(?:{_CURRENCY}|{_PRICE_WORD}|%|\bза\s+\d)", re.IGNORECASE
)
_DEADLINE_NUM = re.compile(
    rf"\b(?:за|через|к)\s+(\d[\d\s.,]*\d|\d)\s*{_TIME_UNIT}", re.IGNORECASE
)
_DEADLINE_NO_NUM = re.compile(
    rf"\b(?:за|через)\s+({_TIME_UNIT})", re.IGNORECASE
)
_WEEKDAY_DEADLINE = re.compile(rf"\bк\s+({_WEEKDAY})", re.IGNORECASE)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _numbers(text: str) -> set[str]:
    return {re.sub(r"\s", "", m.group()) for m in _NUMBER.finditer(text or "")}


def _split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT.split(text or "") if s.strip()]


def _stem_of(word: str, stems: list[str]) -> str | None:
    low = word.lower()
    return next((s for s in stems if s in low), None)


def contains_unbacked_claim(reply: str, knowledge: str) -> bool:
    """Safety-net guardrail (spec §7): flags `reply` only when it makes a
    concrete, unbacked PRICE/DISCOUNT or DEADLINE claim, or states a large
    bare number, that `knowledge` does not support:

    1. Price/discount context (currency token, price word, '%', or a number
       preceded by "за") with a number not present in knowledge.
    2. Deadline context: "за/через/к <N> <time-unit>" with N not in
       knowledge, OR a bare "за/через <time-unit>" / "к <weekday>" whose
       unit/weekday isn't mentioned anywhere in knowledge at all.
    3. Bare-number backstop: any number >= 100 not present in knowledge.

    Small incidental counts (< 100) with no price/deadline wording are left
    alone. This is a NET for whatever the system prompt's "don't invent
    prices/deadlines" instruction missed -- not the primary defense.
    """
    text = reply or ""
    known_numbers = _numbers(knowledge)
    knowledge_lower = (knowledge or "").lower()

    for m in _WEEKDAY_DEADLINE.finditer(text):
        stem = _stem_of(m.group(1), _WEEKDAY_STEMS)
        if stem and stem not in knowledge_lower:
            return True

    for m in _DEADLINE_NUM.finditer(text):
        num = re.sub(r"\s", "", m.group(1))
        if num not in known_numbers:
            return True

    for m in _DEADLINE_NO_NUM.finditer(text):
        stem = _stem_of(m.group(1), _TIME_UNIT_STEMS)
        if stem and stem not in knowledge_lower:
            return True

    for sentence in _split_sentences(text):
        if _PRICE_CONTEXT.search(sentence):
            for num in _numbers(sentence):
                if num not in known_numbers:
                    return True

    for num in _numbers(text):
        digits = re.sub(r"\D", "", num)
        if digits and int(digits) >= 100 and num not in known_numbers:
            return True

    return False


def within_hourly_limit(store: Store, contact_id: str, *, now: float, limit: int) -> bool:
    sent = store.count_messages_since(contact_id, role="assistant", since_ts=now - 3600.0)
    return sent < limit


def within_daily_cap(store: Store, *, now: float, cap: int) -> bool:
    day_start = now - (now % 86400.0)
    sent = store.count_outbound_between(day_start, day_start + 86400.0)
    return sent < cap
