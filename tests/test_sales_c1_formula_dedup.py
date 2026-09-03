# -*- coding: utf-8 -*-
"""C-1 / D2-3: одна формула редакции — ОДИН раз на ответ.

Спека sales-competence §1.1. Из живой стенограммы:

    …SMM-ведення (750–900 $/мес), точний термін узгоджуємо індивідуально,
    зафіксувати KPI під ваші цифри (точну вартість узгоджуємо індивідуально)
    точну вартість узгоджуємо індивідуально…

Отчёт засчитал это как «тройной семантический повтор модели». Это не модель:
три фрагмента — дословные константы `_REDACTION_DEADLINE`/`_REDACTION_PRICE`,
которые вставил НАШ редактор. Значит и лечится не семантическим дедупом, а
запретом ставить одну и ту же формулу дважды в одной реплике.

Вторая и последующие вставки СХЛОПЫВАЮТСЯ: клауза удаляется вместе с формулой,
а не заменяется на неё (§4 D2-3).
"""
from __future__ import annotations

from chatter.core.guardrails import redact_unbacked

KNOWLEDGE = "Хімчистка салону - 4 500 грн."


def test_three_identical_deadline_formulas_collapse_to_one():
    res = redact_unbacked(
        "Зробимо за 3 дні, встигнемо за 5 днів, закриємо за 7 днів.",
        KNOWLEDGE, language="uk")
    assert res.text.count("узгоджуємо індивідуально") == 1
    assert res.text == "Точний термін узгоджуємо індивідуально."
    assert res.clean is True


def test_collapse_keeps_the_audit_trail_of_every_cut():
    """Схлопнутая клауза всё равно ВЫРЕЗАНА — след обязан остаться, иначе
    аудит покажет одно удаление вместо трёх."""
    res = redact_unbacked(
        "Зробимо за 3 дні, встигнемо за 5 днів, закриємо за 7 днів.",
        KNOWLEDGE, language="uk")
    assert len({r.number for r in res.records}) == 3


def test_different_formulas_both_survive():
    """Дедуп по ФОРМУЛЕ, а не «оставить одну замену на ответ»: срок и цена —
    разные факты, и схлопывать их вместе значило бы врать."""
    res = redact_unbacked(
        "Зробимо за 3 дні, а вартість буде 9 900 грн.",
        KNOWLEDGE, language="uk")
    assert res.text.count("Точний термін узгоджуємо індивідуально") == 1
    assert res.text.count("точну вартість узгоджуємо індивідуально") == 1


def test_single_formula_reply_is_unchanged():
    """Негативный контроль: обычный одиночный случай обязан остаться прежним."""
    res = redact_unbacked("Зробимо за 3 дні.", KNOWLEDGE, language="uk")
    assert res.text == "Точний термін узгоджуємо індивідуально."


def test_capitalised_first_and_lowercase_second_are_the_SAME_formula():
    """Дедуп сравнивает формулу, а не её написание: первая вставка в начале
    предложения приходит с большой буквы, и наивное сравнение строк пропустило
    бы повтор."""
    res = redact_unbacked(
        "За 3 дні зробимо, потім закриємо за 7 днів.", KNOWLEDGE, language="uk")
    assert res.text.lower().count("точний термін узгоджуємо індивідуально") == 1
