# -*- coding: utf-8 -*-
"""Секция `payments` конфига и валидатор СТАРТА (спека §5.1, §8.3-бис).

Решение владельца 5: фича работает из коробки, и конфиг, при котором бот молчит
про реквизиты, считается СЛОМАННЫМ, а не осторожным. Отсюда две вещи, которые
проверяет этот файл:

1. **Тумблер один.** `enabled` в файле — ФАКТ, не цель: гардиан деплоит из
   рабочего дерева, и включённая в файле фича поднялась бы на ребуте без команды
   владельца (та же причина, что у `funnel_gate`). Включает команда пульта.
2. **Валидатор — сторож, а не документация.** `enabled: true` без единого канала,
   которым можно ответить, роняет СТАРТ. Тихое «включено, но сказать нечего»
   запрещено.

Все ошибки — исключения, ни одного молчаливого дефолта: на деньгах молчаливый
дефолт это класс бага (урок P17).
"""
from __future__ import annotations

import logging

import pytest

from chatter.payments.money import from_major
from chatter.payments.settings import (
    PaymentsConfig, PaymentsConfigError, assert_startable,
    client_ready_channels, load_payments, usable_channels)

KNOWLEDGE = "Пакет ведення: від 600 USD до 900 USD на місяць."

CHANNEL = {
    "id": "iban_eur",
    "kind": "bank_transfer",
    "mode": "manual",
    "currency": "USD",
    "requisites_template": "iban_main",
    "display": "Банківський переказ",
}

PRICING = {
    "amount_source": "price_upper",
    "positions": {
        "smm_basic": {
            "title": "Ведення",
            "currency": "USD",
            "price_range": [600, 900],
            "ladder": [
                {"amount": 900, "scope_key": "full"},
                {"amount": 600, "scope_key": "lite"},
            ],
        },
    },
}

SCOPE_TEXTS = {"full": "повний обсяг", "lite": "менше сторіз"}


def _raw(**over) -> dict:
    base = {"channels": [dict(CHANNEL)], "pricing": PRICING, "scope_texts": SCOPE_TEXTS}
    base.update(over)
    return base


def _book(*, client=True, test=False) -> dict:
    out: dict = {}
    if client:
        out["templates"] = {"iban_main": {"body": "IBAN UA00 0000"}}
    if test:
        out["test_templates"] = {"iban_main": {"body": "TEST IBAN UA99 9999"}}
    return out


def _load(raw=None, book=None, knowledge=KNOWLEDGE) -> PaymentsConfig:
    return load_payments(_raw() if raw is None else raw,
                         requisites_raw=_book() if book is None else book,
                         knowledge=knowledge)


# ── дефолты: фича работает из коробки ──────────────────────────────────────

def test_missing_block_gives_disabled_config_with_working_defaults():
    """Блок можно не писать вовсе — это НЕ ошибка. Ошибка была бы обратной:
    клиент, забывший блок, не должен переставать стартовать."""
    cfg = load_payments(None, requisites_raw=None, knowledge="")
    assert cfg.enabled is False
    assert cfg.escalate_on_complexity is True
    assert cfg.owner_approval_above is None
    assert (cfg.daily_invoice_cap, cfg.per_contact_invoice_cap, cfg.due_hours) == (20, 3, 72)


def test_enabled_defaults_to_false_even_with_a_full_block():
    """`enabled: false` в файле — не осторожность, а предохранитель деплоя:
    гардиан поднимает рабочее дерево, и включённая в файле фича встала бы на
    ребуте без команды владельца."""
    assert _load().enabled is False


def test_unknown_key_in_the_block_is_loud():
    with pytest.raises(PaymentsConfigError, match="due_hourz"):
        _load(_raw(due_hourz=48))


# ── тумблер: строгий bool ──────────────────────────────────────────────────

def test_enabled_must_be_a_real_bool_not_a_truthy_string():
    """`enabled: "no"` — строка, и bool("no") это True. На тумблере денег
    молчаливое приведение типов означает включённую фичу там, где владелец
    писал «выключено»."""
    with pytest.raises(PaymentsConfigError, match="enabled"):
        _load(_raw(enabled="no"))


def test_enabled_true_is_accepted_as_a_value():
    assert _load(_raw(enabled=True)).enabled is True


# ── каналы ─────────────────────────────────────────────────────────────────

def test_channel_fields_are_parsed_into_the_final_form():
    ch = _load().channels[0]
    assert (ch.id, ch.kind, ch.mode, ch.currency) == ("iban_eur", "bank_transfer", "manual", "USD")
    assert (ch.requisites_template, ch.display) == ("iban_main", "Банківський переказ")


def test_duplicate_channel_id_is_an_error():
    """Кнопки и снапшоты счёта адресуют канал по id — два канала с одним id
    означают, что счёт ссылается неизвестно на какой."""
    with pytest.raises(PaymentsConfigError, match="iban_eur"):
        _load(_raw(channels=[dict(CHANNEL), dict(CHANNEL)]))


def test_unknown_channel_mode_is_an_error():
    with pytest.raises(PaymentsConfigError, match="mode"):
        _load(_raw(channels=[dict(CHANNEL, mode="semi")]))


def test_channel_currency_must_be_iso_not_a_symbol():
    with pytest.raises(PaymentsConfigError, match="currency"):
        _load(_raw(channels=[dict(CHANNEL, currency="$")]))


def test_channel_without_requisites_template_is_an_error():
    with pytest.raises(PaymentsConfigError, match="requisites_template"):
        _load(_raw(channels=[{k: v for k, v in CHANNEL.items()
                              if k != "requisites_template"}]))


# ── числовые предохранители ────────────────────────────────────────────────

