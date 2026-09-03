# -*- coding: utf-8 -*-
"""C-1 / D2-1: число, названное САМИМ ЛИДОМ, — не выдумка бота.

Спека `docs/superpowers/specs/2026-08-04-chatter-sales-competence.md` §1.2, §4.

Форензика спеки: `_findings` держит правило `large_number` — любое число ≥ 100,
которого нет в knowledge, объявляется необеспеченным. Функция видит только
`reply` и `knowledge`, истории диалога не видит вообще. Поэтому бюджет, который
назвал сам лид, режется как выдумка бота, и «использует цифры клиента» —
критерий приёмки — недостижим НИ ПРИ КАКОМ playbook.

ГРАНИЦА (§4, D2-1). Число лида обеспечивает ПЕРЕСКАЗ («вы называли бюджет
$1000»), но НЕ нашу цену («для вас это будет $1000»). Половина файла —
негативный контроль ровно на эту границу: она же «Риск 5» спеки («лид продиктует
$100 за айдентику, а бот повторит это как цену»). Правка, которая лечит
подавление ценой слепоты к выдуманной цене, обязана падать здесь.
"""
from __future__ import annotations

import pytest

from chatter.core.guardrails import (
    _findings, contains_unbacked_claim, lead_numbers, redact_unbacked,
)

# Прайс без 1000 и без 120: числа лида в нём отсутствуют ПО ПОСТРОЕНИЮ.
KNOWLEDGE = (
    "Комплексна хімчистка салону — 4 500–7 500 грн, тривалість 6–10 годин.\n"
    "Детейлінг-мийка — 1 200–2 000 грн.\n"
)

LEAD_SAID_1000 = ["Бюджет у мене $1000, гостей близько 120."]


def _rules(reply: str, *, lead: frozenset[str] = frozenset()) -> set[str]:
    return {f.rule for f in _findings(reply, KNOWLEDGE, lead_nums=lead)}


# --------------------------------------------------------------------------
# lead_numbers: что вообще считается «числом лида»
# --------------------------------------------------------------------------

def test_lead_numbers_collects_from_lead_texts_only():
    assert lead_numbers(LEAD_SAID_1000) == frozenset({"1000", "120"})


def test_lead_numbers_normalises_thin_spaces_like_findings_do():
    """«1 000» у лида и «1000» в ответе — одно число. Иначе правило не
    сработает ровно там, где лид пишет по-человечески."""
    assert "1000" in lead_numbers(["бюджет 1 000 доларів"])


def test_lead_numbers_of_nothing_is_empty():
    assert lead_numbers([]) == frozenset()
    assert lead_numbers(["без цифр зовсім"]) == frozenset()


# --------------------------------------------------------------------------
# ПОЛОЖИТЕЛЬНОЕ: пересказ числа лида перестаёт быть «выдумкой»
# --------------------------------------------------------------------------

def test_bare_echo_of_lead_number_is_backed():
    """Голое эхо без ценового контекста — только large_number. Сегодня режется."""
    reply = "Гостей 120 — врахував."
    assert _rules(reply) == {"large_number"}                      # как сейчас
    assert _rules(reply, lead=lead_numbers(LEAD_SAID_1000)) == set()


def test_retelling_lead_budget_with_currency_is_backed():
    """Главный кейс спеки: бот пересказывает бюджет лида и называет источник."""
    reply = "Ви називали бюджет $1000 — рахую під нього."
    assert _rules(reply) == {"price", "large_number"}              # как сейчас
    assert _rules(reply, lead=lead_numbers(LEAD_SAID_1000)) == set()


def test_retelling_survives_redaction_intact():
    res = redact_unbacked(reply := "Ви називали бюджет $1000 — рахую під нього.",
                          KNOWLEDGE, language="uk",
                          lead_numbers=lead_numbers(LEAD_SAID_1000))
    assert res.text == reply
    assert res.records == ()
    assert res.clean is True


def test_contains_unbacked_claim_honours_lead_numbers():
    reply = "Ви називали бюджет $1000 — рахую під нього."
    assert contains_unbacked_claim(reply, KNOWLEDGE) is True
    assert contains_unbacked_claim(
        reply, KNOWLEDGE, lead_numbers=lead_numbers(LEAD_SAID_1000)) is False


# --------------------------------------------------------------------------
# НЕГАТИВНЫЙ КОНТРОЛЬ (Риск 5): числом лида НЕЛЬЗЯ назначить НАШУ цену
# --------------------------------------------------------------------------

@pytest.mark.parametrize("reply", [
    "Для вас це буде $1000.",
    "Вартість робіт — $1000.",
    "Ваша ціна — $1000.",
    "Зробимо за $1000.",
])
def test_our_price_in_lead_number_stays_flagged(reply):
    """Число лида стоит в ценовом утверждении О НАШЕЙ услуге — режется, как и
    было. «Ваша ціна» проверяет, что притяжательного местоимения НЕ достаточно:
    иначе лид продиктует цену, а бот повторит её как нашу."""
    assert "price" in _rules(reply, lead=lead_numbers(LEAD_SAID_1000))


def test_attribution_plus_our_price_word_still_dies():
    """Ключевой случай Риска 5: атрибуция ЕСТЬ, но в той же фразе бот назначает
    число лида НАШЕЙ ценой. Одной атрибуции недостаточно — вето по ценовому
    слову обязано пережить любую правку, иначе лид диктует прайс."""
    reply = "Ви називали бюджет $1000 — саме така і буде вартість робіт."
    assert "price" in _rules(reply, lead=lead_numbers(LEAD_SAID_1000))


def test_number_lead_never_said_stays_flagged():
    assert "price" in _rules("Ви називали бюджет $3300.",
                             lead=lead_numbers(LEAD_SAID_1000))


def test_invented_deadline_is_untouched_by_the_rule():
    assert _rules("Зробимо за 3 дні.", lead=lead_numbers(LEAD_SAID_1000)) \
        >= {"deadline"}


def test_knowledge_prices_still_pass_and_invented_ones_still_die():
    assert _rules("Хімчистка — 4 500–7 500 грн.",
                  lead=lead_numbers(LEAD_SAID_1000)) == set()
    assert "price" in _rules("Хімчистка — 9 900 грн.",
                             lead=lead_numbers(LEAD_SAID_1000))


def test_empty_lead_numbers_change_nothing():
    """Отсутствие истории обязано оставлять поведение ДОСЛОВНО прежним."""
    for reply in ("Ви називали бюджет $1000 — рахую під нього.",
                  "Гостей 120 — врахував.",
                  "Хімчистка — 4 500–7 500 грн."):
        assert _rules(reply) == _rules(reply, lead=frozenset())
