from __future__ import annotations

from chatter.core.escalation import parse_escalation_keywords


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
