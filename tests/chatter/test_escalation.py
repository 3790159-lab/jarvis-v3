from __future__ import annotations

from chatter.core.escalation import (
    EscalationReason,
    deterministic_escalation,
    mentions_owner_contact,
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
    # (без «скидкой» — иначе сработал бы более ранний unbacked_promise; здесь
    #  проверяем именно цифровой слой.)
    r = deterministic_escalation(
        incoming_text="сколько стоит?", reply="Всего 999 рублей за всё!",
        knowledge=KNOWLEDGE, keywords=KEYWORDS)
    assert r is not None and r.tag == "unbacked_claim"


def test_unbacked_promise_in_reply_escalates():
    # H1: безцифровое обещание в ОТВЕТЕ Ани, которого нет в knowledge.
    r = deterministic_escalation(
        incoming_text="а скидку дадите?", reply="Конечно, могу сделать скидку.",
        knowledge=KNOWLEDGE, keywords=KEYWORDS)
    assert r is not None and r.tag == "unbacked_promise"


def test_promise_checked_on_reply_not_incoming():
    # Обещание проверяем в ОТВЕТЕ Ани, не во входящем лида. Лид спросил про
    # скидку — это не обещание Ани, не триггер.
    r = deterministic_escalation(
        incoming_text="есть скидка?", reply="Актуальные цены пришлю.",
        knowledge=KNOWLEDGE, keywords=[])
    assert r is None


def test_honest_refusal_in_reply_not_escalated():
    # Негейт-гард: честный отказ обязан проходить, не подавляться.
    r = deterministic_escalation(
        incoming_text="скидка будет?", reply="Скидок нет, цена фиксированная.",
        knowledge=KNOWLEDGE, keywords=[])
    assert r is None


def test_promise_wins_over_keyword_for_suppression_priority():
    # Обещание в ответе + keyword во входящем: unbacked_promise ВЫИГРЫВАЕТ, иначе
    # keyword эскалирует, но обещание уходит лиду неподавленным.
    r = deterministic_escalation(
        incoming_text="жалоба! и скидку дайте", reply="Хорошо, сделаю скидку.",
        knowledge=KNOWLEDGE, keywords=KEYWORDS)
    assert r is not None and r.tag == "unbacked_promise"


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


# --- owner_handoff: передача владельцу в ОТВЕТЕ (дрил 07-19, undercovered H2) --

def test_owner_handoff_reply_escalates_without_keyword_or_promise_verb():
    # «лучше обсудить с владельцем Дмитрием» — не keyword, не глагол-стем
    # обещания, но это ПЕРЕДАЧА владельцу → карточка обязана уйти детерминированно
    # (не полагаясь на опциональный классификатор).
    r = deterministic_escalation(
        incoming_text="Дадите скидку на большой заказ?",
        reply="Со скидками я не работаю. Лучше обсудить с владельцем Дмитрием.",
        knowledge=KNOWLEDGE, keywords=["жалоба"], owner_id="Дмитрий")
    assert r is not None and r.tag == "owner_handoff"


def test_owner_handoff_is_not_a_suppress_trigger():
    # owner_handoff ЭСКАЛИРУЕТ, но НЕ suppress: честный отказ в ответе сохраняем
    # (в отличие от unbacked_promise). Проверяем сам тег — suppress-логика в run.
    r = deterministic_escalation(
        incoming_text="скидку?", reply="Обсудим с владельцем.",
        knowledge=KNOWLEDGE, keywords=[], owner_id="Дмитрий")
    assert r is not None and r.tag == "owner_handoff"


def test_suppress_promise_wins_over_owner_handoff():
    # Ответ И обещает скидку (suppress) И зовёт владельца → suppress важнее,
    # иначе скидка уйдёт лиду неподавленной.
    r = deterministic_escalation(
        incoming_text="скидку?", reply="Сделаю скидку, обсудим с владельцем.",
        knowledge=KNOWLEDGE, keywords=[], owner_id="Дмитрий")
    assert r is not None and r.tag == "unbacked_promise"


def test_clean_reply_no_owner_mention_no_handoff():
    r = deterministic_escalation(
        incoming_text="привет", reply="Привет! Чем могу помочь?",
        knowledge=KNOWLEDGE, keywords=[], owner_id="Дмитрий")
    assert r is None


def test_mentions_owner_contact_predicate():
    # Ловит: глагол контакта, ролевое слово «владел», склонённое имя владельца.
    assert mentions_owner_contact("Дмитрий вам перезвонит")                 # verb
    assert mentions_owner_contact("передам владельцу ваш вопрос")           # role word
    assert mentions_owner_contact("обсудите это с владельцем")              # role word
    assert mentions_owner_contact("свяжу вас с Дмитрием", owner_id="Дмитрий")   # declined name
    assert mentions_owner_contact("Дмитрий поможет", owner_id="Дмитрий")        # bare name
    # НЕ ловит нейтральное:
    assert not mentions_owner_contact("Спасибо, чем ещё помочь?", owner_id="Дмитрий")
    assert not mentions_owner_contact("Цена фиксированная.", owner_id="Дмитрий")


# --- return_promise: обещание вернуться, non-suppress (Task 1.3) ------------

def test_return_promise_reply_escalates_without_other_trigger():
    # Чистое обещание вернуться, без keyword/промиса/владельца → карточка нужна
    # (иначе follow-up некуда регистрировать). НЕ suppress.
    r = deterministic_escalation(
        incoming_text="а когда будут свободные даты?",
        reply="Отвечу позже, как свериться с календарём.",
        knowledge=KNOWLEDGE, keywords=[], owner_id="Дмитрий")
    assert r is not None and r.tag == "return_promise"


def test_suppress_and_owner_handoff_win_over_return_promise():
    # Приоритет: suppress (unbacked_promise) > owner_handoff > return_promise.
    r1 = deterministic_escalation(
        incoming_text="скидку?", reply="Сделаю скидку, уточню и вернусь.",
        knowledge=KNOWLEDGE, keywords=[], owner_id="Дмитрий")
    assert r1 is not None and r1.tag == "unbacked_promise"
    # ПРИМ.: "свяжу"/"перезвоню" и т.п. — одновременно и owner-контакт-глагол, и
    # promise-стем ("свяж" в DEFAULT_PROMISE_TERMS), поэтому для чистой проверки
    # owner_handoff > return_promise берём ролевое слово («владельцем») без
    # контакт-глагола, чтобы не задеть suppress-триггер (проверено вручную:
    # unbacked_promise=None, mentions_owner_contact=True, promises_return=True).
    r2 = deterministic_escalation(
        incoming_text="?", reply="Уточню и вернусь. Обсудим с владельцем.",
        knowledge=KNOWLEDGE, keywords=[], owner_id="Дмитрий")
    assert r2 is not None and r2.tag == "owner_handoff"


# --- decide_escalation: combine deterministic + classifier (спека §4) --------

from chatter.core.classifier import ClassifierResult  # noqa: E402
from chatter.core.escalation import decide_escalation  # noqa: E402


def test_decide_deterministic_only_escalates_reason_preferred():
    det = EscalationReason(tag="keyword", detail="ключевое слово «оплата»")
    d = decide_escalation(det=det, classifier_result=None)
    assert d.escalate is True
    assert "оплата" in d.reason          # детерминированная причина (она достоверна)
    assert d.degraded is False


def test_decide_classifier_escalates_when_no_deterministic():
    cr = ClassifierResult(escalate=True, reason="готов платить", stage_signal="interested")
    d = decide_escalation(det=None, classifier_result=cr)
    assert d.escalate is True
    assert d.reason == "готов платить"
    assert d.stage_signal == "interested"


def test_decide_deterministic_reason_wins_over_classifier_reason():
    det = EscalationReason(tag="keyword", detail="ключевое слово «жалоба»")
    cr = ClassifierResult(escalate=True, reason="что-то другое", stage_signal="needs_human")
    d = decide_escalation(det=det, classifier_result=cr)
    assert "жалоба" in d.reason          # достоверный детерминированный приоритетнее
    assert d.stage_signal == "needs_human"


def test_decide_degraded_classifier_does_not_escalate_but_deterministic_still_does():
    cr = ClassifierResult(escalate=False, reason="", stage_signal=None, degraded=True)
    # деградация одна — не эскалируем
    assert decide_escalation(det=None, classifier_result=cr).escalate is False
    # но детерминированный слой работает даже при мёртвом классификаторе (§4)
    det = EscalationReason(tag="bot_question", detail="спросили, бот ли это")
    d = decide_escalation(det=det, classifier_result=cr)
    assert d.escalate is True and d.degraded is True


def test_decide_clean_no_escalation():
    cr = ClassifierResult(escalate=False, reason="", stage_signal="engaged")
    d = decide_escalation(det=None, classifier_result=cr)
    assert d.escalate is False
    assert d.stage_signal == "engaged"
