"""Phase 43: Analytics service tests."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.analytics import (
    get_decisions_timeseries,
    get_intent_distribution,
    get_success_rate_over_time,
    estimate_costs,
    get_performance_metrics,
    _iter_decisions,
)
import app.services.analytics as analytics_mod


def _make_decisions(tmp_path: Path, records: list) -> None:
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    lines = [json.dumps(r) for r in records]
    (state / "decisions.jsonl").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# _iter_decisions
# ---------------------------------------------------------------------------

class TestIterDecisions:
    def test_returns_empty_when_no_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(analytics_mod, "_ROOT", tmp_path)
        assert _iter_decisions(7) == []

    def test_filters_old_records(self, monkeypatch, tmp_path):
        monkeypatch.setattr(analytics_mod, "_ROOT", tmp_path)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        old = "2020-01-01T00:00:00Z"
        _make_decisions(tmp_path, [
            {"timestamp": today, "intent_chosen": "research"},
            {"timestamp": old, "intent_chosen": "brain"},
        ])
        result = _iter_decisions(7)
        assert len(result) == 1
        assert result[0]["intent_chosen"] == "research"

    def test_handles_malformed_lines(self, monkeypatch, tmp_path):
        monkeypatch.setattr(analytics_mod, "_ROOT", tmp_path)
        state = tmp_path / "state"
        state.mkdir()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        (state / "decisions.jsonl").write_text(
            f"not-json\n{json.dumps({'timestamp': today, 'intent_chosen': 'research'})}\n",
            encoding="utf-8"
        )
        result = _iter_decisions(7)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# get_decisions_timeseries
# ---------------------------------------------------------------------------

class TestDecisionsTimeseries:
    def test_returns_n_days(self, monkeypatch, tmp_path):
        monkeypatch.setattr(analytics_mod, "_ROOT", tmp_path)
        (tmp_path / "state").mkdir()
        result = get_decisions_timeseries(7)
        assert len(result) == 7
        for row in result:
            assert "date" in row
            assert "count" in row

    def test_counts_correctly(self, monkeypatch, tmp_path):
        monkeypatch.setattr(analytics_mod, "_ROOT", tmp_path)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _make_decisions(tmp_path, [
            {"timestamp": f"{today}T10:00:00Z", "intent_chosen": "research"},
            {"timestamp": f"{today}T11:00:00Z", "intent_chosen": "brain"},
        ])
        result = get_decisions_timeseries(7)
        today_row = next(r for r in result if r["date"] == today)
        assert today_row["count"] == 2

    def test_dates_are_sorted_ascending(self, monkeypatch, tmp_path):
        monkeypatch.setattr(analytics_mod, "_ROOT", tmp_path)
        (tmp_path / "state").mkdir()
        result = get_decisions_timeseries(7)
        dates = [r["date"] for r in result]
        assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# get_intent_distribution
# ---------------------------------------------------------------------------

class TestIntentDistribution:
    def test_counts_intents(self, monkeypatch, tmp_path):
        monkeypatch.setattr(analytics_mod, "_ROOT", tmp_path)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _make_decisions(tmp_path, [
            {"timestamp": today, "intent_chosen": "research"},
            {"timestamp": today, "intent_chosen": "research"},
            {"timestamp": today, "intent_chosen": "brain"},
        ])
        dist = get_intent_distribution(7)
        assert dist["research"] == 2
        assert dist["brain"] == 1

    def test_empty_returns_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(analytics_mod, "_ROOT", tmp_path)
        (tmp_path / "state").mkdir()
        assert get_intent_distribution(7) == {}


# ---------------------------------------------------------------------------
# estimate_costs
# ---------------------------------------------------------------------------

class TestEstimateCosts:
    def test_returns_required_keys(self, monkeypatch, tmp_path):
        monkeypatch.setattr(analytics_mod, "_ROOT", tmp_path)
        (tmp_path / "state").mkdir()
        result = estimate_costs(7)
        assert "total_usd" in result
        assert "breakdown" in result
        assert "total_calls" in result

    def test_positive_cost_for_research(self, monkeypatch, tmp_path):
        monkeypatch.setattr(analytics_mod, "_ROOT", tmp_path)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _make_decisions(tmp_path, [
            {"timestamp": today, "intent_chosen": "research"},
        ])
        result = estimate_costs(7)
        assert result["total_usd"] > 0
        assert result["breakdown"].get("research", 0) > 0


# ---------------------------------------------------------------------------
# get_performance_metrics
# ---------------------------------------------------------------------------

class TestPerformanceMetrics:
    def test_returns_dict(self, monkeypatch, tmp_path):
        monkeypatch.setattr(analytics_mod, "_ROOT", tmp_path)
        (tmp_path / "state").mkdir()
        result = get_performance_metrics()
        assert isinstance(result, dict)

    def test_computes_average_latency(self, monkeypatch, tmp_path):
        monkeypatch.setattr(analytics_mod, "_ROOT", tmp_path)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _make_decisions(tmp_path, [
            {"timestamp": today, "intent_chosen": "research", "execution_time_ms": 1000},
            {"timestamp": today, "intent_chosen": "research", "execution_time_ms": 2000},
        ])
        result = get_performance_metrics()
        assert result.get("research") == 1500.0


# ---------------------------------------------------------------------------
# Analytics endpoint in dashboard router
# ---------------------------------------------------------------------------

class TestAnalyticsEndpoints:
    def test_analytics_route_registered(self):
        from app.routers.jarvis_dashboard_router import router
        paths = [r.path for r in router.routes]
        assert "/dashboard/api/analytics" in paths

    def test_export_route_registered(self):
        from app.routers.jarvis_dashboard_router import router
        paths = [r.path for r in router.routes]
        assert "/dashboard/api/export" in paths
