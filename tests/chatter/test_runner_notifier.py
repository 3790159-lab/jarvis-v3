from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from chatter.notify.base import FakeNotifier
from chatter.notify.control_bot import ControlBotNotifier
from chatter.notify.saved_messages import SavedMessagesNotifier
from chatter.storage.db import Store
from chatter.telethon_run import TelethonRunner, build_runner

CLIENTS_DIR = Path(__file__).resolve().parents[2] / "chatter" / "clients"
ALLOWED = 237616472


def _client():
    client = MagicMock()
    client.send_message = AsyncMock(return_value=MagicMock(id=900))
    return client


def _clients_with_control(tmp_path, token_env: str) -> Path:
    dst = tmp_path / "clients"
    shutil.copytree(CLIENTS_DIR, dst)
    settings = (dst / "demo" / "settings.yaml").read_text(encoding="utf-8")
    settings += (
        "control:\n"
        f"  control_bot_token_env: {token_env}\n"
        "  owner_chat_id: 237616472\n"
    )
    (dst / "demo" / "settings.yaml").write_text(settings, encoding="utf-8")
    return dst


def test_no_control_block_uses_saved_messages_and_no_poller():
    runner = build_runner(
        client=_client(), clients_dir=CLIENTS_DIR, persona_slugs=["demo", "demo2"],
        store=Store(":memory:"), loop=asyncio.new_event_loop(), llm_mode="fake")
    assert isinstance(runner.notifier, SavedMessagesNotifier)
    assert runner.poller is None


def test_control_token_present_builds_control_bot_and_poller(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_CTRL_TOKEN", "123:ABC")
    clients = _clients_with_control(tmp_path, "TEST_CTRL_TOKEN")
    runner = build_runner(
        client=_client(), clients_dir=clients, persona_slugs=["demo", "demo2"],
        store=Store(":memory:"), loop=asyncio.new_event_loop(), llm_mode="fake")
    assert isinstance(runner.notifier, ControlBotNotifier)
    assert runner.poller is not None


def test_control_token_named_but_absent_falls_back_to_saved_messages(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_CTRL_TOKEN", raising=False)
    clients = _clients_with_control(tmp_path, "TEST_CTRL_TOKEN")
    runner = build_runner(
        client=_client(), clients_dir=clients, persona_slugs=["demo", "demo2"],
        store=Store(":memory:"), loop=asyncio.new_event_loop(), llm_mode="fake")
    # имя переменной задано, но токена в окружении нет -> фоллбек (инвариант арки)
    assert isinstance(runner.notifier, SavedMessagesNotifier)
    assert runner.poller is None


def test_deps_get_notifier_and_keywords_injected():
    store = Store(":memory:")
    runner = build_runner(
        client=_client(), clients_dir=CLIENTS_DIR, persona_slugs=["demo", "demo2"],
        store=store, loop=asyncio.new_event_loop(), llm_mode="fake")
    deps = runner.personas["demo"].deps
    assert deps.notifier is runner.notifier
    assert deps.escalation_keywords            # из playbook demo
    assert deps.escalation_card is not None    # билдер с именем/ссылкой


class _ChatEnt:
    first_name = "Даниил"
    last_name = None
    title = None
    username = "dan"


class _Event:
    def __init__(self):
        self.chat = _ChatEnt()
        self.chat_id = ALLOWED
        self.message = MagicMock(id=555)


def test_post_pause_card_goes_through_notifier():
    store = Store(":memory:")
    runner = build_runner(
        client=_client(), clients_dir=CLIENTS_DIR, persona_slugs=["demo", "demo2"],
        store=store, loop=asyncio.new_event_loop(), llm_mode="fake")
    fake = FakeNotifier(has_buttons=True)
    runner.notifier = fake
    contact_id = f"{ALLOWED}:demo"
    store.get_or_create_contact(contact_id)

    asyncio.run(runner.post_pause_card(_Event(), contact_id, "я сам отвечу"))

    assert len(fake.cards) == 1
    card = fake.cards[0]
    assert card.kind == "pause"
    assert "Даниил" in card.text_html
    # у карточки паузы есть кнопки (для контрол-бота) и подсказки (для Saved Messages)
    assert card.buttons and card.reply_hints
