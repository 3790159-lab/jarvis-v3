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


def test_pause_with_target_only_no_duration():
    # «Заглуши вот этот диалог насовсем» — реальный сценарий, отдельный от
    # /pause <длительность> <ссылка>. Ссылка не должна приниматься за кривую
    # длительность и падать в error.
    assert parse_command("/pause t.me/ivan") == Command(
        name="pause", duration_seconds=None, target="t.me/ivan")


def test_pause_with_numeric_id_target_only_no_duration():
    # Числовой id тоже похож на «аргумент без буквы h/m» — убедиться, что он
    # уходит в target, а не ошибочно трактуется как кривая длительность.
    assert parse_command("/pause 237616472") == Command(
        name="pause", duration_seconds=None, target="237616472")


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


def test_status_with_multiple_pauses_keeps_each_dialog_distinct():
    # Спека §11/§4: несколько пауз одновременно — с РАЗНЫМИ причинами и ETA.
    # Цикл по pauses, порядок и границы между блоками ничем не проверялись.
    maria = _view(title="Мария К.", link="t.me/maria", since_ts=1 * HOUR,
                  source="command", detail=None, msg_id=None,
                  resume_eta_ts=5 * HOUR)
    out = format_status(kill_switch=False, pauses=[_view(), maria],
                        counters={"takeover": 2, "unattributed_pause": 0, "unknown_outgoing": 0},
                        autoresume_beat_age=12.0, autoresume_interval=60.0,
                        now=2 * HOUR, window_hours=24)
    assert "Заглушено диалогов: 2" in out
    assert "Иван Петров" in out
    assert "Мария К." in out
    assert "вы вмешались" in out
    assert "команда /pause" in out

    # Блоки не слиплись: причина Ивана («вы вмешались», с деталью/msg_id)
    # не приклеилась к строке Марии, у которой своя причина без детали.
    lines = out.splitlines()
    ivan_reason_idx = next(i for i, l in enumerate(lines) if "вы вмешались" in l)
    maria_reason_idx = next(i for i, l in enumerate(lines) if "команда /pause" in l)
    assert "Мария" not in lines[ivan_reason_idx]
    assert "Иван" not in lines[maria_reason_idx]


def test_status_indefinite_pause_says_so_and_omits_auto_resume_line():
    # §8: /pause без длительности бессрочен — is_muted/should_auto_resume это
    # уже гарантируют в коде (pause.py), но владелец видит только ЭКРАН.
    # Без этой строки гарантия существует для кода, не для человека.
    out = format_status(kill_switch=False, pauses=[_view(resume_eta_ts=None)],
                        counters={"takeover": 1, "unattributed_pause": 0, "unknown_outgoing": 0},
                        autoresume_beat_age=12.0, autoresume_interval=60.0,
                        now=1 * HOUR, window_hours=24)
    assert "авто-возврата нет" in out
    assert "авто-возврат через" not in out


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
