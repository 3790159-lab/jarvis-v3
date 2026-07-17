"""Fix 3: команда, набранная владельцем ПРЯМО в диалоге лида (не в пульте).
Выполнить → НЕ писать в историю → удалить сообщение → подсказать пульту."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from chatter.core.console import parse_command
from chatter.notify.base import FakeNotifier
from chatter.storage.db import Store
from chatter.telethon_run import build_runner

CLIENTS_DIR = Path(__file__).resolve().parents[2] / "chatter" / "clients"
ALLOWED = 237616472
CONTACT = f"{ALLOWED}:demo"


def _client():
    c = MagicMock()
    c.send_message = AsyncMock(return_value=MagicMock(id=1))
    c.delete_messages = AsyncMock()
    c.get_entity = AsyncMock(return_value=MagicMock(
        first_name="Лид", last_name=None, title=None, username="lead"))
    return c


class _OutEvent:
    def __init__(self, chat_id, msg_id, text):
        self.chat_id = chat_id
        self.raw_text = text
        self.message = MagicMock(id=msg_id)


def _runner(store):
    return build_runner(
        client=_client(), clients_dir=CLIENTS_DIR, persona_slugs=["demo", "demo2"],
        store=store, loop=asyncio.get_event_loop(), llm_mode="fake")


def test_inline_resume_executes_without_history_and_deletes_and_notifies():
    async def scenario():
        store = Store(":memory:")
        runner = _runner(store)
        fake = FakeNotifier()
        runner.notifier = fake
        store.get_or_create_contact(CONTACT)
        store.mute(CONTACT, source="human_takeover", now=1.0)

        await runner.handle_inline_command(
            _OutEvent(ALLOWED, 555, "/resume"), CONTACT, parse_command("/resume"))

        # исполнено: размучено
        assert store.get_or_create_contact(CONTACT)["paused"] == 0
        # НЕ записано в историю (модель это не увидит)
        assert store.history(CONTACT) == []
        # сообщение удалено из диалога лида
        runner.client.delete_messages.assert_awaited_once()
        # пульт получил подсказку
        assert len(fake.cards) == 1
        assert "resume" in fake.cards[0].text_html.lower()

    asyncio.run(scenario())


def test_inline_pause_command_mutes_this_dialog():
    async def scenario():
        store = Store(":memory:")
        runner = _runner(store)
        runner.notifier = FakeNotifier()
        store.get_or_create_contact(CONTACT)

        await runner.handle_inline_command(
            _OutEvent(ALLOWED, 556, "/pause"), CONTACT, parse_command("/pause"))

        assert store.get_or_create_contact(CONTACT)["paused"] == 1
        assert store.history(CONTACT) == []
        runner.client.delete_messages.assert_awaited_once()

    asyncio.run(scenario())


def _capture_handlers(client):
    handlers = {}
    def add(cb, ev):
        # различаем по фильтру события: outgoing=True у исходящего
        if getattr(ev, "outgoing", None) is True:
            handlers["out"] = cb
        elif getattr(ev, "chats", None) == "me":
            handlers["console"] = cb
        else:
            handlers["in"] = cb
    client.add_event_handler = add
    return handlers


def test_outgoing_command_in_lead_dialog_does_not_mute_it_as_takeover():
    async def scenario():
        store = Store(":memory:")
        client = _client()
        handlers = _capture_handlers(client)
        runner = build_runner(
            client=client, clients_dir=CLIENTS_DIR, persona_slugs=["demo", "demo2"],
            store=store, loop=asyncio.get_event_loop(), llm_mode="fake")
        runner.notifier = FakeNotifier()
        runner.me_id = 111  # владелец аккаунта; лид = ALLOWED
        store.get_or_create_contact(CONTACT)

        # владелец печатает "/resume" в диалоге лида (исходящее)
        await handlers["out"](_OutEvent(ALLOWED, 900, "/resume"))

        # НЕ перехват: диалог НЕ заглушён (иначе /resume в диалоге глушил бы Аню)
        assert store.get_or_create_contact(CONTACT)["paused"] == 0
        # команда удалена, в историю не легла
        client.delete_messages.assert_awaited_once()
        assert store.history(CONTACT) == []

    asyncio.run(scenario())
