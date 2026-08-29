from __future__ import annotations
import threading
import pytest
from chatter.storage.db import Store

def test_store_usable_from_another_thread(tmp_path):
    # Regression: the Telethon transport runs process_batch in a worker thread
    # (asyncio.to_thread), so the Store — created on the main thread — must be
    # usable from other threads. Before check_same_thread=False + a lock, this
    # raised sqlite3.ProgrammingError and the live bot silently never replied.
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    errors: list[Exception] = []

    def worker() -> None:
        try:
            s.add_message("u1", "user", "привет", ts=1.0)
            assert s.history("u1")[-1]["text"] == "привет"
            assert s.count_messages_since("u1", role="user", since_ts=0.0) == 1
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert errors == [], f"Store must be usable from a worker thread: {errors!r}"

def test_get_or_create_contact_defaults(tmp_path):
    s = Store(tmp_path / "c.db")
    c = s.get_or_create_contact("u1")
    assert c["state"] == "new"
    assert c["paused"] == 0 and "human_took_over" not in c.keys()
    # idempotent
    assert s.get_or_create_contact("u1")["state"] == "new"

def test_state_persists(tmp_path):
    # set_flag (which used to be tested alongside set_state here) is retired
    # in arc 3A — pause/unpause coverage now lives in test_store_control.py.
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    s.set_state("u1", "hot")
    c = s.get_or_create_contact("u1")
    assert c["state"] == "hot"

def test_messages_and_history(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    s.add_message("u1", "user", "привет", ts=100.0)
    s.add_message("u1", "assistant", "здравствуйте", ts=101.0)
    hist = s.history("u1")
    assert [(m["role"], m["text"]) for m in hist] == [
        ("user", "привет"), ("assistant", "здравствуйте")]

def test_facts_roundtrip(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    s.set_fact("u1", "budget", "10000")
    s.set_fact("u1", "budget", "12000")  # upsert
    assert s.facts("u1") == {"budget": "12000"}

def test_count_messages_since(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    s.add_message("u1", "assistant", "a", ts=100.0)
    s.add_message("u1", "assistant", "b", ts=200.0)
    assert s.count_messages_since("u1", role="assistant", since_ts=150.0) == 1

def test_count_outbound_between(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    s.add_message("u1", "assistant", "a", ts=100.0)
    s.add_message("u1", "user", "b", ts=110.0)
    assert s.count_outbound_between(0.0, 1000.0) == 1

def test_history_limit_zero_returns_empty(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    s.add_message("u1", "user", "первое", ts=100.0)
    s.add_message("u1", "assistant", "второе", ts=101.0)
    assert s.history("u1", limit=0) == []

def test_history_limit_one_returns_last_row_only(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    s.add_message("u1", "user", "первое", ts=100.0)
    s.add_message("u1", "assistant", "второе", ts=101.0)
    hist = s.history("u1", limit=1)
    assert [(m["role"], m["text"]) for m in hist] == [("assistant", "второе")]

def test_close_closes_connection(tmp_path):
    import sqlite3
    s = Store(tmp_path / "c.db")
    s.close()
    with pytest.raises(sqlite3.ProgrammingError):
        s.get_or_create_contact("u1")

def test_store_is_a_context_manager(tmp_path):
    import sqlite3
    with Store(tmp_path / "c.db") as s:
        s.get_or_create_contact("u1")
    with pytest.raises(sqlite3.ProgrammingError):
        s.get_or_create_contact("u1")

def test_get_runtime_flag_ts_returns_write_time(tmp_path):
    # Дедуп эскалации меряет ВОЗРАСТ активной карточки → нужен ts записи флага,
    # а не только его значение.
    s = Store(tmp_path / "c.db")
    assert s.get_runtime_flag_ts("k") is None      # нет флага → None
    s.set_runtime_flag("k", "v", ts=1234.5)
    assert s.get_runtime_flag_ts("k") == 1234.5
    s.set_runtime_flag("k", "v2", ts=9999.0)       # перезапись двигает ts
    assert s.get_runtime_flag_ts("k") == 9999.0
