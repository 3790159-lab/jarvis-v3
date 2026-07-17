from __future__ import annotations

from chatter.core.brand_safety import forbidden_mention
from chatter.core.escalation import deterministic_escalation

FORBIDDEN = ("рубл", "сбербанк", "тинькофф", "мир", "qiwi", "юмани")
KNOWLEDGE = "Консультація 5000 грн. Оплата Monobank або ПриватБанк."


def test_forbidden_mention_matches_casefold_substring():
    assert forbidden_mention("оплатите в рублях", FORBIDDEN) == "рубл"
    assert forbidden_mention("картой СБЕРБАНКА", FORBIDDEN) == "сбербанк"
    assert forbidden_mention("оплата Monobank", FORBIDDEN) is None
    assert forbidden_mention("", FORBIDDEN) is None
    assert forbidden_mention("что угодно", ()) is None    # пустой denylist


# --- deterministic_escalation: brand-safety слой -----------------------------

def test_reply_mentions_forbidden_term_suppresses_and_escalates():
    r = deterministic_escalation(
        incoming_text="как оплатить?", reply="Можно картой Сбербанка или в рублях.",
        knowledge=KNOWLEDGE, keywords=[], forbidden_terms=FORBIDDEN)
    assert r is not None
    assert r.tag == "forbidden_reply"          # ответ упомянул запрещённое → подавляем
    assert "сбербанк" in r.detail.casefold() or "рубл" in r.detail.casefold()


def test_incoming_mentions_forbidden_escalates():
    # «можно картой Сбербанка?» → лид спросил про российский банк → эскалация
    r = deterministic_escalation(
        incoming_text="можно оплатить картой Сбербанка?", reply="Уточню способы оплаты.",
        knowledge=KNOWLEDGE, keywords=[], forbidden_terms=FORBIDDEN)
    assert r is not None
    assert r.tag == "forbidden_incoming"


def test_clean_ua_conversation_no_forbidden_escalation():
    r = deterministic_escalation(
        incoming_text="як оплатити консультацію?",
        reply="Консультація 5000 грн, оплата Monobank або ПриватБанк.",
        knowledge=KNOWLEDGE, keywords=[], forbidden_terms=FORBIDDEN)
    assert r is None


def test_forbidden_reply_wins_priority_over_keyword():
    # запрещённый термин в ответе критичнее ключевого слова
    r = deterministic_escalation(
        incoming_text="жалоба", reply="платите в рублях",
        knowledge=KNOWLEDGE, keywords=["жалоба"], forbidden_terms=FORBIDDEN)
    assert r is not None and r.tag == "forbidden_reply"
