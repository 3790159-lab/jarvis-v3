"""Чистый парсер команд пульта. Ноль Telethon."""
from __future__ import annotations

from chatter.core.console import Command, PauseView, format_status, parse_command

HOUR = 3600.0


def _view(**kw) -> PauseView:
    base = dict(title="Иван Петров", link="t.me/ivan", since_ts=0.0,
                source="human_takeover", detail="Здравствуйте, я сам перезвоню",
                msg_id=4821, resume_eta_ts=3 * HOUR)
    base.update(kw)
    return PauseView(**base)


def test_global_commands():
    assert parse_command("/status") == Command(name="status")
    assert parse_command("/stop") == Command(name="stop")
    assert parse_command("/start") == Command(name="start")


def test_case_and_whitespace_tolerated():
    assert parse_command("  /STATUS  ") == Command(name="status")


def test_pause_durations():
    assert parse_command("/pause 1h") == Command(name="pause", duration_seconds=3600.0)
    assert parse_command("/pause 30m") == Command(name="pause", duration_seconds=1800.0)
    assert parse_command("/pause") == Command(name="pause", duration_seconds=None)


def test_pause_with_explicit_target():
    assert parse_command("/pause 1h t.me/ivan") == Command(
        name="pause", duration_seconds=3600.0, target="t.me/ivan")
    assert parse_command("/resume 237616472") == Command(name="resume", target="237616472")


def test_not_a_command():
    assert parse_command("просто текст") is None
    assert parse_command("") is None
    assert parse_command("/unknown") is None


def test_bad_duration_is_reported_not_silently_ignored():
    # Молча проглотить «/pause 1час» = владелец думает, что поставил паузу.
    cmd = parse_command("/pause 1час")
    assert cmd == Command(name="pause", error="не понял длительность: '1час' (примеры: 1h, 30m)")


def test_status_answers_why_is_she_silent_with_a_reason_per_dialog():
    out = format_status(kill_switch=False, pauses=[_view()],
                        counters={"takeover": 2, "unattributed_pause": 0, "unknown_outgoing": 0},
                        autoresume_beat_age=12.0, autoresume_interval=60.0,
                        now=1 * HOUR, window_hours=24)
    assert "Иван Петров" in out
    assert "вы вмешались" in out
    assert "Здравствуйте, я сам перезвоню" in out    # ЧТО именно вызвало паузу
    assert "4821" in out                              # атрибуция по id
    assert "РАБОТАЕТ" in out


def test_status_shows_the_kill_switch_first():
    out = format_status(kill_switch=True, pauses=[], counters={},
                        autoresume_beat_age=5.0, autoresume_interval=60.0,
                        now=0.0, window_hours=24)
    assert "ЗАГЛУШЕНА" in out
    assert "/start" in out          # как расколдовать — прямо в ответе


def test_status_flags_a_dead_autoresume_task_instead_of_staying_quiet():
    # Мёртвый таймер выглядит РОВНО как «пауз к возврату нет»: тихо и
    # правдоподобно. Единственная разница — возраст heartbeat.
    out = format_status(kill_switch=False, pauses=[], counters={},
                        autoresume_beat_age=47 * 60.0, autoresume_interval=60.0,
                        now=0.0, window_hours=24)
    assert "⚠️" in out


def test_status_without_any_pause_says_so_plainly():
    out = format_status(kill_switch=False, pauses=[], counters={},
                        autoresume_beat_age=3.0, autoresume_interval=60.0,
                        now=0.0, window_hours=24)
    assert "Заглушено диалогов: 0" in out


def test_status_flags_autoresume_that_never_ran_even_once():
    # Раннер только что стартовал или задача авто-возврата умерла ДО первого
    # прогона: beat_age=None неотличим от «пауз к возврату нет», если не
    # проверить его отдельно от «прогон был давно» (DEV-18 — не молчать).
    out = format_status(kill_switch=False, pauses=[], counters={},
                        autoresume_beat_age=None, autoresume_interval=60.0,
                        now=0.0, window_hours=24)
    assert "⚠️" in out
