"""Чистые решения о глушении. Ноль Telethon, ноль сети, ноль SQLite."""
from __future__ import annotations

from chatter.core.pause import is_attributed, is_muted


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
