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
from chatter.telethon_login import (
    DEFAULT_ENV_FILE, CredentialsError, _parse_env_file, load_api_credentials,
)

# Session-loss errors (spec S2/S8): the account got logged out / the saved
# session is no longer valid. AuthKeyError is the base of the
# AuthKey*Error family (Duplicated/Invalid/NotFound/PermEmpty/Unregistered);
# UnauthorizedError is Telethon's separate "you are not authorized" RPC
# error. Catching both, rather than a bare `except Exception`, keeps this
# from swallowing unrelated bugs -- only genuine session loss maps to the
# graceful "re-run telethon_login" path.
SESSION_LOST_ERRORS = (UnauthorizedError, AuthKeyError)

log = logging.getLogger("chatter.telethon_run")

# Catch-up age cap: on start, don't answer anything older than this. A message
# from last week doesn't need a live reply -- answering ancient backlog reads as
# broken, not attentive. 24h is generous enough to cover any realistic downtime
# (reboot, deploy, crash) while never resurrecting stale conversations.
CATCHUP_MAX_AGE_SECONDS = 24 * 3600

# Own liveness stamp for the guardian (mirrors the main bot's bot_heartbeat.txt):
# the runner rewrites this every HEARTBEAT_INTERVAL_SECONDS so a HUNG runner
# (process alive but event loop wedged) is detected, not just a dead PID.
HEARTBEAT_PATH = Path("state") / "chatter_heartbeat.txt"
HEARTBEAT_INTERVAL_SECONDS = 30


# --- 0. catch-up: pick up messages that arrived while OFFLINE ---------------
@dataclass
class MissedMessage:
    sender_id: int
    texts: list[str]              # chronological (oldest first)
    oldest_age_seconds: float


def select_missed(
    dialogs: list[dict], *, allowlist: frozenset[int], now: float, max_age_seconds: float,
) -> list[MissedMessage]:
    """Pure core of catch-up. Given a snapshot of private dialogs with unread
    messages, decide which to answer after a restart.

    `dialogs` is transport-agnostic: each item is
        {sender_id: int, is_user: bool, is_bot: bool,
         messages: [{text: str, out: bool, date_ts: float}, ...]}

    Keeps, per allowlisted human DM: inbound (`out` False), non-blank messages
    within `max_age_seconds`, in chronological order. Drops groups/channels
    (`is_user` False), bots, non-allowlisted senders, our own outgoing, blanks,
    and anything older than the age cap. Empty result for a dialog with nothing
    left to answer."""
    out: list[MissedMessage] = []
    for d in dialogs:
        if not d.get("is_user") or d.get("is_bot"):
            continue
        if d.get("sender_id") not in allowlist:
            continue
        picked = [
            m for m in sorted(d.get("messages", []), key=lambda m: m["date_ts"])
            if not m.get("out")
            and (m.get("text") or "").strip()
            and (now - m["date_ts"]) <= max_age_seconds
        ]
        if picked:
            out.append(MissedMessage(
                sender_id=d["sender_id"],
                texts=[m["text"] for m in picked],
                oldest_age_seconds=now - picked[0]["date_ts"],
            ))
    return out


