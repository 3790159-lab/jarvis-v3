# -*- coding: utf-8 -*-
"""Pure-logic tests for the evening error-digest (Master-Plan: /suggest_tasks'
log-signal cousin). $0, no network, no filesystem I/O -- everything here takes
plain line lists, mirroring tests/test_garbage_cleanup.py's pure-classifier
style."""
from __future__ import annotations

from datetime import datetime

import pytest

from app.services import error_digest as ed


def _line(ts="2026-07-13 20:00:00", level="ERROR", logger="app.services.foo", msg="boom"):
    return f"{ts} | {level:<8}| {logger:<30}| {msg}"


# ─── parse_log_line ─────────────────────────────────────────────────────────

class TestParseLogLine:
    def test_parses_well_formed_line(self):
        rec = ed.parse_log_line(_line())
        assert rec == {
            "timestamp": datetime(2026, 7, 13, 20, 0, 0),
            "level": "ERROR",
            "logger": "app.services.foo",
            "message": "boom",
        }

    def test_parses_critical_level_no_padding(self):
        rec = ed.parse_log_line(_line(level="CRITICAL", msg="disk full"))
        assert rec["level"] == "CRITICAL"
        assert rec["message"] == "disk full"

    def test_returns_none_for_traceback_continuation_line(self):
        assert ed.parse_log_line("  File \"app/main.py\", line 12, in <module>") is None

    def test_returns_none_for_blank_line(self):
        assert ed.parse_log_line("") is None

    def test_returns_none_for_garbage_date(self):
        assert ed.parse_log_line("not-a-date | ERROR | x | y") is None

    def test_message_with_pipe_characters_kept_whole(self):
        rec = ed.parse_log_line(_line(msg="a | b | c"))
        assert rec["message"] == "a | b | c"


# ─── noise_patterns_from_env ────────────────────────────────────────────────

class TestNoisePatternsFromEnv:
    def test_empty_when_unset(self):
        assert ed.noise_patterns_from_env({}) == []

    def test_splits_csv_and_strips(self):
        env = {"ERROR_DIGEST_NOISE_PATTERNS": " Connection reset , known flaky ,, timeout "}
        assert ed.noise_patterns_from_env(env) == ["Connection reset", "known flaky", "timeout"]


# ─── filter_known_noise ─────────────────────────────────────────────────────

class TestFilterKnownNoise:
    def test_noop_with_no_patterns(self):
        records = [{"level": "ERROR", "logger": "x", "message": "boom"}]
        assert ed.filter_known_noise(records, []) == records

    def test_drops_case_insensitive_substring_match(self):
        records = [
            {"level": "ERROR", "logger": "x", "message": "Connection reset by peer"},
            {"level": "ERROR", "logger": "x", "message": "real bug here"},
        ]
        out = ed.filter_known_noise(records, ["connection reset"])
        assert out == [{"level": "ERROR", "logger": "x", "message": "real bug here"}]


# ─── select_error_records ───────────────────────────────────────────────────

class TestSelectErrorRecords:
    NOW = datetime(2026, 7, 13, 21, 0, 0)

    def test_keeps_error_and_critical_only(self):
        lines = [
            _line(level="ERROR", msg="e1"),
            _line(level="CRITICAL", msg="c1"),
            _line(level="WARNING", msg="w1"),
            _line(level="INFO", msg="i1"),
        ]
        out = ed.select_error_records(lines, self.NOW)
        assert {r["message"] for r in out} == {"e1", "c1"}

    def test_drops_lines_older_than_window(self):
        lines = [
            _line(ts="2026-07-12 20:00:00", msg="old"),  # 25h before NOW
            _line(ts="2026-07-13 20:00:00", msg="fresh"),  # 1h before NOW
        ]
        out = ed.select_error_records(lines, self.NOW, window_hours=24)
        assert [r["message"] for r in out] == ["fresh"]

    def test_drops_future_dated_lines_clock_skew(self):
        lines = [_line(ts="2026-07-14 05:00:00", msg="future")]
        out = ed.select_error_records(lines, self.NOW)
        assert out == []

    def test_drops_unparseable_lines_silently(self):
        lines = [_line(msg="ok"), "garbage traceback frame"]
        out = ed.select_error_records(lines, self.NOW)
        assert [r["message"] for r in out] == ["ok"]

    def test_empty_input_returns_empty(self):
        assert ed.select_error_records([], self.NOW) == []
        assert ed.select_error_records(None, self.NOW) == []


# ─── dedupe_error_records ───────────────────────────────────────────────────

