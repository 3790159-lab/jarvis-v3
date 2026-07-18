from __future__ import annotations

import dataclasses
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


def _deps_clock(clock, *, notifier=None, keywords=None, brain_reply="ок"):
    """Как _deps, но с ВНЕШНИМ (двигаемым) clock — нужно тестам окна дедупа
    эскалации, где важно ПРОШЕДШЕЕ время между ходами лида (фиксированный
    clock у _deps схлопывает всё в один момент)."""
    cfg = load_config(CLIENTS, "demo")
    store = Store(":memory:")
    deps = Deps(
        cfg=cfg, store=store,
        brain=Brain(FakeLLM(scripted=[brain_reply] * 4), cfg),
        rng=random.Random(0), clock=clock, sleep=lambda s: None,
    )
    deps.notifier = notifier
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


class _FailingNotifier(FakeNotifier):
    """Строит карточку, но доставка проваливается (notify → None) — как реальный
    notify(), который глотает сетевые сбои Bot API."""
    def notify(self, card):
        self.cards.append(card)     # карточка собрана
        return None                 # но не доставлена


def test_owner_contact_promise_kept_when_card_delivered():
    # H2: «Дмитрий свяжется» H1 подавляет до нейтрального падеж-безопасного
    # «…свяжу вас с владельцем». Карточка ДОШЛА до владельца → обещание участия
    # владельца допустимо (НЕ снимается до «уточню и вернусь к вам»).
    n = FakeNotifier()
    deps = _deps(notifier=n, keywords=[], brain_reply="Дмитрий свяжется с вами.")
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["позовите владельца"], t, deps)
    joined = " ".join(t.sent)
    assert len(n.cards) == 1
    assert "владельц" in joined.casefold()   # участие владельца обещано — карточка дошла
    assert "вернусь к вам" not in joined      # НЕ снятая (H2-stripped) формулировка


def test_owner_contact_promise_stripped_when_card_not_delivered():
    # H2 ядро: карточка НЕ дошла до владельца (сбой доставки) → Аня НЕ обещает
    # контакт от его имени. Говорит то, что выполнит сама (без имени владельца).
    n = _FailingNotifier()
    deps = _deps(notifier=n, keywords=[], brain_reply="Дмитрий свяжется с вами.")
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["позовите владельца"], t, deps)
    joined = " ".join(t.sent)
    assert joined.strip()                     # НЕ тишина
    assert "Дмитрий" not in joined            # владелец НЕ обещан (карточка не дошла)
    assert "свяж" not in joined.casefold()    # и сырого обещания нет


def test_owner_contact_promise_stripped_when_no_notifier():
    # Нет канала доставки владельцу (notifier=None) → обещать его контакт нельзя.
    deps = _deps(notifier=None, keywords=[], brain_reply="Дмитрий свяжется с вами.")
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["позовите владельца"], t, deps)
    joined = " ".join(t.sent)
    assert joined.strip()
    assert "Дмитрий" not in joined


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


# --- Дрил 07-18: устаревшая карточка → тихая правка → владелец слеп ----------

def test_stale_active_card_reescalation_posts_new_notifying_card():
    # КОРЕНЬ блокера: esc_active-флаг остаётся, пока владелец не тапнул кнопку.
    # Через СУТКИ новая эскалация уходила в ТИХИЙ editMessageText (Telegram не
    # шлёт пуш на правку) → владелец не получал уведомления. Новый ход лида ПОСЛЕ
    # окна дедупа обязан быть НОВОЙ уведомляющей карточкой (sendMessage), не
    # правкой суточной давности.
    now = [1000.0]
    n = FakeNotifier()
    deps = _deps_clock(lambda: now[0], notifier=n)
    _process(deps, "42:demo", "позови человека")      # эскалация 1 → карточка
    assert len(n.cards) == 1
    now[0] += 3600.0                                    # час спустя — новый ход
    _process(deps, "42:demo", "это жалоба")            # эскалация 2 → НОВАЯ карточка
    assert len(n.cards) == 2, "устаревший флаг → должна быть НОВАЯ карточка, не тихая правка"
    assert n.updates == [], "не тихий edit — владелец обязан получить пуш"


