from __future__ import annotations
import argparse
import asyncio
import logging
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from telethon import events
from telethon.errors import AuthKeyError, UnauthorizedError

from chatter.config.loader import Config, load_config
from chatter.core import humanizer as H
from chatter.core.brain import Brain
from chatter.core.llm import AnthropicLLM, FakeLLM, LLMClient
from chatter.run import Deps, process_batch
from chatter.storage.db import Store
from chatter.transport.telethon_tg import TelethonTransport, send_alert

# Session-loss errors (spec S2/S8): the account got logged out / the saved
# session is no longer valid. AuthKeyError is the base of the
# AuthKey*Error family (Duplicated/Invalid/NotFound/PermEmpty/Unregistered);
# UnauthorizedError is Telethon's separate "you are not authorized" RPC
# error. Catching both, rather than a bare `except Exception`, keeps this
# from swallowing unrelated bugs -- only genuine session loss maps to the
# graceful "re-run telethon_login" path.
SESSION_LOST_ERRORS = (UnauthorizedError, AuthKeyError)

log = logging.getLogger("chatter.telethon_run")


# --- 1. eligibility filter -------------------------------------------------
# Pure, sync-testable (spec S3). The event handler extracts booleans from the
# real Telethon event and calls this; no Telethon object is inspected here.
def should_handle(
    *, is_private: bool, is_outgoing: bool, sender_is_bot: bool, is_service: bool,
) -> bool:
    """Only handle private, inbound, human, non-service messages. Groups,
    channels, our own outgoing messages, bot senders and service messages
    (pins, member-joins, etc.) are ignored -- silently, per spec S3."""
    return is_private and not is_outgoing and not sender_is_bot and not is_service


# --- 2. per-persona bundle ---------------------------------------------------
@dataclass
class PersonaBundle:
    cfg: Config
    deps: Deps


def _switch_ack(cfg: Config) -> str:
    """A fixed short control line, NOT via the LLM (spec S7)."""
    if cfg.settings.language == "ru":
        return f"— теперь отвечает {cfg.settings.persona_name} ({cfg.settings.language}) —"
    return f"— now answering: {cfg.settings.persona_name} ({cfg.settings.language}) —"


