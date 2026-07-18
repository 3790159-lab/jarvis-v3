from __future__ import annotations

import pytest

from chatter.core.obligations import unbacked_promise, DEFAULT_PROMISE_TERMS


# --- обещания, которых нет в базе → ОБЯЗАНЫ флагаться --------------------------
FLAG = [
    "Я могу сделать скидку при долгосрочном сотрудничестве",
    "Даю гарантию, что результат вам понравится",
    "Можно оплатить в рассрочку",
    "Перезвоню вам завтра",
    "Дмитрий свяжется с вами",
    "Сделаем для вас исключение",
    "Думаю, договоримся по цене",
    "Обещаю успеть к пятнице",
    "Первый кадр бесплатно в подарок",
    "можу зробити знижку",          # uk: сделаю скидку
    "передзвоню вам пізніше",       # uk: перезвоню
]


@pytest.mark.parametrize("reply", FLAG)
def test_flags_unbacked_promise(reply):
    assert unbacked_promise(reply, knowledge="", promise_terms=DEFAULT_PROMISE_TERMS) is not None


# --- ЧЕСТНЫЕ ОТКАЗЫ → ОБЯЗАНЫ ПРОХОДИТЬ (акцент юзера: бот, который не может ----
# сказать «нет», хуже болтливого; тот же класс, что Сбербанк) -------------------
HONEST_REFUSALS = [
    "Скидок нет",
    "скидок у нас нет",
    "Без гарантий, к сожалению",
    "Рассрочку не делаем",
    "Не могу обещать конкретную дату",
    "Гарантий дать не могу",
    "Скидку сделать не получится",
    "Рассрочки у нас нет",
    "К сожалению, рассрочка не предусмотрена",
    "Никаких скидок мы не даём",
    "знижок немає",                 # uk: скидок нет
    "розстрочку не робимо",         # uk: рассрочку не делаем
]


@pytest.mark.parametrize("reply", HONEST_REFUSALS)
def test_honest_refusal_is_not_flagged(reply):
    assert unbacked_promise(reply, knowledge="", promise_terms=DEFAULT_PROMISE_TERMS) is None


# --- обеспечено knowledge (клиент реально предлагает) → НЕ флагать --------------
def test_positively_backed_by_knowledge_is_not_flagged():
    kb = "Оплата: можно картой или в рассрочку до 3 месяцев."
    assert unbacked_promise("Да, можно оформить рассрочку", knowledge=kb,
                            promise_terms=DEFAULT_PROMISE_TERMS) is None


def test_negated_in_knowledge_still_flags_positive_promise():
    # knowledge содержит стем, но ОТРИЦАТЕЛЬНО («скидок нет») — это НЕ значит,
    # что клиент предлагает скидку. Позитивное обещание Ани всё равно флагаем.
    kb = "Цены фиксированные, скидок нет."
    assert unbacked_promise("Могу сделать скидку", knowledge=kb,
                            promise_terms=DEFAULT_PROMISE_TERMS) is not None


def test_returns_the_matched_term():
    hit = unbacked_promise("Даю гарантию результата", knowledge="",
                           promise_terms=DEFAULT_PROMISE_TERMS)
    assert hit and "гарант" in hit


def test_empty_and_clean_replies_are_none():
    assert unbacked_promise("", knowledge="", promise_terms=DEFAULT_PROMISE_TERMS) is None
    assert unbacked_promise("Здравствуйте! Съёмка длится 2 часа.", knowledge="",
                            promise_terms=DEFAULT_PROMISE_TERMS) is None


def test_no_terms_configured_disables_layer():
    # пустой список термов (не None) — слой не задан, ничего не флагаем
    assert unbacked_promise("Могу сделать скидку", knowledge="", promise_terms=[]) is None
