# -*- coding: utf-8 -*-
"""Книга ПУБЛИЧНЫХ описаний объёма (решение владельца 12.08).

Отдельная книга, а не секция scope_texts, и разделение СТРУКТУРНОЕ — разными
типами и разными функциями. Причина та же, что у двух книг реквизитов: смешать
их можно было бы опиской, а цена ошибки — публикация внутреннего текста.

`scope_texts` описывают «що ЗМІНЮЄТЬСЯ» при уступке — это дельта скидочной
лестницы, и произносить её вслух нельзя. `tier_texts` описывают «що ВХОДИТЬ» в
объём — их бот называет открыто.
"""
from __future__ import annotations

import pytest

from chatter.payments.drill_gate import NotForProduction
from chatter.payments.pricing import load_pricing
from chatter.payments.tier_texts import (
    TierTextsError, assert_tier_texts_usable, load_tier_texts,
    resolve_tier_text,
)

DRILL = "8849893367:volska"
LIVE = "777000:volska"

KNOWLEDGE = "- Створення логотипа — 300 $ або 400 $ залежно від обсягу"
RAW = {
    "amount_source": "price_upper",
    "positions": {
        "logo": {
            "title": "Логотип", "currency": "USD",
            "tiers": [
                {"id": "basic", "amount": 300, "tier_text_key": "tier_basic"},
                {"id": "standard", "amount": 400, "tier_text_key": "tier_std"},
            ],
        },
    },
}


def _pricing():
    return load_pricing(RAW, knowledge=KNOWLEDGE)


def test_a_plain_string_is_a_real_text():
    book = load_tier_texts({"tier_basic": "логотип в одному варіанті"})
    assert book["tier_basic"].text == "логотип в одному варіанті"
    assert book["tier_basic"].placeholder is False


def test_a_stub_is_marked_explicitly_and_never_guessed():
    """Пометка только явная. Строка, ПОХОЖАЯ на заглушку, заглушкой не
    считается: угадывание сделало бы валидатор непредсказуемым в обе стороны."""
    book = load_tier_texts({
        "tier_basic": {"text": "ЗАГЛУШКА: базовий обсяг", "placeholder": True},
        "tier_std": "TODO виглядає як заглушка, але нею не є",
    })
    assert book["tier_basic"].placeholder is True
    assert book["tier_std"].placeholder is False


def test_an_empty_text_is_an_error():
    with pytest.raises(TierTextsError):
        load_tier_texts({"tier_basic": "   "})


def test_a_stub_reaches_the_drill_contact_only():
    book = load_tier_texts(
        {"tier_basic": {"text": "ЗАГЛУШКА: базовий", "placeholder": True}})
    assert resolve_tier_text("tier_basic", book, contact_id=DRILL)
    with pytest.raises(NotForProduction):
        resolve_tier_text("tier_basic", book, contact_id=LIVE)


def test_a_missing_key_is_loud():
    """Молчаливый дефолт здесь означал бы цену без объёма в лицо лиду."""
    with pytest.raises(TierTextsError):
        resolve_tier_text("нет такого", load_tier_texts({}), contact_id=DRILL)


def test_the_whole_book_is_checked_before_the_conversation(  ):
    """Ту же логику, что у scope: узнать про заглушку в момент называния цены
    значит оборвать разговор в самом дорогом месте."""
    book = load_tier_texts({"tier_basic": "базовий", "tier_std": "повний"})
    assert_tier_texts_usable(_pricing(), book, contact_id=LIVE)


def test_a_tier_without_a_text_is_named_before_the_conversation():
    book = load_tier_texts({"tier_basic": "базовий"})
    with pytest.raises(TierTextsError, match="tier_std"):
        assert_tier_texts_usable(_pricing(), book, contact_id=DRILL)


def test_all_missing_tiers_are_named_at_once_not_the_first():
    """Чинить по одной — столько же заходов, сколько ступеней."""
    with pytest.raises(TierTextsError) as exc:
        assert_tier_texts_usable(_pricing(), load_tier_texts({}), contact_id=DRILL)
    assert "tier_basic" in str(exc.value) and "tier_std" in str(exc.value)


def test_stubs_block_a_live_contact_but_not_the_drill():
    book = load_tier_texts({
        "tier_basic": {"text": "ЗАГЛУШКА: базовий", "placeholder": True},
        "tier_std": {"text": "ЗАГЛУШКА: повний", "placeholder": True},
    })
    assert_tier_texts_usable(_pricing(), book, contact_id=DRILL)
    with pytest.raises(NotForProduction):
        assert_tier_texts_usable(_pricing(), book, contact_id=LIVE)


def test_a_position_without_tiers_needs_no_tier_texts():
    """Ярусы только там, где объём различим. Остальные позиции книгу не
    требуют вовсе — иначе ярусы стали бы обязательными для всех."""
    raw = {"amount_source": "price_upper", "positions": {"refine": {
        "title": "Рефайн", "currency": "USD", "price_range": [200, 200],
        "ladder": [{"amount": 200, "scope_key": "refine_only"}]}}}
    pricing = load_pricing(raw, knowledge="- Рефайн — 200 $")
    assert_tier_texts_usable(pricing, load_tier_texts({}), contact_id=LIVE)


# ── проводка книги в конфиг клиента ────────────────────────────────────────

def _payments_raw():
    return {
        "enabled": False,
        "channels": [{"id": "iban_usd", "kind": "bank_transfer",
                      "mode": "manual", "currency": "USD",
                      "requisites_template": "iban_usd", "display": "IBAN"}],
        "pricing": RAW,
        "tier_texts": {"tier_basic": "базовий обсяг", "tier_std": "повний обсяг"},
    }


def test_the_book_is_loaded_from_the_client_config():
    from chatter.payments.settings import load_payments
    pay = load_payments(_payments_raw(), requisites_raw={}, knowledge=KNOWLEDGE)
    assert pay.tier_texts["tier_basic"].text == "базовий обсяг"


def test_a_tier_without_a_text_topples_the_start_not_the_conversation():
    """Ту же роль, что играет проверка ступеней сетки: расхождение конфига с
    прайсом обязано ронять СТАРТ, а не всплыть в разговоре ценой."""
    from chatter.payments.settings import PaymentsConfigError, assert_startable
    raw = _payments_raw()
    raw["enabled"] = True
    raw["tier_texts"] = {"tier_basic": "базовий обсяг"}     # tier_std забыт
    pay = load_payments_or_die(raw)
    with pytest.raises(PaymentsConfigError, match="tier_std"):
        assert_startable(pay, slug="volska")


def load_payments_or_die(raw):
    from chatter.payments.settings import load_payments
    return load_payments(raw, requisites_raw={
        "test_templates": {"iban_usd": {"body": "ТЕСТ"}}}, knowledge=KNOWLEDGE)
