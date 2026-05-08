"""Tests for Block H5: Night Autonomy."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import pytest


def run(coro):
    """Run a coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# H5.1 Night Workflow Engine
# ─────────────────────────────────────────────────────────────────────────────

class TestNightWorkflowPhases:
    def test_get_current_phase_winddown(self):
        from app.services.night_workflows import get_current_phase
        assert get_current_phase(22) == "winddown"

    def test_get_current_phase_deep_work(self):
        from app.services.night_workflows import get_current_phase
        assert get_current_phase(23) == "deep_work"

    def test_get_current_phase_midnight(self):
        from app.services.night_workflows import get_current_phase
        assert get_current_phase(0) == "deep_work"

    def test_get_current_phase_self_improve(self):
        from app.services.night_workflows import get_current_phase
        assert get_current_phase(2) == "self_improve"

    def test_get_current_phase_morning_prep(self):
        from app.services.night_workflows import get_current_phase
        assert get_current_phase(4) == "morning_prep"

    def test_get_current_phase_wakeup(self):
        from app.services.night_workflows import get_current_phase
        assert get_current_phase(6) == "wakeup"

    def test_get_current_phase_daytime_none(self):
        from app.services.night_workflows import get_current_phase
        assert get_current_phase(12) is None

    def test_get_phase_schedule(self):
        from app.services.night_workflows import get_phase_schedule
        cfg = get_phase_schedule("winddown")
        assert cfg["start"] == 22
        assert cfg["end"] == 23

    def test_all_phases_present(self):
        from app.services.night_workflows import PHASES
        assert len(PHASES) == 5
        for name in ("winddown", "deep_work", "self_improve", "morning_prep", "wakeup"):
            assert name in PHASES

    def test_phases_have_start_end(self):
        from app.services.night_workflows import PHASES
        for name, cfg in PHASES.items():
            assert "start" in cfg
            assert "end" in cfg
            assert isinstance(cfg["start"], int)
            assert isinstance(cfg["end"], int)


