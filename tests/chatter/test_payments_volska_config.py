# -*- coding: utf-8 -*-
"""Ф0 п.5: боевой конфиг volska с заглушками — сторож, а не документация.

Конфиг клиента здесь проверяется как КОД: прайс сверяется с knowledge.md
литерально, сетка торга — с вилкой прайса, каждая ступень — с текстом обмена.
Одна опечатка в разряде означала бы цену, которой клиент нигде не публиковал
(риск 10.11), и узнали бы мы об этом от клиента.

Закрепляются ПРАВИЛА, а не сегодняшний снимок прода:
  * включение проходит тогда и только тогда, когда есть чем ответить — пустая
    книга реквизитов роняет старт при любом значении тумблера;
  * тексты объёма, помеченные заглушками, обслуживают только дрил-контакты;
  * загрузка конфига от значения тумблера не зависит вовсе.

Разница не косметическая. Тест, утверждающий «сегодня выключено» или «сегодня
книга пуста», краснеет в тот момент, когда работу ДОДЕЛАЛИ, — и приучает
пролистывать красное. Такие тесты здесь уже были и переписаны (см. блок ниже
про requisites.yaml); тумблер `enabled` был последним из них.
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
# Форма ТРЁХСЕГМЕНТНАЯ, вместе с каноном `DRILL_CONTACTS` (спека web-c §4).
# Литералом, а не выборкой из канона: список, выведенный из проверяемого кода,
# согласен с ним по определению ([[jarvis-literal-lists-not-introspection]]),
# и этот стенд перестал бы отличать «дрил-контакт» от «любой контакт» ровно в
# тот день, когда канон опустеет. Расхождение с каноном держит
# `test_payments_drill_gate.py`.
DRILL_CONTACT = "telegram:8849893367:volska"
LIVE_CONTACT = "telegram:5551234:volska"


@pytest.fixture(scope="module")
def volska():
    return load_config(CLIENTS, "volska")


def test_the_config_loads_at_either_toggle_value(volska):
    """Инвариант вместо снимка: конфиг обязан грузиться И при выключенной, И
    при включённой оплате, и загрузка обязана донести значение без потерь.

    Прежде здесь стояло `enabled is False`, то есть СЕГОДНЯШНЕЕ состояние прода.
    Тест покраснел ровно в тот момент, когда владелец законно включил оплату
    командой пульта (7e5e47eb) — красное на правильном действии учит не верить
    сторожу. Ровно этот разбор уже записан ниже (см. блок про requisites.yaml),
    и этот тест его пропустил.

    Чего здесь НЕТ намеренно: утверждения, что тумблер нельзя поднять «пустым».
    Это держит `assert_startable` — два теста ниже; смешивать два инварианта в
    одном стороже значит потерять оба при первой же правке."""
    raw = _raw_payments()
    for value in (False, True):
        loaded = load_payments({**raw, "enabled": value},
                               requisites_raw=_raw_requisites(),
                               knowledge=volska.knowledge)
        assert loaded.enabled is value, \
            f"загрузка потеряла тумблер: просили {value}, получили {loaded.enabled}"

    # Боевой файл — какое бы значение в нём ни стояло сегодня. Строгий bool, а
    # не «правдоподобное»: `enabled: "no"` это строка, и она означала бы
    # включённую оплату (см. _bool в chatter/payments/settings.py).
    assert volska.settings.payments.enabled is True or \
        volska.settings.payments.enabled is False


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


def test_the_ladder_never_goes_below_the_published_floor(volska):
    """Правило №6 на БОЕВОМ конфиге: пол сетки торга совпадает с нижней границей
    опубликованной вилки, верх — с верхней. Ниже пола не спускаются никогда.

    Сторож именной намеренно. Валидатор ловит это на загрузке, поэтому до сих
    пор мутация «опустить ступень» краснела ПОБОЧНО — падала фикстура, а в
    списке мутаций сторожем был записан вообще посторонний тест про тумблер.
    Такая связка держится лишь до первой правки валидатора, после чего дыра
    молчит."""
    for pos in volska.settings.payments.pricing.positions.values():
        assert pos.steps[-1].amount == pos.low, \
            f"{pos.position_id}: пол сетки {pos.steps[-1].amount.minor} ≠ " \
            f"опубликованному полу {pos.low.minor}"
        assert pos.steps[0].amount == pos.high, \
            f"{pos.position_id}: верх сетки {pos.steps[0].amount.minor} ≠ " \
            f"опубликованному верху {pos.high.minor}"
        for step in pos.steps:
            assert pos.low.minor <= step.amount.minor <= pos.high.minor, \
                f"{pos.position_id}: ступень {step.amount.minor} вне вилки"


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


# ── книга реквизитов: ИНВАРИАНТЫ, а не сегодняшнее состояние ───────────────
#
# `requisites.yaml` untracked (в нём чужие банковские данные), поэтому его
# содержимое зависит от машины: на машине владельца книга заполнена, в свежем
# клоне файла нет вовсе. Тест, утверждающий «книга сегодня пуста», ловил бы не
# дефект, а факт заполнения — и краснел бы ровно в тот момент, когда всё
# сделано правильно. Такие три теста здесь были и переписаны на инварианты,
# которые верны в ОБОИХ состояниях.


def test_test_requisites_never_reach_a_live_contact(volska):
    """Инвариант §8.3-бис, перебором по всем каналам: `source == "test"`
    влечёт, что контакт дрил-овый. Ловит любую будущую правку резолвера,
    открывающую утечку, — включая те, о которых сейчас никто не думает."""
    pay = volska.settings.payments
    for channel in pay.channels:
        try:
            instruction = resolve_instruction(
                channel=channel, book=pay.requisites, contact_id=LIVE_CONTACT)
        except RequisitesUnavailable:
            continue          # выдать нечего — штатный отказ
        assert instruction.source != "test", \
            f"канал {channel.id}: живой лид получил ТЕСТОВЫЕ реквизиты"


def test_a_body_that_is_handed_out_is_never_blank(volska):
    """Правило №4: отказ, а не пустая строка. «Реквізити: » в живом диалоге —
    это полуотрендеренный ответ, который запрещён (§8.3)."""
    pay = volska.settings.payments
    for channel in pay.channels:
        for contact in (LIVE_CONTACT, DRILL_CONTACT):
            try:
                instruction = resolve_instruction(
                    channel=channel, book=pay.requisites, contact_id=contact)
            except RequisitesUnavailable:
                continue
            assert instruction.body_text.strip(), \
                f"канал {channel.id}: выдано пустое тело вместо отказа"


def test_turning_on_is_allowed_exactly_when_there_is_something_to_answer(volska):
    """Сторож самого правила, а не его сегодняшнего исхода: старт проходит
    ТОГДА И ТОЛЬКО ТОГДА, когда есть хотя бы один канал, которым можно
    ответить. Пустая книга → включение падает; заполненная → проходит."""
    pay = volska.settings.payments
    enabled = load_payments({**_raw_payments(), "enabled": True},
                            requisites_raw=_raw_requisites(),
                            knowledge=volska.knowledge)
    if usable_channels(pay):
        assert_startable(enabled, slug="volska")
    else:
        with pytest.raises(Exception, match="iban"):
            assert_startable(enabled, slug="volska")


def test_an_empty_book_always_blocks_the_switch(volska):
    """Половина инварианта, не зависящая от машины вовсе: с пустой книгой
    включение обязано падать ВСЕГДА. Это тот сломанный дефолт, который назвал
    владелец, — «включено, а сказать нечего» (§5.1)."""
    enabled = load_payments(
        {**_raw_payments(), "enabled": True},
        requisites_raw={"templates": {}, "test_templates": {}},
        knowledge=volska.knowledge)
    with pytest.raises(Exception, match="iban"):
        assert_startable(enabled, slug="volska")


def test_client_channels_stay_unready_until_client_requisites_arrive(volska):
    """Тестовые реквизиты НЕ делают канал готовым к живому лиду: `usable` и
    `client_ready` — разные множества, и путать их нельзя (иначе дрил-состояние
    выглядело бы рабочим)."""
    pay = volska.settings.payments
    ready = {c.id for c in client_ready_channels(pay)}
    assert ready == {c.id for c in pay.channels
                     if pay.requisites.templates.get(c.requisites_template, "").strip()}


def _raw_requisites() -> dict:
    import yaml
    path = CLIENTS / "volska" / "requisites.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _raw_payments() -> dict:
    import yaml
    raw = yaml.safe_load((CLIENTS / "volska" / "settings.yaml").read_text(encoding="utf-8"))
    return raw["payments"]


# ── алиасы: боевой конфиг обязан узнавать свои же позиции ──────────────────

def test_every_position_has_aliases(volska):
    """Позиция без алиасов не узнаётся предпассом НИКОГДА: каждый запрос про неё
    уходит владелице как неразобранный. Это не поломка кода, а тихо выключенная
    половина фичи — поэтому сторож стоит на конфиге."""
    pricing = volska.settings.payments.pricing
    missing = sorted(pid for pid, pos in pricing.positions.items() if not pos.aliases)
    assert missing == [], f"позиции без алиасов: {', '.join(missing)}"


def test_each_alias_resolves_back_to_its_own_position(volska):
    """Обратный ход: каждое слово из конфига обязано привести РОВНО к своей
    позиции. Опечатка в алиасе или пересечение с чужим — это названная цена за
    не ту работу, и заметить это на живом лиде дороже всего."""
    from chatter.payments.intent import read_intent

    pricing = volska.settings.payments.pricing
    for pid, pos in pricing.positions.items():
        for alias in pos.aliases:
            got = [i.position_id for i in read_intent(alias, pricing).request.items]
            assert got == [pid], f"алиас {alias!r} позиции {pid!r} дал {got}"
