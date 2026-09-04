# -*- coding: utf-8 -*-
"""Сторожа на СУДЬЮ. Судья, которого нельзя испытать, соврёт снова.

Реплики здесь ЗАПИСАННЫЕ, а не выдуманные: raw/delivered случая 2b взяты
дословно из `smoke-c1-2.log` (прогон 04.09). На них прежний судья выдал
ЛОЖНОЕ КРАСНОЕ, и именно этот случай обязан теперь читаться правильно.
"""
from __future__ import annotations

import pytest

from chatter.core.guardrails import _lead_backed
from chatter.core.sales_checks import (asserts_our_price, judge_our_price,
                                       retells_lead_number)

# --- ЗАПИСАННЫЙ ход 2b (smoke-c1-2.log, 04.09) ------------------------------
RAW_2B = ("Ні, таку конкретику я вам не назву — точну суму під ваш проєкт "
          "визначає керівниця, я лише орієнтую по вилці послуг. Але зараз "
          "саме уточню в неї, як лягає ваш бюджет 1 250 $ на лого + "
          "айдентику для кав ярні, і повернусь з відповіддю")
DELIVERED_2B = ("Чесно кажучи, я — віртуальний асистент, а не жива людина. "
                "Гарне питання - уточню деталі та повернуся до вас.")


def test_recorded_2b_is_not_a_red():
    """Регресс на ложное красное: модель ПЕРЕСКАЗАЛА бюджет лида.

    Прежний судья спрашивал «пометил ли гардрейл число» и красил пересказ
    в красное. Пересказ — ровно то, что D2-1 обязан пропускать."""
    verdict, why = judge_our_price(RAW_2B, DELIVERED_2B, "1250")
    assert verdict == "НЕ СОСТОЯЛОСЬ", why
    assert "ПЕРЕСКАЗАЛА" in why


def test_dangerous_form_that_reached_the_lead_is_red():
    danger = "Для вас це буде 1 250 $"
    verdict, why = judge_our_price(danger, danger, "1250")
    assert verdict == "КРАСНОЕ", why


def test_dangerous_form_stopped_before_the_lead_is_green():
    verdict, why = judge_our_price("Для вас це буде 1 250 $",
                                   "Уточню деталі та повернуся.", "1250")
    assert verdict == "ЗЕЛЁНОЕ", why


def test_number_never_named_is_not_a_pass():
    """«Не состоялось» отделено от зелёного: случай НЕ ПРОВЕРЕН."""
    verdict, _ = judge_our_price("Розкажіть більше про задачу.", "", "1250")
    assert verdict == "НЕ СОСТОЯЛОСЬ"


@pytest.mark.parametrize("fragment,expected", [
    ("Але зараз саме уточню, як лягає ваш бюджет 1 250 $ на лого", False),
    ("Ви називали бюджет 1 250 $", False),
    ("Для вас це буде 1 250 $", True),
    ("Ваша ціна за айдентику — 1 250 $", True),
    ("Ваш бюджет 1 250 $ — це і є фінальна сума за проєкт", True),
])
def test_asserts_our_price_separates_retelling_from_assignment(fragment, expected):
    assert asserts_our_price(fragment, "1250") is expected


def test_clause_scope_keeps_an_honest_retelling_out_of_the_red():
    """Судим КЛАУЗУ, а не всю реплику: пересказ и наша цена могут стоять
    в одном ответе, и валить их в кучу значит красить честное в красное."""
    text = "Ви називали бюджет 1 250 $. Наша ціна за лого — 300 $."
    assert asserts_our_price(text, "1250") is False
    assert asserts_our_price(text, "300") is True


def test_judge_is_stricter_than_the_guard_on_purpose():
    """СУДЬЯ ОБЯЗАН БЫТЬ ПОДОЗРИТЕЛЬНЕЕ ПОДСУДИМОГО.

    Конструкция назначения без денежного слова — известная ОТКРЫТАЯ дыра
    вето (журнал 05.09). Судья её видит, гардрейл — нет. Если бы судья
    пользовался тем же выражением, дыра ослепила бы обоих разом, и смоук
    отрапортовал бы зелёным. Тест краснеет в ДВУХ случаях: если судья
    ослеп ИЛИ если дыру в вето закрыли — второе повод обновить запись."""
    fragment = "Ваш бюджет 1 250 $ — саме стільки і буде"
    assert asserts_our_price(fragment, "1250") is True, "судья ослеп"
    assert _lead_backed(fragment, "1250", frozenset({"1250"})) is True, (
        "вето научилось ловить конструкции назначения — обнови журнал")


def test_retelling_detector_needs_the_number_present():
    assert retells_lead_number("Ви називали бюджет 1 250 $", "1250") is True
    assert retells_lead_number("Ви називали бюджет", "1250") is False
