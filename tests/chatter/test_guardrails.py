from __future__ import annotations
from chatter.core.guardrails import contains_unbacked_claim, within_hourly_limit, within_daily_cap
from chatter.storage.db import Store

KNOWLEDGE = "Консультация 5000 руб. Фотосессия 15000 руб. Работаю по предоплате 50%."

def test_backed_price_is_ok():
    assert contains_unbacked_claim("Консультация стоит 5000 руб.", KNOWLEDGE) is False

def test_invented_price_flagged():
    assert contains_unbacked_claim("Могу сделать за 3000 руб, специально для вас.", KNOWLEDGE) is True

def test_no_numbers_is_ok():
    assert contains_unbacked_claim("Расскажите, что именно хотите?", KNOWLEDGE) is False

def test_percent_backed():
    assert contains_unbacked_claim("Предоплата 50%.", KNOWLEDGE) is False

def test_hourly_limit(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    for t in range(3):
        s.add_message("u1", "assistant", "x", ts=1000.0 + t)
    # limit 3, window 3600s, now=1002 -> already 3 in last hour -> not allowed
    assert within_hourly_limit(s, "u1", now=1002.0, limit=3) is False
    assert within_hourly_limit(s, "u1", now=1002.0, limit=5) is True

def test_daily_cap(tmp_path):
    s = Store(tmp_path / "c.db")
    s.get_or_create_contact("u1")
    for t in range(2):
        s.add_message("u1", "assistant", "x", ts=100.0 + t)
    assert within_daily_cap(s, now=200.0, cap=2) is False
    assert within_daily_cap(s, now=200.0, cap=3) is True
