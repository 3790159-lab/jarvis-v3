from __future__ import annotations
import random
from chatter.config.loader import Timings, WorkHours
from chatter.core import humanizer as H

# Demo-representative timings, TUNED to land total day reply-rhythm time
# (read_delay + typing_duration) in a "human, not machine, not abandoned"
# band. These are the values Milestone D's demo clients/demo/settings.yaml
# should use verbatim.
DEMO_TIMINGS = Timings(
    read_delay_min=1.5, read_delay_max=4.0,
    cps_min=4.0, cps_max=7.0, jitter_min=0.9, jitter_max=1.2,
    split_pause_min=0.6, split_pause_max=1.8, split_max_len=160,
    night_multiplier=2.5, debounce_window=3.0, debounce_max=15.0,
)
WH = WorkHours(start=9, end=22)

# Representative 2-sentence reply: short enough to fit in one part
# (< split_max_len), long enough to be realistic.
REPLY = "Супер, поняла вас! Расскажите чуть подробнее — вам консультация или съёмка?"

DAY_HOUR = 12   # inside 9-22 -> not night
NIGHT_HOUR = 2  # outside 9-22 -> night

N_SEEDS = 200


def _total_seconds(reply: str, seed: int, t: Timings, hour: int) -> float:
    actions = H.compose_reply(reply, random.Random(seed), t, WH, now_hour=hour)
    return sum(a.seconds for a in actions if isinstance(a, H.Pause))


def test_day_reply_rhythm_within_human_band():
    """~10-25s: not a 3s machine reply, not a 90s abandoned chat."""
    totals = [_total_seconds(REPLY, seed, DEMO_TIMINGS, DAY_HOUR) for seed in range(N_SEEDS)]
    assert all(8.0 <= v <= 30.0 for v in totals), (min(totals), max(totals))
    mean = sum(totals) / len(totals)
    assert 12.0 <= mean <= 22.0, mean


def test_night_reply_rhythm_larger_but_not_absurd():
    """Night replies must be slower than day (bot-tell #5: instant 3am reply
    is a giveaway), but bounded (a 90s+ delay burns the lead)."""
    day_totals = [_total_seconds(REPLY, seed, DEMO_TIMINGS, DAY_HOUR) for seed in range(N_SEEDS)]
    day_mean = sum(day_totals) / len(day_totals)

    night_totals = [_total_seconds(REPLY, seed, DEMO_TIMINGS, NIGHT_HOUR) for seed in range(N_SEEDS)]
    assert all(v > day_mean for v in night_totals), (min(night_totals), day_mean)
    assert all(15.0 <= v <= 90.0 for v in night_totals), (min(night_totals), max(night_totals))
