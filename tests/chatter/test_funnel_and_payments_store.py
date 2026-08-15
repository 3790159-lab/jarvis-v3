"""Фундамент дашборда: история воронки + оплаты.

Спека `docs/dashboard/CLIENT_SCREENS.md` §5.2. Сегодня `advance_funnel` зовёт
`set_state`, который ПЕРЕЗАПИСЫВАЕТ поле без ts и без прошлого значения —
поэтому «сколько квалифицировалось за неделю» из базы недостижимо в принципе.
Оплат нет вообще. Обе таблицы пишутся только вперёд, значит каждый день без
них — безвозвратно потерянная история.
"""
from __future__ import annotations

import pytest

from chatter.core.escalation import advance_funnel
from chatter.payments.model import PaymentRecord, make_dedup_key
from chatter.payments.money import from_major
from chatter.storage.db import Store


@pytest.fixture()
def store():
    return Store(":memory:")


# ------------------------------------------------------------- переходы

def test_transition_is_recorded_with_both_ends_and_signal(store):
    store.get_or_create_contact("c1")
    store.record_transition("c1", from_state="new", to_state="qualifying",
                            signal="engaged", ts=100.0)

    rows = store.transitions_between(0.0, 1000.0)
    assert len(rows) == 1
    r = rows[0]
    assert (r["contact_id"], r["from_state"], r["to_state"], r["signal"], r["ts"]) == \
           ("c1", "new", "qualifying", "engaged", 100.0)


def test_advance_funnel_records_the_transition_it_makes(store):
    store.get_or_create_contact("c1")
    advance_funnel(store, "c1", stage_signal="engaged", escalated=False)

    rows = store.transitions_between(0.0, 1e12)
    assert [(r["from_state"], r["to_state"]) for r in rows] == [("new", "qualifying")]


def test_no_transition_row_when_state_did_not_change(store):
    """Шум ломает метрику: «квалифицировано» посчитается по числу переходов,
    и холостой ход не имеет права его раздувать."""
    store.get_or_create_contact("c1")
    advance_funnel(store, "c1", stage_signal=None, escalated=False)

    assert store.transitions_between(0.0, 1e12) == []


def test_escalation_transition_is_recorded_with_its_own_signal(store):
    store.get_or_create_contact("c1")
    advance_funnel(store, "c1", stage_signal=None, escalated=True)

    rows = store.transitions_between(0.0, 1e12)
    assert len(rows) == 1
    assert rows[0]["to_state"] == "escalated"
    assert rows[0]["signal"] == "escalated"


def test_transitions_are_filtered_by_period(store):
    store.get_or_create_contact("c1")
    store.record_transition("c1", from_state="new", to_state="qualifying",
                            signal="engaged", ts=50.0)
    store.record_transition("c1", from_state="qualifying", to_state="hot",
                            signal="interested", ts=150.0)

    assert len(store.transitions_between(100.0, 200.0)) == 1
    assert len(store.transitions_between(0.0, 200.0)) == 2


def test_full_path_is_reconstructable_after_terminal_state(store):
    """Ровно тот случай, ради которого таблица заводится: контакт дошёл до
    closed, а мы всё ещё видим, что он проходил через hot."""
    store.get_or_create_contact("c1")
    for sig in ("engaged", "interested", "bought"):
        advance_funnel(store, "c1", stage_signal=sig, escalated=False)

    path = [r["to_state"] for r in store.transitions_between(0.0, 1e12)]
    assert path == ["qualifying", "hot", "closed"]
    assert store.get_or_create_contact("c1")["state"] == "closed"


# --------------------------------------------------------------- оплаты

def test_payment_is_recorded_with_amount(store):
    store.get_or_create_contact("c1")
    store.record_payment(PaymentRecord(
        contact_id="c1", dedup_key=make_dedup_key("tap", 42), ts=100.0,
        confirmed_by="owner", amount=from_major("750", "USD")))

    rows = store.payments_between(0.0, 1000.0)
    assert len(rows) == 1
    assert rows[0]["amount_minor"] == 75000
    assert rows[0]["currency"] == "USD"


def test_payment_without_amount_is_allowed(store):
    """«Без суми» обязателен: запретить — значит потерять и сам факт оплаты
    (спека §3). Количество считается всегда, средний чек — только по суммам."""
    store.get_or_create_contact("c1")
    store.record_payment(PaymentRecord(
        contact_id="c1", dedup_key=make_dedup_key("tap", 42), ts=100.0,
        confirmed_by="owner", amount=None))

    rows = store.payments_between(0.0, 1000.0)
    assert len(rows) == 1
    assert rows[0]["amount_minor"] is None


def test_repeat_tap_on_same_card_updates_not_duplicates(store):
    """Идемпотентность по (contact_id, card_msg_id): повторный тап правит
    сумму, а не плодит вторую оплату."""
    store.get_or_create_contact("c1")
    store.record_payment(PaymentRecord(
        contact_id="c1", dedup_key=make_dedup_key("tap", 42), ts=100.0,
        confirmed_by="owner", amount=None))
    store.record_payment(PaymentRecord(
        contact_id="c1", dedup_key=make_dedup_key("tap", 42), ts=110.0,
        confirmed_by="owner", amount=from_major("900", "USD")))

    rows = store.payments_between(0.0, 1000.0)
    assert len(rows) == 1, "повторный тап создал дубль"
    assert rows[0]["amount_minor"] == 90000


def test_two_different_cards_are_two_payments(store):
    store.get_or_create_contact("c1")
    store.record_payment(PaymentRecord(
        contact_id="c1", dedup_key=make_dedup_key("tap", 42), ts=100.0,
        confirmed_by="owner", amount=from_major("300", "USD")))
    store.record_payment(PaymentRecord(
        contact_id="c1", dedup_key=make_dedup_key("tap", 77), ts=200.0,
        confirmed_by="owner", amount=from_major("400", "USD")))

    assert len(store.payments_between(0.0, 1000.0)) == 2


def test_payments_are_filtered_by_period(store):
    store.get_or_create_contact("c1")
    store.record_payment(PaymentRecord(
        contact_id="c1", dedup_key=make_dedup_key("tap", 1), ts=50.0,
        confirmed_by="owner", amount=from_major("100", "USD")))
    store.record_payment(PaymentRecord(
        contact_id="c1", dedup_key=make_dedup_key("tap", 2), ts=150.0,
        confirmed_by="owner", amount=from_major("200", "USD")))

    assert len(store.payments_between(100.0, 200.0)) == 1


def test_tables_survive_reopen_of_existing_db(tmp_path):
    """Миграция на живой базе: таблиц не было, база уже существует."""
    p = tmp_path / "x.db"
    s1 = Store(str(p))
    s1.get_or_create_contact("c1")
    s1.record_payment(PaymentRecord(
        contact_id="c1", dedup_key=make_dedup_key("tap", 1), ts=1.0,
        confirmed_by="owner", amount=from_major("10", "USD")))
    del s1

    s2 = Store(str(p))
    assert len(s2.payments_between(0.0, 100.0)) == 1
