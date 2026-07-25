# -*- coding: utf-8 -*-
"""P18/P19: долг, рождённый ОБЕЩАНИЕМ БОТА, регистрируется на СВОЁМ ходу и
закрывается по функции.

Найдено живым дрилом №3 (2026-07-26), шаги 3 и 5:
  · P18 — лид попросил прорахунок візитівок, бот пообещал «передам керівниці,
    вона підкаже точну вартість», а `recalc` появился в слоте только на
    СЛЕДУЮЩЕМ ходу. Корень: классификатор получает `store.history(...)`, куда
    ответ бота ЭТОГО хода ещё не записан (он уходит в транспорт позже) — то
    есть собственных обещаний бот на своём ходу физически не видит. Оборвись
    диалог на том шаге — обещание не числилось бы нигде.
  · P19 — к концу диалога `recalc` так и висел `open`, хотя запрос зафиксирован
    и передан керівниці (owner_write доставлен). Открытый долг рендерится в
    brain, и бот дожимает прорахунок, который уже не его. Тот же класс, что
    контракт T2: закрытие по ФУНКЦИИ, а не по слову.
"""
from __future__ import annotations

import random
from pathlib import Path

from chatter.config.loader import load_config
from chatter.core.brain import Brain
from chatter.core.classifier import (
    ClassifierResult, classifier_stable_prefix, classifier_system_prompt,
)
from chatter.core.llm import FakeLLM
from chatter.run import Deps, process_batch
from chatter.storage.db import Store
from chatter.transport.base import Transport

CLIENTS = Path(__file__).resolve().parents[2] / "chatter" / "clients"

PROMISE = ("Візитівки окремо не рахуємо - давайте я передам це керівниці, "
           "вона підкаже точну вартість такого доповнення 🙂")


class _Silent(Transport):
    def receive(self, timeout=None):
        return None

    def send(self, text):
        pass

    def send_typing(self, on):
        pass

    def set_online(self, on):
        pass

    def read_acknowledge(self):
        pass


def _run_turn(reply: str):
    """Прогнать один ход и вернуть окно истории, которое увидел классификатор."""
    seen = {}

    def _classify(history, profile=None, **kw):
        seen["window"] = list(history)
        seen["kw"] = kw
        return ClassifierResult(escalate=False, reason="", profile=None,
                                stage_signal=None)

    cfg = load_config(CLIENTS, "demo")
    deps = Deps(cfg=cfg, store=Store(":memory:"),
                brain=Brain(FakeLLM(scripted=[reply]), cfg),
                rng=random.Random(0), clock=lambda: 1000.0, sleep=lambda s: None)
    deps.notifier = None
    deps.classify = _classify
    deps.escalation_keywords = []
    deps.store.get_or_create_contact("42:demo")
    process_batch("42:demo", ["А якщо додати ще дизайн візитівок — скільки це буде?"],
                  _Silent(), deps)
    return seen, deps


def test_classifier_sees_the_bot_reply_of_this_very_turn():
    """КОРЕНЬ P18. Без ответа бота в окне классификатор судит ход по одной
    реплике лида и обещание бота увидеть не может."""
    seen, _ = _run_turn(PROMISE)
    window = seen["window"]
    assert window, "классификатор вызван с пустым окном"
    last = window[-1]
    assert last["role"] == "assistant", f"последним обязан идти ответ бота, а не {last['role']}"
    assert "керівниці" in last["text"]


def test_lead_message_is_still_in_the_window():
    """Реплику лида дописанный ответ бота не вытесняет."""
    seen, _ = _run_turn(PROMISE)
    roles = [m["role"] for m in seen["window"]]
    texts = " ".join(m["text"] for m in seen["window"])
    assert "user" in roles and "візитівок" in texts


def test_pending_reply_is_not_persisted_twice():
    """Ответ дописывается ТОЛЬКО в окно классификатора. Если он попадёт в
    store, история удвоится и следующий ход будет судить по дублю."""
    seen, deps = _run_turn(PROMISE)
    saved = [m for m in deps.store.history("42:demo") if m["role"] == "assistant"]
    assert len(saved) <= 1, f"ответ сохранён {len(saved)} раз(а)"


# ── правила промпта: создание на своём ходу и закрытие по функции ────────────


def _prompts():
    return [classifier_stable_prefix("PB", "uk", track_obligations=True),
            classifier_system_prompt("PB", "uk", track_obligations=True,
                                     obligations_block="")]


def test_prompt_says_bot_promise_opens_the_debt_on_this_turn():
    for p in _prompts():
        assert "НА ЦЬОМУ ході" in p or "на ЭТОМ ходу" in p, \
            "нет правила «обещание в реплике бота = долг возникает сразу»"


def test_prompt_closes_recalc_when_handed_to_the_owner():
    """P19: «зафиксирован и передан керівниці» = функция выполнена = delivered.
    Ожидание ответа владельца — не долг бота (контракт T2)."""
    for p in _prompts():
        assert "передан" in p.lower() and "recalc" in p
        assert "delivered" in p
