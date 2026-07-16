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


def test_night_reply_is_slower_only_via_the_notice_delay():
    """Night must be slower than day (bot-tell #5: an instant 3am reply is a
    giveaway) -- but ONLY because the message is NOTICED later. At night a human
    doesn't type slower, they just react later, so the night multiplier applies
    to the read/reaction pause ONLY, never to typing speed.

    Consequence: for the SAME seed the typing draw is identical, so
    night_total = day_total + read_base*(mult-1) > day_total, and the extra time
    is modest (a few seconds of extra reaction), NOT the ~1.5x-of-typing blowup
    the old (buggy) model produced, which pushed a 150-char reply past 2 minutes.
    """
    for seed in range(N_SEEDS):
        day = _total_seconds(REPLY, seed, DEMO_TIMINGS, DAY_HOUR)
        night = _total_seconds(REPLY, seed, DEMO_TIMINGS, NIGHT_HOUR)
        assert night > day, (seed, night, day)  # strictly slower at night

    night_totals = [_total_seconds(REPLY, seed, DEMO_TIMINGS, NIGHT_HOUR) for seed in range(N_SEEDS)]
    # still a human band, never absurd (the whole point of the fix)
    assert all(8.0 <= v <= 45.0 for v in night_totals), (min(night_totals), max(night_totals))

    day_mean = sum(_total_seconds(REPLY, s, DEMO_TIMINGS, DAY_HOUR) for s in range(N_SEEDS)) / N_SEEDS
    night_mean = sum(night_totals) / len(night_totals)
    gap = night_mean - day_mean
    # extra delay comes SOLELY from the read pause: read_mean*(mult-1)
    # ~= 2.75 * 1.5 ~= 4s, not the tens of seconds the typing-multiplier bug added.
    assert 1.0 < gap < 10.0, gap


def test_total_reply_time_is_capped():
    """A very long reply must never blow past the response-time ceiling
    (spec: put a cap on total response time, e.g. 90s), day or night."""
    long_reply = "Это довольно длинное предложение для проверки потолка. " * 40
    for hour in (DAY_HOUR, NIGHT_HOUR):
        total = _total_seconds(long_reply, 0, DEMO_TIMINGS, hour)
        assert total <= H.MAX_TOTAL_RESPONSE_SECONDS + 1e-9, (hour, total)
