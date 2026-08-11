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


# ── апселл: лид передумал в СТОРОНУ ДОРОЖЕ уже после счёта ────────────────
# Разрешено ровно при трёх условиях разом: счёт `issued`, денег по нему НОЛЬ,
# и новая ступень ДОРОЖЕ. Любое другое сочетание — к владелице: деньги на счёте
# и скидка на зафиксированную цену это решения человека, а не бота.

def _issued(store, pay, text="Готовий замовити базовий логотип"):
    _turn(store, pay, text, msg_id=1)
    return store.invoices_for(contact_id=DRILL)[-1]


def test_upselling_an_untouched_invoice_replaces_it(store, pay):
    old = _issued(store, pay)
    assert old["amount_total"] == 30000

    turn = _turn(store, pay, "А давайте краще повний варіант", msg_id=2)

    rows = store.invoices_for(contact_id=DRILL)
    assert [(r["amount_total"], r["status"]) for r in rows] == [
        (30000, "cancelled"), (40000, "issued")]
    assert store.quotes_for(DRILL)[-1]["tier_id"] == "standard"
    assert turn.owner_note is None, "апселл без денег владелицу не беспокоит"


def test_the_replaced_invoice_names_why_it_was_cancelled(store, pay):
    """«Отменён» без причины — это дыра в разборе спора о деньгах."""
    _issued(store, pay)
    _turn(store, pay, "А давайте краще повний варіант", msg_id=2)
    old = store.invoices_for(contact_id=DRILL)[0]
    assert old["cancelled_reason"], "причина отмены не записана"


def test_the_debt_of_the_replaced_invoice_is_closed(store, pay):
    """Долг снятого счёта обязан закрыться вместе с ним: модель к нему не
    допущена (фикс 12.08), значит незакрытый здесь не закроет уже никто."""
    from chatter.core.obligations_slot import invoice_okey
    old = _issued(store, pay)
    _turn(store, pay, "А давайте краще повний варіант", msg_id=2)
    row = next(o for o in store.get_obligations(DRILL)
               if o.okey == invoice_okey(old["invoice_id"]))
    assert row.status == "cancelled"


def test_money_on_the_invoice_sends_it_to_the_owner(store, pay):
    """Любая сумма — уже движение денег: замена счёта под пришедшей оплатой
    это решение человека, а не бота."""
    from chatter.payments.model import PaymentRecord, make_dedup_key
    from chatter.payments.money import from_major
    old = _issued(store, pay)
    store.apply_payment(PaymentRecord(
        contact_id=DRILL, dedup_key=make_dedup_key("tap", 1), ts=NOW,
        confirmed_by="owner", amount=from_major(100, "USD"),
        invoice_id=old["invoice_id"], stage_no=1), now=NOW)

    turn = _turn(store, pay, "А давайте краще повний варіант", msg_id=2)

    still = store.get_invoice(old["invoice_id"])
    assert still["status"] != "cancelled", "счёт с деньгами снят ботом"
    assert turn.owner_note is not None
    assert len(store.invoices_for(contact_id=DRILL)) == 1


def test_a_cheaper_tier_after_the_invoice_goes_to_the_owner(store, pay):
    """Ступень вниз по уже выставленному счёту — это СКИДКА на зафиксированную
    цену. Ею распоряжается человек."""
    _turn(store, pay, "Готовий замовити повний логотип", msg_id=1)
    turn = _turn(store, pay, "А давайте базовий", msg_id=2)

    rows = store.invoices_for(contact_id=DRILL)
    assert [r["status"] for r in rows] == ["issued"], "бот снял счёт ради скидки"
    assert rows[0]["amount_total"] == 40000
    assert turn.owner_note is not None


def test_the_same_tier_again_changes_nothing(store, pay):
    """Повтор выбора — не апселл. Новый счёт на ту же сумму означал бы вторую
    просьбу заплатить за то же самое."""
    _issued(store, pay)
    _turn(store, pay, "Так, базовий", msg_id=2)
    rows = store.invoices_for(contact_id=DRILL)
    assert [r["status"] for r in rows] == ["issued"]


# ── отменённый счёт не ломает пересчёт и не считается деньгами ────────────

def test_recompute_leaves_a_cancelled_invoice_alone(store, pay):
    """`cancelled` — решение человека, а не проекция от сумм. Пересчёт обязан
    его не трогать и не падать на нём."""
    old = _issued(store, pay)
    _turn(store, pay, "А давайте краще повний варіант", msg_id=2)
    assert store.recompute_status(old["invoice_id"], now=NOW + 10 * 86400) == "cancelled"


def test_a_cancelled_invoice_carries_no_money(store, pay):
    old = _issued(store, pay)
    _turn(store, pay, "А давайте краще повний варіант", msg_id=2)
    assert store.received_minor(old["invoice_id"]) == 0


def test_a_cancelled_invoice_is_not_the_open_one(store, pay):
    """Иначе блок счёта в промпте твердил бы про снятый счёт."""
    from chatter.payments.prompt import pick_open_invoice
    _issued(store, pay)
    _turn(store, pay, "А давайте краще повний варіант", msg_id=2)
    open_inv = pick_open_invoice(store.invoices_for(contact_id=DRILL))
    assert open_inv["amount_total"] == 40000
