from __future__ import annotations
import asyncio
import logging
import time
from typing import Awaitable, Callable

from telethon.errors import FloodWaitError

from chatter.transport.base import Transport

log = logging.getLogger("chatter.transport.telethon_tg")

# Cap on the backoff sleep after a FloodWaitError, regardless of how large
# `e.seconds` (or the consecutive-attempt multiplier) grows -- spec S8: "not
# hammer", but also not stall the process indefinitely on one send.
MAX_FLOODWAIT_BACKOFF_SECONDS = 300.0


def send_alert(client, loop: asyncio.AbstractEventLoop, text: str) -> None:
    """The ONLY proactive-send path this codebase allows (spec S4/S8
    exception): delivers `text` to the bot account's OWN Saved Messages
    (`client.send_message('me', ...)`) -- never a real user/dialog -- and
    ALWAYS logs to stderr via the module logger regardless of whether the
    Saved-Messages send itself succeeds.

    Called from a worker thread (TelethonTransport methods run there, and so
    does main()'s error handling after client.run_until_disconnected()
    raises), so -- exactly like TelethonTransport.send -- it marshals the
    actual Telethon call onto `loop` via run_coroutine_threadsafe and blocks
    for the result.

    Never raises: an alert path that itself can crash the caller (a FloodWait
    backoff retry, or main()'s session-loss handler) would defeat the point.
    On any failure -- including the send itself timing out or flooding --
    this just logs and returns.
    """
    log.warning("ALERT: %s", text)
    try:
        fut = asyncio.run_coroutine_threadsafe(client.send_message("me", text), loop)
        fut.result(timeout=30)
    except Exception:
        log.error("failed to deliver alert to Saved Messages: %s", text, exc_info=True)


class TelethonTransport(Transport):
    """Bridges the SYNC core (process_batch runs in a worker thread, see
    chatter.telethon_run) to an ASYNC Telethon client living on its own event
    loop. Constructed per outbound chat.

    Every method here is called from the worker thread. Each one marshals the
    actual Telethon call onto `loop` via `asyncio.run_coroutine_threadsafe`
    and blocks on `.result()` until it completes -- so from process_batch's
    point of view this is an ordinary synchronous Transport, exactly like
    FakeConsoleTransport.
    """

    def __init__(
        self, client, chat, loop: asyncio.AbstractEventLoop,
        backoff_sleep: Callable[[float], None] = time.sleep,
    ):
        self._client = client
        self._chat = chat
        self._loop = loop
        self._typing_ctx = None
        # Injectable so tests can assert the backoff duration without
        # actually blocking the test for real seconds (spec S8: the FloodWait
        # sleep is a real, blocking `time.sleep` in production -- this
        # transport already runs in a worker thread, so blocking it is fine
        # and does not stall the Telethon event loop).
        self._backoff_sleep = backoff_sleep

    def receive(self, timeout: float | None = None) -> str | None:
        raise NotImplementedError(
            "TelethonTransport is event-driven; use the runner's accumulation, "
            "not receive()"
        )

    def _call_with_floodwait_retry(
        self, make_coro: Callable[[], Awaitable], *, desc: str,
    ):
        """Runs `make_coro()` on the loop. On `FloodWaitError`: alert to
        Saved Messages, back off (sleep ~`e.seconds`, capped, growing with
        consecutive floods on the same call), retry ONCE. If the retry also
        floods, alert again and give up on this send (log + drop) rather than
        looping -- spec S8: "not hammer / not retry aggressively".
        """
        last_exc: FloodWaitError | None = None
        for attempt in (1, 2):
            fut = asyncio.run_coroutine_threadsafe(make_coro(), self._loop)
            try:
                return fut.result()
            except FloodWaitError as e:
                last_exc = e
                send_alert(
                    self._client, self._loop,
                    f"FloodWait {e.seconds}s on {desc} (attempt {attempt})",
                )
                if attempt == 2:
                    break
                wait = min(e.seconds * attempt, MAX_FLOODWAIT_BACKOFF_SECONDS)
                self._backoff_sleep(wait)
        log.error(
            "giving up on %s after repeated FloodWait (last=%ss)",
            desc, getattr(last_exc, "seconds", "?"),
        )
        return None

    def send(self, text: str) -> None:
        self._call_with_floodwait_retry(
            lambda: self._client.send_message(self._chat, text),
            desc=f"send_message to {self._chat}",
        )

    def send_typing(self, on: bool) -> None:
        if on:
            ctx = self._client.action(self._chat, "typing")
            self._call_with_floodwait_retry(ctx.__aenter__, desc=f"typing-on {self._chat}")
            self._typing_ctx = ctx
        else:
            ctx, self._typing_ctx = self._typing_ctx, None
            if ctx is None:
                return
            self._call_with_floodwait_retry(
                lambda: ctx.__aexit__(None, None, None), desc=f"typing-off {self._chat}",
            )
