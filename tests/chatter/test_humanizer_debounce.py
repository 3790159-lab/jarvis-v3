from __future__ import annotations
from chatter.core import humanizer as H


def test_coalesce_joins_in_order():
    assert H.coalesce(["привет", "а сколько стоит?"]) == "привет\nа сколько стоит?"

def test_coalesce_single():
    assert H.coalesce(["одно сообщение"]) == "одно сообщение"

def test_coalesce_strips_blanks():
    assert H.coalesce(["  привет ", "", "  ещё "]) == "привет\nещё"


# --- debounce_ready: hard-ceiling redefinition (bot-tell #3) ---
# A pure "instant, precise 3-second debounce" is itself a tell: a real human
# doesn't reply exactly 3.000s after the last message no matter how long the
# other side keeps typing. debounce_ready has two independent triggers:
#   - quiet gap: (now - last_received_at) >= window
#   - hard ceiling: (now - first_received_at) >= max_window
# Either one firing means "ready to reply".

def test_not_ready_when_neither_gap_nor_ceiling_elapsed():
    assert H.debounce_ready(
        first_received_at=100.0, last_received_at=100.0, now=101.0,
        window=3.0, max_window=15.0,
    ) is False

def test_ready_once_quiet_gap_elapses():
    assert H.debounce_ready(
        first_received_at=100.0, last_received_at=100.0, now=103.0,
        window=3.0, max_window=15.0,
    ) is True

def test_quiet_gap_boundary_is_ready():
    assert H.debounce_ready(
        first_received_at=100.0, last_received_at=100.0, now=103.0,
        window=3.0, max_window=15.0,
    ) is True

def test_ceiling_boundary_is_ready():
    assert H.debounce_ready(
        first_received_at=0.0, last_received_at=59.0, now=15.0,
        window=100.0, max_window=15.0,
    ) is True


def test_burst_every_1s_stays_not_ready_until_quiet_gap_or_ceiling():
    """Simulate 5 messages arriving 1s apart. After each new message the
    quiet gap resets (last_received_at advances) so debounce_ready must stay
    False the whole time the burst is ongoing, as long as neither the quiet
    gap (window) nor the hard ceiling (max_window) has elapsed."""
    window = 3.0
    max_window = 15.0
    first = 0.0
    last = 0.0
    for i in range(1, 6):  # messages arrive at t=1,2,3,4,5
        now = float(i)
        last = now
        # right after each message arrives the quiet gap is ~0 and the
        # burst so far (<=5s) hasn't hit the 15s ceiling either
        assert H.debounce_ready(
            first_received_at=first, last_received_at=last, now=now,
            window=window, max_window=max_window,
        ) is False
    # no more messages arrive; once the quiet gap (3s) elapses since the
    # last message (t=5), debounce_ready flips True
    assert H.debounce_ready(
        first_received_at=first, last_received_at=last, now=last + window,
        window=window, max_window=max_window,
    ) is True


def test_nonstop_typer_never_hits_quiet_gap_but_hits_ceiling():
    """Messages every 4s with window=5.0: each 4s gap is always < window, so
    the quiet path never fires (right after each arrival, now == last, so
    the quiet gap is 0). But the hard ceiling (15.0) guarantees a reply is
    composed once first-elapsed >= max_window, regardless of the ongoing
    burst -- the quiet gap alone would never trigger it."""
    window = 5.0
    max_window = 15.0
    first = 0.0
    # message arrivals at t=4,8,12,...,56 (every 4s for 60s). Right after
    # each arrival, now == last, so the quiet gap is 0 < window: the quiet
    # path never contributes. Before the ceiling elapses, not ready.
    for last in range(4, 60, 4):
        now = float(last)
        expected_ready = (now - first) >= max_window
        assert H.debounce_ready(
            first_received_at=first, last_received_at=now, now=now,
            window=window, max_window=max_window,
        ) is expected_ready
        if not expected_ready:
            assert now < max_window
    # explicit ceiling-path assertion from the spec: the quiet gap here is
    # 60-56=4 < window(5), so only the ceiling can explain True
    assert (60.0 - 56.0) < window
    assert H.debounce_ready(
        first_received_at=0.0, last_received_at=56.0, now=60.0,
        window=5.0, max_window=15.0,
    ) is True


def test_ceiling_exactly_at_max_window_is_ready():
    assert H.debounce_ready(
        first_received_at=0.0, last_received_at=14.9, now=15.0,
        window=100.0, max_window=15.0,
    ) is True
