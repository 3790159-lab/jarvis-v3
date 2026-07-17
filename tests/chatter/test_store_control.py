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


def test_runtime_flag_roundtrip_and_default():
    with Store(":memory:") as s:
        assert s.get_runtime_flag("kill_switch") is None
        s.set_runtime_flag("kill_switch", "1", ts=100.0)
        assert s.get_runtime_flag("kill_switch") == "1"
        s.set_runtime_flag("kill_switch", "0", ts=200.0)   # /start перезаписывает
        assert s.get_runtime_flag("kill_switch") == "0"


def test_events_count_within_window_only():
    with Store(":memory:") as s:
        s.add_event("takeover", contact_id="c1", detail="msg 1", ts=100.0)
        s.add_event("takeover", contact_id="c1", detail="msg 2", ts=200.0)
        s.add_event("unknown_outgoing", ts=200.0)
        assert s.count_events("takeover", since_ts=150.0) == 1
        assert s.count_events("takeover", since_ts=0.0) == 2
        assert s.count_events("unattributed_pause", since_ts=0.0) == 0


def test_card_maps_saved_message_to_contact_and_survives_restart(tmp_path):
    # Адресация реплаем обязана пережить рестарт: карточка остаётся лежать в
    # Saved Messages, и владелец ответит на неё через час.
    db = tmp_path / "s.db"
    with Store(db) as s:
        s.add_card(msg_id=555, contact_id="237616472:demo", kind="pause", ts=100.0)
    with Store(db) as s2:
        assert s2.card_contact(555) == "237616472:demo"
        assert s2.card_contact(999) is None


def test_has_contact_true_for_existing_false_for_unknown():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        assert s.has_contact("c1") is True
        assert s.has_contact("нет-такого") is False


def test_has_contact_does_not_create_a_row():
    # Главный тест: has_contact — это ТОЛЬКО проверка, get_or_create_contact
    # для этого не годится, потому что он создал бы строку и объявил
    # управляемым любой чат, куда владелец написал с этого же аккаунта.
    with Store(":memory:") as s:
        s.has_contact("нет-такого")
        rows = s._conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        assert rows == 0
