"""P20 (D): точечная редакция вместо подавления всего ответа.

Дефект (б) инцидента 2026-07-29: один незабэканный номер убивал ВЕСЬ ответ —
вместе с ним гибли 750–900 $ и 600–800 $, обеспеченные knowledge. Лид трижды
не увидел ни одной цены.

Контракт: редактируется КЛАУЗА целиком (грамматически цельный кусок между
запятыми/скобками/точками), а не число внутри неё — иначе в предложении
остаётся дыра. Соседние обеспеченные числа обязаны выжить.
"""
from __future__ import annotations

import re

import pytest

from chatter.core.guardrails import contains_unbacked_claim, redact_unbacked

KNOWLEDGE = (
    "# Ціни\n"
    "- SMM-ведення — 750–900 $\n"
    "- Маркетингова стратегія — 600–800 $\n"
    "# Терміни\n"
    "- Маркетингова стратегія — 21 календарний день\n"
    "- Створення логотипа — 5–10 робочих днів\n"
)


def test_redaction_replaces_only_the_offending_clause():
    reply = ("Маркетингова стратегія - 600–800 $, термін розробки 99 днів, "
             "потім презентуємо результат.")
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")

    assert res.clean is True
    assert "99" not in res.text
    assert "600–800 $" in res.text, "обеспеченная цена не имела права погибнуть"
    assert "потім презентуємо результат" in res.text, "хвост клаузы уцелел"


def test_neighbouring_backed_numbers_survive():
    """Требование владельца: редакция не трогает соседние обеспеченные числа."""
    reply = ("SMM-ведення - 750–900 $, стратегія - 600–800 $, "
             "термін 99 днів, працюємо 5–10 робочих днів по логотипу.")
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")

    for kept in ("750–900 $", "600–800 $", "5–10 робочих днів"):
        assert kept in res.text, f"{kept} погибло вместе с 99"
    assert "99" not in res.text


def test_redacted_text_is_grammatically_clean():
    reply = ("Стратегія (600–800 $, термін 99 днів, потім презентуємо результат) "
             "— це наш основний напрямок.")
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")
    t = res.text

    assert ",," not in t and ", ," not in t, f"двойная запятая: {t!r}"
    assert " ," not in t, f"пробел перед запятой: {t!r}"
    assert "()" not in t and "( " not in t.replace("( ", "(", 0), f"пустая скобка: {t!r}"
    assert not re.search(r"\(\s*[,;]", t), f"скобка открывается запятой: {t!r}"
    assert t.count("(") == t.count(")"), f"скобки разъехались: {t!r}"


@pytest.mark.parametrize("language,expected", [
    ("uk", "узгоджуємо"),
    ("ru", "согласовываем"),
    ("en", "agreed"),
])
def test_replacement_is_localised(language, expected):
    reply = "Стратегія - 600–800 $, термін 99 днів."
    res = redact_unbacked(reply, KNOWLEDGE, language=language)
    assert expected in res.text


def test_replacement_does_not_promise_owner_contact():
    """Ключевое: редакция НЕ имеет права упоминать владельца/керівницю.

    H2-гейт (run.py) при недоставленной карточке затирает любой ответ, который
    `mentions_owner_contact` считает обещанием контакта владельца, — и наша
    аккуратная редакция превратилась бы обратно в заглушку."""
    from chatter.core.escalation import mentions_owner_contact

    reply = "Стратегія - 600–800 $, термін 99 днів."
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")
    assert mentions_owner_contact(
        res.text, owner_id="Ольга", owner_ref="керівницею") is False


def test_clean_flag_never_lies():
    """Инвариант безопасности: clean=True ⇔ в тексте НЕТ необеспеченных чисел.
    Редакция не имеет права выпустить выдуманную цену/срок лиду."""
    samples = [
        "Стратегія - 600–800 $, термін 99 днів.",
        "Зробимо за 99 днів.",
        "Ціна 12345 $ за проєкт.",
        "SMM 750–900 $ за 99 днів.",
        "Все добре, розкажіть про задачу?",
        "Стратегія 600–800 $.",
    ]
    for reply in samples:
        res = redact_unbacked(reply, KNOWLEDGE, language="uk")
        assert res.clean == (not contains_unbacked_claim(res.text, KNOWLEDGE)), \
            f"clean солгал на {reply!r} → {res.text!r}"


def test_backed_reply_is_returned_untouched():
    reply = "Стратегія - 600–800 $, термін 21 календарний день."
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")

    assert res.text == reply
    assert res.records == ()
    assert res.clean is True


def test_records_are_pii_free():
    """Каждая редакция логируется — но без текста лида и без текста ответа."""
    reply = "Стратегія - 600–800 $, термін 99 днів, дзвоніть Олені."
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")

    assert res.records, "редакция обязана оставить след"
    blob = " ".join(f"{r.number}|{r.rule}|{r.clause_chars}" for r in res.records)
    for leaked in ("Олен", "дзвоніть", "стратегі", "Стратегі"):
        assert leaked not in blob, f"в записи протёк текст: {blob!r}"
    assert "99" in blob and any(r.rule for r in res.records)


def test_collateral_inside_one_clause_is_reported():
    """Честная граница метода: если выдуманное и обеспеченное числа стоят в ОДНОЙ
    клаузе, клауза уходит целиком — обеспеченное гибнет вместе с ним. Это
    по-прежнему НАМНОГО уже, чем подавление всего ответа, но мы это фиксируем."""
    reply = "Пропоную SMM 750–900 $ за 99 днів. Портфоліо надішлю окремо."
    res = redact_unbacked(reply, KNOWLEDGE, language="uk")

    assert res.clean is True
    assert "99" not in res.text
    assert "Портфоліо надішлю окремо" in res.text, "соседнее предложение уцелело"