def test_reescalation_within_window_still_edits_not_duplicate():
    # Окно дедупа (случайное двойное срабатывание в пределах ~минуты) по-прежнему
    # правит ту же карточку, а не плодит.
    now = [1000.0]
    n = FakeNotifier()
    deps = _deps_clock(lambda: now[0], notifier=n)
    _process(deps, "42:demo", "позови человека")
    now[0] += 5.0                                       # в пределах окна
    _process(deps, "42:demo", "снова позови")
    assert len(n.cards) == 1
    assert len(n.updates) == 1


def test_dedup_edit_failure_reports_not_delivered_and_strips_owner_promise():
    # H2 × дедуп: правка карточки в пределах окна МОЖЕТ провалиться (сеть).
    # Тогда delivered=False (а не «безусловно True», как было) → H2 снимает
    # обещание контакта владельца.
    now = [1000.0]
    n = FakeNotifier(update_ok=False)                   # правка карточки проваливается
    deps = _deps_clock(lambda: now[0], notifier=n, keywords=[],
                       brain_reply="Дмитрий свяжется с вами.")
    _process(deps, "42:demo", "позовите владельца")    # эскалация 1: notify OK, флаг ставится
    now[0] += 5.0                                        # в пределах окна → правка (провал)
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["и ещё вопрос, позовите"], t, deps)
    joined = " ".join(t.sent).casefold()
    assert joined.strip()                               # НЕ тишина
    assert "дмитрий" not in joined and "владельц" not in joined  # обещание снято
    assert "свяж" not in joined


def test_owner_name_promise_on_keyword_escalation_stripped_when_not_delivered():
    # Q4-дыра: эскалация по КЛЮЧЕВОМУ СЛОВУ (не suppress-тег), а ответ brain
    # называет владельца по имени БЕЗ глагол-стема обещания («Дмитрий поможет»).
    # Старый H2 стрипал только suppress-теги → обещание утекало. Карточка не
    # дошла → обещание участия владельца снять.
    n = _FailingNotifier()
    deps = _deps(notifier=n, brain_reply="Дмитрий вам поможет с этим.")
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["позови человека"], t, deps)   # keyword «позови»
    joined = " ".join(t.sent)
    assert joined.strip()
    assert "Дмитрий" not in joined            # обещание участия владельца снято (карточка не дошла)


def test_honest_disclosure_owner_mention_survives_failed_delivery():
    # H3-защита при расширении H2: честное «я бот, подключу владельца» упоминает
    # владельца, но это НЕ обещание за него — H2 его НЕ трогает даже при
    # неудачной доставке карточки (иначе сломали бы гарантию честности).
    n = _FailingNotifier()
    deps = _deps(notifier=n, keywords=[])
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["ты бот?"], t, deps)
    joined = " ".join(t.sent).casefold()
    assert "бот" in joined or "ассистент" in joined or "виртуальн" in joined  # честное раскрытие
    assert "дмитрий" in joined or "владел" in joined      # владелец упомянут и НЕ вырезан


# --- «мелочь»: падеж имени владельца в шаблоне suppress-ответа ---------------

def test_suppress_reply_owner_reference_is_case_safe_no_bare_nominative():
    # «Позову Дмитрий» не склонялось. Теперь падеж обходится формулировкой
    # «свяжу вас с владельцем» (глагол «свяж» держит H2-детекцию).
    n = FakeNotifier()
    deps = _deps(notifier=n, keywords=[], brain_reply="Дмитрий свяжется с вами.")
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["позовите владельца"], t, deps)   # доставлено → обещание оставлено
    joined = " ".join(t.sent)
    assert "позову Дмитрий" not in joined            # сломанный именительный падеж ушёл
    assert "владельцем" in joined.casefold()          # падеж-безопасная формулировка