# --- 3. per-chat debounce/coalescing ----------------------------------------
class ChatDebouncer:
    """Per-chat inbound accumulation. Buffers text, extends the window on
    every new message, fires once `humanizer.debounce_ready` says so (quiet
    gap elapsed OR hard ceiling reached), then dispatches
    `humanizer.coalesce(buffer)`-equivalent (the raw list; `on_ready` decides
    how to join) via `on_ready`.

    The DECISION is the pure, already-tested `debounce_ready` from
    core.humanizer -- reused verbatim, not reimplemented (spec S6). This
    class only supplies the async accumulation loop around it, which spec S6
    explicitly allows to live on the transport/runner side.

    `clock` and `async_sleep` are injected (default to real time/asyncio.sleep)
    so tests can drive this deterministically without waiting real
    wall-clock seconds -- see tests/chatter/test_telethon_run.py's
    ManualSleeper.
    """

    def __init__(
        self, *, window: float, max_window: float,
        on_ready: Callable[[list[str]], Awaitable[None]],
        clock: Callable[[], float] = time.monotonic,
        async_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._window = window
        self._max_window = max_window
        self._clock = clock
        self._async_sleep = async_sleep
        self._on_ready = on_ready
        self._buffer: list[str] = []
        self._first_at = 0.0
        self._last_at = 0.0
        self.task: "asyncio.Task | None" = None

    def add(self, text: str) -> None:
        now = self._clock()
        if not self._buffer:
            self._first_at = now
        self._buffer.append(text)
        self._last_at = now
        if self.task is None or self.task.done():
            self.task = asyncio.ensure_future(self._run())

    async def _run(self) -> None:
        while True:
            now = self._clock()
            if H.debounce_ready(
                first_received_at=self._first_at, last_received_at=self._last_at,
                now=now, window=self._window, max_window=self._max_window,
            ):
                break
            remaining_quiet = self._window - (now - self._last_at)
            remaining_ceiling = self._max_window - (now - self._first_at)
            wait = max(0.0, min(remaining_quiet, remaining_ceiling))
            await self._async_sleep(wait)
        batch, self._buffer = self._buffer, []
        await self._on_ready(batch)


# --- 4/5/7. runner: never-writes-first, allowlist, /switch -----------------
class TelethonRunner:
    """Owns the loaded personas, the allowlist, the in-memory
    {sender_id: persona} switch map, and one ChatDebouncer per chat.

    `handle_event` is the ONLY path that ever constructs a TelethonTransport
    and calls .send/.send_typing -- there is no other code path to
    `client.send_message`/`client.action` anywhere in this module, which is
    what makes "never writes first" (spec S4) structurally true: with zero
    incoming events, handle_event is never called, so send is never called.
    """

    def __init__(
        self, *, client, personas: dict[str, PersonaBundle], primary_slug: str,
        allowlist: frozenset[int], loop: asyncio.AbstractEventLoop,
    ):
        if primary_slug not in personas:
            raise ValueError(f"primary persona '{primary_slug}' not among loaded personas")
        self.client = client
        self.personas = personas
        self.primary_slug = primary_slug
        self.allowlist = allowlist
        self.loop = loop
        self._sender_persona: dict[int, str] = {}
        self._debouncers: dict[int, ChatDebouncer] = {}

    def persona_for(self, sender_id: int) -> str:
        return self._sender_persona.get(sender_id, self.primary_slug)

    def toggle_persona(self, sender_id: int) -> str:
        """Flip demo<->demo2 for this sender. Requires exactly the two-persona
        case described in spec S7; with >2 personas loaded this picks the
        first other slug deterministically (dict insertion order)."""
        current = self.persona_for(sender_id)
        other = next(slug for slug in self.personas if slug != current)
        self._sender_persona[sender_id] = other
        return other

    async def handle_event(self, event) -> None:
        """Wired to events.NewMessage(incoming=True). `event` duck-types the
        Telethon NewMessage.Event attributes this reads: is_private, out,
        sender.bot, action, sender_id, raw_text, chat_id."""
        eligible = should_handle(
            is_private=bool(event.is_private),
            is_outgoing=bool(event.out),
            sender_is_bot=bool(getattr(event.sender, "bot", False)),
            is_service=getattr(event, "action", None) is not None,
        )
        if not eligible:
            log.debug("ignored non-eligible event from %s", getattr(event, "sender_id", "?"))
            return

        sender_id = event.sender_id
        if sender_id not in self.allowlist:
            log.info("ignored non-allowlisted %s", sender_id)
            return

        text = (event.raw_text or "").strip()
        chat = event.chat_id

        if text == "/switch":
            new_slug = self.toggle_persona(sender_id)
            new_cfg = self.personas[new_slug].cfg
            transport = TelethonTransport(self.client, chat, self.loop)
            await asyncio.to_thread(transport.send, _switch_ack(new_cfg))
            return

        persona_slug = self.persona_for(sender_id)
        deb = self._debouncers.get(chat)
        if deb is None or deb.task is None or deb.task.done():
            deb = self._new_debouncer(chat=chat, sender_id=sender_id, persona_slug=persona_slug)
            self._debouncers[chat] = deb
        deb.add(text)

    def _new_debouncer(self, *, chat, sender_id: int, persona_slug: str) -> ChatDebouncer:
        bundle = self.personas[persona_slug]
        t = bundle.cfg.settings.timings

        async def _on_ready(batch: list[str]) -> None:
            contact_id = f"{sender_id}:{persona_slug}"
            transport = TelethonTransport(self.client, chat, self.loop)
            await asyncio.to_thread(process_batch, contact_id, batch, transport, bundle.deps)

        return ChatDebouncer(window=t.debounce_window, max_window=t.debounce_max, on_ready=_on_ready)


# --- runner assembly / CLI --------------------------------------------------
def _build_llm(cfg: Config, mode: str) -> LLMClient:
    if mode == "real" or (mode == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        return AnthropicLLM(cfg.settings.model)
    return FakeLLM()


def load_personas(
    clients_dir: Path, slugs: list[str], store: Store, *, llm_mode: str = "auto",
) -> dict[str, PersonaBundle]:
    personas: dict[str, PersonaBundle] = {}
    for slug in slugs:
        cfg = load_config(clients_dir, slug)
        llm = _build_llm(cfg, llm_mode)
        deps = Deps(
            cfg=cfg, store=store, brain=Brain(llm, cfg),
            rng=random.Random(), clock=time.time, sleep=time.sleep,
        )
        personas[slug] = PersonaBundle(cfg=cfg, deps=deps)
    return personas


def build_runner(
    *, client, clients_dir: Path, persona_slugs: list[str], store: Store,
    loop: asyncio.AbstractEventLoop, llm_mode: str = "auto",
) -> TelethonRunner:
    """Loads personas, resolves the allowlist from the PRIMARY (first) persona
    (spec S5), and wires client.add_event_handler(..., events.NewMessage(
    incoming=True)). Kept separate from main() so tests can pass a mocked
    `client` and never touch the network."""
    if not persona_slugs:
        raise ValueError("need at least one persona")
    personas = load_personas(clients_dir, persona_slugs, store, llm_mode=llm_mode)
    primary_slug = persona_slugs[0]
    telegram_cfg = personas[primary_slug].cfg.settings.telegram
    if telegram_cfg is None:
        raise ValueError(
            f"persona '{primary_slug}' has no [telegram] block in settings.yaml "
            "(the allowlist is required to run the Telethon transport)"
        )
    runner = TelethonRunner(
        client=client, personas=personas, primary_slug=primary_slug,
        allowlist=frozenset(telegram_cfg.allowlist), loop=loop,
    )

    async def _handler(event) -> None:
        await runner.handle_event(event)

    client.add_event_handler(_handler, events.NewMessage(incoming=True))
    return runner


def run_client(client, loop: asyncio.AbstractEventLoop) -> int:
    """Runs `client.start()` + `client.run_until_disconnected()` -- the
    actual network-facing lifetime of the userbot -- with session-loss
    mapped to a graceful, alerted stop instead of a silent death or a bare
    traceback (spec S2/S8).

    Factored out of main() so a test can simulate `client.start()` raising a
    session-loss error using a plain mock, with NO real TelegramClient and NO
    network. `client` and `loop` are the same objects `build_runner` was
    given -- `loop` is reused (not the client's own) purely so `send_alert`
    can marshal the alert coroutine the same way TelethonTransport does.
    """
    try:
        client.start()
        client.run_until_disconnected()
        return 0
    except SESSION_LOST_ERRORS as e:
        text = (
            f"session lost / logged out ({type(e).__name__}: {e}) -- "
            "re-run `python -m chatter.telethon_login` to sign in again"
        )
        log.error(text)
        send_alert(client, loop, text)  # best-effort; send_alert never raises
        return 2


def main(argv: list[str] | None = None) -> int:
    # Same rationale as chatter/run.py's main(): Windows consoles default to a
    # legacy codepage that silently mangles Cyrillic instead of raising.
    for _stream in (sys.stdin, sys.stdout):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    p = argparse.ArgumentParser(prog="chatter.telethon_run")
    p.add_argument("--personas", default="demo,demo2",
                    help="comma-separated persona slugs; the FIRST is primary "
                         "(supplies the allowlist) and the default for new senders")
    p.add_argument("--clients-dir", default=str(Path(__file__).resolve().parent / "clients"))
    p.add_argument("--session", default=str(Path(".secrets") / "chatter_telethon.session"))
    p.add_argument("--db", default=str(Path(".secrets") / "chatter_telethon.db"))
    p.add_argument("--llm", choices=["auto", "real", "fake"], default="auto")
    args = p.parse_args(argv)

    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        print(
            "[telethon_run] TELEGRAM_API_ID / TELEGRAM_API_HASH not set in env "
            "-- see chatter/telethon_login.py (Milestone F)",
            file=sys.stderr,
        )
        return 1

    from telethon import TelegramClient  # deferred: only main() ever constructs a real client

    session_path = os.environ.get("TELETHON_SESSION", args.session)
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(session_path, int(api_id), api_hash)

    slugs = [s.strip() for s in args.personas.split(",") if s.strip()]
    store = Store(args.db)
    loop = asyncio.get_event_loop()
    build_runner(
        client=client, clients_dir=Path(args.clients_dir), persona_slugs=slugs,
        store=store, loop=loop, llm_mode=args.llm,
    )

    print(f"[telethon_run] personas={slugs} session={session_path}")
    return run_client(client, loop)


if __name__ == "__main__":
    raise SystemExit(main())
