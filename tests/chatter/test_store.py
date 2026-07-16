from __future__ import annotations
from chatter.storage.db import Store

def test_get_or_create_contact_defaults(tmp_path):
    s = Store(tmp_path / "c.db")
    c = s.get_or_create_contact("u1")
    assert c["state"] == "new"
    assert c["paused"] == 0 and c["human_took_over"] == 0
    # idempotent
    assert s.get_or_create_contact("u1")["state"] == "new"

def test_state_and_flags_persist(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    s.set_state("u1", "hot")
    s.set_flag("u1", "paused", True)
    c = s.get_or_create_contact("u1")
    assert c["state"] == "hot" and c["paused"] == 1

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
