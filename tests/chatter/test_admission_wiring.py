"""Арка 3C: перевёрнутый гейт в handle_event. Незнакомец-лид → отвечаем;
знакомый (User.contact) → НЕ отвечаем, уведомляем владельца; denylist → тихо."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from chatter.core.brain import Brain
from chatter.config.loader import load_config
from chatter.core.llm import FakeLLM
from chatter.notify.base import FakeNotifier
from chatter.run import Deps
from chatter.storage.db import Store
from chatter.telethon_run import PersonaBundle, TelethonRunner
import random
import time

CLIENTS_DIR = Path(__file__).resolve().parents[2] / "chatter" / "clients"


@dataclass
class _Sender:
    bot: bool = False
    contact: bool = False


@dataclass
class _Event:
    sender_id: int
    raw_text: str = "привет"
    chat_id: int | None = None
    is_private: bool = True
    out: bool = False
    sender: _Sender = field(default_factory=_Sender)
    action: object = None

    def __post_init__(self):
        if self.chat_id is None:
            self.chat_id = self.sender_id

    async def get_input_chat(self):
        return f"inputpeer:{self.chat_id}"


def _bundle(slug, store):
    cfg = load_config(CLIENTS_DIR, slug)
    deps = Deps(cfg=cfg, store=store, brain=Brain(FakeLLM(), cfg),
                rng=random.Random(0), clock=time.time, sleep=lambda s: None)
    return PersonaBundle(cfg=cfg, deps=deps)


def _runner(*, funnel_gate, denylist=frozenset(), allowlist=frozenset({237616472}), notifier=None):
    store = Store(":memory:")
    client = MagicMock()
    client.send_message = AsyncMock(return_value=MagicMock(id=1))
    loop = asyncio.get_event_loop()
    r = TelethonRunner(
        client=client, personas={"demo": _bundle("demo", store)}, primary_slug="demo",
        allowlist=allowlist, loop=loop, denylist=denylist, funnel_gate=funnel_gate)
    r.notifier = notifier or FakeNotifier()
    r.me_id = 111
    return r


def test_funnel_gate_stranger_is_answered():
    async def scenario():
        r = _runner(funnel_gate=True)
        await r.handle_event(_Event(sender_id=999, sender=_Sender(contact=False)))
        await asyncio.sleep(0)
        assert 999 in r._debouncers        # незнакомец-лид → в обработку
    asyncio.run(scenario())


def test_funnel_gate_known_contact_not_answered_but_owner_notified():
    async def scenario():
        n = FakeNotifier()
        r = _runner(funnel_gate=True, notifier=n)
        await r.handle_event(_Event(sender_id=999, sender=_Sender(contact=True)))
        await asyncio.sleep(0)
        assert 999 not in r._debouncers    # знакомому НЕ отвечаем
        assert len(n.cards) == 1           # владельцу — уведомление
        assert "999" in n.cards[0].text_html or "знаком" in n.cards[0].text_html.lower()
    asyncio.run(scenario())


def test_funnel_gate_known_contact_notice_debounced():
    async def scenario():
        n = FakeNotifier()
        r = _runner(funnel_gate=True, notifier=n)
        await r.handle_event(_Event(sender_id=999, sender=_Sender(contact=True)))
        await r.handle_event(_Event(sender_id=999, sender=_Sender(contact=True)))
        await asyncio.sleep(0)
        assert len(n.cards) == 1           # один и тот же знакомый — одно уведомление
    asyncio.run(scenario())


def test_funnel_gate_denylist_silent():
    async def scenario():
        n = FakeNotifier()
        r = _runner(funnel_gate=True, denylist=frozenset({999}), notifier=n)
        await r.handle_event(_Event(sender_id=999))
        await asyncio.sleep(0)
        assert 999 not in r._debouncers
        assert n.cards == []               # denylist — тихо, без уведомления
    asyncio.run(scenario())


def test_gate_off_preserves_old_allowlist_behaviour():
    async def scenario():
        r = _runner(funnel_gate=False)
        await r.handle_event(_Event(sender_id=999))     # не в allowlist
        await r.handle_event(_Event(sender_id=237616472))  # в allowlist
        await asyncio.sleep(0)
        assert 999 not in r._debouncers
        assert 237616472 in r._debouncers
    asyncio.run(scenario())
