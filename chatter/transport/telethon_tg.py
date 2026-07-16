from __future__ import annotations
import asyncio
from chatter.transport.base import Transport


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

    def __init__(self, client, chat, loop: asyncio.AbstractEventLoop):
        self._client = client
        self._chat = chat
        self._loop = loop
        self._typing_ctx = None

    def receive(self, timeout: float | None = None) -> str | None:
        raise NotImplementedError(
            "TelethonTransport is event-driven; use the runner's accumulation, "
            "not receive()"
        )

    def send(self, text: str) -> None:
        fut = asyncio.run_coroutine_threadsafe(
            self._client.send_message(self._chat, text), self._loop,
        )
        fut.result()

    def send_typing(self, on: bool) -> None:
        if on:
            ctx = self._client.action(self._chat, "typing")
            fut = asyncio.run_coroutine_threadsafe(ctx.__aenter__(), self._loop)
            fut.result()
            self._typing_ctx = ctx
        else:
            ctx, self._typing_ctx = self._typing_ctx, None
            if ctx is None:
                return
            fut = asyncio.run_coroutine_threadsafe(ctx.__aexit__(None, None, None), self._loop)
            fut.result()
