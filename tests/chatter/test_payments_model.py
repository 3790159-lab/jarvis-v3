"""Чистая часть модели счёта: ключи идемпотентности и проекция статуса
(спека §3.1-§3.3, §14 пп.2, 4, 5).

Здесь нет ни одного обращения к БД: проекция статуса — функция от сумм, а не
метод хранилища. Так её можно проверить всеми граничными случаями, включая те,
которые в Ф0 не производятся (частичная, переплата, просрочка), — и Ф1 включит
их, не переписывая.
"""
from __future__ import annotations

import pytest

from chatter.payments.model import (
    CONFIRMED_BY, DEDUP_SOURCES, DedupKeyError, PaymentRecord, make_dedup_key,
    project_status, validate_dedup_key,
)
from chatter.payments.money import Money
from chatter.payments.statuses import TransitionError

USD = "USD"


# ── ключ идемпотентности: один, с префиксом источника (§14 п.2) ─────────────

def test_prefixes_are_a_closed_set():
    assert DEDUP_SOURCES == ("tap", "panel", "evt")


@pytest.mark.parametrize("source,ident,expected", [
    ("tap", 12345, "tap:12345"),
    ("panel", "a1b2c3", "panel:a1b2c3"),
    ("evt", "wise:EV-77", "evt:wise:EV-77"),
])
def test_make_dedup_key(source, ident, expected):
    assert make_dedup_key(source, ident) == expected


def test_unknown_source_is_rejected():
    """Новый источник обязан быть объявлен, а не появиться строкой на месте."""
    with pytest.raises(DedupKeyError):
        make_dedup_key("webhook", "x")


def test_empty_identity_is_rejected():
    """Пустая личность — это сентинел под другим именем: две записи из одного
    источника схлопнулись бы в одну (сегодняшний дефект веб-панели)."""
    for ident in ("", "   ", None):
        with pytest.raises(DedupKeyError):
            make_dedup_key("panel", ident)


def test_validate_rejects_keys_without_a_known_prefix():
    for key in ("12345", "tap", ":", "tapx:1", "", "  "):
        with pytest.raises(DedupKeyError):
            validate_dedup_key(key)


def test_validate_accepts_all_declared_prefixes():
    for key in ("tap:1", "panel:tok", "evt:wise:1"):
        validate_dedup_key(key)


# ── запись оплаты ──────────────────────────────────────────────────────────

def test_confirmed_by_is_owner_or_provider_only():
    """Клиента здесь нет: «я оплатил» словами — не событие (правило №2)."""
    assert CONFIRMED_BY == ("owner", "provider")


def test_payment_record_rejects_unknown_confirmer():
    with pytest.raises(ValueError):
        PaymentRecord(contact_id="c", dedup_key="tap:1", ts=1.0,
                      confirmed_by="client", amount=Money(100, USD))


def test_payment_record_rejects_bad_dedup_key():
    with pytest.raises(DedupKeyError):
        PaymentRecord(contact_id="c", dedup_key="1", ts=1.0,
                      confirmed_by="owner", amount=Money(100, USD))


def test_amount_received_defaults_to_the_declared_amount():
    """В Ф0 конверсии нет, зачислено == заявлено. Поле существует с первого дня,
    потому что в Ф2 остаток считается по ЗАЧИСЛЕННОМУ, иначе счёт не закроется
    никогда (риск 10.4)."""
    rec = PaymentRecord(contact_id="c", dedup_key="tap:1", ts=1.0,
                        confirmed_by="owner", amount=Money(90000, USD))
    assert rec.amount_received == Money(90000, USD)


def test_amount_may_be_absent_because_owner_often_knows_only_the_fact():
    rec = PaymentRecord(contact_id="c", dedup_key="tap:1", ts=1.0,
                        confirmed_by="owner", amount=None)
    assert rec.amount is None and rec.amount_received is None


# ── проекция статуса (§14 п.4) ─────────────────────────────────────────────

def _p(total, received, current="issued", due=100.0, now=50.0):
    return project_status(amount_total_minor=total, received_minor=received,
                          current=current, due_ts=due, now=now)


def test_nothing_received_stays_issued():
    assert _p(40000, 0) == "issued"


def test_full_amount_settles():
    assert _p(40000, 40000) == "paid"


def test_partial_is_not_paid():
    """Главная причина, по которой статус — проекция: «оплачено» при половине
    денег в Ф1 закрыло бы и счёт, и лида в воронке."""
    assert _p(40000, 20000) == "partially_paid"


def test_more_than_total_is_overpaid_not_paid():
    assert _p(40000, 45000) == "overpaid"


def test_one_minor_unit_short_is_not_paid():
    """Ровно тот случай, ради которого деньги целые: на float остаток $0.01
    возникал бы сам по себе (риск 10.1)."""
    assert _p(40000, 39999) == "partially_paid"


def test_overdue_marks_but_does_not_cancel():
    assert _p(40000, 0, due=10.0, now=50.0) == "overdue"
    assert _p(40000, 20000, due=10.0, now=50.0) == "overdue"


def test_payment_after_due_still_settles():
    """Просрочка — пометка, а не отмена: просроченный счёт остаётся
    оплачиваемым (§3.2)."""
    assert _p(40000, 40000, current="overdue", due=10.0, now=50.0) == "paid"


@pytest.mark.parametrize("current", ["draft", "awaiting_owner", "cancelled", "refunded"])
def test_projection_does_not_touch_non_money_states(current):
    """Отменённый счёт не воскресает от поступления, а черновик не становится
    оплаченным в обход выставления."""
    assert _p(40000, 40000, current=current) == current


def test_projection_refuses_an_impossible_transition_loudly():
    """Если карта переходов и проекция разойдутся, это обязано упасть, а не
    записать статус, которого нет на карте."""
    with pytest.raises(TransitionError):
        project_status(amount_total_minor=40000, received_minor=0,
                       current="teleported", due_ts=1.0, now=2.0)


def test_unknown_total_cannot_be_settled():
    """Счёт без суммы (awaiting_owner) не может стать оплаченным: сравнивать
    не с чем, а «наверное хватило» — это выдуманный факт."""
    assert project_status(amount_total_minor=None, received_minor=40000,
                          current="issued", due_ts=100.0, now=50.0) == "issued"
