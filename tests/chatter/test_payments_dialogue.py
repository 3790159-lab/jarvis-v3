# -*- coding: utf-8 -*-
"""Проводка хода: углы, до которых сквозной тест не достаёт напрямую.

Сквозной путь живёт в `test_payments_path_e2e.py` и он же гейт приёмки. Здесь —
ровно то, что на нём проверяется дорого или неотличимо: округление срока к
рабочим часам, поведение конфига с заглушками у живого лида против дрила,
идемпотентность выставления, и главное отрицательное свойство — «нет
реквизитов → счёта тоже нет».
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from chatter.payments.dialogue import payment_turn
from chatter.payments.settings import load_payments
from chatter.storage.db import Store

KYIV = ZoneInfo("Europe/Kyiv")
NOW = 1_786_000_000.0                     # чт 2026-08-06 10:06 Киев
LIVE = "555000111:volska"
DRILL = "237616472:volska"                # из DRILL_CONTACTS
KNOWLEDGE = "Логотип: $300–$400."
IBAN = "IBAN UA00 1111 2222"

RAW = {
    "enabled": True,
    "due_hours": 72,
    "channels": [{"id": "iban_usd", "kind": "bank_transfer", "mode": "manual",
                  "currency": "USD", "requisites_template": "iban_usd",
                  "display": "Переказ на рахунок"}],
    "pricing": {
        "amount_source": "price_upper",
        "positions": {"logo": {
            "title": "Логотип", "currency": "USD", "aliases": ["логотип"],
            "price_range": [300, 400],
            "ladder": [{"amount": 400, "scope_key": "logo_full"},
                       {"amount": 300, "scope_key": "logo_floor"}]}},
    },
    "scope_texts": {"logo_full": {"text": "повний обсяг"},
                    "logo_floor": {"text": "базовий обсяг"}},
}
BOOK = {"templates": {"iban_usd": {"body": IBAN}}}
READY = "Давайте почнемо з логотипом, куди платити?"


def _payments(*, scope_stub: bool = False, book: dict | None = None,
              **over) -> object:
    raw = {**RAW, **over}
    if scope_stub:
        raw["scope_texts"] = {
            "logo_full": {"text": "ЗАГЛУШКА", "placeholder": True},
            "logo_floor": {"text": "ЗАГЛУШКА", "placeholder": True}}
    return load_payments(raw, requisites_raw=BOOK if book is None else book,
                         knowledge=KNOWLEDGE)


def _store() -> Store:
    return Store(":memory:")


def _turn(store, payments, *, contact_id=LIVE, text=READY, msg_id=1, now=NOW,
          **kw):
    return payment_turn(store=store, payments=payments, contact_id=contact_id,
                        text=text, msg_id=msg_id, now=now, **kw)


def _kyiv(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(KYIV)


# ── срок с часом (§8.4) ────────────────────────────────────────────────────

def test_due_is_a_round_hour_inside_the_working_day():
    store, pay = _store(), _payments()
    _turn(store, pay, work_hours=(9, 20))
    due = _kyiv(store.invoices_for(contact_id=LIVE)[0]["due_ts"])
    assert (due.hour, due.minute, due.second) == (10, 0, 0), \
        "срок с минутами — это «до 10:06 у неділю», а не срок, который называют людям"
    assert due.day == 9


def test_due_landing_before_the_working_day_moves_to_its_start():
    """72 ч от полуночи упали бы на 00:06 — «оплатіть до 00:06» это не срок,
    а недоразумение: в это время клиенту никто не ответит."""
    store, pay = _store(), _payments()
    midnight = datetime(2026, 8, 6, 0, 30, tzinfo=KYIV).timestamp()
    _turn(store, pay, now=midnight, work_hours=(9, 20))
    due = _kyiv(store.invoices_for(contact_id=LIVE)[0]["due_ts"])
    assert (due.hour, due.minute) == (9, 0)


def test_due_landing_after_the_working_day_moves_to_its_end():
    store, pay = _store(), _payments()
    late = datetime(2026, 8, 6, 22, 30, tzinfo=KYIV).timestamp()
    _turn(store, pay, now=late, work_hours=(9, 20))
    due = _kyiv(store.invoices_for(contact_id=LIVE)[0]["due_ts"])
    assert (due.hour, due.minute) == (20, 0)


# ── тестовые активы: дрил против живого (§8.3-бис) ─────────────────────────

def test_stub_scope_texts_stop_the_quote_for_a_live_lead():
    """Сегодняшнее состояние конфига volska: тексты объёма — заглушки. Живому
    лиду цена не называется вовсе; счёт уходит владелице."""
    store, pay = _store(), _payments(scope_stub=True)
    turn = _turn(store, pay)

    inv = store.invoices_for(contact_id=LIVE)
    assert len(inv) == 1 and inv[0]["status"] == "awaiting_owner"
    assert inv[0]["amount_total"] is None
    assert "AMOUNT" not in turn.values
    assert turn.owner_note is not None


def test_the_same_stub_config_serves_a_drill_contact_in_full():
    """Иначе живой прогон Ф0 невозможен вовсе — ради этого §8.3-бис и введён."""
    store, pay = _store(), _payments(scope_stub=True)
    turn = _turn(store, pay, contact_id=DRILL)

    inv = store.invoices_for(contact_id=DRILL)
    assert len(inv) == 1 and inv[0]["status"] == "issued"
    assert turn.values["AMOUNT"].startswith("400")
    assert turn.owner_note is None


# ── отрицательные свойства ────────────────────────────────────────────────

def test_no_requisites_means_no_invoice_at_all():
    """Счёт без реквизитов — это «заплати неизвестно куда до четверга»: срок
    идёт, долг записан, а заплатить некуда. Лучше не выставлять вовсе."""
    store, pay = _store(), _payments(book={"templates": {}})
    turn = _turn(store, pay)

    assert store.invoices_for(contact_id=LIVE) == []
    assert turn.values == {}


def test_disabled_feature_writes_nothing_and_says_nothing():
    store, pay = _store(), _payments(enabled=False)
    turn = _turn(store, pay)

    assert (turn.block, turn.values, turn.owner_note) == ("", {}, None)
    assert store.invoices_for(contact_id=LIVE) == []
    assert store.quotes_for(LIVE) == []


def test_issuing_twice_from_one_lead_message_is_one_invoice():
    """§14 п.15. Ретрай пайплайна не имеет права выставить второй счёт: фантомы
    съедают лимит и раздваивают ответ на вопрос «сколько я должен»."""
    store, pay = _store(), _payments()
    _turn(store, pay, msg_id=7)
    _turn(store, pay, msg_id=7)

    assert len(store.invoices_for(contact_id=LIVE)) == 1


def test_an_open_invoice_is_reused_not_replaced():
    store, pay = _store(), _payments()
    _turn(store, pay, msg_id=1)
    first = store.invoices_for(contact_id=LIVE)[0]

    turn = _turn(store, pay, msg_id=2, text="То куди все ж таки платити?")

    assert len(store.invoices_for(contact_id=LIVE)) == 1
    assert turn.values["AMOUNT"].startswith("400")
    assert turn.values["DUE"], "срок открытого счёта пропал из подстановки"
    assert first["invoice_id"] in turn.block or "РАХУНОК" in turn.block


# ── котировка без счёта ───────────────────────────────────────────────────

def test_price_question_quotes_without_an_invoice_and_demands_the_disclaimer():
    """§2.2: число без оговорки — это оферта. Ответ, назвавший цену без неё,
    подавляется целиком, поэтому проводка обязана этого потребовать."""
    store, pay = _store(), _payments()
    turn = _turn(store, pay, text="Скільки коштує логотип?")

    assert store.invoices_for(contact_id=LIVE) == []
    assert store.quotes_for(LIVE)[0]["amount_minor"] == 40000
    assert turn.values["AMOUNT"].startswith("400")
    assert turn.requires_disclaimer is True


def test_an_issued_invoice_does_not_demand_the_disclaimer():
    """Обратная сторона: у выставленного счёта сумма ФИКСИРОВАНА. «Орієнтовно
    $400» в счёте — это приглашение поспорить о том, что уже зафиксировано."""
    store, pay = _store(), _payments()
    turn = _turn(store, pay)

    assert store.invoices_for(contact_id=LIVE)[0]["status"] == "issued"
    assert turn.requires_disclaimer is False


def test_a_quote_never_survives_into_a_second_active_row():
    """История «кому что обещали» append-only: активная котировка одна."""
    store, pay = _store(), _payments()
    _turn(store, pay, text="Скільки коштує логотип?", msg_id=1)
    _turn(store, pay, text="А ще раз, скільки коштує логотип?", msg_id=2)

    rows = store.quotes_for(LIVE)
    assert len(rows) == 2
    assert [r["status"] for r in rows] == ["superseded", "active"]


@pytest.mark.parametrize("text", ["Доброго дня!", "Дякую, подумаю"])
def test_ordinary_turn_creates_neither_quote_nor_invoice(text):
    store, pay = _store(), _payments()
    turn = _turn(store, pay, text=text)

    assert store.quotes_for(LIVE) == []
    assert store.invoices_for(contact_id=LIVE) == []
    assert turn.values.get("REQUISITES") == IBAN, \
        "операция А не зависит ни от чего: реквизиты доступны всегда при enabled"


# ── «давайте почнемо» без названия услуги ─────────────────────────────────

def test_readiness_without_a_service_falls_back_to_the_active_quote():
    """Живая последовательность: «скільки коштує логотип?» → «ок, давайте
    почнемо». Во второй реплике услуги нет, и позиция берётся из АКТИВНОЙ
    котировки. Без этого самый обычный путь к оплате упирался бы в владельца."""
    store, pay = _store(), _payments()
    _turn(store, pay, text="Скільки коштує логотип?", msg_id=1)

    turn = _turn(store, pay, text="Ок, давайте почнемо", msg_id=2)

    inv = store.invoices_for(contact_id=LIVE)
    assert len(inv) == 1 and inv[0]["status"] == "issued"
    assert inv[0]["amount_total"] == 40000
    assert turn.values["AMOUNT"].startswith("400")


def test_readiness_without_a_service_and_without_a_quote_calls_the_owner():
    """Обратная сторона: котировки нет — угадывать позицию нечем и не из чего."""
    store, pay = _store(), _payments()
    turn = _turn(store, pay, text="Готовий оплатити, куди платити?")

    inv = store.invoices_for(contact_id=LIVE)
    assert len(inv) == 1 and inv[0]["status"] == "awaiting_owner"
    assert turn.owner_note.reasons == ("no_parse",), \
        "причина не доехала до владельца — карточка без неё бесполезна"


def test_the_owner_note_names_why_the_amount_was_withheld():
    """Заглушки объёма — не «непонятная ошибка», а конкретная причина, и
    владелица должна прочитать её словами, а не догадаться."""
    store, pay = _store(), _payments(scope_stub=True)
    turn = _turn(store, pay)

    assert turn.owner_note.reasons and "заглушк" in turn.owner_note.reasons[0]
