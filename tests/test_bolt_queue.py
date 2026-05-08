# -*- coding: utf-8 -*-
"""Tests for bolt_queue.py (Phase L.2)."""
from __future__ import annotations

import datetime as dt_mod
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_SAMPLE_SPEC = {
    "app_name": "TrackFit",
    "tagline": "Fitness tracker",
    "features": [{"name": "Log", "description": "Log workouts", "priority": "high"}],
    "bolt_diy_prompt": "Build TrackFit...",
}


def _make_queue(tmp_dir: str):
    import app.services.bolt_queue as bq_mod
    old_dir = bq_mod._QUEUE_DIR
    bq_mod._QUEUE_DIR = Path(tmp_dir)
    bq_mod._LOG_FILE = Path(tmp_dir) / "log.jsonl"
    from app.services.bolt_queue import BoltQueue
    q = BoltQueue()
    return q, bq_mod, old_dir


def test_bolt_queue_add_returns_id():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_SPEC)
            assert isinstance(qid, str)
            assert len(qid) >= 15
        finally:
            mod._QUEUE_DIR = old


def test_bolt_queue_add_creates_file():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_SPEC)
            f = Path(tmp) / f"{qid}.json"
            assert f.exists()
        finally:
            mod._QUEUE_DIR = old


def test_bolt_queue_status_pending_on_add():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_SPEC)
            item = q.get_by_id(qid)
            assert item["status"] == "pending"
        finally:
            mod._QUEUE_DIR = old


def test_bolt_queue_get_by_id_missing_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            assert q.get_by_id("missing") is None
        finally:
            mod._QUEUE_DIR = old


def test_bolt_queue_get_pending():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid1 = q.add(_SAMPLE_SPEC)
            qid2 = q.add(_SAMPLE_SPEC)
            q.mark_completed(qid1, "http://localhost:5173")
            pending = q.get_pending()
            assert len(pending) == 1
            assert pending[0]["id"] == qid2
        finally:
            mod._QUEUE_DIR = old


def test_bolt_queue_mark_processing():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_SPEC)
            assert q.mark_processing(qid) is True
            assert q.get_by_id(qid)["status"] == "processing"
        finally:
            mod._QUEUE_DIR = old


def test_bolt_queue_mark_completed():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_SPEC)
            assert q.mark_completed(qid, "http://localhost:5173/app") is True
            item = q.get_by_id(qid)
            assert item["status"] == "completed"
            assert item["app_url"] == "http://localhost:5173/app"
            assert item["processed_at"] is not None
        finally:
            mod._QUEUE_DIR = old


def test_bolt_queue_mark_failed():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_SPEC)
            assert q.mark_failed(qid, "bolt.diy offline") is True
            item = q.get_by_id(qid)
            assert item["status"] == "failed"
            assert "bolt.diy" in item["error"]
        finally:
            mod._QUEUE_DIR = old


def test_bolt_queue_mark_nonexistent_returns_false():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            assert q.mark_completed("bad", "url") is False
            assert q.mark_failed("bad", "err") is False
            assert q.mark_processing("bad") is False
        finally:
            mod._QUEUE_DIR = old


def test_bolt_queue_status_summary():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid1 = q.add(_SAMPLE_SPEC)
            qid2 = q.add(_SAMPLE_SPEC)
            q.mark_completed(qid1, "url")
            q.mark_failed(qid2, "err")
            s = q.get_status_summary()
            assert s["completed"] == 1
            assert s["failed"] == 1
            assert s["pending"] == 0
        finally:
            mod._QUEUE_DIR = old


def test_bolt_queue_get_recent_limit():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            for _ in range(5):
                q.add(_SAMPLE_SPEC)
                time.sleep(0.01)
            recent = q.get_recent(limit=3)
            assert len(recent) == 3
        finally:
            mod._QUEUE_DIR = old


def test_bolt_queue_clear_completed_old():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_SPEC)
            q.mark_completed(qid, "url")
            item = q.get_by_id(qid)
            item["processed_at"] = (dt_mod.datetime.utcnow() - dt_mod.timedelta(days=10)).isoformat()
            from app.services.block_l_common import save_json_safe
            save_json_safe(Path(tmp) / f"{qid}.json", item)
            removed = q.clear_completed(older_than_days=7)
            assert removed == 1
        finally:
            mod._QUEUE_DIR = old


def test_bolt_queue_clear_keeps_recent():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_SPEC)
            q.mark_completed(qid, "url")
            removed = q.clear_completed(older_than_days=7)
            assert removed == 0
        finally:
            mod._QUEUE_DIR = old


def test_bolt_queue_log_written():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            q.add(_SAMPLE_SPEC)
            log_file = Path(tmp) / "log.jsonl"
            assert log_file.exists()
            assert "added" in log_file.read_text(encoding="utf-8")
        finally:
            mod._QUEUE_DIR = old
