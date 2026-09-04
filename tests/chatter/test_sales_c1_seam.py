# -*- coding: utf-8 -*-
"""C-1 / D2-1 — ШОВ: правило обязано работать в БОЕВОМ ходу, а не в юните.

`guardrails` научился не резать числа лида, но пока `run.py` не передаёт ему
эти числа, правка — мёртвый код: гардрейл по-прежнему видит только reply и
knowledge. Здесь сквозной ход через `process_batch` без сети: лид называет
бюджет, модель пересказывает его, и текст обязан дойти до лида ДОСЛОВНО.

Негативная половина — на том же шве: назначить число лида НАШЕЙ ценой ход
по-прежнему не даёт.
"""
from __future__ import annotations

import random
from pathlib import Path

from chatter.config.loader import load_config
from chatter.core.brain import Brain
from chatter.core.llm import FakeLLM
from chatter.run import Deps, _lead_numbers_for_turn, process_batch
from chatter.storage.db import Store
from chatter.transport.base import Transport

CLIENTS = Path(__file__).resolve().parents[2] / "chatter" / "clients"
CONTACT = "telegram:77:demo"
LEAD_TURN = "Бюджет у мене $1000, гостей близько 120."


class _Recorder(Transport):
    """Транспорт, который ЗАПОМИНАЕТ отправленное лиду."""
    def __init__(self):
        self.sent: list[str] = []

    def receive(self, timeout=None): return None
    def send(self, text): self.sent.append(text)
    def send_typing(self, on): pass
    def set_online(self, on): pass
    def read_acknowledge(self): pass


class _NullNotifier:
    def notify(self, card): return None


def _deps(store, cfg, reply: str) -> Deps:
    return Deps(
        cfg=cfg, store=store, brain=Brain(FakeLLM(scripted=[reply]), cfg),
        rng=random.Random(0), clock=lambda: 1000.0, sleep=lambda s: None,
        notifier=_NullNotifier(), escalation_keywords=[], classify=None,
        control=cfg.settings.control)


def _run(reply: str) -> list[str]:
    store = Store(":memory:")
    cfg = load_config(CLIENTS, "demo")
    store.get_or_create_contact(CONTACT)
    tr = _Recorder()
    process_batch(CONTACT, [LEAD_TURN], tr, _deps(store, cfg, reply))
    return tr.sent


# --------------------------------------------------------------------------
# окно, из которого берутся числа лида
# --------------------------------------------------------------------------

class _Limits:
    history_budget_tokens = 4000
    history_max_messages = 20


def test_lead_numbers_for_turn_takes_lead_messages_and_current_incoming():
    history = [
        {"role": "user", "text": "у мене 120 гостей"},
        {"role": "assistant", "text": "рахую 999 варіантів"},   # НЕ лид
    ]
    got = _lead_numbers_for_turn(history, limits=_Limits(),
                                 incoming_text="бюджет 1 000 доларів")
    assert "120" in got and "1000" in got
    assert "999" not in got, "число из ответа БОТА не может обеспечивать бота"


def test_lead_numbers_for_turn_survives_empty_history():
    assert _lead_numbers_for_turn([], limits=_Limits(), incoming_text="") == frozenset()


# --------------------------------------------------------------------------
# сквозной ход
# --------------------------------------------------------------------------

def test_retelling_lead_budget_reaches_the_lead_verbatim():
    """Тире НЕ используем: конвейер нормализует «—» в дефис, и тест ловил бы
    типографику вместо гардрейла."""
    reply = "Ви називали бюджет $1000, рахую під нього."
    assert _run(reply) == [reply]


def test_our_price_from_lead_number_still_does_not_reach_the_lead():
    reply = "Для вас це буде $1000."
    sent = _run(reply)
    assert sent and reply not in sent, "выдуманная НАША цена уехала лиду"


# --------------------------------------------------------------------------
# Три сторожа, дописанные по слепым мишеням гейта шва (прогон 04.09, 4/7).
# Пишутся ОТ СПЕКИ sales-competence §4 D2-1, а не от кода: граница обеспечения
# — «окно истории, которое и так грузится в промпт», и число лида обеспечивает
# ПЕРЕСКАЗ, а не нашу цену.
# --------------------------------------------------------------------------

