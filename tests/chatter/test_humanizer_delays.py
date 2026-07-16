from __future__ import annotations
import random
from chatter.config.loader import Timings, WorkHours
from chatter.core import humanizer as H

TIMINGS = Timings(
    read_delay_min=1.0, read_delay_max=5.0, cps_min=3.0, cps_max=6.0,
    jitter_min=0.8, jitter_max=1.4, split_pause_min=0.5, split_pause_max=2.0,
    split_max_len=160, night_multiplier=2.0, debounce_window=3.0, debounce_max=15.0,
)
WH = WorkHours(start=9, end=22)

def test_is_night_true_before_open_and_after_close():
    assert H.is_night(3, WH) is True
    assert H.is_night(23, WH) is True

def test_is_night_false_during_hours():
    assert H.is_night(9, WH) is False
    assert H.is_night(21, WH) is False

def test_read_delay_within_range_day():
    rng = random.Random(0)
    for _ in range(50):
        d = H.read_delay(rng, TIMINGS, night=False)
        assert TIMINGS.read_delay_min <= d <= TIMINGS.read_delay_max

def test_read_delay_night_is_larger():
    d_day = H.read_delay(random.Random(1), TIMINGS, night=False)
    d_night = H.read_delay(random.Random(1), TIMINGS, night=True)
    assert d_night == d_day * TIMINGS.night_multiplier
