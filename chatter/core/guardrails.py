from __future__ import annotations
import re
from chatter.storage.db import Store

_NUMBER = re.compile(r"\d[\d\s.,]*\d|\d")


def _numbers(text: str) -> set[str]:
    return {re.sub(r"\s", "", m.group()) for m in _NUMBER.finditer(text or "")}


def contains_unbacked_claim(reply: str, knowledge: str) -> bool:
    """True if the reply cites a number (price/term/percent) not present in knowledge.md."""
    known = _numbers(knowledge)
    return any(n not in known for n in _numbers(reply))


def within_hourly_limit(store: Store, contact_id: str, *, now: float, limit: int) -> bool:
    sent = store.count_messages_since(contact_id, role="assistant", since_ts=now - 3600.0)
    return sent < limit


def within_daily_cap(store: Store, *, now: float, cap: int) -> bool:
    day_start = now - (now % 86400.0)
    sent = store.count_outbound_between(day_start, day_start + 86400.0)
    return sent < cap
