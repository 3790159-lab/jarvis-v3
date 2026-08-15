"""Прайс и сетка торга (спека §2.3, решение владельца 3).

Торг — ДИСКРЕТНАЯ СЕТКА констант, а не арифметика: у готовых чисел нет ни одной
болезни вычисленных. Пол — нижняя граница вилки, ниже нельзя НИКОГДА.

Валидатор здесь — сторож, а не документация: расхождение сетки с прайсом должно
ронять СТАРТ, иначе бот однажды назовёт цену, которой в прайсе нет (риск 10.11).
"""
from __future__ import annotations

import pytest

from chatter.payments.money import Money
from chatter.payments.pricing import (
    PricingConfigError, is_floor, load_pricing, next_step, step_at,
)

KNOWLEDGE = """
# Послуги та ціни
- Створення логотипа — 300–400 $
- Рефайн (редизайн) логотипа — 200 $
"""

RAW = {
    "amount_source": "price_upper",
    "positions": {
        "logo_create": {
            "title": "Створення логотипа",
            "currency": "USD",
            "price_range": [300, 400],
            "ladder": [
                {"amount": 400, "scope_key": "logo_full"},
                {"amount": 375, "scope_key": "logo_s375"},
                {"amount": 350, "scope_key": "logo_s350"},
                {"amount": 325, "scope_key": "logo_s325"},
                {"amount": 300, "scope_key": "logo_floor"},
            ],
        },
        "logo_refine": {                      # позиция БЕЗ сетки — торг невозможен
            "title": "Рефайн логотипа",
            "currency": "USD",
            "price_range": [200, 200],
            "ladder": [{"amount": 200, "scope_key": "refine_full"}],
        },
    },
}


def _raw(**over):
    import copy
    raw = copy.deepcopy(RAW)
    raw.update(over)
    return raw


def test_loads_positions_with_money_amounts():
    p = load_pricing(RAW, knowledge=KNOWLEDGE)
    pos = p.positions["logo_create"]
    assert pos.currency == "USD"
    assert step_at(pos, 0).amount == Money(40000, "USD")
    assert step_at(pos, 0).scope_key == "logo_full"


def test_top_step_is_the_upper_bound_because_amount_source_is_price_upper():
    """Решение владельца 1: бот называет ВЕРХНЮЮ границу. Ступень 0 обязана ей
    быть — иначе первое же названное число разойдётся с политикой."""
    p = load_pricing(RAW, knowledge=KNOWLEDGE)
    assert p.amount_source == "price_upper"
    assert p.positions["logo_create"].top.amount == Money(40000, "USD")


def test_floor_is_the_lower_bound_and_is_recognised():
    p = load_pricing(RAW, knowledge=KNOWLEDGE)
    pos = p.positions["logo_create"]
    assert pos.floor.amount == Money(30000, "USD")
    assert is_floor(pos, 4) is True
    assert is_floor(pos, 3) is False


def test_next_step_walks_down_one_rung_at_a_time():
    """§2.3: шаг только на ОДНУ ступень за ход. Прыжок через ступень — это
    подарок, которого никто не просил."""
    p = load_pricing(RAW, knowledge=KNOWLEDGE)
    pos = p.positions["logo_create"]
    assert next_step(pos, 0).amount == Money(37500, "USD")
    assert next_step(pos, 3).amount == Money(30000, "USD")


def test_below_the_floor_there_is_nothing():
    """Пол не пробивается. None — это «зови владельца», а не «придумай сам»."""
    p = load_pricing(RAW, knowledge=KNOWLEDGE)
    assert next_step(p.positions["logo_create"], 4) is None


def test_single_step_position_has_no_bargaining_room():
    p = load_pricing(RAW, knowledge=KNOWLEDGE)
    pos = p.positions["logo_refine"]
    assert is_floor(pos, 0) is True
    assert next_step(pos, 0) is None


# --- валидатор: всё ниже обязано ронять СТАРТ -------------------------------

