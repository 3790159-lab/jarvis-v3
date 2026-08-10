# -*- coding: utf-8 -*-
"""Ф0 п.5: боевой конфиг volska с заглушками — сторож, а не документация.

Конфиг клиента здесь проверяется как КОД: прайс сверяется с knowledge.md
литерально, сетка торга — с вилкой прайса, каждая ступень — с текстом обмена.
Одна опечатка в разряде означала бы цену, которой клиент нигде не публиковал
(риск 10.11), и узнали бы мы об этом от клиента.

Состояние на сегодня названо явно и закреплено тестами:
  * реквизитов Ольги ещё НЕТ — книга пуста, и оплата ВКЛЮЧИТЬСЯ НЕ МОЖЕТ;
  * тексты объёма — заглушки с явной пометкой, и они обслуживают только
    дрил-контакты.

Оба факта — не «недоделка, которую забыли», а предохранители: они держат фичу
выключенной ровно до того момента, когда её станет чем наполнить.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chatter.config.loader import ConfigError, load_config
from chatter.config.yaml_edit import set_payments_enabled
from chatter.payments.drill_gate import NotForProduction
from chatter.payments.instructions import RequisitesUnavailable, resolve_instruction
from chatter.payments.money import from_major
from chatter.payments.scope import assert_pricing_usable
from chatter.payments.settings import (
    assert_startable, client_ready_channels, load_payments, usable_channels)

CLIENTS = Path(__file__).resolve().parents[2] / "chatter" / "clients"
DRILL_CONTACT = "8849893367:volska"
LIVE_CONTACT = "5551234:volska"


@pytest.fixture(scope="module")
def volska():
    return load_config(CLIENTS, "volska")


def test_volska_loads_with_payments_off(volska):
    """Значение в файле — ФАКТ, не цель: гардиан деплоит из рабочего дерева, и
    включённая в файле фича встала бы на ребуте без команды владельца."""
    assert volska.settings.payments.enabled is False


def test_only_manual_channels_are_declared(volska):
    """Upwork/PayPal/Wise из knowledge в конфиг не выписаны: это merchant-каналы,
    а обещание автоподтверждения на нереализованной интеграции — риск 10.3."""
    pay = volska.settings.payments
    assert [c.id for c in pay.channels] == ["iban_usd", "iban_eur"]
    assert {c.mode for c in pay.channels} == {"manual"}


def test_price_bounds_are_literals_from_knowledge(volska):
    """Валидатор прайса уже отработал на загрузке — здесь фиксируем, что
    сверяются именно опубликованные числа, а не наши."""
    positions = volska.settings.payments.pricing.positions
    assert positions["smm_month"].high == from_major(900, "USD")
    assert positions["smm_month"].low == from_major(750, "USD")
    for pos in positions.values():
        for bound in (pos.low, pos.high):
            assert str(bound.minor // 100) in volska.knowledge


def test_every_ladder_step_has_a_scope_text(volska):
    pay = volska.settings.payments
    for pos in pay.pricing.positions.values():
        for step in pos.steps:
            assert step.scope_key in pay.scope_texts, \
                f"ступень {pos.position_id}:{step.scope_key} нечем объяснить"


def test_all_scope_texts_are_marked_as_stubs_today(volska):
    """Пометка ЯВНАЯ. Строка, похожая на TODO, заглушкой не считается — иначе
    валидатор был бы непредсказуем в обе стороны."""
    texts = volska.settings.payments.scope_texts
    assert texts, "тексты объёма пропали — торговать нечем"
    assert all(t.placeholder for t in texts.values()), \
        "часть заглушек уже настоящая — сними pytest-ожидание вместе с последней"


def test_stub_scopes_do_not_serve_a_live_lead(volska):
    """§8.3-бис п.4: конфиг с заглушками не проходит валидацию для НЕ-дрил
    контакта, и проверка идёт ДО разговора — обнаружить заглушку на третьем шаге
    торга значит оборвать диалог в самом дорогом месте."""
    pay = volska.settings.payments
    with pytest.raises(NotForProduction):
        assert_pricing_usable(pay.pricing, pay.scope_texts, contact_id=LIVE_CONTACT)


def test_stub_scopes_are_allowed_for_the_drill_contact(volska):
    pay = volska.settings.payments
    assert_pricing_usable(pay.pricing, pay.scope_texts, contact_id=DRILL_CONTACT)


# ── реквизитов ещё нет: это состояние, а не поломка ────────────────────────

def test_requisites_book_is_empty_today(volska):
    pay = volska.settings.payments
    assert pay.requisites.templates == {} and pay.requisites.test_templates == {}
    assert usable_channels(pay) == () and client_ready_channels(pay) == ()


def test_nobody_gets_requisites_while_the_book_is_empty(volska):
    """Отказ, а не пустая строка: подставлять что-либо вместо реквизитов
    запрещено (правило №4). Дрил-контакт тоже не исключение."""
    pay = volska.settings.payments
    for contact in (LIVE_CONTACT, DRILL_CONTACT):
        with pytest.raises(RequisitesUnavailable):
            resolve_instruction(channel=pay.channels[0], book=pay.requisites,
                                contact_id=contact)


def test_payments_cannot_be_turned_on_before_requisites_exist(volska):
    """Главный сторож этого конфига. Включение сегодня обязано ПАДАТЬ: фича,
    которой нечего сказать, — это ровно тот сломанный дефолт, который назвал
    владелец (§5.1)."""
    enabled = load_payments(
        {**_raw_payments(), "enabled": True},
        requisites_raw={"templates": {}, "test_templates": {}},
        knowledge=volska.knowledge)
    with pytest.raises(Exception, match="iban"):
        assert_startable(enabled, slug="volska")


def test_the_live_file_itself_refuses_to_start_when_switched_on(tmp_path):
    """То же самое, но на РЕАЛЬНОМ файле, а не на собранном в тесте словаре:
    здесь ловится расхождение между конфигом volska и тем, что мы о нём думаем."""
    import shutil
    dst = tmp_path / "clients"
    shutil.copytree(CLIENTS, dst, ignore=shutil.ignore_patterns(".versions"))
    p = dst / "volska" / "settings.yaml"
    p.write_text(set_payments_enabled(p.read_text(encoding="utf-8"), True),
                 encoding="utf-8")
    with pytest.raises(ConfigError, match="iban"):
        load_config(dst, "volska")


def _raw_payments() -> dict:
    import yaml
    raw = yaml.safe_load((CLIENTS / "volska" / "settings.yaml").read_text(encoding="utf-8"))
    return raw["payments"]
