"""Кодек `callback_data` — РЕЕСТР ВЕРСИЙ формата (§14 пп.1, 10).

Почему реестр, а не цепочка `startswith`: кнопка, улетевшая в Telegram, живёт
в истории чата вечно и тапабельна через месяцы. Смена формата обязывает парсер
понимать legacy НАВСЕГДА — это единственная переделка арки, которую устранить
нельзя (§14.1). Дешёвой она остаётся, только если версии объявлены явно и
каждая новая — это строка в таблице, а не ещё одна ветка в разросшемся if'е.

Второе решение: действия по счёту адресуют `invoice_id`, а не `contact_id`. При
`per_contact_invoice_cap: 3` кнопка ✅ «Выставить», адресованная контакту, не
знает, какой из трёх счетов утверждать. Формат вводится в Ф0, пока счёт заведомо
один, потому что задним числом его не сменить.
"""
from __future__ import annotations

from dataclasses import dataclass

from chatter.payments.money import MINOR_EXPONENT, Money, MoneyError, from_major

# Telegram отвергает callback_data длиннее 64 БАЙТ. Кнопка с превышением не
# ломается громко — она просто не работает, и узнаёшь об этом от владельца.
CALLBACK_LIMIT = 64

INVOICE_ACTIONS: tuple[str, ...] = ("inv_ok", "inv_edit", "inv_no")


class CallbackFormatError(ValueError):
    """Собрать callback_data невозможно (превышение лимита, битые данные)."""


@dataclass(frozen=True)
class ParsedCallback:
    """Общий предок: вызыватель различает формы по isinstance, а не по строке."""


@dataclass(frozen=True)
class PaidAction(ParsedCallback):
    contact_id: str
    amount: Money | None
    version: int


@dataclass(frozen=True)
class InvoiceAction(ParsedCallback):
    kind: str
    invoice_id: str


def _parse_paid_v1(rest: str) -> PaidAction | None:
    """v1: `paid:<contact_id>` — факт без суммы."""
    if not rest:
        return None
    return PaidAction(contact_id=rest, amount=None, version=1)


def _parse_paidamt_v1(rest: str) -> PaidAction | None:
    """v1: `paidamt:<major>:<contact_id>` — сумма МАЖОРНОЙ строкой.

    Сумма стоит ПЕРЕД contact_id, потому что сам contact_id содержит двоеточие
    и разобрать хвост нечем."""
    raw, sep, contact_id = rest.partition(":")
    if not sep or not contact_id:
        return None
    try:
        amount = from_major(raw.replace(",", ".").strip(), "USD")
    except MoneyError:
        return None
    if amount.minor <= 0:
        return None
    return PaidAction(contact_id=contact_id, amount=amount, version=1)


def _parse_paidamt_v2(rest: str) -> PaidAction | None:
    """v2: `paidamt2:<minor>:<ccy>:<contact_id>` — минорные целые и валюта.

    Мажорная строка v1 была неоднозначна ровно там, где это дорого: «750»
    у валюты с другой экспонентой значит другую сумму."""
    raw, sep, tail = rest.partition(":")
    if not sep:
        return None
    ccy, sep2, contact_id = tail.partition(":")
    if not sep2 or not contact_id or ccy not in MINOR_EXPONENT:
        return None
    try:
        minor = int(raw)
    except ValueError:
        return None
    if minor <= 0:
        return None
    return PaidAction(contact_id=contact_id, amount=Money(minor, ccy), version=2)


def _parse_invoice(kind: str):
    def _inner(rest: str) -> InvoiceAction | None:
        # Только наш формат id: иначе кнопка, адресованная контакту, тихо
        # проехала бы как «счёт с таким id» и утвердила бы не то.
        if not rest.startswith("INV-"):
            return None
        return InvoiceAction(kind=kind, invoice_id=rest)
    return _inner


# Реестр: префикс → разбор. Новая версия формата — строка здесь, а не ветка.
_REGISTRY: dict[str, object] = {
    "paid": _parse_paid_v1,
    "paidamt": _parse_paidamt_v1,
    "paidamt2": _parse_paidamt_v2,
    **{k: _parse_invoice(k) for k in INVOICE_ACTIONS},
}


def parse_callback(data: str | None) -> ParsedCallback | None:
    """Разобрать callback_data. `None` = не наш формат или битые данные.

    Никаких догадок: мусорная сумма даёт None, а не 0 — записанная оплата на 0
    выглядела бы успехом и не была бы замечена (DEV-18)."""
    head, sep, rest = (data or "").partition(":")
    if not sep:
        return None
    handler = _REGISTRY.get(head)
    if handler is None:
        return None
    return handler(rest)


def _guard_limit(data: str) -> str:
    if len(data.encode("utf-8")) > CALLBACK_LIMIT:
        raise CallbackFormatError(
            f"callback_data {len(data.encode('utf-8'))} байт > {CALLBACK_LIMIT}: "
            f"Telegram отвергнет кнопку молча, лучше упасть здесь")
    return data


def build_paid_amount(amount: Money, contact_id: str) -> str:
    """Собрать кнопку ТЕКУЩЕЙ версии. v1 не собирается никогда — только
    разбирается."""
    return _guard_limit(f"paidamt2:{amount.minor}:{amount.ccy}:{contact_id}")


def build_invoice_action(kind: str, invoice_id: str) -> str:
    if kind not in INVOICE_ACTIONS:
        raise CallbackFormatError(f"неизвестное действие по счёту {kind!r}")
    if not invoice_id.startswith("INV-"):
        raise CallbackFormatError(f"{invoice_id!r} не похож на id счёта")
    return _guard_limit(f"{kind}:{invoice_id}")
