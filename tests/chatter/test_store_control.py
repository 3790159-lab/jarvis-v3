"""Пауза в SQLite: атрибуция обязательна, состояние переживает рестарт."""
from __future__ import annotations

import pytest

from chatter.storage.db import Store


def test_mute_requires_a_known_source():
    # Пауза без причины = баг-класс (спека §4): самозаглушка тиха и вечна,
    # поэтому поставить её без атрибуции должно быть ТЕХНИЧЕСКИ невозможно.
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        with pytest.raises(ValueError):
            s.mute("c1", source="потому что", now=100.0)


def test_mute_records_who_and_why():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="human_takeover", msg_id=4821,
               detail="Здравствуйте, я сам перезвоню", now=100.0)
        row = s.get_or_create_contact("c1")
        assert row["paused"] == 1
        assert row["pause_source"] == "human_takeover"
        assert row["pause_msg_id"] == 4821
        assert row["pause_detail"] == "Здравствуйте, я сам перезвоню"
        assert row["paused_at"] == 100.0
        assert row["pause_until"] is None


def test_pause_survives_restart(tmp_path):
    # Гардиан перезапускает раннер штатно — пауза в памяти была бы потеряна
    # ровно тогда, когда владелец на неё рассчитывает.
    db = tmp_path / "s.db"
    with Store(db) as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="command", until=999.0, now=100.0)
    with Store(db) as s2:
        row = s2.get_or_create_contact("c1")
        assert row["paused"] == 1
        assert row["pause_source"] == "command"
        assert row["pause_until"] == 999.0


def test_unmute_clears_every_attribution_field():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="human_takeover", msg_id=7, detail="x", now=100.0)
        s.unmute("c1")
        row = s.get_or_create_contact("c1")
        assert row["paused"] == 0
        # Осколки старой атрибуции соврут в /status при следующей паузе.
        assert row["pause_source"] is None
        assert row["pause_msg_id"] is None
        assert row["pause_detail"] is None
        assert row["pause_until"] is None
        assert row["paused_at"] is None


def test_muted_contacts_returns_only_paused_rows():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.get_or_create_contact("c2")
        s.mute("c1", source="command", now=100.0)
        assert [r["contact_id"] for r in s.muted_contacts()] == ["c1"]


def test_note_human_out_tracks_owner_last_manual_message():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.note_human_out("c1", ts=500.0)
        assert s.get_or_create_contact("c1")["last_human_out_ts"] == 500.0
