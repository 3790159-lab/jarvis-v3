"""Деньги в МИНОРНЫХ ЦЕЛЫХ (спека §14 п.1, риск 10.1).

Почему тип, а не соглашение «храним центы в int»: соглашение держится ровно до
первого `amount / 100` в чужом модуле. Здесь float отвергается КОНСТРУКТОРОМ, и
ошибка всплывает в момент написания кода, а не через месяц остатком в $0.01,
из-за которого счёт никогда не станет `paid`.

Существующая `payments.amount REAL` перестраивается в Ф0 (окно: 0 строк в
боевой БД на 2026-08-10), поэтому дуального представления суммы в проекте не
заводится вовсе.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Валюты объявляются ЯВНО вместе с экспонентой. Молчаливый дефолт «везде 2»
# сломался бы на JPY (минорных единиц нет) ценой в 100 раз меньше — ровно тот
# класс бага, что молчаливый оптимистичный дефолт в §1.3.
MINOR_EXPONENT: dict[str, int] = {
    "USD": 2,
    "EUR": 2,
    "UAH": 2,
}


class MoneyError(ValueError):
    """Любая попытка обойти минорные целые или смешать валюты."""


@dataclass(frozen=True)
class Money:
    minor: int          # ЦЕЛОЕ в минорных единицах: 400 $ == 40000
    ccy: str            # ISO-4217, НЕ символ (§14 п.8)

    def __post_init__(self) -> None:
        # bool — подкласс int, и Money(True) прошёл бы как 1 цент незаметно.
        if isinstance(self.minor, bool) or not isinstance(self.minor, int):
            raise MoneyError(
                f"сумма обязана быть целым в минорных единицах, получено "
                f"{type(self.minor).__name__}={self.minor!r}")
        if self.ccy not in MINOR_EXPONENT:
            raise MoneyError(
                f"неизвестная валюта {self.ccy!r}: нужен ISO-4217 из "
                f"{sorted(MINOR_EXPONENT)}, а не символ")


def _exp(ccy: str) -> int:
    if ccy not in MINOR_EXPONENT:
        raise MoneyError(f"неизвестная валюта {ccy!r}")
    return MINOR_EXPONENT[ccy]


def from_major(value: str | int, ccy: str) -> Money:
    """Из «400» / «400.50» / 400 в минорные. float НЕ принимается: он и есть
    источник риска 10.1, и принять его — значит впустить неточность внутрь."""
    if isinstance(value, bool) or isinstance(value, float):
        raise MoneyError(
            f"float на деньгах запрещён (риск 10.1), получено {value!r}; "
            f"передавай строку или целое")
    exp = _exp(ccy)
    try:
        dec = Decimal(str(value))
    except InvalidOperation as exc:
        raise MoneyError(f"непарсимая сумма {value!r}") from exc
    scaled = dec.scaleb(exp)
    if scaled != scaled.to_integral_value():
        # Тихое округление на деньгах — тот же класс, что молчаливый дефолт:
        # оно превращает ошибку конфига в незаметную потерю копеек.
        raise MoneyError(
            f"{value!r} не выражается целым числом минорных единиц {ccy} "
            f"(экспонента {exp}); округление здесь запрещено")
    return Money(int(scaled), ccy)


def format_major(m: Money) -> str:
    """Для показа человеку: «400», «400.50». Пустые копейки не печатаются —
    цена в диалоге должна выглядеть как в прайсе."""
    exp = _exp(m.ccy)
    dec = (Decimal(m.minor).scaleb(-exp)).normalize()
    if dec == dec.to_integral_value():
        return str(dec.quantize(Decimal(1)))
    return f"{dec:.{exp}f}"


def _same_ccy(a: Money, b: Money) -> str:
    if a.ccy != b.ccy:
        raise MoneyError(
            f"смешаны валюты {a.ccy} и {b.ccy}: конверсия — не арифметика, "
            f"курс известен только по факту зачисления (§10.4)")
    return a.ccy


def add(a: Money, b: Money) -> Money:
    return Money(a.minor + b.minor, _same_ccy(a, b))


def subtract(a: Money, b: Money) -> Money:
    return Money(a.minor - b.minor, _same_ccy(a, b))
