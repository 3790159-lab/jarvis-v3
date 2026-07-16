from __future__ import annotations

STATES = {"new", "qualifying", "hot", "escalated", "closed", "dead"}
_TERMINAL = {"closed", "dead"}

_TRANSITIONS: dict[str, dict[str, str]] = {
    "new": {"engaged": "qualifying", "ghosted": "dead"},
    "qualifying": {"interested": "hot", "unknown_info": "escalated",
                   "needs_human": "escalated", "ghosted": "dead"},
    "hot": {"needs_human": "escalated", "unknown_info": "escalated",
            "bought": "closed", "ghosted": "dead"},
    "escalated": {"bought": "closed", "ghosted": "dead"},
}

def next_state(current: str, signal: str) -> str:
    if current not in STATES:
        raise ValueError(f"unknown state: {current}")
    if current in _TERMINAL:
        return current
    return _TRANSITIONS.get(current, {}).get(signal, current)
