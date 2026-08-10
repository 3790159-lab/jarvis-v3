"""Статусы счёта: закрытое перечисление + карта переходов ДАННЫМИ (спека §3.2,
§14 пп.4-5).

Ф0 производит четыре статуса из десяти, но перечисление и карта вводятся
целиком: в Ф1 сравнение `status == "paid"` молча пропустит `partially_paid` и
`overpaid`, а ветвление на if'ах придётся переписывать.

Идиома взята у `conversation.py:31` — карта воронки тоже данные, не код.
"""
from __future__ import annotations

import pytest

from chatter.payments.statuses import (
    ACTORS, INVOICE_STATUSES, TRANSITIONS, TransitionError, allowed_actors,
    assert_transition, is_settled,
)


def test_all_ten_statuses_exist_from_day_one():
    assert INVOICE_STATUSES == (
        "draft", "awaiting_owner", "issued", "partially_paid", "paid",
        "overpaid", "overdue", "cancelled", "refund_requested", "refunded",
    )


def test_actors_do_not_include_the_client():
    """Жёсткое правило №2: «я оплатил» словами — не событие. Клиента нет среди
    акторов вообще, значит ветку «клиент подтвердил» физически некуда написать."""
    assert ACTORS == ("owner", "provider", "system")
    assert all(a in ACTORS for _, _, actors in TRANSITIONS for a in actors)


def test_transitions_reference_only_known_statuses():
    for frm, to, _ in TRANSITIONS:
        assert frm in INVOICE_STATUSES and to in INVOICE_STATUSES


@pytest.mark.parametrize("frm,to,actor", [
    ("draft", "issued", "system"),
    ("draft", "awaiting_owner", "system"),
    ("awaiting_owner", "issued", "owner"),
    ("issued", "paid", "system"),
    ("issued", "partially_paid", "system"),
    ("partially_paid", "paid", "system"),
    ("issued", "cancelled", "owner"),
    ("paid", "refund_requested", "system"),
    ("refund_requested", "refunded", "owner"),
])
def test_allowed_edges(frm, to, actor):
    assert_transition(frm, to, actor)


def test_money_statuses_are_a_projection_not_an_owner_write():
    """Владелец ТАПАЕТ, а статус пересчитывает система (§14 п.4). Если бы
    владелец мог поставить `paid` напрямую, в Ф1 он бы закрыл счёт, у которого
    оплачена половина."""
    with pytest.raises(TransitionError):
        assert_transition("issued", "paid", "owner")
    with pytest.raises(TransitionError):
        assert_transition("issued", "paid", "provider")


def test_cancel_is_owner_only():
    assert_transition("issued", "cancelled", "owner")
    for actor in ("system", "provider"):
        with pytest.raises(TransitionError):
            assert_transition("issued", "cancelled", actor)


def test_overdue_does_not_cancel_and_stays_payable():
    """§3.2: просрочка — пометка, а не отмена. Из overdue деньги ещё принимаются."""
    assert_transition("issued", "overdue", "system")
    assert allowed_actors("overdue", "paid") == frozenset({"system"})
    assert allowed_actors("overdue", "partially_paid") == frozenset({"system"})


def test_terminal_states_have_no_way_back_to_money():
    for frm in ("cancelled", "refunded"):
        for to in ("paid", "partially_paid", "overpaid"):
            with pytest.raises(TransitionError):
                assert_transition(frm, to, "system")


def test_unknown_status_or_actor_is_an_error_not_a_silent_no():
    """Сообщение проверяется намеренно: без него тест слеп.

    Убери проверку актора — `assert_transition("issued","paid","client")` всё
    равно упадёт, но по ДРУГОЙ причине («нет в списке допустимых для ребра»), и
    зелёный тест соврал бы, что неизвестный актор отсекается (DEV-26)."""
    with pytest.raises(TransitionError, match="неизвестный статус"):
        assert_transition("issued", "teleported", "system")
    with pytest.raises(TransitionError, match="неизвестный актор"):
        assert_transition("issued", "paid", "client")


def test_is_settled_is_the_only_way_to_ask_about_money():
    """Замена запрещённого `status == "paid"`. partially_paid и overpaid —
    НЕ закрытые деньги: первый недоплачен, второй требует решения владельца."""
    assert is_settled("paid") is True
    assert is_settled("partially_paid") is False
    assert is_settled("overpaid") is False
    assert is_settled("issued") is False