class TestDedupeErrorRecords:
    def _rec(self, msg, logger="x", level="ERROR"):
        return {"level": level, "logger": logger, "message": msg}

    def test_counts_exact_repeats(self):
        records = [self._rec("boom"), self._rec("boom"), self._rec("other")]
        out = ed.dedupe_error_records(records)
        by_msg = {r["message"]: r["count"] for r in out}
        assert by_msg == {"boom": 2, "other": 1}

    def test_preserves_first_seen_order(self):
        records = [self._rec("b"), self._rec("a"), self._rec("b")]
        out = ed.dedupe_error_records(records)
        assert [r["message"] for r in out] == ["b", "a"]

    def test_different_logger_same_message_not_merged(self):
        records = [self._rec("boom", logger="a"), self._rec("boom", logger="b")]
        out = ed.dedupe_error_records(records)
        assert len(out) == 2

    def test_empty_input(self):
        assert ed.dedupe_error_records([]) == []


# ─── top_records ─────────────────────────────────────────────────────────────

class TestTopRecords:
    def test_sorts_by_count_desc(self):
        records = [
            {"message": "low", "count": 1},
            {"message": "high", "count": 9},
            {"message": "mid", "count": 3},
        ]
        out = ed.top_records(records)
        assert [r["message"] for r in out] == ["high", "mid", "low"]

    def test_limits_to_n(self):
        records = [{"message": str(i), "count": i} for i in range(10)]
        out = ed.top_records(records, n=5)
        assert len(out) == 5

    def test_empty_input(self):
        assert ed.top_records([]) == []


# ─── build_digest_report ────────────────────────────────────────────────────

class TestBuildDigestReport:
    NOW = datetime(2026, 7, 13, 21, 0, 0)

    def test_combines_bot_and_backend_lines(self):
        bot = [_line(msg="bot-error")]
        backend = [_line(msg="backend-error")]
        out = ed.build_digest_report(bot_lines=bot, backend_lines=backend, now=self.NOW,
                                      noise_patterns=[])
        assert {r["message"] for r in out} == {"bot-error", "backend-error"}

    def test_empty_when_nothing_survives(self):
        out = ed.build_digest_report(bot_lines=[], backend_lines=[], now=self.NOW,
                                      noise_patterns=[])
        assert out == []

    def test_applies_noise_patterns(self):
        bot = [_line(msg="known flaky retry"), _line(msg="real bug")]
        out = ed.build_digest_report(bot_lines=bot, backend_lines=[], now=self.NOW,
                                      noise_patterns=["known flaky"])
        assert [r["message"] for r in out] == ["real bug"]

    def test_dedupes_and_counts_across_both_logs(self):
        bot = [_line(msg="dup")]
        backend = [_line(msg="dup")]
        out = ed.build_digest_report(bot_lines=bot, backend_lines=backend, now=self.NOW,
                                      noise_patterns=[])
        assert out == [{"level": "ERROR", "logger": "app.services.foo", "message": "dup", "count": 2}]

    def test_limits_to_top_n(self):
        bot = [_line(msg=f"e{i}") for i in range(7)]
        out = ed.build_digest_report(bot_lines=bot, backend_lines=[], now=self.NOW,
                                      noise_patterns=[], top_n=5)
        assert len(out) == 5

    def test_defaults_noise_patterns_from_env_when_omitted(self, monkeypatch):
        monkeypatch.setenv("ERROR_DIGEST_NOISE_PATTERNS", "real bug")
        bot = [_line(msg="real bug"), _line(msg="genuine issue")]
        out = ed.build_digest_report(bot_lines=bot, backend_lines=[], now=self.NOW)
        assert [r["message"] for r in out] == ["genuine issue"]
        monkeypatch.delenv("ERROR_DIGEST_NOISE_PATTERNS", raising=False)


# ─── format_digest_message ──────────────────────────────────────────────────

class TestFormatDigestMessage:
    def test_includes_count_suffix_for_repeats(self):
        records = [{"level": "ERROR", "logger": "x", "message": "boom", "count": 3}]
        text = ed.format_digest_message(records)
        assert "boom" in text
        assert "(x3)" in text

    def test_no_suffix_for_singletons(self):
        records = [{"level": "ERROR", "logger": "x", "message": "boom", "count": 1}]
        text = ed.format_digest_message(records)
        assert "(x1)" not in text
        assert "(x" not in text

    def test_numbers_entries(self):
        records = [
            {"level": "ERROR", "logger": "x", "message": "a", "count": 1},
            {"level": "CRITICAL", "logger": "y", "message": "b", "count": 1},
        ]
        text = ed.format_digest_message(records)
        assert "1." in text and "2." in text
