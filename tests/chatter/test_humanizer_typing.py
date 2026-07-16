from __future__ import annotations
import random
import pytest
from chatter.core import humanizer as H
from tests.chatter.test_humanizer_delays import TIMINGS

def test_typing_duration_scales_with_length():
    short = H.typing_duration("hi", random.Random(0), TIMINGS)
    long = H.typing_duration("hi" * 100, random.Random(0), TIMINGS)
    assert long > short

def test_typing_duration_within_expected_bounds():
    text = "a" * 60  # 60 chars
    rng = random.Random(0)
    d = H.typing_duration(text, rng, TIMINGS)
    # slowest: 60/3 * 1.4 = 28.0 ; fastest: 60/6 * 0.8 = 8.0
    assert 8.0 <= d <= 28.0

def test_typing_duration_has_no_night_parameter():
    """A person does not type slower at 3am -- they NOTICE the message later
    (that lives in the read/reaction pause, not here). typing_duration must not
    accept a `night` argument at all, so it can never be scaled by the night
    multiplier (design bug: night used to multiply typing_duration too, making a
    150-char reply take ~2 minutes)."""
    with pytest.raises(TypeError):
        H.typing_duration("hi", random.Random(0), TIMINGS, night=True)

def test_typing_duration_empty_is_zero():
    assert H.typing_duration("", random.Random(0), TIMINGS) == 0.0