@pytest.mark.parametrize("field", ["daily_invoice_cap", "per_contact_invoice_cap", "due_hours"])
def test_caps_and_due_hours_must_be_positive(field):
    """Кап `0` не «запретить всё», а «сломанный конфиг»: анти-луп с нулём
    выключает фичу молча, и владелец узнает об этом от клиента."""
    with pytest.raises(PaymentsConfigError, match=field):
        _load(_raw(**{field: 0}))


def test_owner_approval_above_is_money_with_currency_not_a_bare_number():
    """Голое число здесь означало бы «в какой-то валюте» — а порог, сравниваемый
    не с той валютой, это backstop, который молча не срабатывает."""
    cfg = _load(_raw(owner_approval_above={"amount": 5000, "currency": "USD"}))
    assert cfg.owner_approval_above == from_major(5000, "USD")


def test_owner_approval_above_bare_number_is_an_error():
    with pytest.raises(PaymentsConfigError, match="owner_approval_above"):
        _load(_raw(owner_approval_above=5000))


def test_owner_approval_above_is_off_by_default():
    """Решение 2: порог по сумме — backstop, а не основной гейт (тот по
    сложности). Выключен, пока владелец не включил осознанно."""
    assert _load().owner_approval_above is None


# ── валидатор СТАРТА ───────────────────────────────────────────────────────

def test_disabled_config_never_blocks_start():
    """Выключенная фича не имеет права ронять старт — иначе один кривой блок
    payments лишает клиента бота целиком."""
    assert_startable(load_payments({"channels": []}, requisites_raw=None, knowledge=""),
                     slug="volska")


def test_enabled_without_channels_fails_start():
    cfg = _load(_raw(enabled=True, channels=[]))
    with pytest.raises(PaymentsConfigError, match="ни одного канала"):
        assert_startable(cfg, slug="volska")


def test_enabled_with_a_channel_but_no_requisites_at_all_fails_start():
    """Ровно тот сломанный дефолт, который назвал владелец: «включено, но
    сказать нечего». Канал есть, а тела реквизитов нет ни в одной книге."""
    cfg = _load(_raw(enabled=True), book={})
    with pytest.raises(PaymentsConfigError, match="iban_main"):
        assert_startable(cfg, slug="volska")


def test_enabled_with_empty_requisites_body_fails_start():
    """Пробел в конфиге не имеет права стать «Реквізити: » в живом диалоге —
    пустое тело равно отсутствующему."""
    cfg = _load(_raw(enabled=True), book={"templates": {"iban_main": {"body": "   "}}})
    with pytest.raises(PaymentsConfigError, match="iban_main"):
        assert_startable(cfg, slug="volska")


def test_enabled_with_client_requisites_starts_clean(caplog):
    cfg = _load(_raw(enabled=True))
    with caplog.at_level(logging.WARNING):
        assert_startable(cfg, slug="volska")
    assert not [r for r in caplog.records if "payments" in r.getMessage()]


def test_auto_channel_alone_fails_start():
    """`mode: auto` объявляет намерение, а не факт, и в Ф0 не реализован вовсе.
    Единственный auto-канал = включённая фича, которой нечем ответить."""
    cfg = _load(_raw(enabled=True, channels=[dict(CHANNEL, mode="auto")]))
    with pytest.raises(PaymentsConfigError, match="auto"):
        assert_startable(cfg, slug="volska")


# ── состояние дрила: только тестовые реквизиты ─────────────────────────────

def test_test_only_requisites_start_but_shout(caplog):
    """Дрил-состояние (§8.3-бис): реквизиты для теста дал владелец, клиентских
    ещё нет. Старт разрешён — иначе живой прогон Ф0 невозможен, — но молчать об
    этом нельзя: ЖИВОЙ лид получит отказ, а не реквизиты."""
    cfg = _load(_raw(enabled=True), book=_book(client=False, test=True))
    with caplog.at_level(logging.WARNING):
        assert_startable(cfg, slug="volska")
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "volska" in msg and "iban_eur" in msg


def test_test_only_requisites_are_not_client_ready():
    cfg = _load(_raw(enabled=True), book=_book(client=False, test=True))
    assert [c.id for c in usable_channels(cfg)] == ["iban_eur"]
    assert client_ready_channels(cfg) == ()


def test_client_requisites_make_the_channel_client_ready():
    cfg = _load(_raw(enabled=True))
    assert [c.id for c in client_ready_channels(cfg)] == ["iban_eur"]


# ── прайс и тексты объёма едут той же секцией ──────────────────────────────

def test_pricing_is_validated_against_knowledge_on_load():
    """Валидатор прайса — тот же сторож старта: цена, которой нет в knowledge,
    это цена, которой клиент нигде не публиковал."""
    with pytest.raises(PaymentsConfigError, match="knowledge"):
        _load(knowledge="прайс не опубликован")


def test_scope_texts_are_loaded_with_placeholder_flag():
    cfg = load_payments(
        _raw(scope_texts={"full": "повний обсяг",
                          "lite": {"text": "ЗАГЛУШКА", "placeholder": True}}),
        requisites_raw=_book(), knowledge=KNOWLEDGE)
    assert cfg.scope_texts["lite"].placeholder is True
    assert cfg.scope_texts["full"].placeholder is False


def test_ladder_step_without_scope_text_fails_start():
    """Ступень, которую нечем объяснить, — это «молча дешевле» на третьем шаге
    торга. Ловим ДО разговора, а не в самом дорогом месте."""
    cfg = _load(_raw(enabled=True, scope_texts={"full": "повний обсяг"}))
    with pytest.raises(PaymentsConfigError, match="lite"):
        assert_startable(cfg, slug="volska")
