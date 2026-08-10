# -*- coding: utf-8 -*-
"""Блок счёта в промпте и подстановка ПОСЛЕ модели (§8.3, §14 пп.13, 16).

Две вещи, ради которых существует этот модуль.

**Блок счёта.** `render_slot_block` инъектит в brain только `owed_by=bot`, а долг
оплаты — `owed_by=client`: без отдельного блока модель о неоплаченном счёте не
узнаёт ВООБЩЕ. Блок идёт отдельным куском ПОСЛЕ cache-breakpoint'а: в стабильном
префиксе изменчивое убивает кэш (регрессия 23.07).

**Подстановка после модели.** Модель, увидевшая IBAN, может его «поправить» —
переставить символ, дописать пробел, перевести. Один символ IBAN — это деньги,
ушедшие не туда (риск 10.5). Единственная надёжная защита: модель этих символов
не видит, она пишет плейсхолдер, а подставляет код — и ПОСЛЕ guardrails, иначе
`large_number` вырежет подставленную сумму как необеспеченную (§14 п.16).

Следствие, которое здесь и закреплено: ответ с неподставленным плейсхолдером
подавляется ЦЕЛИКОМ. Полуотрендеренные реквизиты лиду уходить не должны.
"""
from __future__ import annotations

import pytest

from chatter.payments.prompt import (
    UnsubstitutedPlaceholder, find_placeholders, format_due, missing_values,
    render_invoice_block, substitute)

DAY = 86400.0
NOW = 1_786_000_000.0


def _inv(status="issued", due_ts=NOW + 3 * DAY, amount_total=90000):
    return {"invoice_id": "INV-volska-000012", "status": status,
            "amount_total": amount_total, "currency": "USD", "due_ts": due_ts}


# ── плейсхолдеры ───────────────────────────────────────────────────────────

def test_finds_the_three_phase0_placeholders():
    text = "Сума {AMOUNT}, переказ на {REQUISITES} до {DUE}."
    assert find_placeholders(text) == ["AMOUNT", "REQUISITES", "DUE"]


def test_lowercase_braces_are_not_placeholders():
    """Плейсхолдер — служебная форма, а не любая фигурная скобка: «{смайл}» в
    тексте лида не имеет права подавить ответ."""
    assert find_placeholders("{смайл} {amount}") == []


def test_substitute_replaces_known_names():
    out = substitute("до {DUE} на {REQUISITES}", {"DUE": "18:00", "REQUISITES": "UA00"})
    assert out == "до 18:00 на UA00"


def test_missing_values_names_what_is_missing():
    assert missing_values("{AMOUNT} на {REQUISITES}", {"AMOUNT": "900 USD"}) == ["REQUISITES"]


def test_unknown_placeholder_counts_as_missing():
    """Модель придумала {PHONE} — значения нет, и это тоже повод подавить:
    «почти отрендеренный» ответ хуже, чем никакой."""
    assert missing_values("напиши на {PHONE}", {"AMOUNT": "900 USD"}) == ["PHONE"]


def test_braces_inside_a_substituted_value_do_not_suppress_the_reply():
    """Проверка нехватки идёт по тексту ДО подстановки, и подстановка
    однопроходная. Иначе реквизиты с фигурной скобкой роняли бы каждый ответ, а
    значение из конфига могло бы подставить себя второй раз."""
    values = {"REQUISITES": "IBAN {UA00}"}
    assert missing_values("на {REQUISITES}", values) == []
    assert substitute("на {REQUISITES}", values) == "на IBAN {UA00}"


# ── блок счёта ─────────────────────────────────────────────────────────────

def test_no_invoice_no_block():
    assert render_invoice_block(None, now=NOW) == ""


def test_paid_invoice_does_not_hang_in_the_prompt():
    """Закрытый счёт — не контекст, а шум: модель напоминала бы об оплаченном."""
    assert render_invoice_block(_inv(status="paid"), now=NOW) == ""


def test_cancelled_invoice_does_not_hang_in_the_prompt():
    assert render_invoice_block(_inv(status="cancelled"), now=NOW) == ""


def test_open_invoice_tells_the_model_the_debt_is_on_the_client():
    block = render_invoice_block(_inv(), now=NOW)
    assert block.strip()
    assert "{AMOUNT}" in block and "{REQUISITES}" in block and "{DUE}" in block


def test_block_carries_no_digits_at_all():
    """Приёмка §8.5 п.3: промпт, ушедший в модель, не содержит ни суммы, ни
    реквизитов. Номер счёта тоже не едет — он состоит из цифр и не нужен модели."""
    block = render_invoice_block(_inv(), now=NOW)
    assert not any(ch.isdigit() for ch in block), block


def test_awaiting_owner_invoice_does_not_promise_a_deadline():
    """Сумму ещё не подтвердила владелица — обещать срок оплаты не с чего."""
    block = render_invoice_block(_inv(status="awaiting_owner", amount_total=None,
                                      due_ts=None), now=NOW)
    assert block.strip()
    assert "{DUE}" not in block and "{AMOUNT}" not in block


def test_overdue_invoice_is_still_a_block():
    assert render_invoice_block(_inv(status="overdue"), now=NOW).strip()


# ── срок с часом (§8.4) ────────────────────────────────────────────────────

def test_due_is_rendered_with_the_hour_and_the_weekday():
    """Требование владельца: срок С ЧАСОМ, не «через 3 дня»."""
    # 2026-08-14 18:00 Europe/Kyiv — это пятница (день недели берётся из даты,
    # а не из примера спеки, где он назван на глаз).
    ts = 1786719600.0
    assert format_due(ts, language="uk") == "18:00 у пʼятницю, 14 серпня"


def test_due_language_follows_the_client():
    ts = 1786719600.0
    assert "пятницу" in format_due(ts, language="ru")
    assert "Friday" in format_due(ts, language="en")


def test_due_without_timestamp_is_an_error_not_an_empty_string():
    """Пустая строка вместо срока дала бы «оплатіть до .» в живом диалоге."""
    with pytest.raises(UnsubstitutedPlaceholder):
        format_due(None, language="uk")
