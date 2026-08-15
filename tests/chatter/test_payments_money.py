"""Деньги — минорные целые (спека payments §14 п.1, риск 10.1).

Почему отдельный тип, а не float: `payments.amount REAL` даёт ошибку, которая
при ступенях НАКАПЛИВАЕТСЯ — остаток $0.01 и счёт, который никогда не станет
`paid`. Здесь float запрещён КОНСТРУКТИВНО, а не соглашением: конструктор его
не принимает.

DEV-26: каждый запрет проверяется тестом, который краснеет, если запрет снять.
"""
from __future__ import annotations

import pytest

from chatter.payments.money import (
    MINOR_EXPONENT, Money, MoneyError, add, format_major, from_major, subtract,
)


def test_minor_is_the_stored_unit():
    m = Money(40000, "USD")
    assert m.minor == 40000 and m.ccy == "USD"


def test_from_major_whole_and_fractional():
    assert from_major("400", "USD") == Money(40000, "USD")
    assert from_major("400.50", "USD") == Money(40050, "USD")
    assert from_major(400, "USD") == Money(40000, "USD")


def test_float_is_rejected_everywhere():
    """Главный запрет арки. float в конструкторе — это риск 10.1 целиком."""
    with pytest.raises(MoneyError):
        Money(400.0, "USD")          # type: ignore[arg-type]
    with pytest.raises(MoneyError):
        from_major(400.5, "USD")     # type: ignore[arg-type]


def test_bool_is_not_an_int_here():
    """isinstance(True, int) — True. Без явной проверки Money(True) прошёл бы
    как 1 цент и это никогда бы не всплыло."""
    with pytest.raises(MoneyError):
        Money(True, "USD")           # type: ignore[arg-type]


def test_extra_precision_is_an_error_not_a_rounding():
    """Тихое округление на деньгах — тот же класс, что молчаливый дефолт."""
    with pytest.raises(MoneyError):
        from_major("400.567", "USD")


def test_unknown_currency_rejected():
    with pytest.raises(MoneyError):
        Money(100, "XXX")
    with pytest.raises(MoneyError):
        Money(100, "$")              # символ — не ISO-код (§14 п.8)


def test_format_major_drops_empty_cents_keeps_real_ones():
    assert format_major(Money(40000, "USD")) == "400"
    assert format_major(Money(40050, "USD")) == "400.50"
    assert format_major(Money(40005, "USD")) == "400.05"


def test_arithmetic_refuses_to_mix_currencies():
    with pytest.raises(MoneyError):
        add(Money(100, "USD"), Money(100, "EUR"))
    with pytest.raises(MoneyError):
        subtract(Money(100, "USD"), Money(100, "EUR"))


def test_arithmetic_is_exact():
    """Ровно то, что ломается на float: 0.1+0.2. В минорных — точно."""
    assert add(Money(10, "USD"), Money(20, "USD")) == Money(30, "USD")
    assert subtract(Money(90000, "USD"), Money(50000, "USD")) == Money(40000, "USD")


def test_exponent_table_is_explicit():
    """Валюта без объявленной экспоненты не должна проходить молча: у JPY
    минорных единиц нет, и деление на 100 дало бы цену в 100 раз меньше."""
    assert MINOR_EXPONENT["USD"] == 2
    assert MINOR_EXPONENT["EUR"] == 2
    assert "JPY" not in MINOR_EXPONENT
