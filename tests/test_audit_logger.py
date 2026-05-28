# -*- coding: utf-8 -*-
"""Tests for :mod:`app.services.audit.audit_logger`.

File I/O is redirected to ``tmp_path`` via the ``JARVIS_AUDIT_DIR`` env var,
and the ``_forward_to_admin`` Telegram HTTP call is patched to a spy. No
real bot, no real network.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from app.services.audit import audit_logger


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def audit_env(tmp_path, monkeypatch):
    """Isolated audit dir + cleared in-memory caches."""
    monkeypatch.setenv("JARVIS_AUDIT_DIR", str(tmp_path))
    monkeypatch.delenv("JARVIS_ADMIN_USER_ID", raising=False)
    monkeypatch.delenv("JARVIS_AUDIT_FORWARD_ENABLED", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    audit_logger._reset_seen_users_cache()
    yield tmp_path
    audit_logger._reset_seen_users_cache()


def _read_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── file writes ──────────────────────────────────────────────────────────────


def test_audit_event_writes_jsonl_line(audit_env):
    """A single audit_event appends one JSON line to today's file."""
    audit_logger.audit_event(
        user_id=111,
        username="vasya",
        chat_id="111",
        event="command",
        details={"command": "/start"},
    )
    files = list(audit_env.glob("*.jsonl"))
    assert len(files) == 1
    rows = _read_jsonl(files[0])
    assert len(rows) == 2  # synthetic user_first_seen + the command event
    cmd = rows[-1]
    assert cmd["event"] == "command"
    assert cmd["user_id"] == 111
    assert cmd["username"] == "vasya"
    assert cmd["chat_id"] == "111"
    assert cmd["details"] == {"command": "/start"}


def test_audit_event_creates_directory_if_missing(audit_env, monkeypatch):
    """When state/audit/ doesn't exist, audit_event creates it (no error)."""
    nested = audit_env / "nested" / "audit"
    monkeypatch.setenv("JARVIS_AUDIT_DIR", str(nested))
    assert not nested.exists()
    audit_logger.audit_event(
        user_id=222, username=None, chat_id="222", event="command",
    )
    assert nested.exists()
    files = list(nested.glob("*.jsonl"))
    assert len(files) == 1


def test_audit_event_appends_multiple_lines(audit_env):
    """Three audit_events on the same day → three event lines (plus 1 first_seen)."""
    for cmd in ("/start", "/swapbatch_source", "/swapbatch_batch"):
        audit_logger.audit_event(
            user_id=111, username="vasya", chat_id="111",
            event="command", details={"command": cmd},
        )
    files = list(audit_env.glob("*.jsonl"))
    assert len(files) == 1
    rows = _read_jsonl(files[0])
    # 1 user_first_seen + 3 command events
    assert len(rows) == 4
    assert rows[0]["event"] == "user_first_seen"
    assert [r["details"]["command"] for r in rows[1:]] == [
        "/start", "/swapbatch_source", "/swapbatch_batch",
    ]


def test_audit_daily_rotation(audit_env):
    """Events on different dates land in different per-day files."""
    day1 = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 5, 28, 10, 0, 0, tzinfo=timezone.utc)
    audit_logger.audit_event(
        user_id=111, username="v", chat_id="111", event="command",
        details={"command": "/a"}, _now=day1,
    )
    audit_logger.audit_event(
        user_id=111, username="v", chat_id="111", event="command",
        details={"command": "/b"}, _now=day2,
    )
    files = sorted(p.name for p in audit_env.glob("*.jsonl"))
    assert files == ["2026-05-27.jsonl", "2026-05-28.jsonl"]


# ── timestamp format ────────────────────────────────────────────────────────


def test_event_has_iso8601_timestamp(audit_env):
    """The ts field round-trips through datetime.fromisoformat."""
    audit_logger.audit_event(
        user_id=111, username="x", chat_id="111", event="command",
    )
    rows = _read_jsonl(next(audit_env.glob("*.jsonl")))
    parsed = datetime.fromisoformat(rows[-1]["ts"])
    # Should be timezone-aware so log files stay interpretable across hosts.
    assert parsed.tzinfo is not None


# ── user_first_seen ──────────────────────────────────────────────────────────


def test_user_first_seen_emitted_before_first_real_event(audit_env):
    """The FIRST event for a user_id auto-prepends user_first_seen."""
    audit_logger.audit_event(
        user_id=42, username="newbie", chat_id="42",
        event="command", details={"command": "/hi"},
    )
    rows = _read_jsonl(next(audit_env.glob("*.jsonl")))
    assert rows[0]["event"] == "user_first_seen"
    assert rows[0]["user_id"] == 42
    assert rows[0]["username"] == "newbie"
    assert rows[1]["event"] == "command"


def test_user_first_seen_only_once_per_user(audit_env):
    """A second event from the same user_id does NOT re-emit user_first_seen."""
    audit_logger.audit_event(
        user_id=42, username="newbie", chat_id="42", event="command",
    )
    audit_logger.audit_event(
        user_id=42, username="newbie", chat_id="42", event="command",
    )
    rows = _read_jsonl(next(audit_env.glob("*.jsonl")))
    first_seens = [r for r in rows if r["event"] == "user_first_seen"]
    assert len(first_seens) == 1


def test_user_first_seen_uses_prior_day_history(audit_env, monkeypatch):
    """A user who appeared in a prior day's log is NOT re-flagged as first-seen."""
    day1 = datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 5, 27, 10, 0, 0, tzinfo=timezone.utc)
    audit_logger.audit_event(
        user_id=42, username="v", chat_id="42", event="command", _now=day1,
    )
    # Fresh process: cache is cold, must reconstruct from disk.
    audit_logger._reset_seen_users_cache()
    audit_logger.audit_event(
        user_id=42, username="v", chat_id="42", event="command", _now=day2,
    )
    day2_rows = _read_jsonl(audit_env / "2026-05-27.jsonl")
    assert all(r["event"] != "user_first_seen" for r in day2_rows)