class _TwoMessageLimits:
    """Окно упирается в КОЛИЧЕСТВО сообщений."""
    history_budget_tokens = 4000
    history_max_messages = 2


class _TinyBudgetLimits:
    """Окно упирается в БЮДЖЕТ ТОКЕНОВ (8 ток ≈ одна короткая реплика)."""
    history_budget_tokens = 8
    history_max_messages = 20


def test_lead_numbers_stop_at_the_edge_of_the_prompt_window():
    """Мишень 1 гейта. Число, которого бот на этом ходу НЕ ВИДЕЛ, не обеспечено.

    Спека §4 D2-1 задаёт границей ровно окно промпта: модель может пересказать
    только то, что ей показали. Взять всю историю значит обеспечивать бота
    числом, которого в его контексте не было, — то есть подписаться под
    выдумкой, совпавшей со старой репликой лида.

    Проверяем ОБЕ оси окна: и лимит сообщений, и бюджет токенов. Мутант,
    снимающий `select_window` целиком, обязан покраснеть на любой из них.
    """
    history = [
        {"role": "user", "text": "раньше я называл 3333"},   # за окном
        {"role": "user", "text": "и ещё 4444"},
        {"role": "user", "text": "сейчас думаю про 555"},
    ]

    by_count = _lead_numbers_for_turn(history, limits=_TwoMessageLimits(),
                                      incoming_text="")
    assert "555" in by_count and "4444" in by_count, by_count
    assert "3333" not in by_count, "число ЗА окном промпта бот не видел"

    by_budget = _lead_numbers_for_turn(history, limits=_TinyBudgetLimits(),
                                       incoming_text="")
    assert by_budget == frozenset({"555"}), by_budget


def test_lead_number_from_stored_history_reaches_the_lead_verbatim():
    """Мишень 4 гейта. Число лежит ТОЛЬКО в истории — источник обязан читаться.

    В текущей реплике цифр нет вовсе, бюджет назван ходом раньше. Если шов
    берёт числа не из истории контакта, а из пустого списка, пересказ снова
    становится «выдумкой» и лид получает заглушку вместо ответа.
    """
    store = Store(":memory:")
    cfg = load_config(CLIENTS, "demo")
    store.get_or_create_contact(CONTACT)
    store.add_message(CONTACT, "user", "Бюджет у меня $1000, гостей около 120.",
                      ts=900.0)

    reply = "Вы называли бюджет $1000, считаю под него."
    tr = _Recorder()
    process_batch(CONTACT, ["А что входит в съёмку?"], tr,
                  _deps(store, cfg, reply))

    assert tr.sent == [reply], tr.sent


def test_mixed_reply_keeps_the_lead_number_and_cuts_only_the_invented_deadline():
    """Мишень 5 гейта. Один ответ — и необеспеченный срок, и число лида.

    Существующие сторожа подают ЧИСТЫЙ пересказ: гардрейл его не помечает,
    `det.tag` не равен `unbacked_claim`, и путь редакции не исполняется вовсе.
    Поэтому «редакция не получает числа лида» была слепой мишенью: решение
    живое, но никем не доказанное.

    Здесь редакция ОБЯЗАНА исполниться (выдуманный срок «за 9 дней» в базе
    demo не значится) и обязана вырезать РОВНО его, оставив бюджет лида
    дословно. Отняв у редакции числа лида, мутант режет и бюджет — тот самый
    дефект, ради которого писалась арка, только на пути редакции.
    """
    store = Store(":memory:")
    cfg = load_config(CLIENTS, "demo")
    store.get_or_create_contact(CONTACT)

    reply = "Ваш бюджет $1000 полностью покрывает съёмку, всё будет готово за 9 дней."
    expected = ("Ваш бюджет $1000 полностью покрывает съёмку, "
                "точный срок согласовываем индивидуально.")
    tr = _Recorder()
    process_batch(CONTACT, ["Бюджет у меня $1000, гостей около 120."], tr,
                  _deps(store, cfg, reply))

    assert tr.sent == [expected], tr.sent
