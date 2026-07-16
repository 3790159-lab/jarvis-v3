from __future__ import annotations
import pytest
from chatter.core.conversation import next_state, STATES

def test_states_set():
    assert STATES == {"new", "qualifying", "hot", "escalated", "closed", "dead"}

@pytest.mark.parametrize("start,signal,expected", [
    ("new", "engaged", "qualifying"),
    ("qualifying", "interested", "hot"),
    ("qualifying", "unknown_info", "escalated"),
    ("hot", "needs_human", "escalated"),
    ("hot", "bought", "closed"),
    ("qualifying", "ghosted", "dead"),
])
def test_transitions(start, signal, expected):
    assert next_state(start, signal) == expected

def test_unknown_signal_keeps_state():
    assert next_state("hot", "smalltalk") == "hot"

def test_terminal_states_are_sticky():
    assert next_state("closed", "engaged") == "closed"
    assert next_state("dead", "interested") == "dead"

def test_invalid_state_raises():
    with pytest.raises(ValueError):
        next_state("bogus", "engaged")
