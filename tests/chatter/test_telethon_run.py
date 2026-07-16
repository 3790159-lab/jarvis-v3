from __future__ import annotations
import asyncio
import itertools
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon import events
from telethon.errors import AuthKeyError, UnauthorizedError

from chatter.config.loader import load_config
from chatter.core.brain import Brain
from chatter.core.llm import FakeLLM
from chatter.storage.db import Store
from chatter.transport.telethon_tg import TelethonTransport
from chatter.run import Deps
import chatter.telethon_run as tr
from chatter.telethon_run import (
    ChatDebouncer, PersonaBundle, TelethonRunner, build_runner, run_client, should_handle,
)

CLIENTS_DIR = Path(__file__).resolve().parent.parent.parent / "chatter" / "clients"
ALLOWED = 237616472


# ---------------------------------------------------------------------------
# 1. should_handle -- pure eligibility filter, full 2^4 matrix
# ---------------------------------------------------------------------------
def test_should_handle_only_true_for_private_incoming_human_nonservice():
    for is_private, is_outgoing, sender_is_bot, is_service in itertools.product([True, False], repeat=4):
        expected = is_private and not is_outgoing and not sender_is_bot and not is_service
        got = should_handle(
            is_private=is_private, is_outgoing=is_outgoing,
            sender_is_bot=sender_is_bot, is_service=is_service,
        )
        assert got == expected, (is_private, is_outgoing, sender_is_bot, is_service)


# ---------------------------------------------------------------------------
# 2. ChatDebouncer -- deterministic, no real waits, via a manual gate
# ---------------------------------------------------------------------------
class ManualSleeper:
    """Test double for ChatDebouncer's injected async_sleep. Instead of really
    waiting, it parks on an asyncio.Event until the test explicitly releases
    it, after moving the fake clock however far the test wants. This makes
    the debounce loop's mid-wait interleaving (a new message arriving while
    "waiting") fully deterministic and instant -- no real wall-clock delay,
    ever, unlike production which uses real asyncio.sleep."""

    def __init__(self):
        self.requested: list[float] = []
        self._gate = asyncio.Event()

    async def sleep(self, seconds: float) -> None:
        self.requested.append(seconds)
        self._gate.clear()
        await self._gate.wait()

    def release(self) -> None:
        self._gate.set()


async def _pump(n: int = 3) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


def test_two_rapid_messages_coalesce_into_one_batch():
    async def scenario():
        clock = {"t": 0.0}
        received: list[list[str]] = []

        async def on_ready(batch):
            received.append(list(batch))

        sleeper = ManualSleeper()
        deb = ChatDebouncer(
            window=3.0, max_window=15.0, clock=lambda: clock["t"],
            async_sleep=sleeper.sleep, on_ready=on_ready,
        )
        deb.add("hello")
        await _pump()  # task starts, computes wait=3, parks on the gate
        clock["t"] = 0.5
        deb.add("there")  # well within the window -- extends last_at to 0.5
        await _pump()
        assert received == []  # nothing fired yet -- still gated

        clock["t"] = 3.5  # 3s of quiet since the SECOND message
        sleeper.release()
        await _pump()
        await deb.task

        assert received == [["hello", "there"]]

    asyncio.run(scenario())


def test_message_after_ceiling_still_fires_even_if_quiet_gap_not_elapsed():
    async def scenario():
        clock = {"t": 0.0}
        received: list[list[str]] = []

        async def on_ready(batch):
            received.append(list(batch))

        sleeper = ManualSleeper()
        deb = ChatDebouncer(
            window=3.0, max_window=5.0, clock=lambda: clock["t"],
            async_sleep=sleeper.sleep, on_ready=on_ready,
        )
        deb.add("m1")
        await _pump()
        assert sleeper.requested == [3.0]  # min(window=3, ceiling=5)

        clock["t"] = 3.0
        deb.add("m2")  # resets the quiet gap (last_at=3.0)
        sleeper.release()
        await _pump()
        assert sleeper.requested == [3.0, 2.0]  # min(3-(3-3)=3, 5-(3-0)=2) = 2

        clock["t"] = 5.0  # ceiling hit; quiet gap since m2 is only 2s (< window=3)
        sleeper.release()
        await _pump()
        await deb.task

        assert received == [["m1", "m2"]]  # fired via the CEILING, not quiet

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# fixtures shared by the runner-level tests
# ---------------------------------------------------------------------------
@dataclass
class FakeSender:
    bot: bool = False