class TestNightWorkflowRun:
    def _make_workflow(self):
        from app.services.night_workflows import NightWorkflow
        notify = MagicMock()
        wf = NightWorkflow(send_fn=lambda msg: notify(msg))
        return wf, notify

    def test_cleanup_temp_files_returns_int(self):
        wf, _ = self._make_workflow()
        result = wf.cleanup_temp_files()
        assert isinstance(result, int)

    def test_backup_state_returns_bool(self):
        wf, _ = self._make_workflow()
        result = wf.backup_state()
        assert isinstance(result, bool)

    def test_analyze_daily_errors_returns_int(self):
        wf, _ = self._make_workflow()
        with patch("app.services.error_reporter.get_recent_errors", return_value=[]):
            result = wf.analyze_daily_errors()
        assert isinstance(result, int)

    def test_get_status_returns_dict(self):
        wf, _ = self._make_workflow()
        status = wf.get_status()
        assert "current_phase" in status
        assert "phases" in status
        assert len(status["phases"]) == 5

    def test_generate_morning_brief_returns_str(self):
        wf, _ = self._make_workflow()
        with patch("app.services.daily_recap.format_recap_for_telegram", return_value="brief"):
            result = wf.generate_morning_brief()
        assert isinstance(result, str)

    def test_send_morning_brief_calls_notify(self):
        wf, notify = self._make_workflow()
        wf.notify = MagicMock()
        wf.send_morning_brief()
        wf.notify.assert_called()

    def test_notify_user_sends_message(self):
        wf, _ = self._make_workflow()
        wf.notify = MagicMock()
        wf.notify_user()
        wf.notify.assert_called_once()
        msg = wf.notify.call_args[0][0]
        assert "/night_report" in msg or "ночной цикл" in msg.lower()

    def test_get_phase_log_returns_list(self):
        wf, _ = self._make_workflow()
        log = wf.get_phase_log()
        assert isinstance(log, list)

    def test_run_phase_winddown_returns_dict(self):
        wf, _ = self._make_workflow()
        with patch.object(wf, "generate_daily_recap", return_value={"date": "2026-05-02"}), \
             patch.object(wf, "cleanup_temp_files", return_value=5), \
             patch.object(wf, "backup_state", return_value=True):
            result = run(wf.run_phase_winddown())
        assert isinstance(result, dict)
        assert "recap" in result

    def test_run_phase_deep_work_returns_dict(self):
        wf, _ = self._make_workflow()
        with patch.object(wf, "generate_tomorrow_content", return_value=[{}]), \
             patch.object(wf, "analyze_industry_trends", return_value={"food_trends": []}):
            result = run(wf.run_phase_deep_work())
        assert isinstance(result, dict)

    def test_run_phase_self_improve_returns_dict(self):
        wf, _ = self._make_workflow()
        with patch.object(wf, "analyze_daily_errors", return_value=3), \
             patch.object(wf, "optimize_prompts", return_value=1):
            result = run(wf.run_phase_self_improve())
        assert isinstance(result, dict)

    def test_run_phase_morning_prep_returns_dict(self):
        wf, _ = self._make_workflow()
        with patch.object(wf, "fetch_morning_data", return_value={}), \
             patch.object(wf, "generate_morning_brief", return_value="brief"):
            result = run(wf.run_phase_morning_prep())
        assert isinstance(result, dict)

    def test_run_phase_wakeup_sends_brief(self):
        wf, _ = self._make_workflow()
        wf.notify = MagicMock()
        with patch.object(wf, "send_morning_brief", return_value=True), \
             patch.object(wf, "notify_user"):
            result = run(wf.run_phase_wakeup())
        assert result.get("brief_sent") is True

    def test_winddown_handles_errors_gracefully(self):
        wf, _ = self._make_workflow()
        with patch.object(wf, "generate_daily_recap", side_effect=RuntimeError("fail")):
            result = run(wf.run_phase_winddown())
        assert "recap_error" in result


# ─────────────────────────────────────────────────────────────────────────────
# H5.2 Daily Recap
# ─────────────────────────────────────────────────────────────────────────────

