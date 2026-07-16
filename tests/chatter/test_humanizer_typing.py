from __future__ import annotations
import random
from chatter.core import humanizer as H
from tests.chatter.test_humanizer_delays import TIMINGS

def test_typing_duration_scales_with_length():
    short = H.typing_duration("hi", random.Random(0), TIMINGS, night=False)
    long = H.typing_duration("hi" * 100, random.Random(0), TIMINGS, night=False)
    assert long > short

def test_typing_duration_within_expected_bounds():
    text = "a" * 60  # 60 chars
    rng = random.Random(0)
    d = H.typing_duration(text, rng, TIMINGS, night=False)
    # slowest: 60/3 * 1.4 = 28.0 ; fastest: 60/6 * 0.8 = 8.0
    assert 8.0 <= d <= 28.0

def test_typing_duration_night_multiplier():
    text = "hello world"
    d_day = H.typing_duration(text, random.Random(5), TIMINGS, night=False)
    d_night = H.typing_duration(text, random.Random(5), TIMINGS, night=True)
    assert d_night == d_day * TIMINGS.night_multiplier

def test_typing_duration_empty_is_zero():
    assert H.typing_duration("", random.Random(0), TIMINGS, night=False) == 0.0
