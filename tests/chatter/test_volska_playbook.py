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
