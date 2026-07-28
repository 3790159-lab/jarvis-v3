"""P20: проводка редакции и состояния фолбэка через process_batch.

Живой замер 2026-07-29: лид трижды получил одну и ту же заглушку, а четыре
обеспеченные цены не доехали. Здесь фиксируем поведение целиком, на реальном
пути process_batch.
"""
from __future__ import annotations

import random
import time
from pathlib import Path

import pytest

from chatter.config.loader import load_config
from chatter.core.brain import Brain
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


def _deps(replies, *, notifier=None):
    cfg = load_config(CLIENTS, "demo")
    store = Store(":memory:")
    deps = Deps(
        cfg=cfg, store=store, brain=Brain(FakeLLM(scripted=list(replies)), cfg),
        rng=random.Random(0), clock=lambda: 1000.0, sleep=lambda s: None,
    )
    deps.notifier = notifier
    deps.classify = None
    deps.escalation_keywords = ["позови", "жалоба"]
    return deps


def _out(transport):
    return " ".join(transport.sent)


def test_unbacked_number_is_redacted_not_whole_reply_suppressed():
    """Ядро дефекта (б): одно необеспеченное число не имеет права убивать
    обеспеченную цену рядом."""
    deps = _deps(["Консультация стоит 5000 грн, а срочный выезд 99999 грн."])
    tr = RecordingTransport()
    process_batch("lead", ["сколько стоит?"], tr, deps)

    out = _out(tr)
    assert "5000" in out, f"обеспеченная цена погибла: {out!r}"
    assert "99999" not in out, f"выдуманная цена уехала лиду: {out!r}"
    assert "уточню детали" not in out.lower(), f"скатились в заглушку: {out!r}"


def test_unbacked_promise_without_numbers_is_still_fully_suppressed():
    """ПРЕДОХРАНИТЕЛЬ: редакция работает только по ЧИСЛАМ. Безцифровое обещание
    (H1: скидка/гарантия) она не видит — значит для него обязано остаться
    полное подавление, иначе редакция стала бы дырой в гардрейле."""
    deps = _deps(["Дам вам скидку, договоримся."])
    tr = RecordingTransport()
    process_batch("lead", ["а скидка будет?"], tr, deps)

    out = _out(tr)
    assert "скидку" not in out.lower(), f"обещание уехало лиду: {out!r}"


def test_two_suppressions_in_a_row_are_not_byte_identical():
    """Дефект (а): msg 318/320/322 были байт-в-байт одинаковы."""
    deps = _deps(["Дам вам скидку, договоримся.", "Дам вам скидку, договоримся."])
    tr = RecordingTransport()
    process_batch("lead", ["а скидка?"], tr, deps)
    first = _out(tr)

    tr2 = RecordingTransport()
    process_batch("lead", ["ну так что со скидкой?"], tr2, deps)
    second = _out(tr2)

    assert first.strip() and second.strip(), "лид не имеет права получить тишину"
    assert first.strip().casefold() != second.strip().casefold(), (
        f"лид получил ту же реплику дважды: {first!r}")


def test_awaiting_owner_wording_after_card_delivered(monkeypatch):
    """Дефект (а), смысловая часть: вопрос УЖЕ у владельца → «вже передала»,
    а не «уточню и вернусь» (последнее — ложь на повторе)."""
    monkeypatch.setenv("CHATTER_OBLIGATIONS_SLOT", "1")
    notifier = FakeNotifier()
    deps = _deps(["Дам вам скидку, договоримся."] * 2, notifier=notifier)
    tr = RecordingTransport()
    process_batch("lead", ["позови человека, есть жалоба"], tr, deps)

    obl = deps.store.get_obligations("lead")
    ow = next((o for o in obl if o.okey == "owner_write"), None)
    assert ow is not None and ow.status == "delivered", (
        f"предусловие теста не выполнено: {obl}")

    tr2 = RecordingTransport()
    process_batch("lead", ["ну так что там?"], tr2, deps)
    out = _out(tr2).lower()
    assert "переда" in out, f"нет состояния «уже передала»: {out!r}"


def test_redaction_is_logged_pii_free(caplog):
    """Требование владельца: каждая редакция логируется — числом и правилом,
    без текста лида и без текста ответа."""
    caplog.set_level("INFO")
    deps = _deps(["Консультация стоит 5000 грн, а срочный выезд 99999 грн."])
    tr = RecordingTransport()
    process_batch("lead", ["сколько?"], tr, deps)

    marks = [r.getMessage() for r in caplog.records if "redact" in r.getMessage()]
    assert marks, f"редакция не оставила следа в логе: {[r.getMessage() for r in caplog.records]}"
    blob = " ".join(marks)
    assert "99999" in blob, "в логе нет числа-причины"
    for leaked in ("Консультация", "срочный выезд", "сколько?"):
        assert leaked not in blob, f"в лог протёк текст: {blob!r}"
