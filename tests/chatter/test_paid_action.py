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
    s.get_or_create_contact("telegram:777:demo")
    return s


def _route(store, data):
    """Вызов БЕЗ личности события. После снятия сентинела `0` такие оплаты
    отвергаются — остаётся только для проверки самого отказа."""
    return route_callback(data, store=store, now=1000.0, language="ru",
                          snooze_seconds=3600.0)


def _route_panel(store, data, *, token="tok-1"):
    """Веб-панель: личность события приходит СВОИМ токеном, сгенерированным
    браузером в момент клика."""
    return route_callback(data, store=store, now=1000.0, language="ru",
                          snooze_seconds=3600.0, event_token=token)


def _route_card(store, data, *, card_msg_id):
    """Тап, пришедший С КАРТОЧКИ: id сообщения приезжает вместе с событием."""
    return route_callback(data, store=store, now=1000.0, language="ru",
                          snooze_seconds=3600.0, card_msg_id=card_msg_id)


def test_paid_action_exists_in_the_shared_enum():
    """Действие добавляется ОДИН раз — иначе веб и TG разъедутся."""
    assert Action.PAID.value == "paid"
    assert Action.PAID_AMOUNT.value == "paidamt"


def test_paid_without_amount_records_the_fact(store):
    _route_panel(store, "paid:777:demo")

    rows = store.payments_between(0.0, 1e12)
    assert len(rows) == 1
    assert rows[0]["contact_id"] == "telegram:777:demo"
    assert rows[0]["amount_minor"] is None


def test_paid_with_amount_records_the_sum(store):
    _route_panel(store, "paidamt:750:777:demo")

    rows = store.payments_between(0.0, 1e12)
    assert len(rows) == 1
    assert rows[0]["amount_minor"] == 75000
    assert rows[0]["contact_id"] == "telegram:777:demo"


def test_amount_then_correction_updates_same_payment(store):
    """Поток «спочатку Оплачено, потім уточнив суму» не имеет права дать две
    оплаты: карточка одна — оплата одна."""
    _route_card(store, "paid:777:demo", card_msg_id=151)
    _route_card(store, "paidamt:900:777:demo", card_msg_id=151)

    rows = store.payments_between(0.0, 1e12)
    assert len(rows) == 1, f"дубль оплаты: {rows}"
    assert rows[0]["amount_minor"] == 90000


def test_payment_closes_the_funnel_as_bought(store):
    _route_panel(store, "paidamt:750:777:demo")

    assert store.get_or_create_contact("telegram:777:demo")["state"] == "closed"
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
    _route_panel(store, "paid:777:demo")
    kinds = [e["kind"] for e in store.recent_events(50)] \
        if hasattr(store, "recent_events") else None
    if kinds is not None:
        assert "payment" in kinds


# ── Идемпотентность при ЖИВОЙ карточке эскалации ────────────────────────────
#
# Тесты выше держали контракт «карточка одна — оплата одна» вслепую: в их
# фикстуре карточки НЕТ, поэтому обе ветки падали на сентинел 0 и совпадали
# случайно. В проде карточка есть, и `route_callback` затирает флаг
# `esc_active` ПОСЛЕ каждого решающего действия — то есть ключ идемпотентности
# уничтожался тем же вызовом, который его использовал. Стендовый прогон
# 2026-08-10 дал две строки вместо одной на обоих потоках ниже.
#
# Личность оплаты обязана определяться КАРТОЧКОЙ, а не изменяемым флагом,
# которым владеет другая механика (Fix 2, закрытие карточки эскалации).

_ESC_CARD = 151


@pytest.fixture()
def store_with_open_card():
    """Фикстура прод-реальности: у контакта ОТКРЫТА карточка эскалации."""
    from chatter.core.escalation import esc_active_key

    s = Store(":memory:")
    s.get_or_create_contact("telegram:777:demo")
    s.set_runtime_flag(esc_active_key("telegram:777:demo"),
                       f"bot:237616472:{_ESC_CARD}", ts=1000.0)
    return s


def test_repeat_tap_on_the_same_card_does_not_double_revenue(store_with_open_card):
    """Повтор тапа по ОДНОЙ карточке не имеет права удвоить выручку."""
    _route_card(store_with_open_card, "paidamt:900:777:demo", card_msg_id=_ESC_CARD)
    _route_card(store_with_open_card, "paidamt:900:777:demo", card_msg_id=_ESC_CARD)

    rows = store_with_open_card.payments_between(0.0, 1e12)
    assert len(rows) == 1, f"дубль оплаты: {rows}"
    assert sum(r["amount_minor"] or 0 for r in rows) == 90000


