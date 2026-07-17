"""Чистые решения о глушении. Ноль Telethon, ноль сети, ноль SQLite."""
from __future__ import annotations

from chatter.core.pause import is_attributed, is_muted, should_auto_resume


def _row(**kw) -> dict:
    base = {"paused": 0, "pause_source": None, "pause_until": None,
            "paused_at": None, "last_human_out_ts": None}
    base.update(kw)
    return base


def test_kill_switch_mutes_everything_including_unknown_contacts():
    assert is_muted(_row(), kill_switch=True, now=100.0) is True
    assert is_muted(None, kill_switch=True, now=100.0) is True


def test_not_paused_is_not_muted():
    assert is_muted(_row(), kill_switch=False, now=100.0) is False
    assert is_muted(None, kill_switch=False, now=100.0) is False


def test_paused_without_deadline_stays_muted():
    row = _row(paused=1, pause_source="human_takeover", paused_at=50.0)
    assert is_muted(row, kill_switch=False, now=1_000_000.0) is True


def test_expired_deadline_is_not_muted_even_before_the_timer_task_runs():
    # Защита в глубину: если периодическая задача авто-возврата умерла,
    # /pause 1h обязан истечь сам, а не залипнуть навсегда.
    row = _row(paused=1, pause_source="command", pause_until=200.0)
    assert is_muted(row, kill_switch=False, now=199.0) is True
    assert is_muted(row, kill_switch=False, now=200.0) is False


def test_pause_without_a_source_is_flagged_as_a_bug_not_silently_trusted():
    assert is_attributed(_row(paused=1, pause_source="human_takeover")) is True
    assert is_attributed(_row(paused=1, pause_source=None)) is False
    # Не заглушено — атрибутировать нечего.
    assert is_attributed(_row(paused=0, pause_source=None)) is True


HOUR = 3600.0


def test_expired_command_pause_auto_resumes():
    row = _row(paused=1, pause_source="command", pause_until=200.0)
    assert should_auto_resume(row, now=201.0, auto_resume_hours=6) is True
    assert should_auto_resume(row, now=199.0, auto_resume_hours=6) is False


def test_takeover_resumes_after_owner_goes_quiet():
    row = _row(paused=1, pause_source="human_takeover",
               paused_at=0.0, last_human_out_ts=10 * HOUR)
    assert should_auto_resume(row, now=10 * HOUR + 6 * HOUR, auto_resume_hours=6) is True


def test_takeover_does_not_resume_under_the_owners_hands():
    # Владелец переписывается прямо сейчас: разморозить диалог = Аня влезет
    # в живой разговор. Отсчёт от ЕГО последнего сообщения, не от paused_at.
    row = _row(paused=1, pause_source="human_takeover",
               paused_at=0.0, last_human_out_ts=10 * HOUR)
    assert should_auto_resume(row, now=10 * HOUR + 300, auto_resume_hours=6) is False


def test_indefinite_command_pause_never_auto_resumes():
    # Явная команда владельца не отменяется таймером за его спиной.
    row = _row(paused=1, pause_source="command", pause_until=None, paused_at=0.0)
    assert should_auto_resume(row, now=10_000 * HOUR, auto_resume_hours=6) is False


def test_unpaused_row_is_not_resumed():
    assert should_auto_resume(_row(), now=100.0, auto_resume_hours=6) is False
