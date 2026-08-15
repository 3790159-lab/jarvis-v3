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


def _run_turn(reply: str, monkeypatch=None):
    """Прогнать один ход и вернуть то, что увидел классификатор.

    Слот обязательств гейтится флагом (по умолчанию OFF = поведение байт-в-байт
    как до арки), а `pending_reply` живёт именно в этой ветке — значит флаг
    включаем явно, иначе тест проверял бы мёртвый путь."""
    if monkeypatch is not None:
        monkeypatch.setenv("CHATTER_OBLIGATIONS_SLOT", "1")
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


def test_classifier_gets_the_bot_reply_of_this_very_turn(monkeypatch):
    """КОРЕНЬ P18. Ответ бота обязан доехать до классификатора на СВОЁМ ходу —
    но параметром, а не репликой в переписке (иначе API 400, см. сторож ниже)."""
    seen, _ = _run_turn(PROMISE, monkeypatch)
    assert "керівниці" in (seen["kw"].get("pending_reply") or ""), \
        "ответ текущего хода не доехал до классификатора"


def test_conversation_window_still_ends_with_the_lead():
    """Окно остаётся перепиской: последним идёт сообщение ЛИДА."""
    seen, _ = _run_turn(PROMISE)
    window = seen["window"]
    assert window and window[-1]["role"] == "user"
    assert "візитівок" in window[-1]["text"]


def test_pending_reply_is_not_persisted_twice():
    """Ответ едет контекстом. Если он попадёт в store, история удвоится и
    следующий ход будет судить по дублю."""
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


# ── СТОРОЖ: переписка обязана заканчиваться репликой ЛИДА ────────────────────
# Инцидент 2026-07-26 01:2x: первая версия фикса P18 дописывала ответ бота в
# МАССИВ СООБЩЕНИЙ. API ответил 400 «This model does not support assistant
# message prefill. The conversation must end with a user message», классификатор
# падал на КАЖДОМ ходу пять ходов подряд — без эскалации и без карточки на шаге
# оплаты. Тест на фейке classify этого не поймал: фейк принимал любое окно.
# Поэтому сторож стоит на РЕАЛЬНОЙ сборке сообщений.


def test_messages_never_end_with_an_assistant_turn():
    from chatter.core.classifier import build_classifier_messages
    msgs = build_classifier_messages([
        {"role": "user", "text": "а скільки візитівки?"},
        {"role": "assistant", "text": PROMISE},
    ])
    assert msgs, "сообщения не собрались"
    assert msgs[-1]["role"] == "user", (
        "переписка заканчивается ответом бота — API отвергнет это как prefill "
        "(400) и классификатор умрёт на каждом ходу")


def test_bot_reply_travels_in_the_system_block_not_in_the_conversation():
    """Ответ текущего хода классификатор получает КОНТЕКСТОМ, а не репликой."""
    from chatter.core.classifier import classifier_volatile_suffix
    tail = classifier_volatile_suffix("профиль", "", track_obligations=True,
                                      pending_reply=PROMISE)
    assert "керівниці" in tail


def test_production_binding_accepts_what_run_py_passes():
    """DEV-19: фейк classify в тестах принимал **kw и молчал, а ПРОДОВАЯ
    привязка (_bind_classifier) могла бы не принять новый аргумент — тогда
    раннер падал бы на первом же ходу. Сверяем с реальной сигнатурой."""
    import inspect

    from chatter.telethon_run import _bind_classifier

    params = inspect.signature(_bind_classifier.__wrapped__ if
                               hasattr(_bind_classifier, "__wrapped__")
                               else _bind_classifier).parameters
    assert params, "привязка без параметров — сигнатура не читается"
    src = inspect.getsource(_bind_classifier)
    for kw in ("track_obligations", "obligations_block", "pending_reply"):
        assert kw in src, f"продовая привязка не принимает {kw}"