def test_ladder_top_must_equal_price_range_upper():
    raw = _raw()
    raw["positions"]["logo_create"]["ladder"][0]["amount"] = 420
    with pytest.raises(PricingConfigError, match="верх"):
        load_pricing(raw, knowledge=KNOWLEDGE)


def test_ladder_floor_must_equal_price_range_lower():
    raw = _raw()
    raw["positions"]["logo_create"]["ladder"][-1]["amount"] = 250
    with pytest.raises(PricingConfigError, match="пол"):
        load_pricing(raw, knowledge=KNOWLEDGE)


def test_range_bounds_must_be_literals_in_knowledge():
    """Правило №5 с технической гарантией: если границы нет в knowledge, бот
    назовёт число, которого клиент нигде не публиковал."""
    with pytest.raises(PricingConfigError, match="knowledge"):
        load_pricing(RAW, knowledge="# Послуги та ціни\n- Створення логотипа — за домовленістю\n")


def test_intermediate_steps_need_not_be_in_knowledge():
    """Осознанное исключение: промежуточные ступени — внутренние переговорные
    и в модель НЕ попадают вовсе (§14 п.16), поэтому обеспечивать их knowledge
    не требуется. Верх и пол — обязаны, они публичные."""
    load_pricing(RAW, knowledge=KNOWLEDGE)   # 375/350/325 в KNOWLEDGE нет — и это норма


def test_ladder_must_descend_strictly():
    raw = _raw()
    raw["positions"]["logo_create"]["ladder"][2]["amount"] = 380
    with pytest.raises(PricingConfigError, match="убыв"):
        load_pricing(raw, knowledge=KNOWLEDGE)


def test_every_step_must_carry_a_distinct_scope():
    """Без своего объёма ступень — это «молча дешевле» (риск 10.12): цена вниз,
    обмена нет. Одинаковый scope на двух ступенях — та же болезнь."""
    raw = _raw()
    raw["positions"]["logo_create"]["ladder"][1]["scope_key"] = "logo_full"
    with pytest.raises(PricingConfigError, match="scope"):
        load_pricing(raw, knowledge=KNOWLEDGE)

    raw = _raw()
    del raw["positions"]["logo_create"]["ladder"][1]["scope_key"]
    with pytest.raises(PricingConfigError, match="scope"):
        load_pricing(raw, knowledge=KNOWLEDGE)


def test_currency_must_be_iso_not_symbol():
    raw = _raw()
    raw["positions"]["logo_create"]["currency"] = "$"
    with pytest.raises(PricingConfigError):
        load_pricing(raw, knowledge=KNOWLEDGE)


def test_empty_ladder_is_an_error():
    raw = _raw()
    raw["positions"]["logo_create"]["ladder"] = []
    with pytest.raises(PricingConfigError):
        load_pricing(raw, knowledge=KNOWLEDGE)


def test_unknown_amount_source_rejected():
    with pytest.raises(PricingConfigError):
        load_pricing(_raw(amount_source="lower_bound"), knowledge=KNOWLEDGE)


# ── алиасы: как позицию называет КЛИЕНТ (для предпасса intent.py) ──────────

def test_aliases_are_casefolded_and_deduped():
    """Регистр снимается один раз, при загрузке. Приводить его на каждом ходу
    разговора — это тихий шанс когда-нибудь забыть."""
    raw = _raw()
    raw["positions"]["logo_create"]["aliases"] = ["Логотип", "логотип", "ЛОГО"]
    pricing = load_pricing(raw, knowledge=KNOWLEDGE)
    assert pricing.positions["logo_create"].aliases == ("логотип", "лого")


def test_a_multiword_alias_is_an_error_not_a_dead_string():
    """Предпасс сравнивает алиас с ОДНИМ словом лида, поэтому «фірмовий стиль»
    не совпадёт никогда. Молча непригодный алиас хуже отсутствующего: в конфиге
    он выглядит работающим, а позиция не узнаётся ни разу."""
    raw = _raw()
    raw["positions"]["logo_create"]["aliases"] = ["фірмовий стиль"]
    with pytest.raises(PricingConfigError, match="одн"):
        load_pricing(raw, knowledge=KNOWLEDGE)