def test_paid_then_amount_on_open_card_updates_one_payment(store_with_open_card):
    """Документированный поток «спочатку Оплачено, потім уточнив суму» —
    при живой карточке, как в проде."""
    _route_card(store_with_open_card, "paid:777:demo", card_msg_id=_ESC_CARD)
    _route_card(store_with_open_card, "paidamt:900:777:demo", card_msg_id=_ESC_CARD)

    rows = store_with_open_card.payments_between(0.0, 1e12)
    assert len(rows) == 1, f"дубль оплаты: {rows}"
    assert rows[0]["amount_minor"] == 90000


def test_payment_identity_comes_from_the_card_message_id(store_with_open_card):
    """Ключ оплаты — id сообщения-карточки, пришедший С ТАПОМ.

    Фикстура с ОТКРЫТОЙ карточкой намеренно: на голом store оба тапа и так
    падают на сентинел, и тест не смог бы отличить правильную реализацию от
    старой (проверено мутацией — на голом store он её переживал)."""
    _route_card(store_with_open_card, "paidamt:900:777:demo", card_msg_id=151)
    _route_card(store_with_open_card, "paidamt:900:777:demo", card_msg_id=151)

    rows = store_with_open_card.payments_between(0.0, 1e12)
    assert len(rows) == 1, f"один и тот же тап дал дубль: {rows}"


def test_different_cards_are_different_payments(store):
    """Обратная сторона: две РАЗНЫЕ карточки — две разные оплаты."""
    _route_card(store, "paidamt:300:777:demo", card_msg_id=151)
    _route_card(store, "paidamt:400:777:demo", card_msg_id=152)

    rows = store.payments_between(0.0, 1e12)
    assert len(rows) == 2
    assert sum(r["amount_minor"] for r in rows) == 70000


def test_esc_active_flag_does_not_influence_payment_identity(store_with_open_card):
    """Сторож против регресса: флаг `esc_active` принадлежит механике карточек
    и не должен участвовать в личности оплаты. Меняем флаг между тапами —
    оплата обязана остаться одной."""
    from chatter.core.escalation import esc_active_key

    _route_card(store_with_open_card, "paidamt:900:777:demo", card_msg_id=151)
    store_with_open_card.set_runtime_flag(
        esc_active_key("telegram:777:demo"), "bot:237616472:999", ts=1001.0)
    _route_card(store_with_open_card, "paidamt:900:777:demo", card_msg_id=151)

    rows = store_with_open_card.payments_between(0.0, 1e12)
    assert len(rows) == 1, f"флаг протёк в ключ оплаты: {rows}"


# ── личность события: сентинел `0` снят ─────────────────────────────────────
#
# Прежде вызыватель без карточки писал оплату с card_msg_id=0. Ключ был
# стабилен ровно до ВТОРОГО такого вызывателя: все оплаты веб-панели по одному
# контакту схлопывались в одну строку — цена, признанная в коде и молча
# оплачиваемая выручкой.


def test_payment_without_identity_is_refused_and_writes_nothing(store):
    res = _route(store, "paidamt:900:777:demo")

    assert store.payments_between(0.0, 1e12) == [], "оплата без личности записалась"
    assert res.feedback_html, "отказ обязан быть слышен (DEV-18)"


def test_two_panel_payments_with_different_tokens_are_two_rows(store):
    """СЕГОДНЯШНИЙ ДЕФЕКТ, закрытый: два разных нажатия в панели — две оплаты."""
    _route_panel(store, "paidamt:300:777:demo", token="tok-a")
    _route_panel(store, "paidamt:400:777:demo", token="tok-b")

    rows = store.payments_between(0.0, 1e12)
    assert len(rows) == 2
    assert sum(r["amount_minor"] for r in rows) == 70000


def test_retry_of_the_same_panel_click_stays_one_payment(store):
    """Токен генерируется в момент КЛИКА, поэтому повтор запроса (сеть, двойной
    тап) несёт тот же токен и оплата остаётся одной."""
    _route_panel(store, "paidamt:900:777:demo", token="tok-same")
    _route_panel(store, "paidamt:900:777:demo", token="tok-same")

    rows = store.payments_between(0.0, 1e12)
    assert len(rows) == 1
    assert rows[0]["dedup_key"] == "panel:tok-same"


def test_card_and_panel_keys_never_collide(store):
    """Разные источники с одинаковой «личностью» — разные оплаты: префикс
    источника входит в ключ."""
    _route_card(store, "paidamt:100:777:demo", card_msg_id=7)
    _route_panel(store, "paidamt:200:777:demo", token="7")

    rows = store.payments_between(0.0, 1e12)
    assert len(rows) == 2
    assert {r["dedup_key"] for r in rows} == {"tap:7", "panel:7"}


def test_money_is_stored_in_minor_units(store):
    """Риск 10.1: на float центы копятся и счёт не закрывается никогда."""
    _route_panel(store, "paidamt:750.50:777:demo")

    rows = store.payments_between(0.0, 1e12)
    assert rows[0]["amount_minor"] == 75050
    assert isinstance(rows[0]["amount_minor"], int)
