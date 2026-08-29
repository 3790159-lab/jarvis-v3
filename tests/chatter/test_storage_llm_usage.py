"""Таблица llm_usage + sink-фабрика (спека 2026-07-23-chatter-prompt-caching)."""
from __future__ import annotations

from chatter.storage.db import Store, usage_sink_for


def _rec(**kw):
    base = {"tag": "brain", "model": "claude-sonnet-5",
            "input_tokens": 100, "output_tokens": 20,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 5000}
    base.update(kw)
    return base


def test_add_llm_usage_writes_row_with_ts():
    store = Store(":memory:")
    store.add_llm_usage(**_rec(), ts=123.5)
    rows = store._conn.execute("SELECT * FROM llm_usage").fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r["tag"] == "brain"
    assert r["model"] == "claude-sonnet-5"
    assert r["input_tokens"] == 100
    assert r["cache_creation_input_tokens"] == 5000
    assert r["ts"] == 123.5


def test_add_llm_usage_defaults_ts_to_now():
    store = Store(":memory:")
    store.add_llm_usage(**_rec())
    r = store._conn.execute("SELECT ts FROM llm_usage").fetchone()
    assert r["ts"] > 1_700_000_000  # похоже на текущий epoch, не 0


def test_llm_usage_totals_aggregates_by_tag():
    store = Store(":memory:")
    store.add_llm_usage(**_rec(tag="brain", input_tokens=10,
                               cache_read_input_tokens=100))
    store.add_llm_usage(**_rec(tag="brain", input_tokens=5,
                               cache_read_input_tokens=200,
                               cache_creation_input_tokens=0))
    store.add_llm_usage(**_rec(tag="classifier", input_tokens=7))
    totals = store.llm_usage_totals()
    assert totals["brain"]["calls"] == 2
    assert totals["brain"]["input_tokens"] == 15
    assert totals["brain"]["cache_read_input_tokens"] == 300
    assert totals["classifier"]["calls"] == 1
    assert totals["classifier"]["input_tokens"] == 7


def test_usage_sink_for_writes_through_store():
    store = Store(":memory:")
    sink = usage_sink_for(store)
    sink(_rec(tag="classifier"))
    totals = store.llm_usage_totals()
    assert totals["classifier"]["calls"] == 1


def test_existing_db_gets_new_table(tmp_path):
    """База, созданная ДО этой арки, получает llm_usage при следующем открытии
    (CREATE TABLE IF NOT EXISTS) — без миграций и краш-петли гардиана."""
    path = tmp_path / "old.db"
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE contacts (contact_id TEXT PRIMARY KEY, state TEXT "
                 "NOT NULL DEFAULT 'new', paused INTEGER NOT NULL DEFAULT 0, "
                 "paused_at REAL)")
    conn.commit()
    conn.close()
    store = Store(path)
    store.add_llm_usage(**_rec())
    assert store.llm_usage_totals()["brain"]["calls"] == 1
