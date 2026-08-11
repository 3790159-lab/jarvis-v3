# -*- coding: utf-8 -*-
"""Ярусы в разговоре: перечень объёмов, выбор, счёт по ВЫБРАННОЙ ступени.

Главный инвариант тот же, что у реквизитов: цифры собирает КОД, модель пишет
служебную подстановку. `{TIERS}` подставляется ПОСЛЕ guardrails ровно по той же
причине, что `{AMOUNT}` — иначе `large_number` вырежет суммы как необеспеченные.
"""
from __future__ import annotations

import pytest

from chatter.payments.dialogue import payment_turn
from chatter.payments.prompt import find_placeholders, has_disclaimer
from chatter.payments.settings import load_payments
from chatter.storage.db import Store

DRILL = "8849893367:volska"
NOW = 1_786_500_000.0
KNOWLEDGE = "- Створення логотипа — 300 $ або 400 $ залежно від обсягу"

PAY_RAW = {
    "enabled": True,
    "channels": [{"id": "iban_usd", "kind": "bank_transfer", "mode": "manual",
                  "currency": "USD", "requisites_template": "iban_usd",
                  "display": "IBAN"}],
    "pricing": {
        "amount_source": "price_upper",
        "positions": {"logo": {
            "title": "Логотип", "currency": "USD",
            "aliases": ["логотип", "лого"],
            "tiers": [
                {"id": "basic", "amount": 300, "tier_text_key": "t_basic",
                 "aliases": ["базов", "базов"]},
                {"id": "standard", "amount": 400, "tier_text_key": "t_std",
                 "aliases": ["повн", "розширен"]},
            ]}},
    },
    "tier_texts": {
        "t_basic": "логотип в одному варіанті",
        "t_std": "логотип з варіаціями та брендгайдом",
    },
}
REQ_RAW = {"test_templates": {"iban_usd": {"body": "ТЕСТ реквізити"}}}


@pytest.fixture()
def pay():
    return load_payments(PAY_RAW, requisites_raw=REQ_RAW, knowledge=KNOWLEDGE)


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.get_or_create_contact(DRILL)
    return s


def _turn(store, pay, text, msg_id=1):
    return payment_turn(store=store, payments=pay, contact_id=DRILL, text=text,
                        msg_id=msg_id, now=NOW, knowledge_version="k")


# ── объём не назван: перечисляем ступени ──────────────────────────────────

def test_a_price_question_lists_the_volume_steps(store, pay):
    turn = _turn(store, pay, "Скільки коштує логотип?")
    assert "{TIERS}" in turn.block, "модель не получила служебную подстановку"
    assert find_placeholders(turn.values.get("TIERS", "")) == [], \
        "в значении подстановки не должно быть других плейсхолдеров"


def test_the_substituted_value_names_every_price_with_its_scope(store, pay):
    """Цена без объёма — цена ни за что: лид не поймёт, за что платит больше."""
    value = _turn(store, pay, "Скільки коштує логотип?").values["TIERS"]
    assert "300 USD" in value and "400 USD" in value
    assert "логотип в одному варіанті" in value
    assert "логотип з варіаціями та брендгайдом" in value


def test_the_model_never_sees_the_digits(store, pay):
    """Тот же контракт, что у реквизитов: цифры в промпт не едут."""
    block = _turn(store, pay, "Скільки коштує логотип?").block
    assert "300" not in block and "400" not in block


def test_without_a_chosen_volume_the_caveat_is_still_required(store, pay):
    """Пока объём не выбран, итоговая сумма не зафиксирована — «як зараз»."""
    assert _turn(store, pay, "Скільки коштує логотип?").requires_disclaimer


def test_the_quote_without_a_choice_stays_price_upper(store, pay):
    _turn(store, pay, "Скільки коштує логотип?")
    q = store.quotes_for(DRILL)[-1]
    assert q["amount_source"] == "price_upper"
    assert q["tier_id"] is None
    assert q["amount_minor"] == 40000


# ── объём выбран: цена точная ─────────────────────────────────────────────

def test_a_chosen_tier_prices_by_that_tier_not_by_the_top(store, pay):
    """Ради этого всё и делалось: лид, которому подходят 300, платит 300."""
    _turn(store, pay, "Беру базовий логотип")
    q = store.quotes_for(DRILL)[-1]
    assert (q["amount_minor"], q["tier_id"], q["amount_source"]) == (
        30000, "basic", "tier_selected")


def test_a_chosen_tier_needs_no_caveat(store, pay):
    """«Орієнтовно» рядом с опубликованной ценой за названный объём — ложь:
    сумма точная, и оговорка приглашала бы спорить о решённом.

    Реплика БЕЗ готовности платить намеренно: с ней ход уходит в ветку счёта,
    где оговорка не назначается вовсе, и тест смотрел бы мимо правила
    (поймано мутацией DEV-26)."""
    assert not _turn(store, pay, "Мені базовий логотип ближче").requires_disclaimer


def test_moving_up_a_tier_is_a_new_quote_and_supersedes_the_old(store, pay):
    """Реплики намеренно БЕЗ готовности платить: «беру» после фикса 12.08 уже
    выставляет счёт, а выставленный счёт закрывает дальнейшее котирование —
    сумма зафиксирована, и вторая активная котировка означает, что «что мы ему
    называли» перестало иметь ответ."""
    _turn(store, pay, "Мені базовий логотип ближче", msg_id=1)
    _turn(store, pay, "А давайте повний варіант", msg_id=2)
    rows = store.quotes_for(DRILL)
    assert [(r["tier_id"], r["status"]) for r in rows][-2:] == [
        ("basic", "superseded"), ("standard", "active")]


def test_the_invoice_is_issued_by_the_chosen_tier(store, pay):
    """Счёт по ВЫБРАННОЙ ступени, а не по верхней."""
    _turn(store, pay, "Готовий замовити базовий логотип")
    inv = store.invoices_for(contact_id=DRILL)[-1]
    assert inv["amount_total"] == 30000
    assert inv["amount_source"] == "tier_selected"


# ── подавление: перечень цен без оговорки не уходит ───────────────────────

def test_a_reply_listing_prices_without_a_caveat_is_suppressed():
    """Дыра, которую легко не заметить: guardrail оговорки смотрел только на
    `{AMOUNT}`, а перечень ступеней называет цены через `{TIERS}`."""
    from chatter.payments.prompt import needs_caveat
    assert needs_caveat("ось варіанти: {TIERS}")
    assert needs_caveat("сума {AMOUNT}")
    assert not needs_caveat("добрий день, чим допомогти?")


def test_the_caveat_words_of_the_tiers_block_are_recognised():
    assert has_disclaimer("точну суму зафіксуємо в рахунку після вибору обсягу")
