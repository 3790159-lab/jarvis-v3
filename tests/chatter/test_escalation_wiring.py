from __future__ import annotations

import random
import time
from pathlib import Path

import pytest

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


def test_reescalation_edits_existing_card_not_duplicate():
    # Fix 2: один лид = одна карточка. Повторная эскалация того же контакта
    # РЕДАКТИРУЕТ существующую карточку, а не плодит новую.
    n = FakeNotifier()
    deps = _deps(notifier=n)
    _process(deps, "42:demo", "позови человека")
    _process(deps, "42:demo", "это жалоба, верните деньги")
    assert len(n.cards) == 1        # только одна отправка
    assert len(n.updates) == 1      # вторая эскалация = правка
    assert n.updates[0][0] == n.card_handles[0]   # правится ТА ЖЕ карточка


def test_new_card_after_owner_acted():
    # После действия владельца (active-card очищен) новая эскалация = новая карточка.
    n = FakeNotifier()
    deps = _deps(notifier=n)
    _process(deps, "42:demo", "позови человека")
    assert len(n.cards) == 1
    # владелец нажал кнопку -> active-card очищен (эмулируем то, что делает route_callback)
    deps.store.set_runtime_flag("esc_active:42:demo", "", ts=1000.0)
    _process(deps, "42:demo", "снова жалоба")
    assert len(n.cards) == 2        # новая карточка, не правка


# --- brand-safety wiring (валюта/оплата) ------------------------------------

def test_demo_config_is_hryvnia_no_rubles():
    cfg = load_config(CLIENTS, "demo")
    assert cfg.settings.currency == "грн"
    assert "руб" not in cfg.knowledge.casefold()   # рубли выпилены
    assert "сбп" not in cfg.knowledge.casefold()
    assert "монобанк" in cfg.knowledge.casefold() or "monobank" in cfg.knowledge.casefold()
    assert cfg.settings.forbidden_terms                 # denylist задан


def test_forbidden_reply_suppressed_and_escalated():
    # Аня в ответе ляпнула про рубли/Сбербанк → подавить + карточка владельцу
    n = FakeNotifier()
    deps = _deps(notifier=n, keywords=[], brain_reply="Да, можно картой Сбербанка в рублях.")
    transport = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["как оплатить?"], transport, deps)
    joined = " ".join(transport.sent).casefold()
    assert "сбербанк" not in joined and "рубл" not in joined   # подавлено
    assert len(n.cards) == 1                                    # эскалация владельцу
    assert deps.store.get_or_create_contact("42:demo")["state"] == "escalated"


def test_lead_asks_about_sberbank_escalates():
    n = FakeNotifier()
    deps = _deps(notifier=n, keywords=[], brain_reply="Уточню способы оплаты и вернусь.")
    process_batch("42:demo", ["можно оплатить картой Сбербанка?"], RecordingTransport(), deps)
    assert len(n.cards) == 1                                    # лид про росбанк → эскалация
    assert deps.store.get_or_create_contact("42:demo")["state"] == "escalated"


def test_suppressed_payment_reply_is_helpful_not_silence_not_stall():
    # Ловушка: честный «Сбербанком не принимаем, только Monobank» содержит
    # запрещённое → подавляется. Лид должен получить КОРРЕКТНЫЙ ответ (верные
    # способы), а НЕ тишину и НЕ пустой «уточню и вернусь».
    n = FakeNotifier()
    deps = _deps(notifier=n, keywords=[],
                 brain_reply="Сбербанком не принимаем, только Monobank.")
    t = RecordingTransport()
    process_batch("42:demo", ["можно картой Сбербанка?"], t, deps)
    got = " ".join(t.sent)
    assert got.strip()                                  # НЕ тишина
    assert "сбербанк" not in got.casefold() and "рубл" not in got.casefold()  # подавлено
    # безопасный ответ называет ВЕРНЫЙ способ (steer to sale, не глухой стол)
    assert "monobank" in got.casefold() or "приватбанк" in got.casefold()
    assert len(n.cards) == 1                            # + эскалация владельцу


def test_unbacked_promise_reply_suppressed_and_escalated():
    # H1: Аня пообещала скидку (нет в knowledge, без цифры) → подавить на
    # нейтральный ПОЛЕЗНЫЙ ответ (не тишина, не глухой стол) + эскалация.
    n = FakeNotifier()
    deps = _deps(notifier=n, keywords=[], brain_reply="Конечно, могу сделать скидку при заказе.")
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["а можно скидку?"], t, deps)
    joined = " ".join(t.sent)
    assert joined.strip()                          # НЕ тишина
    assert "скидк" not in joined.casefold()        # обещание подавлено
    assert len(n.cards) == 1                        # эскалация владельцу
    assert deps.store.get_or_create_contact("42:demo")["state"] == "escalated"


