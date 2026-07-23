"""Контракт прозы volska/playbook.md — она уходит ЦЕЛИКОМ в системный промпт
brain и классификатора (brain.py:64, classifier.py:77), поэтому калибровку
поведения держим тестом на прозу: если ключевые правила исчезнут из файла,
поведение тихо поедет, а зелёные тесты demo/demo2 этого НЕ ловят.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chatter.core.escalation import parse_escalation_keywords

PLAYBOOK = (
    Path(__file__).resolve().parents[2] / "chatter" / "clients" / "volska" / "playbook.md"
).read_text(encoding="utf-8")


def test_keyword_backstop_intact():
    # детерминированный слой не должен опустеть при правках прозы
    kw = parse_escalation_keywords(PLAYBOOK)
    assert "дедлайн" in kw and "керівниц" in kw


def test_reprice_confirms_fixation_not_repeats_range():
    """Дрил volska 2026-07-22 03:22-03:23: на повторный запрос сметы ПОСЛЕ уже
    данной вилки Ольга ПОВТОРИЛА вилку (300–400/700–900) вместо подтверждения
    фиксации. Playbook обязан явно требовать: не повторять вилку, а подтвердить,
    что запрос взят в прорахунок."""
    low = PLAYBOOK.casefold()
    # шаблон подтверждения фиксации присутствует
    assert "у прорахунок" in low, "нет шаблона подтверждения фиксации прорахунку"
    # явное правило «не повторювати вилку» на повторном запросе
    assert "не повторюй" in low or "не повторюват" in low, (
        "нет правила «не повторювати вилку» на повторному запиті сметы")


# --- дрил 2026-07-23: голая вилка без вопроса и без следующего шага ----------
def test_price_answer_must_bundle_qualifying_question():
    """Дрил 2026-07-23: Ольга дважды выдала вилку цен БЕЗ уточняющего вопроса.
    Правило: на любой ценовой вопрос — вилка ИЗ knowledge + сразу вопрос по
    задаче (сфера, обсяг, терміни, що вже є). Голая цифра — дефект."""
    low = PLAYBOOK.casefold()
    assert "гола цифра" in low or "голою" in low, (
        "нет правила «вилка не йде голою» (вилка + уточнююче питання)")
    assert "уточнююче питання" in low
    for marker in ("обсяг", "терміни", "що вже є"):
        assert marker in low, f"в правиле квалификации нет оси «{marker}»"


def test_every_reply_must_end_with_next_step():
    """Ни одна реплика Ольги не заканчивается тупиком: питання / бриф /
    приклади робіт / передача керівниці."""
    low = PLAYBOOK.casefold()
    assert "наступним кроком" in low or "наступний крок" in low
    assert "бриф" in low and "приклади робіт" in low


def test_price_range_named_once_per_dialog():
    """Вилка по услуге называется ОДИН раз за диалог; повтор = ухиляння,
    вместо него — движение вперёд (уточнення / фіксація прорахунку)."""
    low = PLAYBOOK.casefold()
    assert "один раз" in low
    assert "рухай" in low or "вперед" in low or "вперёд" in low


def test_qualification_before_detailed_quote():
    """Клиент без описанной задачи получает вилку + питання, а не развёрнутую
    смету — квалификация до конкретики."""
    low = PLAYBOOK.casefold()
    assert "кваліфікація до конкретики" in low
    assert "розгорнут" in low  # «не розгорнута смета» / «розгорнуто — тільки коли…»
