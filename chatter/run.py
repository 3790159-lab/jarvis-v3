from __future__ import annotations
import argparse
import datetime as _dt
import os
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from chatter.config.loader import Config, load_config
from chatter.core import humanizer as H
from chatter.core.brain import Brain
from chatter.core.disclosure import honest_disclosure, is_bot_question
from chatter.core.guardrails import (
    contains_unbacked_claim, within_daily_cap, within_hourly_limit,
)
from chatter.core.llm import AnthropicLLM, FakeLLM
from chatter.storage.db import Store
from chatter.transport.base import Transport
from chatter.transport.fake import FakeConsoleTransport


@dataclass
class Deps:
    cfg: Config
    store: Store
    brain: Brain
    rng: random.Random
    clock: Callable[[], float]
    sleep: Callable[[float], None]


def _persona_first_line(persona: str) -> str:
    for line in persona.splitlines():
        if line.strip():
            return line.strip()
    return ""


def gather_batch(transport: Transport, deps: Deps, first: str) -> list[str]:
    """Collect a burst of inbound messages.

    Tracks `first_received_at` (set once, when the burst starts) and
    `last_received_at` (advanced on every new message). Keeps waiting until
    `debounce_ready` fires either because the quiet gap since the last
    message elapsed, or the hard ceiling since the first message was hit.
    """
    t = deps.cfg.settings.timings
    batch = [first]
    first_received_at = deps.clock()
    last_received_at = first_received_at
    while not H.debounce_ready(
        first_received_at=first_received_at,
        last_received_at=last_received_at,
        now=deps.clock(),
        window=t.debounce_window,
        max_window=t.debounce_max,
    ):
        now = deps.clock()
        remaining_quiet = t.debounce_window - (now - last_received_at)
        remaining_ceiling = t.debounce_max - (now - first_received_at)
        remaining = max(0.0, min(remaining_quiet, remaining_ceiling))
        msg = transport.receive(timeout=remaining)
        if msg is None:
            break
        batch.append(msg)
        last_received_at = deps.clock()
    return batch


def process_batch(contact_id: str, incoming: list[str], transport: Transport, deps: Deps) -> None:
    """Coalesce the batch, guard on rate limits, decide a reply
    (disclosure > guardrails > brain), then deliver it via the humanizer's
    action plan (compose_reply): Pause/Typing/Say interpreted in order."""
    text = H.coalesce(incoming)
    if not text:
        return
    deps.store.add_message(contact_id, "user", text, ts=deps.clock())

    limits = deps.cfg.settings.limits
    if not within_hourly_limit(deps.store, contact_id, now=deps.clock(), limit=limits.per_contact_hourly):
        print(f"  [rate limit] hourly limit hit for {contact_id}; skipping")
        return
    if not within_daily_cap(deps.store, now=deps.clock(), cap=limits.daily_cap):
        print("  [rate limit] daily cap hit; skipping")
        return

    if is_bot_question(text):
        reply = honest_disclosure(
            owner_id=deps.cfg.settings.owner_id,
            persona_line=_persona_first_line(deps.cfg.persona),
        )
    else:
        reply = deps.brain.reply(deps.store.history(contact_id))
        if contains_unbacked_claim(reply, deps.cfg.knowledge):
            deps.store.set_state(contact_id, "escalated")  # arc 3 does the actual handoff
            print(f"  [escalation flag] unbacked claim for {contact_id}: {reply!r}")
            reply = (
                f"Хороший вопрос — уточню детали и вернусь. "
                f"Если удобно, позову {deps.cfg.settings.owner_id}."
            )

    now_hour = _dt.datetime.fromtimestamp(deps.clock()).hour
    actions = H.compose_reply(
        reply, deps.rng, deps.cfg.settings.timings, deps.cfg.settings.work_hours, now_hour,
    )
    for action in actions:
        if isinstance(action, H.Pause):
            deps.sleep(action.seconds)
        elif isinstance(action, H.Typing):
            transport.send_typing(action.on)
        elif isinstance(action, H.Say):
            transport.send(action.text)
            deps.store.add_message(contact_id, "assistant", action.text, ts=deps.clock())


def _build_llm(cfg: Config, mode: str):
    if mode == "real" or (mode == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        return AnthropicLLM(cfg.settings.model)
    return FakeLLM()


def main(argv: list[str] | None = None) -> int:
    # Windows consoles often default stdin/stdout to a legacy codepage (e.g. cp1251)
    # instead of UTF-8, which silently corrupts Cyrillic instead of raising -- and
    # specifically breaks disclosure.is_bot_question()'s match on the
    # honesty-critical "ты бот?" question (observed live: without this, a merged
    # batch containing "ты бот?" fell through to the brain/guardrail path instead
    # of the honest disclosure). Force UTF-8 explicitly rather than relying on the
    # operator to set PYTHONUTF8=1 externally.
    for _stream in (sys.stdin, sys.stdout):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(prog="chatter.run")
    p.add_argument("--client", required=True)
    p.add_argument("--transport", choices=["fake"], default="fake")
    p.add_argument("--clients-dir", default=str(Path(__file__).resolve().parent / "clients"))
    p.add_argument("--llm", choices=["auto", "real", "fake"], default="auto")
    p.add_argument("--db", default=":memory:")
    p.add_argument("--contact", default="console-user")
    args = p.parse_args(argv)

    cfg = load_config(Path(args.clients_dir), args.client)  # raises ConfigError loudly at startup
    llm = _build_llm(cfg, args.llm)
    deps = Deps(
        cfg=cfg, store=Store(args.db), brain=Brain(llm, cfg),
        rng=random.Random(), clock=time.time, sleep=time.sleep,
    )
    transport = FakeConsoleTransport()
    deps.store.get_or_create_contact(args.contact)

    print(f"[chatter] client={cfg.slug} llm={type(llm).__name__} lang={cfg.settings.language}")
    print("[chatter] пишите сообщения (Ctrl-D для выхода). Быстрые подряд склеятся.\n")
    try:
        while True:
            first = transport.receive(timeout=None)
            if first is None:
                break
            batch = gather_batch(transport, deps, first)
            process_batch(args.contact, batch, transport, deps)
    finally:
        deps.store.close()
    print("\n[chatter] пока!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
