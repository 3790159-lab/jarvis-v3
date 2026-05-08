# -*- coding: utf-8 -*-
"""Tests for figma_queue.py (Phase L.1)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_SAMPLE_BRIEF = {
    "project_name": "Test Project",
    "project_type": "landing",
    "design_style": "modern",
    "color_palette": {"primary": "#2563EB"},
    "components": [{"name": "Header", "content": "Nav", "type": "navigation"}],
}


def _make_queue(tmp_dir: str):
    """Create a FigmaQueue with overridden _QUEUE_DIR pointing to tmp_dir."""
    import app.services.figma_queue as fq_mod
    old_dir = fq_mod._QUEUE_DIR
    fq_mod._QUEUE_DIR = Path(tmp_dir)
    fq_mod._LOG_FILE = Path(tmp_dir) / "log.jsonl"
    from app.services.figma_queue import FigmaQueue
    q = FigmaQueue()
    return q, fq_mod, old_dir


def test_figma_queue_add_returns_id():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_BRIEF)
            assert isinstance(qid, str)
            assert len(qid) >= 15  # at least YYYYMMDD_HHMMSS
        finally:
            mod._QUEUE_DIR = old


def test_figma_queue_add_creates_file():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_BRIEF)
            f = Path(tmp) / f"{qid}.json"
            assert f.exists()
        finally:
            mod._QUEUE_DIR = old


def test_figma_queue_add_status_pending():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_BRIEF)
            item = q.get_by_id(qid)
            assert item["status"] == "pending"
        finally:
            mod._QUEUE_DIR = old


def test_figma_queue_get_by_id():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_BRIEF)
            item = q.get_by_id(qid)
            assert item is not None
            assert item["id"] == qid
        finally:
            mod._QUEUE_DIR = old


def test_figma_queue_get_by_id_missing_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            result = q.get_by_id("nonexistent_id")
            assert result is None
        finally:
            mod._QUEUE_DIR = old


def test_figma_queue_get_pending_returns_pending_only():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid1 = q.add(_SAMPLE_BRIEF)
            qid2 = q.add(_SAMPLE_BRIEF)
            q.mark_completed(qid1, "http://figma.com/file/test")
            pending = q.get_pending()
            assert len(pending) == 1
            assert pending[0]["id"] == qid2
        finally:
            mod._QUEUE_DIR = old


def test_figma_queue_mark_processing():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_BRIEF)
            result = q.mark_processing(qid)
            assert result is True
            item = q.get_by_id(qid)
            assert item["status"] == "processing"
        finally:
            mod._QUEUE_DIR = old


def test_figma_queue_mark_completed():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_BRIEF)
            result = q.mark_completed(qid, "http://figma.com/file/abc123")
            assert result is True
            item = q.get_by_id(qid)
            assert item["status"] == "completed"
            assert item["figma_url"] == "http://figma.com/file/abc123"
            assert item["processed_at"] is not None
        finally:
            mod._QUEUE_DIR = old


def test_figma_queue_mark_failed():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_BRIEF)
            result = q.mark_failed(qid, "MCP not connected")
            assert result is True
            item = q.get_by_id(qid)
            assert item["status"] == "failed"
            assert "MCP" in item["error"]
        finally:
            mod._QUEUE_DIR = old


def test_figma_queue_mark_nonexistent_returns_false():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            assert q.mark_completed("bad_id", "url") is False
            assert q.mark_failed("bad_id", "err") is False
            assert q.mark_processing("bad_id") is False
        finally:
            mod._QUEUE_DIR = old


def test_figma_queue_get_status_summary():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid1 = q.add(_SAMPLE_BRIEF)
            qid2 = q.add(_SAMPLE_BRIEF)
            q.mark_completed(qid1, "url1")
            summary = q.get_status_summary()
            assert summary["pending"] == 1
            assert summary["completed"] == 1
        finally:
            mod._QUEUE_DIR = old


def test_figma_queue_get_recent():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            for _ in range(3):
                q.add(_SAMPLE_BRIEF)
                time.sleep(0.01)
            recent = q.get_recent(limit=2)
            assert len(recent) == 2
        finally:
            mod._QUEUE_DIR = old


def test_figma_queue_clear_completed_removes_old():
    import datetime as dt_mod
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_BRIEF)
            q.mark_completed(qid, "url")
            # Backdate processed_at
            item = q.get_by_id(qid)
            old_date = (dt_mod.datetime.utcnow() - dt_mod.timedelta(days=10)).isoformat()
            item["processed_at"] = old_date
            from app.services.block_l_common import save_json_safe
            save_json_safe(Path(tmp) / f"{qid}.json", item)

            removed = q.clear_completed(older_than_days=7)
            assert removed == 1
            assert not (Path(tmp) / f"{qid}.json").exists()
        finally:
            mod._QUEUE_DIR = old


def test_figma_queue_clear_keeps_recent():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_BRIEF)
            q.mark_completed(qid, "url")
            removed = q.clear_completed(older_than_days=7)
            assert removed == 0  # Just completed, within 7 days
        finally:
            mod._QUEUE_DIR = old


def test_figma_queue_log_file_created():
    with tempfile.TemporaryDirectory() as tmp:
        q, mod, old = _make_queue(tmp)
        try:
            qid = q.add(_SAMPLE_BRIEF)
            log_file = Path(tmp) / "log.jsonl"
            assert log_file.exists()
            content = log_file.read_text(encoding="utf-8")
            assert "added" in content
        finally:
            mod._QUEUE_DIR = old
