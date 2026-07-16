from __future__ import annotations
import random
from chatter.core import humanizer as H
from tests.chatter.test_humanizer_delays import TIMINGS, WH


def test_compose_reply_starts_with_pause_then_typing_on():
    actions = H.compose_reply("Привет!", random.Random(0), TIMINGS, WH, now_hour=12)
    assert isinstance(actions[0], H.Pause)
    # the read pause (index 0) must come strictly before Typing(on=True)
    first_typing_on = next(i for i, a in enumerate(actions) if isinstance(a, H.Typing) and a.on)
    assert first_typing_on > 0
    assert isinstance(actions[first_typing_on], H.Typing)


def test_compose_reply_ends_with_typing_off():
    actions = H.compose_reply("Привет!", random.Random(0), TIMINGS, WH, now_hour=12)
    assert isinstance(actions[-1], H.Typing)
    assert actions[-1].on is False


def test_compose_reply_has_exactly_one_typing_on_and_off():
    actions = H.compose_reply(
        "Первое предложение здесь, и оно довольно длинное само по себе. "
        "Второе предложение тоже тут, и добавляет ещё немного текста. "
        "Третье предложение продолжает мысль дальше и дальше.",
        random.Random(3), TIMINGS, WH, now_hour=12,
    )
    typing_on = [a for a in actions if isinstance(a, H.Typing) and a.on]
    typing_off = [a for a in actions if isinstance(a, H.Typing) and not a.on]
    assert len(typing_on) == 1
    assert len(typing_off) == 1


def test_every_say_is_immediately_preceded_by_pause():
    actions = H.compose_reply(
        "Первое предложение здесь, и оно довольно длинное само по себе. "
        "Второе предложение тоже тут, и добавляет ещё немного текста. "
        "Третье предложение продолжает мысль дальше и дальше.",
        random.Random(7), TIMINGS, WH, now_hour=12,
    )
    for i, a in enumerate(actions):
        if isinstance(a, H.Say):
            assert i > 0
            assert isinstance(actions[i - 1], H.Pause)


def test_instant_typing_indicator_never_produced():
    """The first action must never be Typing(on=True) -- the read pause
    always comes first, otherwise the bot 'sees' the message and starts
    typing in the same instant, which is a dead giveaway (bot-tell #1)."""
    for seed in range(20):
        actions = H.compose_reply("Здравствуйте, чем могу помочь?", random.Random(seed), TIMINGS, WH, now_hour=12)
        assert not (isinstance(actions[0], H.Typing) and actions[0].on)
        assert isinstance(actions[0], H.Pause)


def test_compose_reply_night_uses_is_night():
    # WH start=9 end=22 -> hour 2 is night
    actions_night = H.compose_reply("Привет!", random.Random(0), TIMINGS, WH, now_hour=2)
    actions_day = H.compose_reply("Привет!", random.Random(0), TIMINGS, WH, now_hour=12)
    pause_night = actions_night[0].seconds
    pause_day = actions_day[0].seconds
    assert pause_night == pause_day * TIMINGS.night_multiplier


def test_say_parts_match_split_message():
    text = (
        "Первое предложение здесь, и оно довольно длинное само по себе. "
        "Второе предложение тоже тут, и добавляет ещё немного текста. "
        "Третье предложение продолжает мысль дальше и дальше."
    )
    actions = H.compose_reply(text, random.Random(1), TIMINGS, WH, now_hour=12)
    says = [a.text for a in actions if isinstance(a, H.Say)]
    assert says == H.split_message(text, TIMINGS)
