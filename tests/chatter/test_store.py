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
    assert c["paused"] == 0 and c["human_took_over"] == 0
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

def test_follow_up_register_and_get(tmp_path):
    s = Store(tmp_path / "c.db")
    assert s.get_follow_up("42:demo") is None
    s.register_follow_up("42:demo", topic="скидку?", lead_last_ts=100.0,
                         card_ref="bot:1:7", now=100.0)
    fu = s.get_follow_up("42:demo")
    assert fu["state"] == "pending_owner"
    assert fu["mode"] == "verbatim"
    assert fu["topic"] == "скидку?"
    assert fu["card_ref"] == "bot:1:7"
    assert fu["lead_last_ts"] == 100.0
    assert fu["holding_sent"] == 0

def test_follow_up_reregister_supersedes(tmp_path):
    s = Store(tmp_path / "c.db")
    s.register_follow_up("42:demo", topic="A", lead_last_ts=1.0, card_ref="bot:1:7", now=1.0)
    s.set_follow_up("42:demo", now=1.5, state="ready", owner_answer="да", mode="instruction")
    s.register_follow_up("42:demo", topic="B", lead_last_ts=2.0, card_ref="bot:1:9", now=2.0)
    fu = s.get_follow_up("42:demo")
    assert fu["state"] == "pending_owner"
    assert fu["topic"] == "B"
    assert fu["owner_answer"] is None
    assert fu["mode"] == "verbatim"

def test_follow_up_set_and_list_by_state(tmp_path):
    s = Store(tmp_path / "c.db")
    s.register_follow_up("42:demo", topic="A", lead_last_ts=1.0, card_ref="r", now=1.0)
    s.register_follow_up("99:demo", topic="B", lead_last_ts=1.0, card_ref="r2", now=1.0)
    s.set_follow_up("42:demo", now=5.0, state="ready", owner_answer="ответ", owner_answer_ts=5.0)
    ready = s.follow_ups_by_state("ready")
    assert [f["contact_id"] for f in ready] == ["42:demo"]
    assert ready[0]["owner_answer"] == "ответ" and ready[0]["owner_answer_ts"] == 5.0
    assert {f["contact_id"] for f in s.follow_ups_by_state("pending_owner")} == {"99:demo"}

def test_follow_up_set_rejects_unknown_field(tmp_path):
    import pytest
    s = Store(tmp_path / "c.db")
    s.register_follow_up("42:demo", topic="A", lead_last_ts=1.0, card_ref="r", now=1.0)
    with pytest.raises(ValueError):
        s.set_follow_up("42:demo", now=1.0, bogus_column="x")

def test_follow_up_set_updates_updated_ts_and_orders(tmp_path):
    s = Store(tmp_path / "c.db")
    s.register_follow_up("42:demo", topic="A", lead_last_ts=1.0, card_ref="r", now=1.0)
    s.register_follow_up("99:demo", topic="B", lead_last_ts=1.0, card_ref="r2", now=1.0)
    s.set_follow_up("42:demo", now=10.0, state="ready")
    s.set_follow_up("99:demo", now=20.0, state="ready")
    assert s.get_follow_up("42:demo")["updated_ts"] == 10.0        # not 0.0
    # follow_ups_by_state ordered by updated_ts ascending
    assert [f["contact_id"] for f in s.follow_ups_by_state("ready")] == ["42:demo", "99:demo"]