def test_honest_refusal_reply_not_suppressed_not_escalated():
    # Негейт-гард на уровне process_batch: честный отказ проходит дословно,
    # не подавляется и не эскалирует (бот обязан уметь сказать «нет»).
    n = FakeNotifier()
    deps = _deps(notifier=n, keywords=[], brain_reply="Скидок нет, цена фиксированная.")
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["скидка есть?"], t, deps)
    joined = " ".join(t.sent).casefold()
    assert "скидок нет" in joined                   # правда доставлена
    assert n.cards == []                            # не эскалировано


def test_demo_has_safe_payment_reply():
    cfg = load_config(CLIENTS, "demo")
    assert cfg.settings.safe_payment_reply
    assert "monobank" in cfg.settings.safe_payment_reply.casefold() \
        or "приватбанк" in cfg.settings.safe_payment_reply.casefold()


# --- fallback-карточка (без Telethon-entity): честные имя и «что хочет» -------

def _card_line(text_html: str, prefix: str) -> str:
    """Строка карточки, начинающаяся с prefix (напр. «Хочет:», «Почему:»)."""
    for line in text_html.splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"нет строки с префиксом {prefix!r} в:\n{text_html}")


def test_fallback_card_name_is_bare_id_not_composite_key():
    # Баг 5a: fallback-путь (нет Telethon-entity) печатал СЫРОЙ composite key
    # «777:demo» как имя лида. Владелец должен видеть «777» (как no-entity
    # fallback самого раннера через display_name), а не внутренний ключ Store.
    n = FakeNotifier()
    deps = _deps(notifier=n)                       # notifier есть, escalation_card НЕ инъектим
    _process(deps, "777:demo", "позови человека")
    assert len(n.cards) == 1
    text = n.cards[0].text_html
    assert "777:demo" not in text                 # composite key НЕ утекает
    assert _card_line(text, "🔴 Горячий лид:") == "🔴 Горячий лид: 777"


def test_fallback_card_summary_is_lead_message_not_reason_on_keyword_only():
    # Баг 5b: при срабатывании ТОЛЬКО детерминированного слоя «Хочет» и «Почему»
    # схлопывались в одну строку (обе = det.detail). det.detail — это ПРИЧИНА
    # (сработавшее слово), а не то, что лид хочет. «Хочет» обязан показывать
    # реплику лида, «Почему» — причину; это РАЗНЫЕ строки.
    n = FakeNotifier()
    deps = _deps(notifier=n)
    _process(deps, "42:demo", "у меня жалоба на качество печати")
    assert len(n.cards) == 1
    text = n.cards[0].text_html
    wants = _card_line(text, "Хочет:")
    why = _card_line(text, "Почему:")
    assert wants == "Хочет: у меня жалоба на качество печати"   # реплика лида
    assert "ключевое слово" not in wants                       # НЕ причина
    assert why == "Почему: ключевое слово «жалоба»"            # причина — здесь
    assert wants != why                                        # не схлопнуто


# --- фаззинг: сырой текст лида теперь течёт в HTML-карточку (Fix 5b) ----------
# Лид — НЕдоверенный источник. После 5b его реплика идёт в «Хочет:» карточки с
# parse_mode=HTML. Карточка обязана переживать любой ввод: не ронять process_batch,
# экранировать HTML-спецсимволы (иначе Telegram отвергнет всё сообщение) и не
# раздуваться на гигантском вводе. «позови»/«жалоба» — чтобы гарантировать эскалацию.
FUZZ_LEAD_INPUTS = [
    '«ёлочки» и „лапки" — позови',              # кириллические кавычки: должны выжить
    "<script>alert('xss')</script> позови",     # HTML: обязан быть экранирован, не сырой
    "жалоба 🔥😤🙈 позови человека",              # эмодзи: должны выжить
    "позови " + "я" * 5000,                     # 5000 символов: усечь, не упасть, не раздуть
    '"><b>позови</b> & <i>жалоба',              # ломающая разметку смесь < > & " '
]


@pytest.mark.parametrize("lead_text", FUZZ_LEAD_INPUTS)
def test_escalation_card_survives_adversarial_lead_input(lead_text):
    n = FakeNotifier()
    deps = _deps(notifier=n)
    _process(deps, "42:demo", lead_text)                 # НЕ должно бросить исключение
    assert len(n.cards) == 1
    text = n.cards[0].text_html
    # HTML-спецсимволы лида экранированы: ни одного сырого тега/инъекции из текста
    assert "<script>" not in text
    assert "alert('xss')" not in text                    # апостроф/угловые → сущности
    # «Хочет:» ограничена по длине (safe_snippet) — 5000 символов не раздувают карточку
    wants = _card_line(text, "Хочет:")
    assert len(wants) < 400


def test_escalation_path_handles_empty_and_blank_lead_input_without_crash():
    # Пустой / пробельный ввод — не крэш и не пустая карточка (process_batch
    # выходит на coalesce раньше эскалации). Класс «пустой аргумент».
    n = FakeNotifier()
    deps = _deps(notifier=n)
    _process(deps, "42:demo", "")           # пусто
    _process(deps, "42:demo", "   \n\t ")   # только пробелы
    assert n.cards == []