def write_heartbeat(path: Path = HEARTBEAT_PATH, *, now: float | None = None) -> None:
    """Stamp the runner's liveness file with the current unix time (seconds).
    Best-effort: a failure here must never take down the runner."""
    ts = int(time.time() if now is None else now)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(ts), encoding="ascii")
    except Exception:
        log.warning("failed to write heartbeat to %s", path, exc_info=True)


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
        chat_id = event.chat_id
        log.info("IN %s [%s]: %s", sender_id, self.persona_for(sender_id), text)

        # Send to the event's INPUT PEER (carries access_hash), not the bare
        # chat_id int: client.send_message/action(<int>) does get_input_entity,
        # which fails ("Could not find the input entity") on a fresh session
        # that hasn't cached that user. The event already has the resolvable peer.
        peer = await event.get_input_chat()

        if text == "/switch":
            new_slug = self.toggle_persona(sender_id)
            new_cfg = self.personas[new_slug].cfg
            transport = TelethonTransport(self.client, peer, self.loop)
            await asyncio.to_thread(transport.send, _switch_ack(new_cfg))
            return

        persona_slug = self.persona_for(sender_id)
        deb = self._debouncers.get(chat_id)
        if deb is None or deb.task is None or deb.task.done():
            deb = self._new_debouncer(peer=peer, sender_id=sender_id, persona_slug=persona_slug)
            self._debouncers[chat_id] = deb
        deb.add(text)

    def _new_debouncer(self, *, peer, sender_id: int, persona_slug: str) -> ChatDebouncer:
        bundle = self.personas[persona_slug]
        t = bundle.cfg.settings.timings

        async def _on_ready(batch: list[str]) -> None:
            contact_id = f"{sender_id}:{persona_slug}"
            transport = TelethonTransport(self.client, peer, self.loop)
            bundle.deps.store.get_or_create_contact(contact_id)  # process_batch assumes the row exists
            log.info("process START %s batch=%r", contact_id, batch)
            try:
                await asyncio.to_thread(process_batch, contact_id, batch, transport, bundle.deps)
                log.info("process END %s", contact_id)
            except Exception:
                # A fire-and-forget debouncer task swallows exceptions otherwise;
                # surface them loudly (this is what a silent no-reply looked like).
                log.exception("process_batch FAILED for %s", contact_id)

        return ChatDebouncer(window=t.debounce_window, max_window=t.debounce_max, on_ready=_on_ready)

    # --- catch-up on start --------------------------------------------------
    async def _collect_dialogs(self, *, per_dialog_scan: int = 50) -> list[dict]:
        """Snapshot private dialogs that have UNREAD messages into the
        transport-agnostic dicts `select_missed` consumes. The ONLY place that
        touches the live Telethon dialog/message iterators -- kept thin so the
        decision logic stays pure and unit-tested."""
        dialogs: list[dict] = []
        async for dialog in self.client.iter_dialogs():
            if not getattr(dialog, "is_user", False):
                continue
            if getattr(dialog, "unread_count", 0) <= 0:
                continue
            entity = dialog.entity
            sender_id = getattr(entity, "id", None)
            if sender_id is None:
                continue
            messages: list[dict] = []
            async for m in self.client.iter_messages(
                entity, limit=min(int(dialog.unread_count), per_dialog_scan),
            ):
                messages.append({
                    "text": getattr(m, "message", None) or "",
                    "out": bool(getattr(m, "out", False)),
                    "date_ts": m.date.timestamp(),
                })
            dialogs.append({
                "sender_id": sender_id,
                "is_user": True,
                "is_bot": bool(getattr(entity, "bot", False)),
                "messages": messages,
            })
        return dialogs

    async def catch_up_missed(
        self, *, now: float | None = None, max_age_seconds: float = CATCHUP_MAX_AGE_SECONDS,
        collect: Callable[[], Awaitable[list[dict]]] | None = None,
    ) -> None:
        """On start, answer messages that arrived while the runner was OFFLINE
        (spec liveness: a restart/crash must not silently drop leads). Scans
        unread private dialogs, selects allowlisted inbound within the age cap,
        and runs each through the SAME process_batch path as a live message --
        but with the message's age, so the "sorry for the pause" path fires."""
        now = time.time() if now is None else now
        collect = collect or self._collect_dialogs
        try:
            dialogs = await collect()
        except Exception:
            log.exception("catch-up: failed to collect dialogs; skipping catch-up")
            return
        missed = select_missed(dialogs, allowlist=self.allowlist, now=now, max_age_seconds=max_age_seconds)
        log.info("catch-up: %d dialog(s) with missed messages", len(missed))
        for mm in missed:
            try:
                await self._process_missed(mm)
            except Exception:
                log.exception("catch-up: FAILED for sender %s", mm.sender_id)

    async def _process_missed(self, mm: MissedMessage) -> None:
        persona_slug = self.persona_for(mm.sender_id)
        bundle = self.personas[persona_slug]
        contact_id = f"{mm.sender_id}:{persona_slug}"
        peer = await self.client.get_input_entity(mm.sender_id)
        transport = TelethonTransport(self.client, peer, self.loop)
        bundle.deps.store.get_or_create_contact(contact_id)
        log.info("catch-up process START %s texts=%r age=%.0fs",
                 contact_id, mm.texts, mm.oldest_age_seconds)
        await asyncio.to_thread(
            process_batch, contact_id, mm.texts, transport, bundle.deps,
            missed_age_seconds=mm.oldest_age_seconds,
        )
        log.info("catch-up process END %s", contact_id)


