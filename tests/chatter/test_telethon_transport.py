from __future__ import annotations
import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

from telethon.errors import FloodWaitError

from chatter.transport.base import Transport
from chatter.transport.telethon_tg import TelethonTransport, send_alert


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


# ---------------------------------------------------------------------------
# send_alert -- the ONLY proactive-send path (spec S4/S8): Saved Messages
# ('me'), never a real dialog, never raises even if delivery itself fails.
# ---------------------------------------------------------------------------
def test_send_alert_delivers_to_saved_messages():
    loop, thread = _running_loop()
    try:
        client = MagicMock()
        client.send_message = AsyncMock(return_value=None)

        send_alert(client, loop, "something broke")

        client.send_message.assert_called_once_with("me", "something broke")
    finally:
        _stop(loop, thread)


def test_send_alert_never_raises_when_delivery_itself_fails():
    loop, thread = _running_loop()
    try:
        client = MagicMock()
        client.send_message = AsyncMock(side_effect=RuntimeError("network gone"))

        send_alert(client, loop, "something broke")  # must not raise
    finally:
        _stop(loop, thread)


# ---------------------------------------------------------------------------
# FloodWait backoff (spec S8): alert + bounded, non-hammering retry.
# ---------------------------------------------------------------------------
def _make_flooding_client(*, chat_flood_count: int):
    """A client whose send_message: floods (raises FloodWaitError) the first
    `chat_flood_count` times it is called for the OUTBOUND chat, then
    succeeds -- but always succeeds instantly when called for 'me' (the
    alert target), so send_alert's own call never perturbs the flood
    sequence under test. Returns (client, call_counter)."""
    counter = {"chat_calls": 0}

    async def _send_message(target, text):
        if target == "me":
            return None
        counter["chat_calls"] += 1
        if counter["chat_calls"] <= chat_flood_count:
            raise FloodWaitError(request=None, capture=7)
        return None

    client = MagicMock()
    client.send_message = AsyncMock(side_effect=_send_message)
    return client, counter


def _alert_calls(client):
    return [c for c in client.send_message.call_args_list if c.args[0] == "me"]


def test_send_retries_once_after_floodwait_then_succeeds():
    loop, thread = _running_loop()
    try:
        client, counter = _make_flooding_client(chat_flood_count=1)
        sleeps: list[float] = []
        transport = TelethonTransport(client, chat=12345, loop=loop, backoff_sleep=sleeps.append)

        transport.send("hello there")

        # the flooded attempt + the successful retry
        assert counter["chat_calls"] == 2
        # backed off ~e.seconds (7), NOT hammered
        assert sleeps == [7]
        alerts = _alert_calls(client)
        assert len(alerts) == 1
        assert "FloodWait 7s" in alerts[0].args[1]
    finally:
        _stop(loop, thread)


def test_send_gives_up_after_repeated_floodwait_instead_of_looping():
    loop, thread = _running_loop()
    try:
        client, counter = _make_flooding_client(chat_flood_count=99)  # always floods
        sleeps: list[float] = []
        transport = TelethonTransport(client, chat=54321, loop=loop, backoff_sleep=sleeps.append)

        transport.send("hello")  # must not raise, must not hang, must not loop

        assert counter["chat_calls"] == 2  # exactly 2 attempts total
        assert sleeps == [7]  # exactly ONE backoff, between attempt 1 and 2
        assert len(_alert_calls(client)) == 2  # one alert per flooded attempt
    finally:
        _stop(loop, thread)