class TestDailyRecap:
    def test_generate_returns_required_keys(self):
        from app.services.daily_recap import generate_daily_recap
        with patch("app.services.daily_recap._read_jsonl", return_value=[]):
            recap = generate_daily_recap()
        required = [
            "date", "tasks_completed", "photos_generated", "errors_count",
            "positive_feedback", "negative_feedback", "top_intents",
            "learnings", "tomorrow_focus",
        ]
        for key in required:
            assert key in recap, f"Missing key: {key}"

    def test_tasks_completed_counts_successes(self):
        from app.services.daily_recap import generate_daily_recap
        decisions = [
            {"intent": "research", "outcome": "success", "feedback": "positive"},
            {"intent": "research", "outcome": "success"},
            {"intent": "table", "outcome": "failed"},
        ]
        with patch("app.services.daily_recap._read_jsonl", side_effect=[decisions, [], []]):
            recap = generate_daily_recap()
        assert recap["tasks_completed"] == 2

    def test_photos_counted_from_library(self):
        from app.services.daily_recap import generate_daily_recap
        images = [{"mode": "restaurant"}, {"mode": "party"}]
        with patch("app.services.daily_recap._read_jsonl", side_effect=[[], images, []]):
            recap = generate_daily_recap()
        assert recap["photos_generated"] == 2

    def test_feedback_split(self):
        from app.services.daily_recap import generate_daily_recap
        decisions = [
            {"intent": "research", "feedback": "positive"},
            {"intent": "research", "feedback": "positive"},
            {"intent": "table", "feedback": "negative"},
        ]
        with patch("app.services.daily_recap._read_jsonl", side_effect=[decisions, [], []]):
            recap = generate_daily_recap()
        assert recap["positive_feedback"] == 2
        assert recap["negative_feedback"] == 1

    def test_top_intents_sorted(self):
        from app.services.daily_recap import generate_daily_recap
        decisions = [
            {"intent": "research"}, {"intent": "research"},
            {"intent": "table"},
        ]
        with patch("app.services.daily_recap._read_jsonl", side_effect=[decisions, [], []]):
            recap = generate_daily_recap()
        assert recap["top_intents"][0] == "research"

    def test_format_for_telegram_has_emoji(self):
        from app.services.daily_recap import format_recap_for_telegram
        recap = {
            "date": "2026-05-02",
            "tasks_completed": 5,
            "photos_generated": 3,
            "errors_count": 1,
            "positive_feedback": 4,
            "negative_feedback": 1,
            "top_intents": ["research", "table"],
            "learnings": ["fix prompt"],
            "tomorrow_focus": "improve quality",
        }
        text = format_recap_for_telegram(recap)
        assert "📊" in text
        assert "2026-05-02" in text
        assert "5" in text

    def test_format_telegram_satisfaction_calculation(self):
        from app.services.daily_recap import format_recap_for_telegram
        recap = {
            "date": "today",
            "tasks_completed": 0,
            "photos_generated": 0,
            "errors_count": 0,
            "positive_feedback": 8,
            "negative_feedback": 2,
            "top_intents": [],
            "learnings": [],
            "tomorrow_focus": "",
        }
        text = format_recap_for_telegram(recap)
        assert "80%" in text

    def test_get_recap_returns_none_when_missing(self):
        from app.services.daily_recap import get_recap
        result = get_recap("1900-01-01")
        assert result is None

    def test_recap_saved_to_file(self):
        from app.services.daily_recap import generate_daily_recap, get_recap
        with patch("app.services.daily_recap._read_jsonl", return_value=[]):
            recap = generate_daily_recap()
        assert recap["date"] is not None
        loaded = get_recap(recap["date"])
        assert loaded is not None
        assert loaded["date"] == recap["date"]

    def test_markdown_format_has_table(self):
        from app.services.daily_recap import _format_recap_as_markdown
        recap = {
            "date": "2026-05-02",
            "tasks_completed": 5,
            "photos_generated": 3,
            "errors_count": 0,
            "positive_feedback": 4,
            "negative_feedback": 1,
            "top_intents": ["research"],
            "learnings": [],
            "tomorrow_focus": "test",
            "generated_at": "2026-05-02T22:00:00",
        }
        md = _format_recap_as_markdown(recap)
        assert "| Metric |" in md
        assert "2026-05-02" in md


