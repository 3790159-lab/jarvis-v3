from __future__ import annotations

from chatter.core.escalation import (
    EscalationReason,
    deterministic_escalation,
    parse_escalation_keywords,
)


PLAYBOOK_RU = """\
# Воронка

1. new → знакомство.

## Ключевые слова эскалации
<!-- одно слово/фраза на строку -->
- позови
- Оплата
- верните
- жалоба

## Чего не обещать
- Нет скидок.
"""


def test_parses_keywords_casefolded():
    kw = parse_escalation_keywords(PLAYBOOK_RU)
    assert kw == ["позови", "оплата", "верните", "жалоба"]


def test_stops_at_next_heading():
    # "Нет скидок" из следующей секции НЕ должно попасть в ключевые слова.
    kw = parse_escalation_keywords(PLAYBOOK_RU)
    assert "нет скидок" not in kw
    assert "нет скидок." not in kw


def test_missing_section_is_empty_not_error():
    # Отсутствие секции = детерминированный слой ключевых слов просто выключен,
    # а не падение (клиент мог не завести секцию).
    assert parse_escalation_keywords("# Воронка\n\nпросто текст") == []


def test_english_heading():
    pb = "## Escalation keywords\n- call\n- refund\n\n## Other\n- x\n"
    assert parse_escalation_keywords(pb) == ["call", "refund"]


def test_ukrainian_heading():
    pb = "## Ключові слова ескалації\n- поклич\n- оплата\n"
    assert parse_escalation_keywords(pb) == ["поклич", "оплата"]


def test_blank_and_comment_lines_ignored():
    pb = "## Ключевые слова эскалации\n\n<!-- коммент -->\n- позови\n\n"
    assert parse_escalation_keywords(pb) == ["позови"]


# --- deterministic_escalation (спека §4, слой 1: бесплатно, без сети) --------

KEYWORDS = ["позови", "оплата", "верните", "жалоба"]
KNOWLEDGE = "Консультация 5000 руб. Съёмка 15000 руб."


def test_keyword_in_incoming_escalates():
    r = deterministic_escalation(
        incoming_text="Можно позови владельца?", reply="Конечно.",
        knowledge=KNOWLEDGE, keywords=KEYWORDS)
    assert isinstance(r, EscalationReason)
    assert r.tag == "keyword"
    assert "позови" in r.detail.casefold()


def test_keyword_match_is_casefold():
    r = deterministic_escalation(
        incoming_text="ОПЛАТА как проходит?", reply="ок",
        knowledge=KNOWLEDGE, keywords=KEYWORDS)
    assert r is not None and r.tag == "keyword"


def test_bot_question_escalates():
    r = deterministic_escalation(
        incoming_text="ты бот?", reply="ок",
        knowledge=KNOWLEDGE, keywords=KEYWORDS)
    assert r is not None and r.tag == "bot_question"


def test_unbacked_claim_in_reply_escalates():
    # Ответ обещает цену, которой нет в knowledge -> guardrail-триггер.
    r = deterministic_escalation(
        incoming_text="сколько стоит?", reply="Всего 999 рублей со скидкой!",
        knowledge=KNOWLEDGE, keywords=KEYWORDS)
    assert r is not None and r.tag == "unbacked_claim"


def test_clean_conversation_no_escalation():
    r = deterministic_escalation(
        incoming_text="привет, расскажите про услуги",
        reply="Привет! Помогу с выбором, что именно интересует?",
        knowledge=KNOWLEDGE, keywords=KEYWORDS)
    assert r is None


def test_empty_keywords_disables_keyword_layer():
    # Пустой список ключевых слов не должен матчить ничего (и не падать).
    r = deterministic_escalation(
        incoming_text="оплата оплата оплата", reply="ок",
        knowledge=KNOWLEDGE, keywords=[])
    assert r is None


def test_keyword_wins_over_bot_question_order():
    # Первый сработавший триггер: порядок keyword -> bot_question -> unbacked.
    r = deterministic_escalation(
        incoming_text="ты бот? и позови человека",
        reply="ок", knowledge=KNOWLEDGE, keywords=KEYWORDS)
    assert r is not None and r.tag == "keyword"
