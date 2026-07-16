from __future__ import annotations
import random
import re
from dataclasses import dataclass
from chatter.config.loader import Timings, WorkHours


def is_night(hour: int, work_hours: WorkHours) -> bool:
    return not (work_hours.start <= hour < work_hours.end)


def read_delay(rng: random.Random, t: Timings, *, night: bool) -> float:
    base = rng.uniform(t.read_delay_min, t.read_delay_max)
    return base * t.night_multiplier if night else base


# Hard ceiling on the TOTAL time a single reply's rhythm may consume (sum of
# all pauses: read + typing + inter-part). A person does not take three minutes
# to answer one chat message; past this the "lead" is burnt and it reads as a
# dead/abandoned chat. When a long reply would exceed this, the whole rhythm is
# scaled down proportionally (ratios preserved) rather than truncated.
MAX_TOTAL_RESPONSE_SECONDS = 90.0


def typing_duration(text: str, rng: random.Random, t: Timings) -> float:
    """Time to *type* `text`. Intentionally has NO night parameter: a human does
    not type slower at 3am, they only NOTICE the message later -- and that lag
    lives in read_delay's night multiplier, not here. (Design bug this fixes:
    the multiplier used to scale typing too, so a 150-char reply took ~2 min.)"""
    n = len(text)
    if n == 0:
        return 0.0
    cps = rng.uniform(t.cps_min, t.cps_max)
    jitter = rng.uniform(t.jitter_min, t.jitter_max)
    return (n / cps) * jitter


_SENTENCE = re.compile(r"[^.!?…]+[.!?…]*\s*")


def split_message(text: str, t: Timings, *, max_parts: int = 3) -> list[str]:
    """Split text on SENTENCE boundaries only (never mid-word, never on commas).

    Returns <= max_parts non-empty parts; every part except possibly the last
    ends with sentence-ending punctuation (., !, ?, …); concatenation preserves
    content (ignoring whitespace).
    """
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= t.split_max_len:
        return [text]
    sentences = [s.strip() for s in _SENTENCE.findall(text) if s.strip()]
    if len(sentences) <= 1:
        return [text]
    target = max(t.split_max_len, (len(text) // max_parts) + 1)
    parts: list[str] = []
    cur = ""
    for s in sentences:
        candidate = (cur + " " + s).strip() if cur else s
        if cur and len(candidate) > target and len(parts) < max_parts - 1:
            parts.append(cur)
            cur = s
        else:
            cur = candidate
    if cur:
        parts.append(cur)
    return parts[:max_parts]


def coalesce(messages: list[str]) -> str:
    """Merge a burst of inbound messages into one prompt, in arrival order."""
    return "\n".join(m.strip() for m in messages if m.strip())


def debounce_ready(
    *, first_received_at: float, last_received_at: float, now: float,
    window: float, max_window: float,
) -> bool:
    """True once either the quiet gap since the last message elapsed, or the
    hard ceiling since the first message in the burst has been reached.

    - Quiet path: (now - last_received_at) >= window
    - Ceiling path: (now - first_received_at) >= max_window

    The quiet window extends as `last_received_at` advances with each new
    inbound message (a non-stop typer keeps resetting it), but the ceiling
    guarantees a reply is eventually sent regardless.
    """
    quiet = (now - last_received_at) >= window
    ceiling = (now - first_received_at) >= max_window
    return quiet or ceiling


@dataclass(frozen=True)
class Pause:
    seconds: float


@dataclass(frozen=True)
class Typing:
    on: bool


@dataclass(frozen=True)
class Say:
    text: str


@dataclass(frozen=True)
class Online:
    """Presence toggle: set the account online for the duration of a reply,
    then release it. A real human is 'online' while answering; an account that
    is perpetually 'last seen long ago' yet keeps replying is a bot-tell."""
    on: bool


@dataclass(frozen=True)
class ReadAck:
    """Mark the inbound message(s) as read. A real human OPENS the chat before
    replying, so the read receipt (check marks) updates; replying while the
    message stays unread is a bot-tell."""


Action = Pause | Typing | Say | Online | ReadAck


def compose_reply(
    reply_text: str, rng: random.Random, t: Timings, work_hours: WorkHours, now_hour: int,
    *, max_total_seconds: float = MAX_TOTAL_RESPONSE_SECONDS,
) -> list[Action]:
    """Build the ordered rhythm of actions for delivering a reply.

    Order models a human opening the chat to answer:
        read pause -> online(on) -> read-ack -> typing(on)
        -> (typing pause, say)* with inter-part pauses
        -> typing(off) -> online(off)

    - The leading read pause always precedes the first Typing(on=True) so the
      bot never shows an instant typing indicator (bot-tell #1).
    - Online is toggled on for the rhythm and off at the very end.
    - The read acknowledgement fires AFTER the read pause and BEFORE typing --
      "notice the message, open the chat (marks read), then start typing".
    - The night multiplier only stretches the read/notice pause (see
      read_delay); typing speed is unaffected (see typing_duration).
    - The total pause time is capped at `max_total_seconds`: if a long reply
      would exceed it, every pause is scaled down proportionally so the ratios
      of the rhythm are preserved.
    """
    night = is_night(now_hour, work_hours)
    actions: list[Action] = [Pause(read_delay(rng, t, night=night))]
    actions.append(Online(on=True))
    actions.append(ReadAck())
    actions.append(Typing(on=True))

    parts = split_message(reply_text, t)
    for i, part in enumerate(parts):
        actions.append(Pause(typing_duration(part, rng, t)))
        actions.append(Say(part))
        if i < len(parts) - 1:
            actions.append(Pause(rng.uniform(t.split_pause_min, t.split_pause_max)))

    actions.append(Typing(on=False))
    actions.append(Online(on=False))
    return _cap_total(actions, max_total_seconds)


def _cap_total(actions: list[Action], max_total_seconds: float) -> list[Action]:
    """Scale every Pause down proportionally if their sum exceeds the ceiling,
    preserving the rhythm's shape. No-op when already under the cap."""
    total = sum(a.seconds for a in actions if isinstance(a, Pause))
    if total <= max_total_seconds or total <= 0:
        return actions
    factor = max_total_seconds / total
    return [Pause(a.seconds * factor) if isinstance(a, Pause) else a for a in actions]