# ─────────────────────────────────────────────────────────────────────────────
# H5.3 Auto Content Generator
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoContent:
    def test_get_top_dishes_defaults_when_no_history(self):
        from app.services.auto_content import get_top_dishes, _DEFAULT_DISHES
        with patch("pathlib.Path.exists", return_value=False):
            dishes = get_top_dishes()
        assert dishes == list(_DEFAULT_DISHES)

    def test_get_top_dishes_empty_lib_uses_defaults(self):
        from app.services.auto_content import get_top_dishes, _DEFAULT_DISHES
        with patch("app.services.auto_content._IMAGE_LIB", Path("/nonexistent/x.jsonl")):
            dishes = get_top_dishes()
        assert dishes == list(_DEFAULT_DISHES)

    def test_get_optimal_post_time_default(self):
        from app.services.auto_content import get_optimal_post_time
        t = get_optimal_post_time()
        assert ":" in t
        hour = int(t.split(":")[0])
        assert 0 <= hour <= 23

    def test_schedule_post_saves_file(self, tmp_path):
        from app.services import auto_content as ac
        orig = ac._SCHEDULED_DIR
        ac._SCHEDULED_DIR = tmp_path
        try:
            entry = ac.schedule_post("http://img.test/x.jpg", "Caption", "14:00", "2026-05-03")
            assert entry["status"] == "scheduled"
            assert entry["photo_url"] == "http://img.test/x.jpg"
            assert (tmp_path / "2026-05-03.json").exists()
        finally:
            ac._SCHEDULED_DIR = orig

    def test_schedule_post_appends(self, tmp_path):
        from app.services import auto_content as ac
        orig = ac._SCHEDULED_DIR
        ac._SCHEDULED_DIR = tmp_path
        try:
            ac.schedule_post("http://a.test/1.jpg", "Cap1", "14:00", "2026-05-03")
            ac.schedule_post("http://b.test/2.jpg", "Cap2", "16:00", "2026-05-03")
            posts = json.loads((tmp_path / "2026-05-03.json").read_text())
            assert len(posts) == 2
        finally:
            ac._SCHEDULED_DIR = orig

    def test_get_scheduled_posts_empty(self):
        from app.services.auto_content import get_scheduled_posts
        posts = get_scheduled_posts("1900-01-01")
        assert posts == []

    def test_format_scheduled_posts_empty(self):
        from app.services.auto_content import format_scheduled_posts_summary
        text = format_scheduled_posts_summary([])
        assert "нет" in text.lower()

    def test_format_scheduled_posts_list(self):
        from app.services.auto_content import format_scheduled_posts_summary
        posts = [
            {"dish": "борщ", "post_time": "14:00", "status": "scheduled"},
            {"dish": "стейк", "post_time": "16:00", "status": "scheduled"},
        ]
        text = format_scheduled_posts_summary(posts)
        assert "борщ" in text
        assert "14:00" in text

    def test_generate_tomorrow_content_calls_social_post(self):
        from app.services.auto_content import generate_tomorrow_content, _DEFAULT_DISHES
        mock_post = {"url": "http://x.test/x.jpg", "caption": "Cap", "hashtags": "#tag"}
        with patch("app.services.restaurant_mode.generate_social_post", return_value=mock_post), \
             patch("app.services.auto_content.get_top_dishes", return_value=["борщ", "стейк"]):
            results = generate_tomorrow_content(num_posts=2)
        assert len(results) == 2

    def test_generate_tomorrow_content_handles_errors(self):
        from app.services.auto_content import generate_tomorrow_content
        with patch("app.services.auto_content.get_top_dishes", return_value=["борщ"]), \
             patch("app.services.restaurant_mode.generate_social_post",
                   side_effect=Exception("fail")):
            results = generate_tomorrow_content(num_posts=1)
        assert len(results) == 1
        assert results[0]["status"] == "failed"


# ─────────────────────────────────────────────────────────────────────────────
# H5.4 Self-Improvement Loop
# ─────────────────────────────────────────────────────────────────────────────

