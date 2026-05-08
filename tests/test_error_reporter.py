"""Tests for Phase 30: Error Reporter + Retry + Auto-Recovery."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.services.error_reporter as reporter_mod
from app.services.error_reporter import (
    capture,
    capture_silent,
    clear_errors,
    format_recent_errors,
    get_error_by_id,
    get_recent_errors,
    report,
    retry,
    setup,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _patch_errors_path(tmp_path):
    return patch.object(reporter_mod, "ERRORS_PATH", tmp_path / "errors.log")


def _reset_reporter():
    reporter_mod._SEND_FN = None
    reporter_mod._NOTIFY_CHAT_IDS = []


# ─── report ──────────────────────────────────────────────────────────────────

class TestReport:
    def test_returns_error_id(self, tmp_path):
        _reset_reporter()
        with _patch_errors_path(tmp_path):
            eid = report(ValueError("test error"))
        assert isinstance(eid, str)
        assert len(eid) == 12

    def test_writes_to_log(self, tmp_path):
        _reset_reporter()
        p = tmp_path / "errors.log"
        with patch.object(reporter_mod, "ERRORS_PATH", p):
            eid = report(RuntimeError("boom"), context="test context")
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["exception_type"] == "RuntimeError"
        assert "boom" in rec["exception_message"]
        assert rec["context"] == "test context"

    def test_multiple_errors_appended(self, tmp_path):
        _reset_reporter()
        p = tmp_path / "errors.log"
        with patch.object(reporter_mod, "ERRORS_PATH", p):
            report(ValueError("e1"))
            report(TypeError("e2"))
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2

    def test_notifies_user_via_send_fn(self, tmp_path):
        sent = []
        setup(lambda cid, txt: sent.append((cid, txt)), [])
        with _patch_errors_path(tmp_path):
            report(ValueError("notify me"), notify_user=True, chat_id="123")
        assert any("123" == cid for cid, _ in sent)
        _reset_reporter()

    def test_critical_notifies_all_chat_ids(self, tmp_path):
        sent = []
        setup(lambda cid, txt: sent.append((cid, txt)), ["admin1", "admin2"])
        with _patch_errors_path(tmp_path):
            report(RuntimeError("critical!"), critical=True, notify_user=False)
        notified = [cid for cid, _ in sent]
        assert "admin1" in notified
        assert "admin2" in notified
        _reset_reporter()

    def test_error_id_in_notification(self, tmp_path):
        sent = []
        setup(lambda cid, txt: sent.append(txt), [])
        with _patch_errors_path(tmp_path):
            eid = report(ValueError("oops"), notify_user=True, chat_id="42")
        assert any(eid[:8] in txt for txt in sent)
        _reset_reporter()

    def test_long_message_truncated(self, tmp_path):
        _reset_reporter()
        p = tmp_path / "errors.log"
        with patch.object(reporter_mod, "ERRORS_PATH", p):
            report(RuntimeError("x" * 1000))
        rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
        assert len(rec["exception_message"]) <= 500


# ─── capture decorator ────────────────────────────────────────────────────────

class TestCapture:
    def test_reraises_exception(self, tmp_path):
        _reset_reporter()
        with _patch_errors_path(tmp_path):
            @capture
            def failing():
                raise ValueError("captured!")

            try:
                failing()
                assert False, "Should have raised"
            except ValueError:
                pass

    def test_logs_the_exception(self, tmp_path):
        _reset_reporter()
        p = tmp_path / "errors.log"
        with patch.object(reporter_mod, "ERRORS_PATH", p):
            @capture
            def failing():
                raise TypeError("type error!")
            try:
                failing()
            except TypeError:
                pass
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert "TypeError" in lines[0]

    def test_passes_through_on_success(self, tmp_path):
        _reset_reporter()
        with _patch_errors_path(tmp_path):
            @capture
            def ok():
                return 42
            assert ok() == 42


class TestCaptureSilent:
    def test_returns_none_on_exception(self, tmp_path):
        _reset_reporter()
        with _patch_errors_path(tmp_path):
            @capture_silent
            def failing():
                raise RuntimeError("silenced")
            result = failing()
        assert result is None

    def test_still_logs(self, tmp_path):
        _reset_reporter()
        p = tmp_path / "errors.log"
        with patch.object(reporter_mod, "ERRORS_PATH", p):
            @capture_silent
            def failing():
                raise RuntimeError("silent log")
            failing()
        assert p.exists()
        assert "RuntimeError" in p.read_text(encoding="utf-8")


# ─── retry decorator ──────────────────────────────────────────────────────────

class TestRetry:
    def test_succeeds_on_first_try(self):
        calls = []

        @retry(max_attempts=3, backoff=0)
        def always_ok():
            calls.append(1)
            return "ok"

        result = always_ok()
        assert result == "ok"
        assert len(calls) == 1

    def test_retries_on_failure(self):
        calls = []

        @retry(max_attempts=3, backoff=0)
        def fail_twice():
            calls.append(1)
            if len(calls) < 3:
                raise ValueError("not yet")
            return "done"

        result = fail_twice()
        assert result == "done"
        assert len(calls) == 3

    def test_raises_after_max_attempts(self):
        calls = []

        @retry(max_attempts=2, backoff=0)
        def always_fails():
            calls.append(1)
            raise ConnectionError("network down")

        try:
            always_fails()
            assert False, "Should have raised"
        except ConnectionError:
            pass
        assert len(calls) == 2

    def test_specific_exception_filter(self):
        @retry(max_attempts=3, backoff=0, exceptions=(ConnectionError,))
        def fails_with_value_error():
            raise ValueError("not retried")

        try:
            fails_with_value_error()
            assert False, "Should have raised"
        except ValueError:
            pass  # Should not retry ValueError when only ConnectionError is caught


# ─── get_recent_errors ───────────────────────────────────────────────────────

class TestGetRecentErrors:
    def test_empty_when_no_log(self, tmp_path):
        with _patch_errors_path(tmp_path):
            result = get_recent_errors()
        assert result == []

    def test_returns_records(self, tmp_path):
        p = tmp_path / "errors.log"
        records = [{"error_id": f"e{i}", "exception_type": "ValueError",
                    "exception_message": f"err {i}",
                    "context": "", "critical": False,
                    "timestamp": "2026-05-01T10:00:00Z",
                    "traceback": ""} for i in range(5)]
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
        with patch.object(reporter_mod, "ERRORS_PATH", p):
            result = get_recent_errors(3)
        assert len(result) == 3

    def test_get_error_by_id_prefix(self, tmp_path):
        p = tmp_path / "errors.log"
        rec = {"error_id": "abc123456789", "exception_type": "TypeError",
               "exception_message": "type err",
               "context": "test", "critical": False,
               "timestamp": "2026-05-01T10:00:00Z", "traceback": ""}
        p.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        with patch.object(reporter_mod, "ERRORS_PATH", p):
            found = get_error_by_id("abc123")
        assert found is not None
        assert found["error_id"] == "abc123456789"

    def test_get_error_by_id_not_found(self, tmp_path):
        with _patch_errors_path(tmp_path):
            result = get_error_by_id("nonexistent")
        assert result is None


# ─── clear_errors ────────────────────────────────────────────────────────────

class TestClearErrors:
    def test_clears_and_returns_count(self, tmp_path):
        p = tmp_path / "errors.log"
        p.write_text('{"error_id":"e1"}\n{"error_id":"e2"}\n', encoding="utf-8")
        with patch.object(reporter_mod, "ERRORS_PATH", p):
            count = clear_errors()
        assert count == 2
        assert p.read_text(encoding="utf-8") == ""

    def test_returns_zero_if_no_log(self, tmp_path):
        with _patch_errors_path(tmp_path):
            count = clear_errors()
        assert count == 0


# ─── format_recent_errors ────────────────────────────────────────────────────

class TestFormatRecentErrors:
    def test_empty_message(self, tmp_path):
        with _patch_errors_path(tmp_path):
            text = format_recent_errors()
        assert "Нет" in text or "No" in text.lower() or "ошибок" in text.lower()

    def test_shows_error_id(self, tmp_path):
        p = tmp_path / "errors.log"
        rec = {"error_id": "abc12345", "exception_type": "ValueError",
               "exception_message": "test message",
               "context": "ctx", "critical": False,
               "timestamp": "2026-05-01T10:00:00Z", "traceback": ""}
        p.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        with patch.object(reporter_mod, "ERRORS_PATH", p):
            text = format_recent_errors()
        assert "abc12345" in text or "abc1234" in text

    def test_shows_trace_hint(self, tmp_path):
        p = tmp_path / "errors.log"
        rec = {"error_id": "e1", "exception_type": "RuntimeError",
               "exception_message": "boom", "context": "",
               "critical": False, "timestamp": "2026-05-01T10:00:00Z", "traceback": ""}
        p.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        with patch.object(reporter_mod, "ERRORS_PATH", p):
            text = format_recent_errors()
        assert "/errors trace" in text
