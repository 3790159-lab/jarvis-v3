# -*- coding: utf-8 -*-
"""Детерминированный предпасс: что лид попросил (§2.4, источник фактов).

Разбор обязан случиться ДО генерации реплики, иначе цена не может прозвучать в
том же ходу: классификатор отвечает после brain и видит pending_reply. Поэтому
факты собирает КОД по конфигу клиента, а не модель.

Главное свойство, вокруг которого написан весь файл: **дефолт закрыт отказом**.
Не разобрали — зовём владельца. Молчаливый оптимистичный дефолт на деньгах это
класс бага (P17), и здесь он проверяется не одним тестом, а каждым.
"""
from __future__ import annotations

import pytest

from chatter.payments.complexity import NeedsOwner, Simple, assess_complexity
from chatter.payments.intent import read_intent
from chatter.payments.pricing import load_pricing

KNOWLEDGE = "Логотип: $300–$400. Презентация: $100–$200."
RAW = {
    "amount_source": "price_upper",
    "positions": {
        "logo": {
            "title": "Создание логотипа", "currency": "USD",
            "aliases": ["логотип", "лого"],
            "price_range": [300, 400],
            "ladder": [{"amount": 400, "scope_key": "logo_full"},
                       {"amount": 300, "scope_key": "logo_floor"}],
        },
        "presentation": {
            "title": "Презентация", "currency": "USD",
            "aliases": ["презентац", "презу"],
            "price_range": [100, 200],
            "ladder": [{"amount": 200, "scope_key": "pres_full"},
                       {"amount": 100, "scope_key": "pres_floor"}],
        },
    },
}


@pytest.fixture()
def pricing():
    return load_pricing(RAW, knowledge=KNOWLEDGE)


# ── алиасы: позиция узнаётся по слову клиента, а не по ключу конфига ───────

def test_position_is_recognised_through_an_inflected_alias(pricing):
    """«логотипом» — то, как лид реально пишет. Алиас матчится основой:
    точное совпадение словоформы означало бы, что предпасс работает только на
    именительном падеже."""
    intent = read_intent("Хочу заказать логотипом для чайного бренда", pricing)
    assert [i.position_id for i in intent.request.items] == ["logo"]


def test_two_aliases_of_one_position_do_not_double_it(pricing):
    """«логотип» и «лого» — одна позиция. Дубль дал бы multiple_services и увёл
    простой запрос к владельцу без причины."""
    intent = read_intent("логотип, він же лого", pricing)
    assert [i.position_id for i in intent.request.items] == ["logo"]


def test_unknown_service_is_not_silently_dropped(pricing):
    """Услуга вне прайса обязана СТАТЬ фактом, а не исчезнуть: исчезнув, она
    превратила бы «логотип и сайт» в простой запрос на логотип."""
    intent = read_intent("Потрібен сайт", pricing)
    assert assess_complexity(intent.request, pricing) == NeedsOwner(("no_parse",))


# ── количество ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,qty", [
    ("Потрібно 3 логотипи", 3),
    ("Потрібно три логотипи", 3),
    ("Нужно два лого", 2),
    ("Логотип", 1),
])
def test_quantity_is_read_next_to_the_alias(pricing, text, qty):
    intent = read_intent(text, pricing)
    assert intent.request.items[0].qty == qty


def test_quantity_belongs_to_its_own_position(pricing):
    """«2 презентации и логотип» — двойка про презентации, логотип один.
    Количество, приклеенное к чужой позиции, назвало бы не ту цену."""
    intent = read_intent("Нужно 2 презентации и логотип", pricing)
    got = {i.position_id: i.qty for i in intent.request.items}
    assert got == {"presentation": 2, "logo": 1}


def test_a_number_far_from_the_alias_is_not_a_quantity(pricing):
    """«працюємо з 2019 року, потрібен логотип» — год не количество."""
    intent = read_intent("Працюємо з 2019 року, потрібен логотип", pricing)
    assert intent.request.items[0].qty == 1