async def heartbeat_loop(
    *, interval: float = HEARTBEAT_INTERVAL_SECONDS, path: Path = HEARTBEAT_PATH,
) -> None:
    """Rewrite the runner's liveness stamp every `interval` seconds forever, so
    the guardian can tell a live runner from a hung one. Never raises out."""
    while True:
        write_heartbeat(path)
        await asyncio.sleep(interval)


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


def run_client(
    client, loop: asyncio.AbstractEventLoop,
    *, on_connected: Callable[[], Awaitable[None]] | None = None,
) -> int:
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
        # Scheduled now, executed once run_until_disconnected() drives the loop:
        # runs AFTER the session is connected (start() blocks until connected),
        # so catch-up's dialog scan and the heartbeat run against a live client.
        if on_connected is not None:
            loop.create_task(on_connected())
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
    for _stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")  # incl. stderr: logging writes there; keeps Cyrillic/emoji readable
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

    try:
        # Same .env fallback as the login script, so a deploy-faithful `.venv`
        # run doesn't require the operator to export TELEGRAM_API_ID/HASH.
        api_id, api_hash = load_api_credentials(os.environ, DEFAULT_ENV_FILE)
    except CredentialsError as e:
        print(f"[telethon_run] {e}", file=sys.stderr)
        return 1

    # For real Haiku replies, make ANTHROPIC_API_KEY available the same way --
    # fall back to the repo .env so `--llm real` works without a manual export.
    if args.llm in ("real", "auto") and not os.environ.get("ANTHROPIC_API_KEY"):
        _key = _parse_env_file(DEFAULT_ENV_FILE).get("ANTHROPIC_API_KEY")
        if _key:
            os.environ["ANTHROPIC_API_KEY"] = _key

    from telethon import TelegramClient  # deferred: only main() ever constructs a real client

    session_path = os.environ.get("TELETHON_SESSION", args.session)
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)

    # Own event loop, set as current: (a) works on Python 3.12+/3.14 where
    # asyncio.get_event_loop() raises at top level, and (b) is the SAME loop
    # Telethon's start()/run_until_disconnected() run on, so the transport's
    # run_coroutine_threadsafe(send/typing, loop) targets the loop that's
    # actually running (otherwise sends would never execute).
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = TelegramClient(session_path, int(api_id), api_hash)

    slugs = [s.strip() for s in args.personas.split(",") if s.strip()]
    store = Store(args.db)
    runner = build_runner(
        client=client, clients_dir=Path(args.clients_dir), persona_slugs=slugs,
        store=store, loop=loop, llm_mode=args.llm,
    )

    async def _on_connected() -> None:
        # Own liveness stamp for the guardian, forever, alongside the one-shot
        # catch-up of anything that arrived while we were down.
        loop.create_task(heartbeat_loop())
        await runner.catch_up_missed()

    print(f"[telethon_run] personas={slugs} session={session_path}")
    return run_client(client, loop, on_connected=_on_connected)


if __name__ == "__main__":
    raise SystemExit(main())
