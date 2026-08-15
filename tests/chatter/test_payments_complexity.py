"""Гейт эскалации — СЛОЖНОСТЬ, а не сумма (спека §2.4, решение владельца 2).

Владельца зовём там, где ошибается понимание запроса, а не калькулятор.
Решение принимает КОД: модель лишь фиксирует факты в `QuoteRequest`.

Дефолт закрыт ОТКАЗОМ (урок P17): нет разбора — зовём владельца, а не
«наверное простой заказ».
"""
from __future__ import annotations

import pytest

from chatter.payments.complexity import (
    NeedsOwner, QuoteRequest, RequestedItem, Simple, assess_complexity,
)
from chatter.payments.pricing import load_pricing

KNOWLEDGE = "- Створення логотипа — 300–400 $\n- Упаковка — 300–400 $\n"

PRICING = load_pricing({
    "amount_source": "price_upper",
    "positions": {
        "logo_create": {"title": "Логотип", "currency": "USD", "price_range": [300, 400],
                        "ladder": [{"amount": 400, "scope_key": "full"},
                                   {"amount": 300, "scope_key": "floor"}]},
        "packaging": {"title": "Упаковка", "currency": "USD", "price_range": [300, 400],
                      "ladder": [{"amount": 400, "scope_key": "p_full"},
                                 {"amount": 300, "scope_key": "p_floor"}]},
    },
}, knowledge=KNOWLEDGE)


def _req(**over):
    base = dict(items=(RequestedItem("logo_create", 1, "логотип"),),
                unknown_services=(), volume_note=None, deadline_note=None, parsed=True)
    base.update(over)
    return QuoteRequest(**base)


def test_simple_single_service_bot_prices_it_itself():
    """Решение владельца: простая одиночная услуга — считает сам."""
    got = assess_complexity(_req(), PRICING)
    assert got == Simple("logo_create", 1)


def test_more_than_one_service_goes_to_owner():
    got = assess_complexity(_req(items=(RequestedItem("logo_create", 1, "лого"),
                                        RequestedItem("packaging", 1, "упаковка"))), PRICING)
    assert isinstance(got, NeedsOwner)
    assert "multiple_services" in got.reasons


def test_one_service_several_units_goes_to_owner():
    """«Три логотипи» — масштаб меняет и цену, и производственный план."""
    got = assess_complexity(_req(items=(RequestedItem("logo_create", 3, "три лого"),)), PRICING)
    assert isinstance(got, NeedsOwner)
    assert "multiple_units" in got.reasons


def test_service_absent_from_price_list_goes_to_owner():
    got = assess_complexity(_req(unknown_services=("розробка сайту",)), PRICING)
    assert isinstance(got, NeedsOwner)
    assert "unknown_service" in got.reasons


def test_position_id_not_in_pricing_goes_to_owner():
    """Классификатор может прислать ключ, которого в конфиге нет (позицию
    убрали, а промпт помнит). Это не «просто», это рассинхрон."""
    got = assess_complexity(_req(items=(RequestedItem("smm", 1, "SMM"),)), PRICING)
    assert isinstance(got, NeedsOwner)
    assert "unknown_position" in got.reasons


@pytest.mark.parametrize("field,reason", [
    ("volume_note", "volume_out_of_norm"),
    ("deadline_note", "deadline_out_of_norm"),
])
def test_volume_or_deadline_out_of_norm_goes_to_owner(field, reason):
    got = assess_complexity(_req(**{field: "на завтра, 5 мовами"}), PRICING)
    assert isinstance(got, NeedsOwner)
    assert reason in got.reasons


def test_no_parse_defaults_to_owner_not_to_simple():
    """Дефолт закрыт отказом. Молчаливый оптимистичный дефолт = класс бага."""
    got = assess_complexity(_req(parsed=False), PRICING)
    assert got == NeedsOwner(("no_parse",))


def test_empty_request_is_not_a_simple_order():
    got = assess_complexity(_req(items=()), PRICING)
    assert isinstance(got, NeedsOwner)
    assert "no_parse" in got.reasons


def test_non_positive_quantity_is_not_silently_one():
    got = assess_complexity(_req(items=(RequestedItem("logo_create", 0, "лого"),)), PRICING)
    assert isinstance(got, NeedsOwner)
    assert "bad_quantity" in got.reasons


