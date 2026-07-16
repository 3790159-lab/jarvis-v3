from __future__ import annotations
import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

from chatter.transport.base import Transport
from chatter.transport.telethon_tg import TelethonTransport


def _running_loop():
    """A real asyncio loop running on a background thread -- exactly the shape
    TelethonTransport is designed for (the Telethon client owns a loop on one
    thread; process_batch calls these methods from a worker thread). No real
    network or TelegramClient involved: `client` is a MagicMock/AsyncMock."""
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    return loop, t


def _stop(loop, thread):
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_is_transport():
    client = MagicMock()
    loop, thread = _running_loop()
    try:
        assert isinstance(TelethonTransport(client, chat=1, loop=loop), Transport)
    finally:
        _stop(loop, thread)


def test_send_schedules_send_message_on_loop():
    loop, thread = _running_loop()
    try:
        client = MagicMock()
        client.send_message = AsyncMock(return_value=None)
        transport = TelethonTransport(client, chat=12345, loop=loop)

        transport.send("hello there")

        client.send_message.assert_called_once_with(12345, "hello there")
    finally:
        _stop(loop, thread)


def test_send_typing_on_enters_action_context_and_holds_it():
    loop, thread = _running_loop()
    try:
        client = MagicMock()
        action_ctx = MagicMock()
        action_ctx.__aenter__ = AsyncMock(return_value=action_ctx)
        action_ctx.__aexit__ = AsyncMock(return_value=None)
        client.action = MagicMock(return_value=action_ctx)
        transport = TelethonTransport(client, chat=999, loop=loop)

        transport.send_typing(True)

        client.action.assert_called_once_with(999, "typing")
        action_ctx.__aenter__.assert_called_once()
        action_ctx.__aexit__.assert_not_called()
        assert transport._typing_ctx is action_ctx
    finally:
        _stop(loop, thread)


def test_send_typing_off_exits_the_held_context():
    loop, thread = _running_loop()
    try:
        client = MagicMock()
        action_ctx = MagicMock()
        action_ctx.__aenter__ = AsyncMock(return_value=action_ctx)
        action_ctx.__aexit__ = AsyncMock(return_value=None)
        client.action = MagicMock(return_value=action_ctx)
        transport = TelethonTransport(client, chat=999, loop=loop)

        transport.send_typing(True)
        transport.send_typing(False)

        action_ctx.__aexit__.assert_called_once()
        assert transport._typing_ctx is None
    finally:
        _stop(loop, thread)


def test_send_typing_off_without_prior_on_is_a_noop():
    loop, thread = _running_loop()
    try:
        client = MagicMock()
        transport = TelethonTransport(client, chat=1, loop=loop)

        transport.send_typing(False)  # no prior on() -- must not blow up

        client.action.assert_not_called()
    finally:
        _stop(loop, thread)
