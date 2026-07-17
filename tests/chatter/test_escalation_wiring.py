from __future__ import annotations

import random
import time
from pathlib import Path

from chatter.config.loader import load_config
from chatter.core.brain import Brain
from chatter.core.classifier import ClassifierResult
from chatter.core.escalation import parse_escalation_keywords
from chatter.core.llm import FakeLLM
from chatter.notify.base import FakeNotifier
from chatter.run import Deps, process_batch
from chatter.storage.db import Store
from chatter.transport.base import Transport

CLIENTS = Path(__file__).resolve().parents[2] / "chatter" / "clients"


class RecordingTransport(Transport):
    def __init__(self):
        self.sent = []

    def receive(self, timeout=None):
        return None

    def send(self, text):
        self.sent.append(text)

    def send_typing(self, on):
        pass

    def set_online(self, on):
        pass

    def read_acknowledge(self):
        pass


def _deps(*, notifier=None, classify=None, keywords=None, brain_reply="Привет, чем помочь?"):
    cfg = load_config(CLIENTS, "demo")
    store = Store(":memory:")
    deps = Deps(
        cfg=cfg, store=store, brain=Brain(FakeLLM(scripted=[brain_reply]), cfg),
        rng=random.Random(0), clock=lambda: 1000.0, sleep=lambda s: None,
    )
    deps.notifier = notifier
    deps.classify = classify
    deps.escalation_keywords = keywords if keywords is not None else ["позови", "верните", "жалоба"]
    return deps


def _process(deps, contact, text):
    deps.store.get_or_create_contact(contact)
    process_batch(contact, [text], RecordingTransport(), deps)


def test_keyword_incoming_posts_escalation_card_and_escalates_state():
    n = FakeNotifier()
    deps = _deps(notifier=n)
    _process(deps, "42:demo", "Можно позови владельца?")
    assert len(n.cards) == 1
    assert n.cards[0].kind == "escalation"
    assert n.cards[0].contact_id == "42:demo"
    assert deps.store.get_or_create_contact("42:demo")["state"] == "escalated"


def test_clean_conversation_no_card_but_funnel_advances():
    n = FakeNotifier()
    deps = _deps(
        notifier=n,
        classify=lambda history: ClassifierResult(
            escalate=False, reason="", stage_signal="engaged"),
    )
    _process(deps, "42:demo", "привет, расскажите про съёмку")
    assert n.cards == []
    # воронка ожила: new -> qualifying по сигналу engaged
    assert deps.store.get_or_create_contact("42:demo")["state"] == "qualifying"


def test_classifier_hot_lead_escalates():
    n = FakeNotifier()
    deps = _deps(
        notifier=n, keywords=[],
        classify=lambda history: ClassifierResult(
            escalate=True, reason="готов внести предоплату", stage_signal="interested"),
    )
    _process(deps, "42:demo", "хочу забронировать на субботу")
    assert len(n.cards) == 1
    assert "предоплат" in n.cards[0].text_html
    assert deps.store.get_or_create_contact("42:demo")["state"] == "escalated"


def test_degraded_classifier_no_escalation_but_counted():
    n = FakeNotifier()
    deps = _deps(
        notifier=n, keywords=[],
        classify=lambda history: ClassifierResult(
            escalate=False, reason="", stage_signal=None, degraded=True),
    )
    _process(deps, "42:demo", "обычное сообщение без триггеров")
    assert n.cards == []                                     # деградация не эскалирует
    assert deps.store.count_events("classifier_error", since_ts=0.0) == 1  # но посчитана


def test_no_notifier_does_not_crash_arc3a_path():
    # Без notifier/classify (путь арки 3A) — гардрейл-переписывание всё равно
    # работает, эскалация просто не шлёт карточку.
    deps = _deps(notifier=None, classify=None, keywords=[], brain_reply="Всего 999 руб со скидкой!")
    transport = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["сколько стоит?"], transport, deps)
    # необеспеченное обещание переписано в честную «уточню и вернусь»
    assert transport.sent
    joined = " ".join(transport.sent)
    assert "999" not in joined
    assert deps.store.get_or_create_contact("42:demo")["state"] == "escalated"


def test_demo_playbook_has_escalation_keywords():
    cfg = load_config(CLIENTS, "demo")
    kw = parse_escalation_keywords(cfg.playbook)
    assert kw, "demo playbook must declare escalation keywords for arc 3B"


def test_payment_intent_does_not_escalate_but_refund_and_complaint_do():
    # Fix 1: «хочу оплатить» = момент продажи (Аня даёт реквизиты сама),
    # эскалируем только возврат/жалобу/спор/«позови человека».
    from chatter.core.escalation import deterministic_escalation
    cfg = load_config(CLIENTS, "demo")
    kw = parse_escalation_keywords(cfg.playbook)
    assert "оплата" not in kw
    assert "жалоба" in kw
    assert any(k in ("возврат", "верните") for k in kw)
    # намерение заплатить — НЕ эскалация
    assert deterministic_escalation(
        incoming_text="как можно оплатить съёмку?", reply="ок",
        knowledge=cfg.knowledge, keywords=kw) is None
    # возврат / жалоба — эскалация
    assert deterministic_escalation(
        incoming_text="верните деньги, я недоволен", reply="ок",
        knowledge=cfg.knowledge, keywords=kw) is not None
    assert deterministic_escalation(
        incoming_text="это жалоба", reply="ок",
        knowledge=cfg.knowledge, keywords=kw) is not None


def test_demo_knowledge_has_payment_requisites():
    # Чтобы Аня могла отдать реквизиты сама (дефолт: есть реквизиты → отдаёт).
    cfg = load_config(CLIENTS, "demo")
    assert "плат" in cfg.knowledge.casefold() or "реквизит" in cfg.knowledge.casefold()