def test_all_reasons_are_reported_not_just_the_first():
    """Владельцу нужен весь список: карточка с одной причиной из трёх вводит
    в заблуждение сильнее, чем отсутствие карточки."""
    got = assess_complexity(_req(items=(RequestedItem("logo_create", 2, "два лого"),
                                        RequestedItem("packaging", 1, "упаковка")),
                                 deadline_note="до п'ятниці"), PRICING)
    assert isinstance(got, NeedsOwner)
    assert set(got.reasons) == {"multiple_services", "multiple_units", "deadline_out_of_norm"}


def test_reasons_are_ordered_by_declaration_not_by_discovery():
    """Порядок причин — по объявлению в REASONS.

    Вход подобран так, что порядок ОБНАРУЖЕНИЯ обратен объявленному:
    `bad_quantity` находится в цикле раньше, чем `unknown_position`, а в REASONS
    стоит позже. Сравнение двух прогонов между собой здесь было бы слепым —
    мутация «отдавать в порядке обхода» его не роняла (DEV-26)."""
    got = assess_complexity(_req(items=(RequestedItem("smm", 0, "SMM"),)), PRICING)
    assert isinstance(got, NeedsOwner)
    assert got.reasons == ("unknown_position", "bad_quantity")


def test_order_does_not_depend_on_how_the_lead_listed_services():
    a = assess_complexity(_req(items=(RequestedItem("logo_create", 2, "x"),
                                      RequestedItem("packaging", 1, "y"))), PRICING)
    b = assess_complexity(_req(items=(RequestedItem("packaging", 1, "y"),
                                      RequestedItem("logo_create", 2, "x"))), PRICING)
    assert a.reasons == b.reasons


def test_reasons_carry_no_duplicates():
    """Две неопознанные услуги не должны давать `unknown_service` дважды —
    владелец читает список причин глазами."""
    got = assess_complexity(_req(items=(RequestedItem(None, 1, "щось"),),
                                 unknown_services=("сайт", "додаток")), PRICING)
    assert got.reasons.count("unknown_service") == 1


def test_result_is_one_of_two_shapes_only():
    """Ни None, ни строка, ни bool: вызыватель не должен уметь ошибиться."""
    for req in (_req(), _req(parsed=False)):
        assert isinstance(assess_complexity(req, PRICING), (Simple, NeedsOwner))


# ── выбранный объём доезжает до вердикта (решение владельца 12.08) ─────────

TIER_KNOWLEDGE = "- Створення логотипа — 300 $ або 400 $ залежно від обсягу\n"
TIER_PRICING = load_pricing({
    "amount_source": "price_upper",
    "positions": {
        "logo_create": {
            "title": "Логотип", "currency": "USD",
            "tiers": [
                {"id": "basic", "amount": 300, "tier_text_key": "t_basic"},
                {"id": "standard", "amount": 400, "tier_text_key": "t_std"},
            ]},
    },
}, knowledge=TIER_KNOWLEDGE)


def _tier_req(tier_id):
    return QuoteRequest(items=(RequestedItem("logo_create", 1, "логотип",
                                             tier_id=tier_id),))


def test_the_chosen_tier_reaches_the_verdict():
    """Без ступени в вердикте счёт не знает, за какой объём он выставлен —
    и выставит его по верхней ступени, которую лид не выбирал."""
    assert assess_complexity(_tier_req("basic"), TIER_PRICING) == Simple(
        "logo_create", tier_id="basic", qty=1)


def test_no_tier_chosen_is_still_simple():
    """Лид объёма не назвал — политика прежняя (price_upper с оговоркой), и это
    рабочий исход, а не повод звать владельца."""
    assert assess_complexity(_tier_req(None), TIER_PRICING) == Simple(
        "logo_create", tier_id=None, qty=1)


def test_an_unknown_tier_calls_the_owner():
    """Дефолт закрыт отказом (P17): подставить вместо неузнанной ступени
    верхнюю — значит выставить счёт за объём, которого лид не выбирал."""
    assert assess_complexity(_tier_req("люкс"), TIER_PRICING) == NeedsOwner(
        ("unknown_tier",))


def test_a_tier_named_for_a_position_without_tiers_calls_the_owner():
    """Ярусов у позиции нет, а объём назван — разбор разошёлся с конфигом."""
    req = QuoteRequest(items=(RequestedItem("logo_create", 1, "логотип",
                                            tier_id="basic"),))
    assert assess_complexity(req, PRICING) == NeedsOwner(("unknown_tier",))


def test_a_plain_position_still_needs_no_tier():
    assert assess_complexity(_req(), PRICING) == Simple("logo_create", qty=1)
