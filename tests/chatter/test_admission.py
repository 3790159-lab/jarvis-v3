from __future__ import annotations

from chatter.core.admission import admission_decision

AL = frozenset({237616472})
DL = frozenset({666})


def _d(sender_id, *, is_contact=False, funnel_gate=True, allowlist=AL, denylist=DL):
    return admission_decision(
        sender_id=sender_id, is_contact=is_contact,
        allowlist=allowlist, denylist=denylist, funnel_gate=funnel_gate)


# --- funnel_gate OFF: old behaviour (answer only allowlist) -----------------
def test_gate_off_allowlisted_answers():
    assert _d(237616472, funnel_gate=False) == "answer"


def test_gate_off_stranger_ignored():
    assert _d(999, funnel_gate=False) == "ignore"


def test_gate_off_contact_still_ignored_if_not_allowlisted():
    # старое поведение не смотрит на contact вообще
    assert _d(999, is_contact=True, funnel_gate=False) == "ignore"


# --- funnel_gate ON: the flip -----------------------------------------------
def test_gate_on_stranger_answers():
    assert _d(999, is_contact=False) == "answer"          # лид


def test_gate_on_known_contact_notifies_owner_not_answered():
    assert _d(999, is_contact=True) == "notify_owner"     # знакомый — не отвечаем


def test_gate_on_denylist_blocked():
    assert _d(666) == "block_denylist"


def test_gate_on_allowlist_force_answers_even_if_contact():
    # allowlist = override: владелец тестирует как лид, даже будучи контактом
    assert _d(237616472, is_contact=True) == "answer"


def test_gate_on_denylist_wins_over_allowlist():
    assert _d(777, allowlist=frozenset({777}), denylist=frozenset({777})) == "block_denylist"