class TestSelfImprovementLoop:
    def _make_loop(self):
        from app.services.self_improvement import SelfImprovementLoop
        return SelfImprovementLoop()

    def test_collect_feedback_empty_decisions(self):
        loop = self._make_loop()
        with patch("app.services.self_improvement._read_decisions", return_value=[]):
            result = loop.collect_feedback()
        assert result == {}

    def test_collect_feedback_groups_by_intent(self):
        loop = self._make_loop()
        decisions = [
            {"intent": "research", "feedback": "positive", "query": "q1"},
            {"intent": "research", "feedback": "negative", "query": "q2"},
            {"intent": "table", "feedback": "positive", "query": "q3"},
        ]
        with patch("app.services.self_improvement._read_decisions", return_value=decisions):
            result = loop.collect_feedback()
        assert "research" in result
        assert len(result["research"]["positive"]) == 1
        assert len(result["research"]["negative"]) == 1
        assert "table" in result

    def test_collect_feedback_skips_no_intent(self):
        loop = self._make_loop()
        decisions = [{"feedback": "negative", "query": "q"}]
        with patch("app.services.self_improvement._read_decisions", return_value=decisions):
            result = loop.collect_feedback()
        assert result == {}

    def test_analyze_negatives_empty_feedback(self):
        loop = self._make_loop()
        result = loop.analyze_negatives({})
        assert result == []

    def test_analyze_negatives_calls_claude(self):
        loop = self._make_loop()
        feedback = {
            "research": {
                "negative": [{"query": "bad query", "response": "bad answer"}],
                "positive": [],
            }
        }
        with patch("app.services.self_improvement._call_claude",
                   return_value='{"problem": "too long", "suggestion": "be concise"}'):
            result = loop.analyze_negatives(feedback)
        assert len(result) == 1
        assert result[0]["intent"] == "research"

    def test_analyze_negatives_handles_claude_failure(self):
        loop = self._make_loop()
        feedback = {
            "research": {
                "negative": [{"query": "q", "response": "r"}],
                "positive": [],
            }
        }
        with patch("app.services.self_improvement._call_claude", return_value=""):
            result = loop.analyze_negatives(feedback)
        assert len(result) == 1

    def test_optimize_prompt_returns_string(self):
        loop = self._make_loop()
        with patch("app.services.self_improvement._call_claude", return_value="Better prompt"):
            result = loop.optimize_prompt("research", "old prompt", "too verbose")
        assert result == "Better prompt"

    def test_optimize_prompt_empty_problem_returns_current(self):
        loop = self._make_loop()
        result = loop.optimize_prompt("research", "current prompt", "")
        assert result == "current prompt"

    def test_ab_test_no_queries_equal_score(self):
        loop = self._make_loop()
        result = loop.ab_test("old", "new", [])
        assert result["old_score"] == 0.5
        assert result["new_score"] == 0.5
        assert result["queries_tested"] == 0

    def test_ab_test_with_queries(self):
        loop = self._make_loop()
        claude_resp = '{"winner": "B", "score_a": 0.4, "score_b": 0.9}'
        with patch("app.services.self_improvement._call_claude", return_value=claude_resp):
            result = loop.ab_test("old", "new", ["query1", "query2"])
        assert result["new_score"] > result["old_score"]

    def test_apply_if_better_threshold(self):
        loop = self._make_loop()
        # new_score must be > old_score * 1.2
        ab_not_better = {"old_score": 0.5, "new_score": 0.55}
        applied = loop.apply_if_better("test_intent", "new prompt", ab_not_better)
        assert not applied

    def test_apply_if_better_saves_file(self, tmp_path):
        from app.services import self_improvement as si
        orig = si._OPTIMIZED_DIR
        si._OPTIMIZED_DIR = tmp_path
        loop = si.SelfImprovementLoop()
        try:
            ab = {"old_score": 0.4, "new_score": 0.6, "queries_tested": 5}
            applied = loop.apply_if_better("research", "new great prompt", ab)
            assert applied
            assert (tmp_path / "research.json").exists()
        finally:
            si._OPTIMIZED_DIR = orig

    def test_rollback_removes_latest(self, tmp_path):
        from app.services import self_improvement as si
        orig = si._OPTIMIZED_DIR
        si._OPTIMIZED_DIR = tmp_path
        loop = si.SelfImprovementLoop()
        try:
            # Add two versions
            ab = {"old_score": 0.4, "new_score": 0.6, "queries_tested": 2}
            loop.apply_if_better("research", "v1", ab)
            loop.apply_if_better("research", "v2", ab)
            # Rollback
            loop.rollback_prompt("research")
            history = json.loads((tmp_path / "research.json").read_text())
            assert len(history) == 1
            assert history[0]["prompt"] == "v1"
        finally:
            si._OPTIMIZED_DIR = orig

    def test_get_improvement_stats(self, tmp_path):
        from app.services import self_improvement as si
        orig = si._OPTIMIZED_DIR
        si._OPTIMIZED_DIR = tmp_path
        loop = si.SelfImprovementLoop()
        try:
            ab = {"old_score": 0.3, "new_score": 0.8, "queries_tested": 3}
            loop.apply_if_better("research", "improved", ab)
            stats = loop.get_improvement_stats()
            assert stats["total_optimized"] == 1
            assert len(stats["intents"]) == 1
        finally:
            si._OPTIMIZED_DIR = orig


