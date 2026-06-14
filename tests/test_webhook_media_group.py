# -*- coding: utf-8 -*-
"""Webhook reader media_group handling — persistent buffer + stale-group flush.

Latent bug (Day 8): ``webhook_reader_thread`` called ``process_update(update)``
with NO shared buffer (a fresh ``{}`` per call) and never flushed stale groups.
So an album's photos each landed in their own throwaway buffer and were never
flushed → total loss on the webhook path. The poll loop never had this bug — it
owns a persistent buffer and flushes groups older than 2s.

This module:
  * characterizes the current root cause (per-call buffer never flushes albums);
  * locks the target contract of the two extracted helpers — a persistent
    buffer accumulates (and dedupes) an album across drains, and the stale-group
    flush drains it exactly once after the 2s timeout.

Same fresh-module-load harness as tests/test_process_update_port.py — all
Telegram / handler / network seams are mocked; no bot token, no HTTP, no pods.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


ALLOWED_CHAT = 12345


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", str(ALLOWED_CHAT))
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("JARVIS_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "0")
    monkeypatch.setenv("JARVIS_VOICE_REPLY_ENABLED", "0")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", str(ALLOWED_CHAT))


def _get_mod():
    mod_name = f"_test_webhook_mg_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _album_update(chat_id, gid, file_unique_id, file_id, update_id=1):
    return {
        "update_id": update_id,
        "message": {
            "from": {"id": chat_id, "username": "tester"},
            "chat": {"id": chat_id},
            "media_group_id": gid,
            "photo": [
                {"file_id": file_id, "file_unique_id": file_unique_id, "file_size": 1000}
            ],
        },
    }


def _write_queue(tmp_path, updates):
    qf = tmp_path / "webhook_queue.jsonl"
    qf.write_text("\n".join(json.dumps(u) for u in updates) + "\n", encoding="utf-8")
    return qf


# ── characterization: the current per-call-buffer wiring loses albums ────────


def test_current_per_call_buffer_never_flushes_albums(monkeypatch):
    """Root cause: today's webhook loop calls process_update WITHOUT a shared
    buffer, so each album photo lands in its own throwaway default buffer and is
    never flushed. Documents the latent total-loss bug; stays true as a property
    of process_update's default-arg behaviour."""
    mod = _get_mod()
    monkeypatch.setattr(mod, "_persona_video_intercept", lambda *a, **k: False)
    flushed = MagicMock()
    monkeypatch.setattr(mod, "_flush_media_group", flushed)
    monkeypatch.setattr(mod, "_handle_file_message", MagicMock())
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    # Mimic today's wiring exactly: no shared buffer (fresh {} per call).
    mod.process_update(_album_update(ALLOWED_CHAT, "mg1", "U_A", "f1"))
    mod.process_update(_album_update(ALLOWED_CHAT, "mg1", "U_B", "f2"))

    flushed.assert_not_called()  # album silently lost — never flushed


# ── target: stale-group flush ───────────────────────────────────────────────


def test_webhook_flush_stale_groups_flushes_after_timeout(monkeypatch):
    mod = _get_mod()
    flushed: list = []
    monkeypatch.setattr(
        mod, "_flush_media_group",
        lambda buf, gid: (flushed.append(gid), buf.pop(gid)),
    )
    buffer = {"mg1": {"msgs": [{}], "seen_uids": set(), "last_seen": 100.0}}

    mod._webhook_flush_stale_groups(buffer, now=103.0)  # 3s elapsed >= 2s

    assert flushed == ["mg1"]
    assert "mg1" not in buffer


def test_webhook_flush_keeps_fresh_groups(monkeypatch):
    mod = _get_mod()
    flushed: list = []
    monkeypatch.setattr(mod, "_flush_media_group", lambda buf, gid: flushed.append(gid))
    buffer = {"mg1": {"msgs": [{}], "seen_uids": set(), "last_seen": 100.0}}

    mod._webhook_flush_stale_groups(buffer, now=101.0)  # 1s elapsed < 2s

    assert flushed == []
    assert "mg1" in buffer  # still buffering — more parts may arrive


# ── target: drain accumulates + dedupes into the persistent buffer ──────────


def test_webhook_drain_accumulates_album_in_persistent_buffer(monkeypatch, tmp_path):
    mod = _get_mod()
    monkeypatch.setattr(mod, "_persona_video_intercept", lambda *a, **k: False)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    qf = _write_queue(tmp_path, [
        _album_update(ALLOWED_CHAT, "mg1", "U_A", "f1", update_id=1),
        _album_update(ALLOWED_CHAT, "mg1", "U_B", "f2", update_id=2),
    ])
    buffer: dict = {}
    with qf.open("r", encoding="utf-8") as f:
        mod._webhook_drain_new_updates(f, 0, buffer)

    assert len(buffer["mg1"]["msgs"]) == 2
    assert "seen_uids" in buffer["mg1"]  # used _buffer_media_group_msg


def test_webhook_drain_advances_offset_and_skips_processed(monkeypatch, tmp_path):
    """Offset semantics preserved: a second drain from the returned offset only
    processes newly-appended lines (no reprocessing)."""
    mod = _get_mod()
    seen: list = []
    monkeypatch.setattr(
        mod, "process_update",
        lambda upd, buf=None: seen.append(upd.get("update_id")),
    )
    qf = _write_queue(tmp_path, [{"update_id": 1, "message": {}}])
    buffer: dict = {}
    with qf.open("r", encoding="utf-8") as f:
        off = mod._webhook_drain_new_updates(f, 0, buffer)
    with qf.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"update_id": 2, "message": {}}) + "\n")
    with qf.open("r", encoding="utf-8") as f:
        mod._webhook_drain_new_updates(f, off, buffer)

    assert seen == [1, 2]


# ── target: end-to-end — album accumulates across a drain, flushes once ──────


def test_webhook_album_accumulates_then_flushes_once(monkeypatch, tmp_path):
    """3 album photos (one a duplicate by file_unique_id) accumulate into the
    persistent buffer, dedupe to 2, and flush in a single album call after the
    stale timeout — the webhook path now matches the poll loop."""
    mod = _get_mod()
    monkeypatch.setattr(mod, "_persona_video_intercept", lambda *a, **k: False)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    album_calls: list = []
    monkeypatch.setattr(
        mod, "_swapbatch_album_intercept",
        lambda cid, msgs: (album_calls.append((cid, list(msgs))) or True),
    )

    qf = _write_queue(tmp_path, [
        _album_update(ALLOWED_CHAT, "mg1", "U_A", "f1", update_id=1),
        _album_update(ALLOWED_CHAT, "mg1", "U_B", "f2", update_id=2),
        _album_update(ALLOWED_CHAT, "mg1", "U_A", "f3", update_id=3),  # dup uid
    ])
    buffer: dict = {}
    with qf.open("r", encoding="utf-8") as f:
        mod._webhook_drain_new_updates(f, 0, buffer)

    # Accumulated + deduped, but not yet flushed (still fresh).
    assert len(buffer["mg1"]["msgs"]) == 2
    assert album_calls == []

    # 3s later the group is stale → exactly one flush of both unique photos.
    mod._webhook_flush_stale_groups(buffer, now=buffer["mg1"]["last_seen"] + 3.0)

    assert len(album_calls) == 1
    assert album_calls[0][0] == str(ALLOWED_CHAT)
    assert len(album_calls[0][1]) == 2
    assert "mg1" not in buffer  # popped — no re-flush
