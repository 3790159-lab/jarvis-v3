"""Проводка слота обязательств в раннер (спека §4). Флаг CHATTER_OBLIGATIONS_SLOT:
off → байт-в-байт как раньше (store не трогается); on → применяем ТОЛЬКО на
здоровом классификаторе (degraded долг не трогает). Харнесс — как в
test_lead_memory (demo cfg + FakeLLM + process_batch)."""
from __future__ import annotations

import random
from pathlib import Path

from chatter.config.loader import load_config
from chatter.core.brain import Brain
from chatter.core.classifier import classify as real_classify
from chatter.core.llm import FakeLLM
from chatter.core.obligations_slot import merge_obligations
from chatter.run import Deps, process_batch
from chatter.storage.db import Store

_CLIENTS = Path(__file__).resolve().parents[2] / "chatter" / "clients"


class _T:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)

    def send_typing(self, on):
        pass

    def set_online(self, on):
        pass

    def read_acknowledge(self):
        pass


def _deps(store, clf_json, brain_replies=("ок",), stop_reasons=None):
    cfg = load_config(_CLIENTS, "demo")
    clf_llm = FakeLLM(scripted=list(clf_json))
    if stop_reasons:
        clf_llm.scripted_stop_reasons = list(stop_reasons)
    deps = Deps(cfg=cfg, store=store, brain=Brain(FakeLLM(scripted=list(brain_replies)), cfg),
                rng=random.Random(0), clock=lambda: 1000.0, sleep=lambda s: None)
    # **kw пробрасывает track_obligations/obligations_block, когда флаг on.
    deps.classify = lambda h, profile=None, **kw: real_classify(
        clf_llm, playbook=cfg.playbook, language=cfg.settings.language,
        history=h, profile=profile, **kw)
    return deps


_OPEN_BRIEF = ('{"escalate": false, "reason": "", "profile": null, '
               '"stage_signal": "engaged", "obligations": '
               '[{"kind": "brief", "owed_by": "bot", "status": "open", "detail": "обіцяний бриф"}]}')


def test_flag_off_noop(monkeypatch):
    monkeypatch.delenv("CHATTER_OBLIGATIONS_SLOT", raising=False)
    store = Store(":memory:")
    store.get_or_create_contact("lead1")
    process_batch("lead1", ["привіт"], _T(), _deps(store, [_OPEN_BRIEF]))
    assert store.get_obligations("lead1") == []      # слот выключен → не тронут


def test_obligation_created_on_healthy_classifier(monkeypatch):
    monkeypatch.setenv("CHATTER_OBLIGATIONS_SLOT", "1")
    store = Store(":memory:")
    store.get_or_create_contact("lead1")
    process_batch("lead1", ["привіт"], _T(), _deps(store, [_OPEN_BRIEF]))
    obs = store.get_obligations("lead1")
    assert len(obs) == 1
    assert obs[0].kind == "brief" and obs[0].status == "open"
    assert obs[0].created_msg_id is not None          # штамп из MAX(messages.id)


def test_degraded_classifier_does_not_touch_obligations(monkeypatch):
    monkeypatch.setenv("CHATTER_OBLIGATIONS_SLOT", "1")
    store = Store(":memory:")
    store.get_or_create_contact("lead1")
    # уже есть открытое обязательство
    store.save_obligations("lead1", merge_obligations(
        [], [{"kind": "brief", "owed_by": "bot", "status": "open", "detail": "бриф"}],
        now=1.0, current_msg_id=1))
    # классификатор деградирует (обрезка) — долг НЕ должен ни закрыться, ни стереться
    deps = _deps(store, ['{"escalate": false, "profile": "СМІ'], stop_reasons=["max_tokens"])
    process_batch("lead1", ["привіт"], _T(), deps)
    obs = store.get_obligations("lead1")
    assert len(obs) == 1 and obs[0].status == "open"   # цел


def test_classifier_closes_obligation_by_function(monkeypatch):
    monkeypatch.setenv("CHATTER_OBLIGATIONS_SLOT", "1")
    store = Store(":memory:")
    store.get_or_create_contact("lead1")
    store.save_obligations("lead1", merge_obligations(
        [], [{"kind": "brief", "owed_by": "bot", "status": "open", "detail": "бриф"}],
        now=1.0, current_msg_id=1))
    delivered = ('{"escalate": false, "reason": "", "profile": null, "stage_signal": "engaged", '
                 '"obligations": [{"kind": "brief", "owed_by": "bot", "status": "delivered", '
                 '"detail": "квал питання задані"}]}')
    process_batch("lead1", ["ось відповіді"], _T(), _deps(store, [delivered]))
    obs = {o.okey: o for o in store.get_obligations("lead1")}
    assert obs["brief"].status == "delivered"
    assert obs["brief"].closed_msg_id is not None
