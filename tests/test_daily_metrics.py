# -*- coding: utf-8 -*-
"""Tests for :mod:`app.services.daily_metrics` — yesterday-vs-today IG snapshot.

State is redirected to ``tmp_path`` via ``DAILY_METRICS_FILE`` so no real
``state/daily_metrics.json`` is touched.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import daily_metrics


@pytest.fixture
def metrics_env(tmp_path, monkeypatch):
    state_file = tmp_path / "daily_metrics.json"
    monkeypatch.setenv("DAILY_METRICS_FILE", str(state_file))
    return state_file


def test_first_run_has_no_baseline_delta(metrics_env):
    delta = daily_metrics.record_and_diff("jtest_lab_", 100, 20)
    assert delta == {"followers_delta": None, "media_delta": None}


def test_first_run_persists_snapshot(metrics_env):
    daily_metrics.record_and_diff("jtest_lab_", 100, 20)
    state = json.loads(metrics_env.read_text(encoding="utf-8"))
    assert state["accounts"]["jtest_lab_"] == {"followers": 100, "media_count": 20}


def test_second_run_computes_delta_against_prior_snapshot(metrics_env):
    daily_metrics.record_and_diff("jtest_lab_", 100, 20)
    delta = daily_metrics.record_and_diff("jtest_lab_", 105, 22)
    assert delta == {"followers_delta": 5, "media_delta": 2}


def test_negative_delta_is_honest(metrics_env):
    daily_metrics.record_and_diff("jtest_lab_", 100, 20)
    delta = daily_metrics.record_and_diff("jtest_lab_", 97, 20)
    assert delta == {"followers_delta": -3, "media_delta": 0}


def test_accounts_are_independent(metrics_env):
    daily_metrics.record_and_diff("jtest_lab_", 100, 20)
    daily_metrics.record_and_diff("vera_ai_ua", 50, 10)
    delta_a = daily_metrics.record_and_diff("jtest_lab_", 110, 20)
    delta_b = daily_metrics.record_and_diff("vera_ai_ua", 40, 10)
    assert delta_a == {"followers_delta": 10, "media_delta": 0}
    assert delta_b == {"followers_delta": -10, "media_delta": 0}


def test_missing_current_value_returns_none_delta_without_losing_baseline(metrics_env):
    """A failed fetch (None) must not clobber the stored baseline for tomorrow."""
    daily_metrics.record_and_diff("jtest_lab_", 100, 20)
    delta = daily_metrics.record_and_diff("jtest_lab_", None, None)
    assert delta == {"followers_delta": None, "media_delta": None}

    # baseline preserved -> next real reading diffs against the OLD snapshot, not None
    delta2 = daily_metrics.record_and_diff("jtest_lab_", 108, 21)
    assert delta2 == {"followers_delta": 8, "media_delta": 1}


def test_missing_previous_value_returns_none_delta(metrics_env):
    """Baseline exists but one field was unknown when it was recorded."""
    daily_metrics.record_and_diff("jtest_lab_", None, 20)
    delta = daily_metrics.record_and_diff("jtest_lab_", 100, 21)
    assert delta["followers_delta"] is None
    assert delta["media_delta"] == 1


def test_corrupt_state_file_treated_as_empty(metrics_env):
    metrics_env.write_text("not json", encoding="utf-8")
    delta = daily_metrics.record_and_diff("jtest_lab_", 100, 20)
    assert delta == {"followers_delta": None, "media_delta": None}


def test_atomic_write(metrics_env, monkeypatch):
    captured = []
    real_replace = __import__("os").replace

    def _spy_replace(src, dst):
        captured.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(daily_metrics.os, "replace", _spy_replace)
    daily_metrics.record_and_diff("jtest_lab_", 100, 20)

    assert captured
    src, dst = captured[-1]
    assert src.endswith(".tmp")
    assert dst == str(metrics_env)
    assert not Path(src).exists()