# ── forwarding ──────────────────────────────────────────────────────────────


def test_forward_to_admin_when_event_forwardable(audit_env, monkeypatch):
    """A forwardable event from a non-admin user triggers a forward."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "999")
    forwarded: list[str] = []
    monkeypatch.setattr(
        audit_logger, "_forward_to_admin", lambda text: forwarded.append(text),
    )
    audit_logger.audit_event(
        user_id=111, username="vasya", chat_id="111",
        event="swapbatch_go", details={"targets": 5},
    )
    # Two forwards expected: synthetic user_first_seen + swapbatch_go.
    assert len(forwarded) == 2
    assert any("First" in t or "first" in t or "📸" in t for t in forwarded)
    assert any("Swap" in t or "🚀" in t for t in forwarded)


def test_no_forward_when_event_not_forwardable(audit_env, monkeypatch):
    """A non-forwardable event (e.g., generic 'command') does NOT forward."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "999")
    forwarded: list[str] = []
    monkeypatch.setattr(
        audit_logger, "_forward_to_admin", lambda text: forwarded.append(text),
    )
    # First event auto-prepends user_first_seen (forwardable) — use a known
    # user so the synthetic event doesn't fire.
    audit_logger.audit_event(
        user_id=111, username="vasya", chat_id="111", event="user_first_seen",
    )
    forwarded.clear()
    audit_logger.audit_event(
        user_id=111, username="vasya", chat_id="111",
        event="command", details={"command": "/foo"},
    )
    assert forwarded == []


def test_no_forward_when_user_is_admin(audit_env, monkeypatch):
    """The admin's own actions are NOT forwarded back to themselves."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "999")
    forwarded: list[str] = []
    monkeypatch.setattr(
        audit_logger, "_forward_to_admin", lambda text: forwarded.append(text),
    )
    audit_logger.audit_event(
        user_id=999, username="admin", chat_id="999",
        event="swapbatch_go", details={"targets": 5},
    )
    assert forwarded == []


def test_no_forward_when_admin_unset(audit_env, monkeypatch):
    """Open mode (no admin) → no recipient → no forward."""
    monkeypatch.delenv("JARVIS_ADMIN_USER_ID", raising=False)
    forwarded: list[str] = []
    monkeypatch.setattr(
        audit_logger, "_forward_to_admin", lambda text: forwarded.append(text),
    )
    audit_logger.audit_event(
        user_id=111, username="vasya", chat_id="111",
        event="swapbatch_go", details={"targets": 5},
    )
    assert forwarded == []


def test_forward_disabled_by_env(audit_env, monkeypatch):
    """JARVIS_AUDIT_FORWARD_ENABLED=0 disables forwarding even with admin set."""
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "999")
    monkeypatch.setenv("JARVIS_AUDIT_FORWARD_ENABLED", "0")
    forwarded: list[str] = []
    monkeypatch.setattr(
        audit_logger, "_forward_to_admin", lambda text: forwarded.append(text),
    )
    audit_logger.audit_event(
        user_id=111, username="vasya", chat_id="111",
        event="swapbatch_go", details={"targets": 5},
    )
    assert forwarded == []


def test_forward_failure_does_not_block(audit_env, monkeypatch):
    """A raise inside _forward_to_admin must not bubble up to the caller.

    The file write still happens, so the log of record is preserved even if
    the admin notification is broken.
    """
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "999")

    def _boom(_text: str) -> None:
        raise RuntimeError("network down")

    monkeypatch.setattr(audit_logger, "_forward_to_admin", _boom)
    # Must not raise.
    audit_logger.audit_event(
        user_id=111, username="vasya", chat_id="111",
        event="swapbatch_go", details={"targets": 5},
    )
    # File write still occurred.
    rows = _read_jsonl(next(audit_env.glob("*.jsonl")))
    assert any(r["event"] == "swapbatch_go" for r in rows)


# ── format snapshots ────────────────────────────────────────────────────────


def test_format_first_seen_message_contains_username_and_id():
    """The user_first_seen forward names the user and their id."""
    text = audit_logger._format_admin_message(
        "user_first_seen", "vasya", 123, {},
    )
    assert "vasya" in text
    assert "123" in text
    assert "📸" in text or "first" in text.lower()


def test_format_batch_received_message_includes_count_and_estimate():
    """The batch-received forward shows photo count, estimated cost, ETA."""
    text = audit_logger._format_admin_message(
        "swapbatch_batch_received",
        "vasya",
        123,
        {"photos_count": 5, "after_dedupe_count": 5,
         "total_usd": 1.86, "minutes": 70},
    )
    assert "5" in text
    assert "1.86" in text
    assert "70" in text


def test_format_error_message_includes_class_and_handler():
    """The error forward names the handler and the exception class."""
    text = audit_logger._format_admin_message(
        "error", "vasya", 123,
        {"handler": "animate_handler",
         "exception_class": "RunpodComfyError",
         "message": "ComfyUI failed to start"},
    )
    assert "animate_handler" in text
    assert "RunpodComfyError" in text
    assert "ComfyUI failed to start" in text
    assert "⚠️" in text


def test_format_no_username_falls_back_gracefully():
    """When username is None, the forward still names the user_id."""
    text = audit_logger._format_admin_message(
        "user_first_seen", None, 123, {},
    )
    assert "123" in text