def test_a_year_right_next_to_the_alias_is_still_not_a_quantity(pricing):
    """Окно расстояния не спасает, когда год стоит вплотную. Нужен второй
    рубеж — правдоподобная граница количества, иначе бот посчитает 2019
    логотипов и назовёт сумму, которой в прайсе нет."""
    intent = read_intent("Ми з 2019 логотип не оновлювали", pricing)
    assert intent.request.items[0].qty == 1


def test_items_are_ordered_by_the_message_not_by_the_config(pricing):
    """Порядок объявлен: карточка владельцу и разбор не должны зависеть от
    того, как отсортированы позиции в конфиге клиента."""
    intent = read_intent("Спочатку презентація, потім логотип", pricing)
    assert [i.position_id for i in intent.request.items] == ["presentation", "logo"]


# ── маркеры объёма и срока (§2.4, отдельными признаками) ──────────────────

def test_deadline_marker_is_captured(pricing):
    intent = read_intent("Логотип, треба терміново", pricing)
    assert intent.request.deadline_note
    assert assess_complexity(intent.request, pricing) == NeedsOwner(
        ("deadline_out_of_norm",))


def test_volume_marker_is_captured(pricing):
    intent = read_intent("Логотип, але хочу 10 концепцій", pricing)
    assert intent.request.volume_note
    assert assess_complexity(intent.request, pricing) == NeedsOwner(
        ("volume_out_of_norm",))


def test_a_plain_single_service_stays_simple(pricing):
    """Обратная сторона: предпасс, который эскалирует всё, не чинит капкан №2.
    Простая одиночная услуга обязана дойти до Simple."""
    intent = read_intent("Скільки коштує логотип?", pricing)
    assert assess_complexity(intent.request, pricing) == Simple("logo", 1)


# ── намерения: цена и готовность платить ──────────────────────────────────

@pytest.mark.parametrize("text", [
    "Скільки коштує логотип?",
    "Сколько стоит логотип?",
    "Яка ціна на логотип?",
    "Какая цена логотипа?",
    "Скиньте прайс, будь ласка",
])
def test_price_question_is_recognised(pricing, text):
    assert read_intent(text, pricing).asks_price


@pytest.mark.parametrize("text", [
    "Куди платити?",
    "Куда вам платить?",
    "Як оплатити логотип?",
    "Виставте рахунок, будь ласка",
    "Выставьте счёт",
    "Готов оплатить",
    "Ок, давайте почнемо",
    "Скиньте реквізити",
])
def test_readiness_to_pay_is_recognised(pricing, text):
    assert read_intent(text, pricing).wants_invoice


@pytest.mark.parametrize("text", [
    "Доброго дня! Бачила вашу роботу",
    "А ви робите логотипи?",
    "Дякую, подумаю",
    # Одиночная основа без пары — обычная речь, а не намерение. Именно на этих
    # двух ломается «упростить пару до одного слова»: платёжное слово в диалоге
    # воронки звучит постоянно, а счёт выставляется один раз.
    "Ми вже платили дизайнеру раніше",
    "Розкажіть, як працює рахунок на Upwork",
])
def test_ordinary_talk_triggers_neither_operation(pricing, text):
    """Ложное срабатывание здесь — это счёт, выставленный тому, кто ничего не
    просил, и карточка владельцу на пустом месте."""
    intent = read_intent(text, pricing)
    assert not intent.wants_invoice
    assert not intent.asks_price


def test_empty_text_parses_to_nothing_rather_than_to_something(pricing):
    intent = read_intent("", pricing)
    assert not intent.wants_invoice and not intent.asks_price
    assert intent.request.parsed is False, \
        "пустой ход отмечен как разобранный — это «клиент ничего не просил» " \
        "вместо «мы не поняли», а зовут владельца только по второму"
    assert assess_complexity(intent.request, pricing) == NeedsOwner(("no_parse",))


def test_pricing_absent_means_no_facts_at_all(pricing):
    """Клиент без прайса: узнавать нечем. Ни одна позиция не имеет права
    «угадаться» — это была бы цена, взятая из воздуха (правило №5)."""
    intent = read_intent("Скільки коштує логотип?", None)
    assert intent.asks_price
    assert intent.request.items == ()
    assert not intent.request.parsed