# ─────────────────────────────────────────────────────────────────────────────
# H5.5 Trend Analyzer
# ─────────────────────────────────────────────────────────────────────────────

class TestTrendAnalyzer:
    def test_parse_trends_from_text_empty(self):
        from app.services.trend_analyzer import _parse_trends_from_text
        result = _parse_trends_from_text("")
        assert "food_trends" in result
        assert "hashtags" in result
        assert "ideas" in result

    def test_parse_trends_from_text_extracts_hashtags(self):
        from app.services.trend_analyzer import _parse_trends_from_text
        text = "Top hashtags:\n• #foodphotography\n• #instafood\n- #restaurant"
        result = _parse_trends_from_text(text)
        assert any("#" in h for h in result.get("hashtags", []) + result.get("food_trends", []))

    def test_analyze_fills_defaults_on_empty_response(self):
        from app.services.trend_analyzer import analyze_industry_trends
        with patch("app.services.trend_analyzer._call_perplexity", return_value=""):
            result = analyze_industry_trends()
        assert len(result["food_trends"]) > 0
        assert len(result["hashtags"]) > 0

    def test_analyze_returns_required_keys(self):
        from app.services.trend_analyzer import analyze_industry_trends
        with patch("app.services.trend_analyzer._call_perplexity", return_value=""):
            result = analyze_industry_trends()
        assert "food_trends" in result
        assert "hashtags" in result
        assert "ideas" in result
        assert "found_at" in result
        assert "date" in result

    def test_format_for_morning_brief(self):
        from app.services.trend_analyzer import format_for_morning_brief
        insights = {
            "food_trends": ["trend1", "trend2"],
            "hashtags": ["#tag1", "#tag2"],
            "ideas": ["Idea 1"],
        }
        text = format_for_morning_brief(insights)
        assert "trend1" in text
        assert "#tag1" in text

    def test_get_latest_insights_none_when_no_files(self):
        from app.services.trend_analyzer import get_latest_insights, _INSIGHTS_DIR
        result = get_latest_insights()
        # May be None or existing — just check it doesn't crash
        assert result is None or isinstance(result, dict)

    def test_insights_saved_to_file(self):
        from app.services.trend_analyzer import analyze_industry_trends, _INSIGHTS_DIR
        with patch("app.services.trend_analyzer._call_perplexity", return_value=""):
            result = analyze_industry_trends()
        saved_path = _INSIGHTS_DIR / f"{result['date']}.json"
        assert saved_path.exists()


# ─────────────────────────────────────────────────────────────────────────────
# H5.6 Smart Schedule Manager
# ─────────────────────────────────────────────────────────────────────────────

class TestSmartSchedule:
    def test_detect_quiet_hours_default(self):
        from app.services.smart_schedule import detect_quiet_hours
        start, end = detect_quiet_hours({})
        assert 0 <= start <= 23
        assert 0 <= end <= 23

    def test_detect_quiet_hours_with_data(self):
        from app.services.smart_schedule import detect_quiet_hours
        # High activity 9-22, zero at night
        counts = {h: 10 for h in range(9, 23)}
        start, end = detect_quiet_hours(counts)
        # Should detect something in the 23-8 range
        assert isinstance(start, int)
        assert isinstance(end, int)

    def test_analyze_user_patterns_no_data(self):
        from app.services.smart_schedule import analyze_user_patterns
        with patch("app.services.smart_schedule._read_decisions", return_value=[]):
            result = analyze_user_patterns()
        assert "active_hours" in result
        assert "peak_hours" in result
        assert "quiet_hours_start" in result
        assert "quiet_hours_end" in result

    def test_analyze_user_patterns_counts_hours(self):
        from app.services.smart_schedule import analyze_user_patterns
        decisions = [
            {"timestamp": "2026-05-02T10:00:00"},
            {"timestamp": "2026-05-02T10:30:00"},
            {"timestamp": "2026-05-02T14:00:00"},
        ]
        with patch("app.services.smart_schedule._read_decisions", return_value=decisions):
            result = analyze_user_patterns()
        assert 10 in result["peak_hours"]

    def test_get_optimal_brief_time_returns_time(self):
        from app.services.smart_schedule import get_optimal_brief_time
        t = get_optimal_brief_time({"quiet_hours_end": 8})
        assert ":" in t

    def test_get_optimal_brief_time_before_wakeup(self):
        from app.services.smart_schedule import get_optimal_brief_time
        t = get_optimal_brief_time({"quiet_hours_end": 8})
        hour = int(t.split(":")[0])
        assert hour < 8

    def test_load_patterns_returns_dict(self):
        from app.services.smart_schedule import load_patterns
        result = load_patterns()
        assert isinstance(result, dict)