@dataclass
class FakeEvent:
    """Duck-types exactly the Telethon NewMessage.Event attributes
    TelethonRunner.handle_event reads. Never a real Telethon object."""
    sender_id: int
    raw_text: str = ""
    chat_id: int | None = None
    is_private: bool = True
    out: bool = False
    sender: FakeSender = field(default_factory=FakeSender)
    action: object = None

    def __post_init__(self):
        if self.chat_id is None:
            self.chat_id = self.sender_id


def _persona_bundle(slug: str, *, store: Store, scripted: list[str] | None = None) -> PersonaBundle:
    cfg = load_config(CLIENTS_DIR, slug)
    llm = FakeLLM(scripted=scripted)
    deps = Deps(
        cfg=cfg, store=store, brain=Brain(llm, cfg),
        rng=random.Random(0), clock=lambda: 1000.0, sleep=lambda _s: None,
    )
    return PersonaBundle(cfg=cfg, deps=deps)


def _loop_for_test() -> asyncio.AbstractEventLoop:
    """Prefer the loop already running under asyncio.run() (needed so
    run_coroutine_threadsafe scheduling in async scenarios actually works);
    fall back to a fresh, unstarted loop for purely-sync tests that never
    exercise TelethonTransport's marshalling (Windows' Proactor policy has no
    "current" loop outside a running context or an explicit set_event_loop)."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop()


def _runner(*, personas=None, allowlist=frozenset({ALLOWED}), client=None):
    if personas is None:
        # One shared Store across personas, matching production (build_runner /
        # load_personas): the composite contact key f"{sender_id}:{persona}"
        # is what keeps dialogues separate, not separate databases.
        shared_store = Store(":memory:")
        personas = {
            "demo": _persona_bundle("demo", store=shared_store),
            "demo2": _persona_bundle("demo2", store=shared_store),
        }
    client = client or MagicMock()
    client.send_message = AsyncMock(return_value=None)
    action_ctx = MagicMock()
    action_ctx.__aenter__ = AsyncMock(return_value=action_ctx)
    action_ctx.__aexit__ = AsyncMock(return_value=None)
    client.action = MagicMock(return_value=action_ctx)
    loop = _loop_for_test()
    return TelethonRunner(
        client=client, personas=personas, primary_slug="demo",
        allowlist=allowlist, loop=loop,
    ), client


# ---------------------------------------------------------------------------
# 3. never writes first
# ---------------------------------------------------------------------------
def test_zero_incoming_events_means_send_is_never_called():
    runner, client = _runner()
    # No handle_event call at all -- the ONLY path to send/typing.
    client.send_message.assert_not_called()
    client.action.assert_not_called()


# ---------------------------------------------------------------------------
# 4. allowlist
# ---------------------------------------------------------------------------
def test_non_allowlisted_sender_is_ignored_no_reply_no_storage():
    async def scenario():
        runner, client = _runner(allowlist=frozenset({ALLOWED}))
        stranger = 999999
        event = FakeEvent(sender_id=stranger, raw_text="hi there")
        await runner.handle_event(event)
        await asyncio.sleep(0)

        client.send_message.assert_not_called()
        bundle = runner.personas["demo"]
        assert bundle.deps.store.history(f"{stranger}:demo") == []

    asyncio.run(scenario())


def test_non_allowlisted_switch_is_ignored():
    async def scenario():
        runner, client = _runner(allowlist=frozenset({ALLOWED}))
        stranger = 999999
        event = FakeEvent(sender_id=stranger, raw_text="/switch")
        await runner.handle_event(event)
        await asyncio.sleep(0)

        client.send_message.assert_not_called()
        assert runner.persona_for(stranger) == "demo"  # unchanged (default/primary)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 5. /switch
# ---------------------------------------------------------------------------
def test_switch_toggles_persona_and_sends_plain_ack_not_via_llm():
    async def scenario():
        runner, client = _runner()
        event = FakeEvent(sender_id=ALLOWED, raw_text="/switch")

        assert runner.persona_for(ALLOWED) == "demo"
        await runner.handle_event(event)
        await asyncio.sleep(0)

        assert runner.persona_for(ALLOWED) == "demo2"
        client.send_message.assert_called_once()
        chat_arg, text_arg = client.send_message.call_args.args
        assert chat_arg == ALLOWED
        assert "Dmitry" in text_arg and "en" in text_arg

        # No dialogue was touched by /switch -- it never goes through process_batch.
        demo_bundle = runner.personas["demo"]
        demo2_bundle = runner.personas["demo2"]
        assert demo_bundle.deps.store.history(f"{ALLOWED}:demo") == []
        assert demo2_bundle.deps.store.history(f"{ALLOWED}:demo2") == []

    asyncio.run(scenario())


def test_switch_back_and_forth():
    async def scenario():
        runner, client = _runner()
        event = FakeEvent(sender_id=ALLOWED, raw_text="/switch")

        await runner.handle_event(event)
        await asyncio.sleep(0)
        assert runner.persona_for(ALLOWED) == "demo2"

        await runner.handle_event(event)
        await asyncio.sleep(0)
        assert runner.persona_for(ALLOWED) == "demo"

    asyncio.run(scenario())


def test_next_message_after_switch_is_processed_under_the_new_persona(monkeypatch):
    async def scenario():
        runner, client = _runner()
        calls = []

        def fake_process_batch(contact_id, batch, transport, deps):
            calls.append((contact_id, deps.cfg.slug))

        monkeypatch.setattr(tr, "process_batch", fake_process_batch)

        await runner.handle_event(FakeEvent(sender_id=ALLOWED, raw_text="/switch"))
        await asyncio.sleep(0)
        assert runner.persona_for(ALLOWED) == "demo2"

        # Tiny windows so this test finishes fast; still exercises the real
        # ChatDebouncer with the real (default) async_sleep/clock.
        runner.personas["demo2"].cfg.settings.timings  # sanity: attribute exists
        object.__setattr__(runner.personas["demo2"].cfg.settings.timings, "debounce_window", 0.01)
        object.__setattr__(runner.personas["demo2"].cfg.settings.timings, "debounce_max", 0.03)

        await runner.handle_event(FakeEvent(sender_id=ALLOWED, raw_text="hello again"))
        deb = runner._debouncers[ALLOWED]
        await deb.task

        assert calls == [(f"{ALLOWED}:demo2", "demo2")]

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 6. process a batch: right contact_id / persona cfg / transport wiring
# ---------------------------------------------------------------------------
def test_coalesced_batch_invokes_process_batch_with_right_contact_and_cfg(monkeypatch):
    async def scenario():
        runner, client = _runner()
        calls = []

        def fake_process_batch(contact_id, batch, transport, deps):
            calls.append((contact_id, batch, deps.cfg.slug, isinstance(transport, TelethonTransport)))

        monkeypatch.setattr(tr, "process_batch", fake_process_batch)
        object.__setattr__(runner.personas["demo"].cfg.settings.timings, "debounce_window", 0.01)
        object.__setattr__(runner.personas["demo"].cfg.settings.timings, "debounce_max", 0.03)

        await runner.handle_event(FakeEvent(sender_id=ALLOWED, raw_text="привет"))
        await runner.handle_event(FakeEvent(sender_id=ALLOWED, raw_text="как дела?"))
        deb = runner._debouncers[ALLOWED]
        await deb.task

        assert calls == [(f"{ALLOWED}:demo", ["привет", "как дела?"], "demo", True)]

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 7. runner assembly wiring
# ---------------------------------------------------------------------------
def test_build_runner_wires_new_message_incoming_handler():
    client = MagicMock()
    client.send_message = AsyncMock(return_value=None)
    store = Store(":memory:")
    loop = _loop_for_test()

    runner = build_runner(
        client=client, clients_dir=CLIENTS_DIR, persona_slugs=["demo", "demo2"],
        store=store, loop=loop, llm_mode="fake",
    )

    client.add_event_handler.assert_called_once()
    handler, event_builder = client.add_event_handler.call_args.args
    assert isinstance(event_builder, events.NewMessage)
    assert runner.allowlist == frozenset({ALLOWED})
    assert runner.primary_slug == "demo"

    # the registered handler really delegates into runner.handle_event
    async def drive():
        await handler(FakeEvent(sender_id=999999, raw_text="hi"))  # non-allowlisted -> no crash, no send
        await asyncio.sleep(0)
    asyncio.run(drive())
    client.send_message.assert_not_called()


def test_build_runner_requires_telegram_block_on_primary_persona(tmp_path):
    # demo2 has no [telegram] block of its own (spec: allowlist comes from
    # the launch/primary persona only) -- using it as primary must fail loudly.
    client = MagicMock()
    store = Store(":memory:")
    loop = _loop_for_test()
    with pytest.raises(ValueError, match="telegram"):
        build_runner(
            client=client, clients_dir=CLIENTS_DIR, persona_slugs=["demo2", "demo"],
            store=store, loop=loop, llm_mode="fake",
        )


# ---------------------------------------------------------------------------
# 8. run_client -- session-loss (spec S2/S8): alert + graceful non-zero
# return, not a silent death or bare traceback. No real TelegramClient
# anywhere -- `client` is a MagicMock, `.start()`/`.run_until_disconnected()`
# are plain (sync) mocks exactly like the real Telethon methods.
# ---------------------------------------------------------------------------
def _running_loop_on_thread():
    """A real asyncio loop running on a background thread, so `send_alert`'s
    run_coroutine_threadsafe(...).result() inside run_client actually
    resolves -- mirrors test_telethon_transport.py's helper of the same
    shape. No real client/network; only a MagicMock is scheduled onto it."""
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    return loop, t


def _stop_loop_thread(loop, thread):
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2)
    assert not thread.is_alive()


@pytest.mark.parametrize("error_cls", [UnauthorizedError, AuthKeyError])
def test_run_client_maps_session_loss_to_alert_and_nonzero_exit(error_cls):
    loop, thread = _running_loop_on_thread()
    try:
        client = MagicMock()
        client.send_message = AsyncMock(return_value=None)  # the alert's own send
        client.start = MagicMock(side_effect=error_cls(request=None, message="AUTH_KEY_UNREGISTERED", code=401))
        client.run_until_disconnected = MagicMock()  # must not even be reached

        rc = run_client(client, loop)

        assert rc != 0
        client.run_until_disconnected.assert_not_called()
        client.send_message.assert_called_once()
        chat_arg, text_arg = client.send_message.call_args.args
        assert chat_arg == "me"
        assert "session lost" in text_arg
        assert "telethon_login" in text_arg
    finally:
        _stop_loop_thread(loop, thread)


def test_run_client_alert_failure_does_not_crash_the_session_loss_path():
    # Even the alert's own Saved-Messages send can fail (e.g. the session is
    # ALSO too dead to send to 'me') -- run_client must still return cleanly.
    loop, thread = _running_loop_on_thread()
    try:
        client = MagicMock()
        client.send_message = AsyncMock(side_effect=RuntimeError("also broken"))
        client.start = MagicMock(side_effect=AuthKeyError(request=None, message="AUTH_KEY_INVALID", code=401))
        client.run_until_disconnected = MagicMock()

        rc = run_client(client, loop)  # must not raise

        assert rc != 0
    finally:
        _stop_loop_thread(loop, thread)


def test_run_client_returns_zero_on_clean_disconnect():
    loop, thread = _running_loop_on_thread()
    try:
        client = MagicMock()
        client.start = MagicMock()
        client.run_until_disconnected = MagicMock()  # returns normally -> clean stop

        rc = run_client(client, loop)

        assert rc == 0
        client.start.assert_called_once()
        client.run_until_disconnected.assert_called_once()
    finally:
        _stop_loop_thread(loop, thread)


def test_run_client_does_not_swallow_unrelated_errors():
    loop, thread = _running_loop_on_thread()
    try:
        client = MagicMock()
        client.start = MagicMock(side_effect=RuntimeError("unrelated bug"))
        client.run_until_disconnected = MagicMock()

        with pytest.raises(RuntimeError, match="unrelated bug"):
            run_client(client, loop)
    finally:
        _stop_loop_thread(loop, thread)
