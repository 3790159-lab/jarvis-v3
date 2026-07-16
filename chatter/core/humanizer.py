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


def typing_duration(text: str, rng: random.Random, t: Timings, *, night: bool) -> float:
    n = len(text)
    if n == 0:
        return 0.0
    cps = rng.uniform(t.cps_min, t.cps_max)
    jitter = rng.uniform(t.jitter_min, t.jitter_max)
    duration = (n / cps) * jitter
    return duration * t.night_multiplier if night else duration


_SENTENCE = re.compile(r"[^.!?…]+[.!?…]*\s*")


def split_message(text: str, t: Timings, *, max_parts: int = 3) -> list[str]:
    """Split text on SENTENCE boundaries only (never mid-word, never on commas).

    Returns <= max_parts non-empty parts; every part except possibly the last
    ends with sentence-ending punctuation (., !, ?, …); concatenation preserves
    content (ignoring whitespace).
    """
    text = text.strip()
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


Action = Pause | Typing | Say


def compose_reply(
    reply_text: str, rng: random.Random, t: Timings, work_hours: WorkHours, now_hour: int,
) -> list[Action]:
    """Build the ordered rhythm of actions for delivering a reply.

    Order is: read pause -> typing on -> (typing pause, say)* with inter-part
    pauses -> typing off. A leading read pause always precedes the first
    Typing(on=True) so the bot never shows an instant typing indicator.
    """
    night = is_night(now_hour, work_hours)
    actions: list[Action] = [Pause(read_delay(rng, t, night=night))]
    actions.append(Typing(on=True))

    parts = split_message(reply_text, t)
    for i, part in enumerate(parts):
        actions.append(Pause(typing_duration(part, rng, t, night=night)))
        actions.append(Say(part))
        if i < len(parts) - 1:
            actions.append(Pause(rng.uniform(t.split_pause_min, t.split_pause_max)))

    actions.append(Typing(on=False))
    return actions
