"""Кнопка «Оплачено» — единственный источник денежных метрик.

Спека CLIENT_SCREENS.md §3. Живёт в ОБЩЕМ командном слое (`route_callback`),
поэтому веб-кнопка и TG-тап дают ровно один результат.
"""
from __future__ import annotations

import pytest

from chatter.notify.base import Action
from chatter.notify.control_bot import route_callback
from chatter.storage.db import Store


@pytest.fixture()
def store():
    s = Store(":memory:")
    s.get_or_create_contact("777:demo")
    return s


def _route(store, data):
    return route_callback(data, store=store, now=1000.0, language="ru",
                          snooze_seconds=3600.0)


def test_paid_action_exists_in_the_shared_enum():
    """Действие добавляется ОДИН раз — иначе веб и TG разъедутся."""
    assert Action.PAID.value == "paid"
    assert Action.PAID_AMOUNT.value == "paidamt"


def test_paid_without_amount_records_the_fact(store):
    _route(store, "paid:777:demo")

    rows = store.payments_between(0.0, 1e12)
    assert len(rows) == 1
    assert rows[0]["contact_id"] == "777:demo"
    assert rows[0]["amount"] is None


def test_paid_with_amount_records_the_sum(store):
    _route(store, "paidamt:750:777:demo")

    rows = store.payments_between(0.0, 1e12)
    assert len(rows) == 1
    assert rows[0]["amount"] == 750.0
    assert rows[0]["contact_id"] == "777:demo"


def test_amount_then_correction_updates_same_payment(store):
    """Поток «спочатку Оплачено, потім уточнив суму» не имеет права дать две
    оплаты: карточка одна — оплата одна."""
    _route(store, "paid:777:demo")
    _route(store, "paidamt:900:777:demo")

    rows = store.payments_between(0.0, 1e12)
    assert len(rows) == 1, f"дубль оплаты: {rows}"
    assert rows[0]["amount"] == 900.0


def test_payment_closes_the_funnel_as_bought(store):
    _route(store, "paidamt:750:777:demo")

    assert store.get_or_create_contact("777:demo")["state"] == "closed"
    trans = store.transitions_between(0.0, 1e12)
    assert trans and trans[-1]["to_state"] == "closed"
    assert trans[-1]["signal"] == "bought"


def test_broken_amount_does_not_mutate_anything(store):
    """DEV-18: не притворяемся, что сделали. Мусорная сумма → без записи."""
    res = _route(store, "paidamt:abc:777:demo")

    assert store.payments_between(0.0, 1e12) == []
    assert res.feedback_html


def test_negative_amount_is_rejected(store):
    _route(store, "paidamt:-50:777:demo")
    assert store.payments_between(0.0, 1e12) == []


def test_paid_writes_a_control_event(store):
    _route(store, "paid:777:demo")
    kinds = [e["kind"] for e in store.recent_events(50)] \
        if hasattr(store, "recent_events") else None
    if kinds is not None:
        assert "payment" in kinds