def test_owner_handoff_reply_posts_card_without_classifier():
    # Q1 (дрил 07-19): «обсудить с владельцем Дмитрием» — не keyword и не
    # глагол-обещание → раньше карточка зависела от опционального классификатора
    # (и не пришла). Теперь эскалация ДЕТЕРМИНИРОВАННА.
    n = FakeNotifier()
    deps = _deps(notifier=n, classify=None, keywords=[],
                 brain_reply="Со скидками я не работаю. Лучше обсудить с владельцем Дмитрием. Что вы планируете?")
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["Дадите скидку на большой заказ?"], t, deps)
    assert len(n.cards) == 1                          # карточка владельцу ушла
    assert deps.store.get_or_create_contact("42:demo")["state"] == "escalated"


def test_owner_handoff_keeps_honest_refusal_when_delivered():
    # НЕ suppress: честный «со скидками не работаю» сохраняется (не заменяется
    # шаблоном), раз карточка дошла.
    n = FakeNotifier()
    deps = _deps(notifier=n, classify=None, keywords=[],
                 brain_reply="Со скидками я не работаю. Обсудим с владельцем.")
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["скидку?"], t, deps)
    joined = " ".join(t.sent).casefold()
    assert "со скидками я не работаю" in joined       # честный отказ сохранён


def test_owner_contact_reply_drops_trailing_sell_question():
    # Q2: после обещания контакта Аня НЕ ведёт дальше встречным вопросом (лида
    # передали — бот не должен продолжать продавать в том же сообщении).
    n = FakeNotifier()
    deps = _deps(notifier=n, classify=None, keywords=[],
                 brain_reply="Со скидками я не работаю. Лучше обсудить с владельцем Дмитрием. Что вы планируете?")
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["скидку?"], t, deps)
    joined = " ".join(t.sent)
    assert "Что вы планируете" not in joined          # хвостовой вопрос убран
    assert "обсудить с владельцем" in joined.casefold()  # сама передача осталась


def test_non_contact_reply_keeps_its_question():
    # Обычный ответ (не эскалация/не контакт) со встречным вопросом НЕ трогаем.
    n = FakeNotifier()
    deps = _deps(notifier=n, classify=None, keywords=[],
                 brain_reply="Отлично! А что именно вы планируете снимать?")
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["расскажите про съёмку"], t, deps)
    joined = " ".join(t.sent).casefold()
    assert "что именно вы планируете снимать" in joined   # вопрос сохранён
    assert n.cards == []                                  # и не эскалировано


def test_owner_handoff_promise_stripped_when_card_not_delivered():
    # H2 теперь ВИДИТ «обсудить с владельцем»/склонённое имя: карточка не дошла →
    # обещание контакта снять (раньше этот путь H2 не покрывал).
    n = _FailingNotifier()
    deps = _deps(notifier=n, classify=None, keywords=[],
                 brain_reply="Со скидками я не работаю. Обсудим с владельцем Дмитрием.")
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["скидку?"], t, deps)
    joined = " ".join(t.sent).casefold()
    assert joined.strip()
    assert "владельц" not in joined and "дмитри" not in joined   # обещание контакта снято


def test_owner_ref_config_customizes_reference():
    # Клиент может задать, как называть владельца в ответах (уже в нужном падеже:
    # «менеджером», «Дмитрием», …). Пусто → дефолт «владельцем».
    n = FakeNotifier()
    deps = _deps(notifier=n, keywords=[], brain_reply="Дмитрий свяжется с вами.")
    deps.cfg = dataclasses.replace(
        deps.cfg, settings=dataclasses.replace(deps.cfg.settings, owner_ref="менеджером"))
    t = RecordingTransport()
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["позовите владельца"], t, deps)
    joined = " ".join(t.sent).casefold()
    assert "менеджером" in joined
    assert "свяж" in joined                           # H2-детекция по глаголу сохраняется