def test_an_empty_alias_is_an_error():
    raw = _raw()
    raw["positions"]["logo_create"]["aliases"] = [""]
    with pytest.raises(PricingConfigError):
        load_pricing(raw, knowledge=KNOWLEDGE)


def test_a_position_without_aliases_loads_but_is_unrecognisable():
    """Не ошибка: клиент вправе не давать синонимов. Цена этого названа вслух —
    позиция не узнаётся предпассом никогда и уходит владельцу."""
    pricing = load_pricing(_raw(), knowledge=KNOWLEDGE)
    assert pricing.positions["logo_create"].aliases == ()


# ── публичные ярусы по объёму (решение владельца 12.08) ────────────────────
# Ступень — ОДНА цена, названная вслух вместе с описанием объёма. Вилки, пола
# и торга внутри ступени не существует как понятия: дешевле — это меньший
# объём, а не тихая скидка за тот же.

TIER_KNOWLEDGE = """
# Послуги та ціни
- Створення логотипа — 300 $ або 400 $ залежно від обсягу
- Рефайн (редизайн) логотипа — 200 $
"""

TIER_RAW = {
    "amount_source": "price_upper",
    "positions": {
        "logo_create": {
            "title": "Створення логотипа",
            "currency": "USD",
            "tiers": [
                {"id": "logo_basic", "amount": 300,
                 "tier_text_key": "tier_logo_basic"},
                {"id": "logo_standard", "amount": 400,
                 "tier_text_key": "tier_logo_standard"},
            ],
        },
        "logo_refine": {                      # позиция БЕЗ ярусов — как сегодня
            "title": "Рефайн логотипа",
            "currency": "USD",
            "price_range": [200, 200],
            "ladder": [{"amount": 200, "scope_key": "refine_full"}],
        },
    },
}


def _tier_raw(**position_overrides):
    """Копия TIER_RAW с правками в logo_create."""
    import copy
    raw = copy.deepcopy(TIER_RAW)
    raw["positions"]["logo_create"].update(position_overrides)
    return raw


def test_a_tiered_position_loads_its_public_steps():
    pricing = load_pricing(TIER_RAW, knowledge=TIER_KNOWLEDGE)
    pos = pricing.positions["logo_create"]
    assert [t.id for t in pos.tiers] == ["logo_basic", "logo_standard"]
    assert [t.amount.minor for t in pos.tiers] == [30000, 40000]
    assert [t.tier_text_key for t in pos.tiers] == [
        "tier_logo_basic", "tier_logo_standard"]


def test_a_tiered_position_has_no_ladder_at_all():
    """Торга внутри ступени не существует: пол, шаги и перекрытия исчезают
    вместе с вилкой. Пустая сетка — не «забыли заполнить», а инвариант."""
    pricing = load_pricing(TIER_RAW, knowledge=TIER_KNOWLEDGE)
    assert pricing.positions["logo_create"].steps == ()


def test_position_bounds_are_derived_from_the_tiers():
    """Границы позиции больше не задаются отдельно — их НЕЛЬЗЯ задать
    отдельно, иначе появится второй источник цены на одну позицию."""
    pos = load_pricing(TIER_RAW, knowledge=TIER_KNOWLEDGE).positions["logo_create"]
    assert (pos.low.minor, pos.high.minor) == (30000, 40000)


def test_ladder_next_to_tiers_is_a_start_error():
    """Два списка на одной позиции — заготовка расхождения: разъедутся не
    сразу, а через месяц, и наружу это выйдет ценой."""
    raw = _tier_raw(ladder=[{"amount": 400, "scope_key": "logo_full"}])
    with pytest.raises(PricingConfigError, match="ladder"):
        load_pricing(raw, knowledge=TIER_KNOWLEDGE)


def test_price_range_next_to_tiers_is_a_start_error():
    raw = _tier_raw(price_range=[300, 400])
    with pytest.raises(PricingConfigError, match="price_range"):
        load_pricing(raw, knowledge=TIER_KNOWLEDGE)


