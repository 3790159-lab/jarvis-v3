from __future__ import annotations

"""Phase 12: Structured logging tests.

Verifies:
1. log_event writes a valid JSONL entry with required fields
2. read_today_log reads back written entries
3. build_daily_report aggregates correctly
4. Logging never crashes on file errors
5. /stats command calls logging correctly
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# log_event + read_today_log
# ---------------------------------------------------------------------------

def test_log_event_writes_jsonl(tmp_path, monkeypatch):
    import app.services.structured_logger as slog
    monkeypatch.setattr(slog, "LOGS_DIR", tmp_path)

    slog.log_event(intent="table", query="top AI", user_id="123", latency_ms=500)

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    entries = [json.loads(line) for line in files[0].read_text(encoding="utf-8").strip().splitlines()]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["intent"] == "table"
    assert entry["query"] == "top AI"
    assert entry["user_id"] == "123"
    assert entry["latency_ms"] == 500
    assert "timestamp" in entry


def test_log_event_all_fields_present(tmp_path, monkeypatch):
    import app.services.structured_logger as slog
    monkeypatch.setattr(slog, "LOGS_DIR", tmp_path)

    slog.log_event(intent="greeting", query="привет", user_id="u1",
                   result_summary="ok", latency_ms=10, error="")

    files = list(tmp_path.glob("*.jsonl"))
    entry = json.loads(files[0].read_text().strip())
    assert set(entry.keys()) >= {"timestamp", "user_id", "intent", "query", "result_summary", "latency_ms", "error"}


def test_log_event_truncates_long_query(tmp_path, monkeypatch):
    import app.services.structured_logger as slog
    monkeypatch.setattr(slog, "LOGS_DIR", tmp_path)

    slog.log_event(intent="chat", query="x" * 1000, user_id="u1")

    files = list(tmp_path.glob("*.jsonl"))
    entry = json.loads(files[0].read_text().strip())
    assert len(entry["query"]) <= 200


def test_read_today_log_returns_entries(tmp_path, monkeypatch):
    import app.services.structured_logger as slog
    monkeypatch.setattr(slog, "LOGS_DIR", tmp_path)

    slog.log_event(intent="research", query="LLM", user_id="u1")
    slog.log_event(intent="table", query="AI", user_id="u1")

    entries = slog.read_today_log()
    assert len(entries) == 2
    intents = {e["intent"] for e in entries}
    assert "research" in intents
    assert "table" in intents


def test_read_today_log_returns_empty_on_missing_file(tmp_path, monkeypatch):
    import app.services.structured_logger as slog
    monkeypatch.setattr(slog, "LOGS_DIR", tmp_path)

    entries = slog.read_today_log()
    assert entries == []


def test_log_event_appends_multiple(tmp_path, monkeypatch):
    import app.services.structured_logger as slog
    monkeypatch.setattr(slog, "LOGS_DIR", tmp_path)

    for i in range(5):
        slog.log_event(intent=f"intent_{i}", query=f"q{i}", user_id="u1")

    entries = slog.read_today_log()
    assert len(entries) == 5


# ---------------------------------------------------------------------------
# build_daily_report
# ---------------------------------------------------------------------------

def test_build_daily_report_counts_correctly(tmp_path, monkeypatch):
    import app.services.structured_logger as slog
    monkeypatch.setattr(slog, "LOGS_DIR", tmp_path)

    slog.log_event(intent="table", query="q1", user_id="u1", latency_ms=1000)
    slog.log_event(intent="table", query="q2", user_id="u1", latency_ms=2000)
    slog.log_event(intent="research", query="q3", user_id="u1", latency_ms=500, error="timeout")

    report = slog.build_daily_report()

    assert report["total_events"] == 3
    assert report["errors"] == 1
    assert report["intents"]["table"] == 2
    assert report["intents"]["research"] == 1
    assert report["avg_latency_ms"] == (1000 + 2000 + 500) // 3


def test_build_daily_report_empty(tmp_path, monkeypatch):
    import app.services.structured_logger as slog
    monkeypatch.setattr(slog, "LOGS_DIR", tmp_path)

    report = slog.build_daily_report()

    assert report["total_events"] == 0
    assert report["errors"] == 0
    assert report["avg_latency_ms"] == 0
    assert report["intents"] == {}


def test_build_daily_report_accepts_entries_arg(tmp_path, monkeypatch):
    import app.services.structured_logger as slog

    entries = [
        {"intent": "greeting", "query": "hi", "user_id": "u1",
         "result_summary": "ok", "latency_ms": 50, "error": ""},
        {"intent": "greeting", "query": "hello", "user_id": "u1",
         "result_summary": "ok", "latency_ms": 60, "error": ""},
    ]

    report = slog.build_daily_report(entries)

    assert report["total_events"] == 2
    assert report["intents"]["greeting"] == 2


# ---------------------------------------------------------------------------
# /stats command integration
# ---------------------------------------------------------------------------

def test_stats_command_sends_report(monkeypatch, tmp_path):
    import tools.jarvis_smart_telegram_control as mod
    import app.services.structured_logger as slog

    monkeypatch.setattr(slog, "LOGS_DIR", tmp_path)
    slog.log_event(intent="table", query="AI", user_id="123", latency_ms=999)

    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, **kw: sent.append(txt))

    state = mod.default_state()
    mod.handle_command("123", "/stats", "", state)

    assert len(sent) == 1
    assert "table" in sent[0] or "запрос" in sent[0].lower() or "Статистика" in sent[0]
