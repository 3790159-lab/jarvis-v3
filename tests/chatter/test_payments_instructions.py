"""Реквизиты: источник, гейт и отказ (спека §8.3, §14 п.7 + решение владельца
о test-реквизитах 2026-08-10).

Три правила, каждое проверяется отдельно:
1. test-реквизиты — ТОЛЬКО дрил-контактам;
2. любому другому контакту — реквизиты КЛИЕНТА;
3. нет клиентских — ОТКАЗ в выдаче. Фича молчит, а не подставляет что попало.

Правило 4 из §0.4 («реквизиты только из конфига») здесь получает вторую
техническую опору: тело реквизитов приходит из книги констант, модель его не
видит вовсе (§8.3), а источник каждой выданной инструкции подписан в `source`.
"""
from __future__ import annotations

import pytest

from chatter.payments.drill_gate import DRILL_CONTACTS, NotForProduction
from chatter.payments.instructions import (
    Channel, PaymentInstruction, RequisitesError, RequisitesUnavailable,
    load_requisites, resolve_instruction,
)

LIVE = "555000111:volska"
DRILL = "8849893367:volska"

IBAN = Channel(id="iban_eur", kind="bank_transfer", mode="manual", currency="EUR",
               requisites_template="iban_eur", display="Банківський переказ (EUR)")

BOOK_BOTH = load_requisites({
    "templates": {"iban_eur": {"body": "IBAN клієнта: UA00 0000"}},
    "test_templates": {"iban_eur": {"body": "TEST IBAN стенда: XX99 9999"}},
})
BOOK_TEST_ONLY = load_requisites({
    "test_templates": {"iban_eur": {"body": "TEST IBAN стенда: XX99 9999"}},
})
BOOK_CLIENT_ONLY = load_requisites({
    "templates": {"iban_eur": {"body": "IBAN клієнта: UA00 0000"}},
})
BOOK_EMPTY = load_requisites({})


# ── правило 1: test-реквизиты только дрилу ──────────────────────────────────

def test_drill_contact_gets_the_test_requisites():
    got = resolve_instruction(channel=IBAN, book=BOOK_BOTH, contact_id=DRILL)
    assert got.source == "test"
    assert "TEST IBAN" in got.body_text


def test_drill_contact_without_test_body_falls_back_to_client():
    got = resolve_instruction(channel=IBAN, book=BOOK_CLIENT_ONLY, contact_id=DRILL)
    assert got.source == "config"
    assert "IBAN клієнта" in got.body_text


# ── правило 2: живому контакту — клиентские, тестовые не подглядываем ───────

def test_live_contact_gets_client_requisites_even_when_test_ones_exist():
    """Наличие test-реквизитов в книге не должно НИКАК влиять на живой контакт."""
    got = resolve_instruction(channel=IBAN, book=BOOK_BOTH, contact_id=LIVE)
    assert got.source == "config"
    assert "IBAN клієнта" in got.body_text
    assert "TEST" not in got.body_text


def test_test_requisites_never_leak_to_a_live_contact():
    """ГЛАВНЫЙ тест решения владельца.

    В книге есть ТОЛЬКО тестовые реквизиты. Соблазн «ну хоть что-то отдать»
    здесь максимальный, и цена ошибки — деньги живого клиента, ушедшие на
    тестовый счёт. Правильный исход ровно один: отказ."""
    with pytest.raises(RequisitesUnavailable):
        resolve_instruction(channel=IBAN, book=BOOK_TEST_ONLY, contact_id=LIVE)


def test_leak_attempt_is_reported_as_a_refusal_not_as_an_empty_instruction():
    """DEV-18: отказ обязан быть слышен. Пустая инструкция уехала бы в диалог
    как «реквизити: » и выглядела бы успехом."""
    with pytest.raises(RequisitesUnavailable) as exc:
        resolve_instruction(channel=IBAN, book=BOOK_TEST_ONLY, contact_id=LIVE)
    assert "iban_eur" in str(exc.value)


# ── правило 3: нет клиентских — отказ ───────────────────────────────────────

def test_no_requisites_at_all_is_a_refusal():
    with pytest.raises(RequisitesUnavailable):
        resolve_instruction(channel=IBAN, book=BOOK_EMPTY, contact_id=LIVE)
    with pytest.raises(RequisitesUnavailable):
        resolve_instruction(channel=IBAN, book=BOOK_EMPTY, contact_id=DRILL)


def test_empty_body_counts_as_absent():
    """«Реквизиты есть, но пустые» — тот же отказ: пробел в конфиге не должен
    превращаться в пустую строку в диалоге."""
    book = load_requisites({"templates": {"iban_eur": {"body": "   "}}})
    with pytest.raises(RequisitesUnavailable):
        resolve_instruction(channel=IBAN, book=book, contact_id=LIVE)


def test_unknown_template_key_is_a_config_error_not_a_silent_skip():
    ch = Channel(id="x", kind="bank_transfer", mode="manual", currency="EUR",
                 requisites_template="missing_key", display="X")
    with pytest.raises(RequisitesUnavailable, match="missing_key"):
        resolve_instruction(channel=ch, book=BOOK_BOTH, contact_id=LIVE)


# ── инвариант, который должен пережить любые правки ────────────────────────

@pytest.mark.parametrize("contact_id", [LIVE, DRILL, "237616472:volska", "1:volska"])
@pytest.mark.parametrize("book", [BOOK_BOTH, BOOK_TEST_ONLY, BOOK_CLIENT_ONLY, BOOK_EMPTY])
def test_source_test_implies_drill_contact(contact_id, book):
    """Перебор всех сочетаний: если инструкция подписана как тестовая, контакт
    ОБЯЗАН быть дрил-контактом. Один этот инвариант ловит любую будущую правку
    резолвера, открывающую утечку."""
    try:
        got = resolve_instruction(channel=IBAN, book=book, contact_id=contact_id)
    except (RequisitesUnavailable, NotForProduction):
        return
    if got.source == "test":
        assert contact_id in DRILL_CONTACTS


# ── конечная форма §14 п.7 ──────────────────────────────────────────────────

def test_instruction_carries_the_final_shape_phase0_partly_unused():
    got = resolve_instruction(channel=IBAN, book=BOOK_CLIENT_ONLY, contact_id=LIVE)
    assert isinstance(got, PaymentInstruction)
    assert got.channel_id == "iban_eur"
    assert got.kind == "bank_transfer"
    assert got.display == "Банківський переказ (EUR)"
    assert got.requisites_ref == "iban_eur"
    # Ф2 заполнит; в Ф0 пусто, но поля существуют — иначе снапшот счёта и
    # провайдерская ссылка потребуют менять форму (§14 п.7).
    assert got.link is None
    assert got.expires_ts is None


def test_channel_mode_auto_is_declared_but_not_implemented_in_phase0():
    """§14 п.9: `mode` — обязательное поле уже в Ф0, `auto` даёт ЯВНУЮ ошибку.
    Иначе в Ф2 mode пришлось бы дописывать во все конфиги клиентов."""
    ch = Channel(id="wise", kind="wise", mode="auto", currency="USD",
                 requisites_template="iban_eur", display="Wise")
    with pytest.raises(RequisitesError, match="auto"):
        resolve_instruction(channel=ch, book=BOOK_BOTH, contact_id=LIVE)