def test_a_single_tier_is_not_a_choice():
    """Одна ступень — это не выбор объёма, а обычная позиция. Такой конфиг
    почти наверняка недописан, и молчать об этом нельзя."""
    raw = _tier_raw(tiers=[{"id": "only", "amount": 400,
                            "tier_text_key": "tier_logo_standard"}])
    with pytest.raises(PricingConfigError, match="ступен"):
        load_pricing(raw, knowledge=TIER_KNOWLEDGE)


def test_tiers_must_strictly_ascend():
    """Порядок — публичный: он же порядок перечисления лиду. Две ступени с
    одной ценой означают выбор без разницы в деньгах."""
    raw = _tier_raw(tiers=[
        {"id": "a", "amount": 400, "tier_text_key": "tier_logo_standard"},
        {"id": "b", "amount": 300, "tier_text_key": "tier_logo_basic"},
    ])
    with pytest.raises(PricingConfigError, match="зроста|возраст"):
        load_pricing(raw, knowledge=TIER_KNOWLEDGE)


def test_tier_ids_are_unique():
    raw = _tier_raw(tiers=[
        {"id": "same", "amount": 300, "tier_text_key": "tier_logo_basic"},
        {"id": "same", "amount": 400, "tier_text_key": "tier_logo_standard"},
    ])
    with pytest.raises(PricingConfigError, match="повторя"):
        load_pricing(raw, knowledge=TIER_KNOWLEDGE)


def test_tier_text_keys_are_unique():
    """Два объёма с одним описанием — выбор, в котором нечего выбирать."""
    raw = _tier_raw(tiers=[
        {"id": "a", "amount": 300, "tier_text_key": "tier_logo_basic"},
        {"id": "b", "amount": 400, "tier_text_key": "tier_logo_basic"},
    ])
    with pytest.raises(PricingConfigError, match="tier_text_key"):
        load_pricing(raw, knowledge=TIER_KNOWLEDGE)


def test_a_tier_without_its_text_key_is_a_start_error():
    """Цена без названного объёма — это цена ни за что: лид не поймёт, за что
    он платит больше, и выберет дешёвое или уйдёт."""
    raw = _tier_raw(tiers=[
        {"id": "a", "amount": 300},
        {"id": "b", "amount": 400, "tier_text_key": "tier_logo_standard"},
    ])
    with pytest.raises(PricingConfigError, match="tier_text_key"):
        load_pricing(raw, knowledge=TIER_KNOWLEDGE)


def test_every_tier_amount_must_be_published_in_knowledge():
    """Правило №5 на ярусах читается по КАЖДОЙ ступени: она публичная целиком,
    промежуточных среди них не бывает."""
    raw = _tier_raw(tiers=[
        {"id": "a", "amount": 333, "tier_text_key": "tier_logo_basic"},
        {"id": "b", "amount": 400, "tier_text_key": "tier_logo_standard"},
    ])
    with pytest.raises(PricingConfigError, match="knowledge"):
        load_pricing(raw, knowledge=TIER_KNOWLEDGE)


def test_the_top_offer_of_a_tiered_position_is_its_priciest_tier():
    """Лид объёма не назвал — политика прежняя, price_upper. Верх у ярусной
    позиции это верхняя ступень, а не верх несуществующей вилки."""
    pos = load_pricing(TIER_RAW, knowledge=TIER_KNOWLEDGE).positions["logo_create"]
    assert pos.top.amount.minor == 40000
    assert pos.top.scope_key == "tier_logo_standard"


def test_a_position_without_tiers_keeps_todays_behaviour():
    """Обратная сторона: ярусы — только там, где объём различим. Остальные
    позиции обязаны работать ровно как вчера."""
    pos = load_pricing(TIER_RAW, knowledge=TIER_KNOWLEDGE).positions["logo_refine"]
    assert pos.tiers == ()
    assert pos.steps and pos.top.amount.minor == 20000


def test_tier_lookup_by_id():
    pos = load_pricing(TIER_RAW, knowledge=TIER_KNOWLEDGE).positions["logo_create"]
    assert pos.tier("logo_basic").amount.minor == 30000
    assert pos.tier("нет такой") is None
