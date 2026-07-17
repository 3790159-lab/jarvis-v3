from __future__ import annotations

from chatter.core.escalation import advance_funnel
from chatter.storage.db import Store


def _store():
    s = Store(":memory:")
    s.get_or_create_contact("c1")
    return s


def test_engaged_advances_new_to_qualifying():
    s = _store()
    new_state = advance_funnel(s, "c1", stage_signal="engaged", escalated=False)
    assert new_state == "qualifying"
    assert s.get_or_create_contact("c1")["state"] == "qualifying"


def test_escalated_forces_escalated_state_regardless_of_signal():
    # Эскалация всегда уводит в 'escalated', даже если сигнал классификатора
    # был про что-то другое (или его нет).
    s = _store()
    s.set_state("c1", "qualifying")
    new_state = advance_funnel(s, "c1", stage_signal="interested", escalated=True)
    assert new_state == "escalated"
    assert s.get_or_create_contact("c1")["state"] == "escalated"


def test_none_signal_no_escalation_leaves_state_unchanged():
    s = _store()
    s.set_state("c1", "qualifying")
    new_state = advance_funnel(s, "c1", stage_signal=None, escalated=False)
    assert new_state == "qualifying"
    assert s.get_or_create_contact("c1")["state"] == "qualifying"


def test_unknown_signal_is_noop():
    s = _store()
    s.set_state("c1", "qualifying")
    new_state = advance_funnel(s, "c1", stage_signal="banana", escalated=False)
    assert new_state == "qualifying"


def test_terminal_state_stays_terminal():
    s = _store()
    s.set_state("c1", "closed")
    new_state = advance_funnel(s, "c1", stage_signal="engaged", escalated=True)
    assert new_state == "closed"
    assert s.get_or_create_contact("c1")["state"] == "closed"
