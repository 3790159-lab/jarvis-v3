"""Гейт глушения в process_batch. Главный тест — отмена НА ЛЕТУ."""
from __future__ import annotations

import random

import pytest

from chatter.config.loader import load_config
from chatter.core.brain import Brain
from chatter.core.llm import FakeLLM
from chatter.run import Deps, process_batch
from chatter.storage.db import Store
from chatter.transport.fake import FakeConsoleTransport
from tests.chatter.test_loader import SETTINGS, _make_client


def _build_deps(tmp_path, *, scripted: list[str], now: float = 1000.0) -> Deps:
    _make_client(tmp_path, settings=SETTINGS)
    cfg = load_config(tmp_path, "demo")
    llm = FakeLLM(scripted=scripted)
    clock = {"t": now}
    return Deps(
        cfg=cfg,
        store=Store(":memory:"),
        brain=Brain(llm, cfg),
        rng=random.Random(0),
        clock=lambda: clock["t"],
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
    )


@pytest.fixture
def fake_transport() -> FakeConsoleTransport:
    return FakeConsoleTransport(preload=[], echo=False)


@pytest.fixture
def live_deps(tmp_path) -> Deps:
    return _build_deps(tmp_path, scripted=["Здравствуйте! Что интересует?"])


@pytest.fixture
def muted_deps(tmp_path) -> Deps:
    deps = _build_deps(tmp_path, scripted=["НЕ ДОЛЖНО ИСПОЛЬЗОВАТЬСЯ"])
    deps.store.get_or_create_contact("c1")
    deps.store.mute("c1", source="command", now=deps.clock())
    return deps


def test_muted_contact_gets_no_reply_but_the_inbound_is_remembered(muted_deps, fake_transport):
    # Входящее обязано лечь в историю ДО гейта: заглушённая Аня всё равно
    # должна помнить, что ей написали, иначе после /resume контекст рваный.
    deps = muted_deps
    process_batch("c1", ["Привет"], fake_transport, deps)
    assert fake_transport.sent == []
    history = deps.store.history("c1")
    assert [m["text"] for m in history] == ["Привет"]
    assert history[0]["role"] == "user"


def test_mute_arriving_mid_reply_aborts_the_remaining_messages(live_deps, fake_transport):
    # Позорный сценарий: Аня ушла в паузу чтения 4с + печать 10с, владелец за
    # это время ответил руками, а Аня через 5 секунд говорит поверх него.
    # Гейт перед КАЖДОЙ Say, а не один раз в начале.
    deps = live_deps
    deps.store.get_or_create_contact("c1")

    original_sleep = deps.sleep

    def sleep_then_owner_jumps_in(seconds: float) -> None:
        original_sleep(seconds)
        deps.store.mute("c1", source="human_takeover", msg_id=1,
                         detail="я сам отвечу", now=deps.clock())

    deps.sleep = sleep_then_owner_jumps_in

    process_batch("c1", ["Сколько стоит?"], fake_transport, deps)
    assert fake_transport.sent == [], "Аня заговорила поверх владельца"


def test_kill_switch_silences_a_contact_that_has_no_pause_of_its_own(live_deps, fake_transport):
    live_deps.store.set_runtime_flag("kill_switch", "1", ts=live_deps.clock())
    process_batch("c1", ["Привет"], fake_transport, live_deps)
    assert fake_transport.sent == []