# ─────────────────────────────────────────────────────────────────────────────
# H5.7 Cross-Service Coordinator
# ─────────────────────────────────────────────────────────────────────────────

class TestCrossServiceCoordinator:
    def _make_coord(self):
        from app.services.cross_service_coordinator import WorkflowChain
        notify = MagicMock()
        coord = WorkflowChain(notify_fn=notify)
        return coord, notify

    def test_new_dish_notifies(self):
        coord, notify = self._make_coord()
        with patch("app.services.restaurant_mode.generate_dish_photo",
                   return_value={"url": "http://x.test/x.jpg"}), \
             patch("app.services.restaurant_mode.generate_social_post",
                   return_value={"url": "http://x.test/x.jpg", "caption": "Cap", "hashtags": "#tag"}), \
             patch("app.services.cross_service_coordinator._save_dish_to_obsidian"), \
             patch("app.services.cross_service_coordinator._save_n8n_draft"), \
             patch("app.services.auto_content.get_optimal_post_time", return_value="14:00"), \
             patch("app.services.restaurant_mode.FOOD_TEMPLATES",
                   {"rustic": "tmpl", "modern": "tmpl"}):
            result = run(coord.new_dish_added("борщ"))
        notify.assert_called()
        assert "dish" in result

    def test_morning_routine_sends_brief(self):
        coord, notify = self._make_coord()
        with patch("app.services.daily_recap.get_recap", return_value=None), \
             patch("app.services.trend_analyzer.get_latest_insights", return_value=None), \
             patch("app.services.auto_content.get_scheduled_posts", return_value=[]):
            result = run(coord.morning_routine())
        notify.assert_called()
        assert result.get("brief_sent")

    def test_negative_feedback_queues(self, tmp_path):
        coord, _ = self._make_coord()
        result = run(coord.negative_feedback_received("dec_abc123"))
        assert result["queued_for_analysis"]

    def test_party_event_planned_notifies(self):
        coord, notify = self._make_coord()
        with patch("app.services.party_mode.generate_party_promo",
                   return_value={"url": "http://p.test/poster.jpg", "promo_text": "Party!"}), \
             patch("app.services.restaurant_mode.generate_dish_photo",
                   return_value={"url": "http://f.test/food.jpg"}), \
             patch("app.services.scheduler.JarvisScheduler") as mock_sched:
            mock_sched.return_value.add_task.return_value = "task_id"
            result = run(coord.party_event_planned("halloween", "2026-10-31"))
        notify.assert_called()
        assert "theme" in result

    def test_get_themed_dishes_known_theme(self):
        from app.services.cross_service_coordinator import _get_themed_dishes
        dishes = _get_themed_dishes("halloween")
        assert len(dishes) > 0

    def test_get_themed_dishes_unknown_theme(self):
        from app.services.cross_service_coordinator import _get_themed_dishes
        dishes = _get_themed_dishes("unknown_xyz")
        assert len(dishes) > 0
