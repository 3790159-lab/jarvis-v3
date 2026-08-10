"""Прайс и сетка торга (спека §2.3, решение владельца 3).

Торг — ДИСКРЕТНАЯ СЕТКА констант, а не арифметика: у готовых чисел нет ни одной
болезни вычисленных. Пол — нижняя граница вилки, ниже нельзя НИКОГДА.

Валидатор здесь — сторож, а не документация: расхождение сетки с прайсом должно
ронять СТАРТ, иначе бот однажды назовёт цену, которой в прайсе нет (риск 10.11).
"""
from __future__ import annotations

import pytest

from chatter.payments.money import Money
from chatter.payments.pricing import (
    PricingConfigError, is_floor, load_pricing, next_step, step_at,
)

KNOWLEDGE = """
# Послуги та ціни
- Створення логотипа — 300–400 $
- Рефайн (редизайн) логотипа — 200 $
"""

RAW = {
    "amount_source": "price_upper",
    "positions": {
        "logo_create": {
            "title": "Створення логотипа",
            "currency": "USD",
            "price_range": [300, 400],
            "ladder": [
                {"amount": 400, "scope_key": "logo_full"},
                {"amount": 375, "scope_key": "logo_s375"},
                {"amount": 350, "scope_key": "logo_s350"},
                {"amount": 325, "scope_key": "logo_s325"},
                {"amount": 300, "scope_key": "logo_floor"},
            ],
        },
        "logo_refine": {                      # позиция БЕЗ сетки — торг невозможен
            "title": "Рефайн логотипа",
            "currency": "USD",
            "price_range": [200, 200],
            "ladder": [{"amount": 200, "scope_key": "refine_full"}],
        },
    },
}


def _raw(**over):
    import copy
    raw = copy.deepcopy(RAW)
    raw.update(over)
    return raw


def test_loads_positions_with_money_amounts():
    p = load_pricing(RAW, knowledge=KNOWLEDGE)
    pos = p.positions["logo_create"]
    assert pos.currency == "USD"
    assert step_at(pos, 0).amount == Money(40000, "USD")
    assert step_at(pos, 0).scope_key == "logo_full"


def test_top_step_is_the_upper_bound_because_amount_source_is_price_upper():
    """Решение владельца 1: бот называет ВЕРХНЮЮ границу. Ступень 0 обязана ей
    быть — иначе первое же названное число разойдётся с политикой."""
    p = load_pricing(RAW, knowledge=KNOWLEDGE)
    assert p.amount_source == "price_upper"
    assert p.positions["logo_create"].top.amount == Money(40000, "USD")


def test_floor_is_the_lower_bound_and_is_recognised():
    p = load_pricing(RAW, knowledge=KNOWLEDGE)
    pos = p.positions["logo_create"]
    assert pos.floor.amount == Money(30000, "USD")
    assert is_floor(pos, 4) is True
    assert is_floor(pos, 3) is False


def test_next_step_walks_down_one_rung_at_a_time():
    """§2.3: шаг только на ОДНУ ступень за ход. Прыжок через ступень — это
    подарок, которого никто не просил."""
    p = load_pricing(RAW, knowledge=KNOWLEDGE)
    pos = p.positions["logo_create"]
    assert next_step(pos, 0).amount == Money(37500, "USD")
    assert next_step(pos, 3).amount == Money(30000, "USD")


def test_below_the_floor_there_is_nothing():
    """Пол не пробивается. None — это «зови владельца», а не «придумай сам»."""
    p = load_pricing(RAW, knowledge=KNOWLEDGE)
    assert next_step(p.positions["logo_create"], 4) is None


def test_single_step_position_has_no_bargaining_room():
    p = load_pricing(RAW, knowledge=KNOWLEDGE)
    pos = p.positions["logo_refine"]
    assert is_floor(pos, 0) is True
    assert next_step(pos, 0) is None


# --- валидатор: всё ниже обязано ронять СТАРТ -------------------------------

def test_ladder_top_must_equal_price_range_upper():
    raw = _raw()
    raw["positions"]["logo_create"]["ladder"][0]["amount"] = 420
    with pytest.raises(PricingConfigError, match="верх"):
        load_pricing(raw, knowledge=KNOWLEDGE)


def test_ladder_floor_must_equal_price_range_lower():
    raw = _raw()
    raw["positions"]["logo_create"]["ladder"][-1]["amount"] = 250
    with pytest.raises(PricingConfigError, match="пол"):
        load_pricing(raw, knowledge=KNOWLEDGE)


def test_range_bounds_must_be_literals_in_knowledge():
    """Правило №5 с технической гарантией: если границы нет в knowledge, бот
    назовёт число, которого клиент нигде не публиковал."""
    with pytest.raises(PricingConfigError, match="knowledge"):
        load_pricing(RAW, knowledge="# Послуги та ціни\n- Створення логотипа — за домовленістю\n")


def test_intermediate_steps_need_not_be_in_knowledge():
    """Осознанное исключение: промежуточные ступени — внутренние переговорные
    и в модель НЕ попадают вовсе (§14 п.16), поэтому обеспечивать их knowledge
    не требуется. Верх и пол — обязаны, они публичные."""
    load_pricing(RAW, knowledge=KNOWLEDGE)   # 375/350/325 в KNOWLEDGE нет — и это норма


def test_ladder_must_descend_strictly():
    raw = _raw()
    raw["positions"]["logo_create"]["ladder"][2]["amount"] = 380
    with pytest.raises(PricingConfigError, match="убыв"):
        load_pricing(raw, knowledge=KNOWLEDGE)


def test_every_step_must_carry_a_distinct_scope():
    """Без своего объёма ступень — это «молча дешевле» (риск 10.12): цена вниз,
    обмена нет. Одинаковый scope на двух ступенях — та же болезнь."""
    raw = _raw()
    raw["positions"]["logo_create"]["ladder"][1]["scope_key"] = "logo_full"
    with pytest.raises(PricingConfigError, match="scope"):
        load_pricing(raw, knowledge=KNOWLEDGE)

    raw = _raw()
    del raw["positions"]["logo_create"]["ladder"][1]["scope_key"]
    with pytest.raises(PricingConfigError, match="scope"):
        load_pricing(raw, knowledge=KNOWLEDGE)


def test_currency_must_be_iso_not_symbol():
    raw = _raw()
    raw["positions"]["logo_create"]["currency"] = "$"
    with pytest.raises(PricingConfigError):
        load_pricing(raw, knowledge=KNOWLEDGE)


def test_empty_ladder_is_an_error():
    raw = _raw()
    raw["positions"]["logo_create"]["ladder"] = []
    with pytest.raises(PricingConfigError):
        load_pricing(raw, knowledge=KNOWLEDGE)


def test_unknown_amount_source_rejected():
    with pytest.raises(PricingConfigError):
        load_pricing(_raw(amount_source="lower_bound"), knowledge=KNOWLEDGE)
