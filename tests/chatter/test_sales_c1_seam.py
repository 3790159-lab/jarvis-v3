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
