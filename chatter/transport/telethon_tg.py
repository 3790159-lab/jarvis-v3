from __future__ import annotations
import asyncio
import logging
import time
from collections import OrderedDict
from typing import Awaitable, Callable

from telethon.errors import FloodWaitError
from telethon.tl.functions.account import UpdateStatusRequest

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


class SentRegistry:
    """id сообщений, которые отправили МЫ. Единственный надёжный признак
    «своё vs владелец печатает руками»: текст сравнивать нельзя (Аня и
    владелец могут написать одно и то же одними словами), а других отличий у
    двух сообщений из ОДНОГО аккаунта нет — оба выглядят как исходящее от
    этого же Telegram-юзера.

    Ограничен по размеру: процесс живёт неделями (гардиан держит его живым
    постоянно), а неограниченное множество id иначе растёт вечно."""

    def __init__(self, max_size: int = 500):
        self._ids: "OrderedDict[int, None]" = OrderedDict()
        self._max = max_size

    def add(self, msg_id: int) -> None:
        self._ids[msg_id] = None
        self._ids.move_to_end(msg_id)
        while len(self._ids) > self._max:
            self._ids.popitem(last=False)   # забываем САМЫЙ старый id

    def is_ours(self, msg_id: int) -> bool:
        return msg_id in self._ids

    def __len__(self) -> int:
        return len(self._ids)


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
        sent_registry: "SentRegistry | None" = None,
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
        # Optional so callers that never wire up takeover-detection (older
        # tests, one-off scripts) keep working -- `send` below checks for
        # None before touching it.
        self._sent = sent_registry

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
        log.info("OUT %s: %s", self._chat, text)
        sent = self._call_with_floodwait_retry(
            lambda: self._client.send_message(self._chat, text),
            desc=f"send_message to {self._chat}",
        )
        # Регистрируем id СВОЕГО сообщения, чтобы telethon_run.decide_outgoing
        # (Task 11) мог опознать его как "ours", а не как перехват владельцем.
        #
        # ВАЖНО: это НЕ закрывает гонку и не должно. send() крутится в
        # worker-потоке (process_batch синхронный) и лишь МАРШАЛИТ корутину
        # send_message на event loop через run_coroutine_threadsafe — а
        # обработчик исходящих в telethon_run живёт НА этом же loop. Значит
        # планировщик вполне может раздать апдейт о новом исходящем раньше,
        # чем этот поток проснётся после fut.result() и допишет id сюда.
        # Порядок "апдейт из Telethon" vs "запись в реестр" НЕ гарантирован
        # этим кодом. Закрывает гонку грейс-окно в telethon_run.decide_outgoing
        # (Task 11: ждём и перепроверяем реестр, а не решаем мгновенно) —
        # здесь важно только НЕ ПОТЕРЯТЬ id совсем.
        #
        # `sent` = None, когда отправка не состоялась вовсе (сдались после
        # повторного FloodWaitError, см. _call_with_floodwait_retry) —
        # регистрировать нечего; getattr(..., "id", None) ловит и этот
        # случай, и случай sent_registry=None (не сконфигурирован вызывающим).
        if self._sent is not None and getattr(sent, "id", None) is not None:
            self._sent.add(sent.id)

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

    def read_acknowledge(self) -> None:
        """Mark this chat's inbound message(s) as read (updates the check marks
        the other side sees). Best-effort presence signal -- goes through the
        same FloodWait-guarded marshaling as send/typing."""
        self._call_with_floodwait_retry(
            lambda: self._client.send_read_acknowledge(self._chat),
            desc=f"read-ack {self._chat}",
        )

    def set_online(self, on: bool) -> None:
        """Toggle the account's global presence. Telethon has no high-level
        setter, so this issues the raw account.updateStatus RPC (offline=not on)
        on the client itself. Marshaled onto the loop like every other call."""
        self._call_with_floodwait_retry(
            lambda: self._client(UpdateStatusRequest(offline=not on)),
            desc=f"set-online({on})",
        )
