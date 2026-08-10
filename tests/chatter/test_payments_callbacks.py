"""Кодек callback_data: реестр версий, а не цепочка startswith (§14 пп.1, 10).

Две причины, по которым это отдельный модуль с версиями:

1. **Старые кнопки живут вечно.** Кнопка, улетевшая в Telegram сегодня,
   тапабельна через месяцы. Любая смена формата обязывает парсер понимать
   legacy — навсегда. Это единственная неустранимая переделка арки (§14.1), и
   дешёвой она остаётся только при реестре версий.
2. **Действия по счёту адресуют invoice_id, а не contact_id.** При
   `per_contact_invoice_cap: 3` кнопка ✅ «Выставить» на contact_id не знает,
   какой счёт утверждать. Вводится в Ф0, когда счёт заведомо один: задним
   числом формат сменить нельзя.
"""
from __future__ import annotations

import pytest

from chatter.payments.callbacks import (
    CALLBACK_LIMIT, CallbackFormatError, InvoiceAction, PaidAction, ParsedCallback,
    build_invoice_action, build_paid_amount, parse_callback,
)
from chatter.payments.money import Money

CID = "8849893367:volska"


# ── legacy: обязано пониматься навсегда ────────────────────────────────────

def test_legacy_paid_without_amount():
    got = parse_callback(f"paid:{CID}")
    assert isinstance(got, PaidAction)
    assert got.contact_id == CID and got.amount is None


def test_legacy_paid_with_dollar_amount():
    """Формат v1 нёс сумму в МАЖОРНЫХ единицах строкой. Кнопки этого формата
    уже в истории чата — понимать их придётся всегда."""
    got = parse_callback(f"paidamt:750:{CID}")
    assert got.amount == Money(75000, "USD")
    assert got.version == 1


def test_legacy_fractional_amount():
    """750.29 выбрано не случайно: float(\"750.29\") * 100 == 75028.999…, и
    разбор через float дал бы 75028 — цент, потерянный молча. На 750.50 тест
    был бы слеп (проверено мутацией DEV-26)."""
    assert parse_callback(f"paidamt:750.29:{CID}").amount == Money(75029, "USD")
    assert parse_callback(f"paidamt:750.50:{CID}").amount == Money(75050, "USD")


def test_legacy_comma_decimal_is_accepted():
    """Владелец печатает сумму с телефона; запятая — норма для украинской
    раскладки."""
    assert parse_callback(f"paidamt:750,50:{CID}").amount == Money(75050, "USD")


# ── v2: минорные целые + валюта ────────────────────────────────────────────

def test_v2_carries_minor_units_and_currency():
    got = parse_callback(f"paidamt2:75050:USD:{CID}")
    assert got.amount == Money(75050, "USD") and got.version == 2
    assert got.contact_id == CID


def test_v2_round_trip():
    data = build_paid_amount(Money(40000, "USD"), CID)
    got = parse_callback(data)
    assert got.amount == Money(40000, "USD") and got.contact_id == CID


def test_builder_emits_the_current_version_not_legacy():
    assert build_paid_amount(Money(40000, "USD"), CID).startswith("paidamt2:")


# ── действия по счёту адресуют invoice_id ──────────────────────────────────

@pytest.mark.parametrize("kind", ["inv_ok", "inv_edit", "inv_no"])
def test_invoice_actions_carry_the_invoice_id(kind):
    got = parse_callback(f"{kind}:INV-volska-000123")
    assert isinstance(got, InvoiceAction)
    assert got.invoice_id == "INV-volska-000123" and got.kind == kind


def test_invoice_action_round_trip():
    data = build_invoice_action("inv_ok", "INV-volska-000123")
    assert parse_callback(data) == InvoiceAction(kind="inv_ok",
                                                 invoice_id="INV-volska-000123")


def test_invoice_actions_are_not_addressed_by_contact():
    """Сторож против возврата к contact_id: при трёх открытых счетах на контакт
    такая кнопка не знает, какой счёт утверждать."""
    got = parse_callback(f"inv_ok:{CID}")
    assert got is None or getattr(got, "invoice_id", "").startswith("INV-"), (
        "действие по счёту приняло contact_id за invoice_id")


# ── границы ────────────────────────────────────────────────────────────────

def test_unknown_prefix_is_none_not_a_guess():
    for data in ("", "paidamt", "wat:1", "paid", ":", "paidamt2:75050:USD"):
        assert parse_callback(data) is None


def test_broken_amount_is_none_not_zero():
    """Нулевая сумма от мусора — это записанная оплата на 0, которую никто не
    заметит. Отказ обязан быть отличим от успеха."""
    for data in (f"paidamt:abc:{CID}", f"paidamt2:abc:USD:{CID}",
                 f"paidamt:-50:{CID}", f"paidamt2:-5000:USD:{CID}",
                 f"paidamt:0:{CID}"):
        assert parse_callback(data) is None, data


def test_unknown_currency_is_rejected():
    assert parse_callback(f"paidamt2:75050:XXX:{CID}") is None


def test_built_data_fits_the_telegram_limit():
    """callback_data > 64 байт Telegram отвергает — кнопка просто не работает,
    и узнаёшь об этом от владельца."""
    data = build_paid_amount(Money(999999999, "USD"), "8849893367:volska")
    assert len(data.encode("utf-8")) <= CALLBACK_LIMIT
    assert len(build_invoice_action("inv_edit", "INV-volska-999999").encode()) <= CALLBACK_LIMIT


def test_builder_refuses_to_emit_oversized_data():
    """Лучше упасть у нас, чем отдать владельцу мёртвую кнопку."""
    with pytest.raises(CallbackFormatError):
        build_invoice_action("inv_ok", "INV-" + "x" * 100)


def test_contact_id_with_colons_survives_the_round_trip():
    """contact_id САМ содержит двоеточие — из-за этого сумма и стоит перед ним."""
    got = parse_callback(build_paid_amount(Money(100, "USD"), "1:2:3:volska"))
    assert got.contact_id == "1:2:3:volska"


def test_every_declared_version_is_reachable_from_the_registry():
    """Реестр — единственный источник версий: ветка, не попавшая в него,
    существовать не должна."""
    versions = {p.version for p in (parse_callback(f"paidamt:750:{CID}"),
                                    parse_callback(f"paidamt2:75000:USD:{CID}"))}
    assert versions == {1, 2}


def test_parsed_callback_is_a_closed_set_of_shapes():
    for data in (f"paid:{CID}", f"paidamt2:1:USD:{CID}", "inv_ok:INV-volska-000001"):
        assert isinstance(parse_callback(data), ParsedCallback)
