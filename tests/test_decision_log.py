"""Tests for Phase 29: Decision Log + Self-Improvement."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.services.decision_log as dlog_mod
from app.services.decision_log import (
    format_stats_message,
    get_recent_decisions,
    get_stats,
    log_decision,
    make_feedback_keyboard,
    parse_feedback_callback,
    record_feedback,
    record_feedback_by_decision_id,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _patch_path(tmp_path):
    """Context manager to redirect DECISIONS_PATH to tmp_path."""
    return patch.object(dlog_mod, "DECISIONS_PATH", tmp_path / "decisions.jsonl")


# ─── log_decision ─────────────────────────────────────────────────────────────

class TestLogDecision:
    def test_returns_decision_id(self, tmp_path):
        with _patch_path(tmp_path):
            did = log_decision("Сколько планет?", "simple_question", "claude_haiku")
        assert isinstance(did, str)
        assert len(did) == 16

    def test_file_written(self, tmp_path):
        p = tmp_path / "decisions.jsonl"
        with patch.object(dlog_mod, "DECISIONS_PATH", p):
            log_decision("test query", "research", "perplexity", execution_time_ms=500.0)
        lines = p.read_text().strip().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["intent_chosen"] == "research"
        assert rec["execution_time_ms"] == 500.0

    def test_multiple_decisions_appended(self, tmp_path):
        p = tmp_path / "decisions.jsonl"
        with patch.object(dlog_mod, "DECISIONS_PATH", p):
            log_decision("q1", "simple_question", "haiku")
            log_decision("q2", "research", "perplexity")
        lines = p.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_message_id_registered_in_map(self, tmp_path):
        dlog_mod._FEEDBACK_MAP.clear()
        with _patch_path(tmp_path):
            did = log_decision("q", "intent", "agent", message_id="msg_42")
        assert dlog_mod._FEEDBACK_MAP.get("msg_42") == did

    def test_long_query_truncated(self, tmp_path):
        p = tmp_path / "decisions.jsonl"
        long_q = "x" * 1000
        with patch.object(dlog_mod, "DECISIONS_PATH", p):
            log_decision(long_q, "simple_question", "haiku")
        rec = json.loads(p.read_text().splitlines()[0])
        assert len(rec["user_query"]) <= 500


# ─── record_feedback ─────────────────────────────────────────────────────────

class TestRecordFeedback:
    def test_positive_feedback_by_message_id(self, tmp_path):
        dlog_mod._FEEDBACK_MAP.clear()
        p = tmp_path / "decisions.jsonl"
        with patch.object(dlog_mod, "DECISIONS_PATH", p):
            did = log_decision("test", "simple_question", "haiku", message_id="m1")
            ok = record_feedback("m1", "positive")
        assert ok is True
        rec = json.loads(p.read_text().splitlines()[0])
        assert rec["user_feedback"] == "positive"

    def test_negative_feedback_by_message_id(self, tmp_path):
        dlog_mod._FEEDBACK_MAP.clear()
        p = tmp_path / "decisions.jsonl"
        with patch.object(dlog_mod, "DECISIONS_PATH", p):
            log_decision("test2", "research", "perplexity", message_id="m2")
            ok = record_feedback("m2", "negative", detail="too short answer")
        assert ok is True
        rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
        assert rec["user_feedback"] == "negative"
        assert "too short" in rec["feedback_detail"]

    def test_unknown_message_id_returns_false(self, tmp_path):
        dlog_mod._FEEDBACK_MAP.clear()
        with _patch_path(tmp_path):
            ok = record_feedback("unknown_msg", "positive")
        assert ok is False

    def test_record_feedback_by_decision_id(self, tmp_path):
        p = tmp_path / "decisions.jsonl"
        with patch.object(dlog_mod, "DECISIONS_PATH", p):
            did = log_decision("q", "research", "perplexity")
            ok = record_feedback_by_decision_id(did, "negative", detail="Wrong")
        assert ok is True
        rec = json.loads(p.read_text().splitlines()[0])
        assert rec["user_feedback"] == "negative"


# ─── get_stats ───────────────────────────────────────────────────────────────

class TestGetStats:
    def test_empty_returns_zero(self, tmp_path):
        with _patch_path(tmp_path):
            stats = get_stats()
        assert stats["total"] == 0
        assert stats["success_rate"] == 0.0

    def test_success_rate_calculation(self, tmp_path):
        p = tmp_path / "decisions.jsonl"
        records = [
            {"decision_id": f"d{i}", "intent_chosen": "research",
             "outcome": "success" if i < 8 else "error",
             "user_feedback": None, "cost_usd": 0.01, "execution_time_ms": 100.0}
            for i in range(10)
        ]
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        with patch.object(dlog_mod, "DECISIONS_PATH", p):
            stats = get_stats(10)
        assert stats["total"] == 10
        assert stats["success_rate"] == 80.0

    def test_intent_distribution(self, tmp_path):
        p = tmp_path / "decisions.jsonl"
        records = [
            {"decision_id": "d1", "intent_chosen": "simple_question",
             "outcome": "success", "user_feedback": None, "cost_usd": 0.0, "execution_time_ms": 0.0},
            {"decision_id": "d2", "intent_chosen": "research",
             "outcome": "success", "user_feedback": None, "cost_usd": 0.0, "execution_time_ms": 0.0},
            {"decision_id": "d3", "intent_chosen": "simple_question",
             "outcome": "success", "user_feedback": None, "cost_usd": 0.0, "execution_time_ms": 0.0},
        ]
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        with patch.object(dlog_mod, "DECISIONS_PATH", p):
            stats = get_stats()
        assert stats["intents"]["simple_question"] == 2
        assert stats["intents"]["research"] == 1

    def test_feedback_counts(self, tmp_path):
        p = tmp_path / "decisions.jsonl"
        records = [
            {"decision_id": "d1", "intent_chosen": "research", "outcome": "success",
             "user_feedback": "positive", "cost_usd": 0.0, "execution_time_ms": 0.0},
            {"decision_id": "d2", "intent_chosen": "research", "outcome": "success",
             "user_feedback": "negative", "cost_usd": 0.0, "execution_time_ms": 0.0},
            {"decision_id": "d3", "intent_chosen": "research", "outcome": "success",
             "user_feedback": None, "cost_usd": 0.0, "execution_time_ms": 0.0},
        ]
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        with patch.object(dlog_mod, "DECISIONS_PATH", p):
            stats = get_stats()
        assert stats["positive_feedback"] == 1
        assert stats["negative_feedback"] == 1


# ─── feedback keyboard ───────────────────────────────────────────────────────

class TestFeedbackKeyboard:
    def test_keyboard_structure(self):
        kb = make_feedback_keyboard("abc123")
        assert "inline_keyboard" in kb
        row = kb["inline_keyboard"][0]
        assert len(row) == 2
        assert "👍" in row[0]["text"]
        assert "👎" in row[1]["text"]

    def test_callback_data_contains_id(self):
        kb = make_feedback_keyboard("myid99")
        row = kb["inline_keyboard"][0]
        assert "myid99" in row[0]["callback_data"]
        assert "myid99" in row[1]["callback_data"]

    def test_parse_positive(self):
        result = parse_feedback_callback("feedback:positive:abc123")
        assert result == {"type": "positive", "decision_id": "abc123"}

    def test_parse_negative(self):
        result = parse_feedback_callback("feedback:negative:xyz789")
        assert result == {"type": "negative", "decision_id": "xyz789"}

    def test_parse_invalid(self):
        assert parse_feedback_callback("something:else") is None
        assert parse_feedback_callback("feedback:only") is None
        assert parse_feedback_callback("") is None


# ─── format_stats_message ────────────────────────────────────────────────────

class TestFormatStatsMessage:
    def test_empty_data_message(self, tmp_path):
        with _patch_path(tmp_path):
            msg = format_stats_message()
        assert "Нет данных" in msg

    def test_contains_success_rate(self, tmp_path):
        p = tmp_path / "decisions.jsonl"
        records = [{"decision_id": f"d{i}", "intent_chosen": "research",
                    "outcome": "success", "user_feedback": None,
                    "cost_usd": 0.01, "execution_time_ms": 200.0}
                   for i in range(5)]
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        with patch.object(dlog_mod, "DECISIONS_PATH", p):
            msg = format_stats_message()
        assert "100.0%" in msg or "success" in msg.lower()

    def test_contains_improve_hint(self, tmp_path):
        p = tmp_path / "decisions.jsonl"
        p.write_text(json.dumps({"decision_id": "d1", "intent_chosen": "research",
                                  "outcome": "success", "user_feedback": None,
                                  "cost_usd": 0.0, "execution_time_ms": 0.0}) + "\n")
        with patch.object(dlog_mod, "DECISIONS_PATH", p):
            msg = format_stats_message()
        assert "/improve" in msg


# ─── analyze_decisions ───────────────────────────────────────────────────────

class TestAnalyzeDecisions:
    def test_no_api_key_returns_message(self, tmp_path):
        with _patch_path(tmp_path):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
                result = dlog_mod.analyze_decisions()
        assert "не задан" in result or "API" in result

    def test_no_data_returns_message(self, tmp_path):
        with _patch_path(tmp_path):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}):
                result = dlog_mod.analyze_decisions()
        assert "данных" in result or "Нет" in result

    def test_calls_claude_with_decisions(self, tmp_path):
        p = tmp_path / "decisions.jsonl"
        records = [{"decision_id": f"d{i}", "intent_chosen": "research",
                    "outcome": "success", "user_feedback": "negative",
                    "cost_usd": 0.0, "execution_time_ms": 0.0,
                    "user_query": f"query {i}"}
                   for i in range(3)]
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")

        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = [MagicMock(text="Рекомендации: улучшить routing")]
        mock_client.messages.create.return_value = mock_resp

        with patch.object(dlog_mod, "DECISIONS_PATH", p):
            with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}):
                with patch("anthropic.Anthropic", return_value=mock_client):
                    result = dlog_mod.analyze_decisions()

        assert "Рекомендации" in result
        mock_client.messages.create.assert_called_once()
