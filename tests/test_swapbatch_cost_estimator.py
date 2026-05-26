# -*- coding: utf-8 -*-
"""Tests for ``app.services.block_m2_face_swap.cost_estimator``."""
from __future__ import annotations

import pytest

from app.services.block_m2_face_swap.cost_estimator import (
    CostEstimate,
    estimate,
    format_cost_report_ru,
)


# ── estimate(...) ────────────────────────────────────────────────────────────


def test_estimate_zero_valid_returns_zero_cost():
    est = estimate(0, skipped_count=5)
    assert est.total_usd == 0.0
    assert est.swap_usd == 0.0
    assert est.animate_usd == 0.0
    assert est.total_minutes == 0.0
    assert est.skipped_count == 5


def test_estimate_default_constants_single_photo():
    # 1 × $0.02 + $0.05 cold = $0.07 swap; 1 × $0.27 + $0.05 = $0.32 animate.
    est = estimate(1)
    assert est.swap_usd == pytest.approx(0.07, abs=0.01)
    assert est.animate_usd == pytest.approx(0.32, abs=0.01)
    assert est.total_usd == pytest.approx(0.39, abs=0.02)


def test_estimate_default_eight_photos():
    est = estimate(8, skipped_count=2)
    # swap: 8 × 0.02 + 0.05 = 0.21
    # animate: 8 × 0.27 + 0.05 = 2.21
    assert est.swap_usd == pytest.approx(0.21, abs=0.01)
    assert est.animate_usd == pytest.approx(2.21, abs=0.01)
    assert est.total_usd == pytest.approx(2.42, abs=0.02)
    assert est.skipped_count == 2


def test_estimate_time_includes_cold_start(monkeypatch):
    monkeypatch.setenv("SWAPBATCH_SWAP_SEC_PER_PHOTO", "10")
    monkeypatch.setenv("SWAPBATCH_ANIMATE_SEC_PER_VIDEO", "600")
    monkeypatch.setenv("SWAPBATCH_COLD_START_SEC", "120")
    est = estimate(2)
    # swap: 120 + 2×10 = 140s = 2.33 min ≈ 2.3
    # animate: 120 + 2×600 = 1320s = 22 min
    assert est.swap_minutes == pytest.approx(2.3, abs=0.1)
    assert est.animate_minutes == pytest.approx(22.0, abs=0.1)
    assert est.total_minutes == pytest.approx(est.swap_minutes + est.animate_minutes, abs=0.1)


def test_estimate_env_overrides_unit_costs(monkeypatch):
    monkeypatch.setenv("SWAPBATCH_SWAP_USD_PER_PHOTO", "0.10")
    monkeypatch.setenv("SWAPBATCH_ANIMATE_USD_PER_VIDEO", "1.00")
    monkeypatch.setenv("SWAPBATCH_COLD_START_USD", "0.00")
    est = estimate(5)
    assert est.swap_usd == pytest.approx(0.50, abs=0.01)
    assert est.animate_usd == pytest.approx(5.00, abs=0.01)


def test_estimate_invalid_arg_count_raises():
    with pytest.raises(ValueError):
        estimate(-1)
    with pytest.raises(ValueError):
        estimate(3, skipped_count=-2)


def test_estimate_handles_garbage_env_gracefully(monkeypatch):
    monkeypatch.setenv("SWAPBATCH_SWAP_USD_PER_PHOTO", "not-a-number")
    # Falls back to default 0.02.
    est = estimate(3)
    assert est.swap_usd == pytest.approx(3 * 0.02 + 0.05, abs=0.01)


# ── format_cost_report_ru(...) ───────────────────────────────────────────────


def test_format_cost_report_contains_required_lines():
    est = estimate(8, skipped_count=2)
    msg = format_cost_report_ru(est, source_face_count=1, total_targets=10)
    assert "📊 Оценка батча" in msg
    assert "Source: 1 лицо" in msg
    assert "Targets: 10 фото" in msg
    assert "8 с лицами" in msg
    assert "2 без лиц" in msg
    assert "/swapbatch_go" in msg
    assert "/swapbatch_cancel" in msg


def test_format_cost_report_zero_valid_says_cannot_proceed():
    est = estimate(0, skipped_count=10)
    msg = format_cost_report_ru(est, source_face_count=1, total_targets=10)
    assert "не может быть запущен" in msg
    assert "/swapbatch_go" not in msg  # don't offer to run an empty batch


def test_format_cost_report_pluralisation_for_multiple_source_faces():
    est = estimate(3)
    msg = format_cost_report_ru(est, source_face_count=2, total_targets=3)
    assert "Source: 2 лиц" in msg  # plural form


def test_format_cost_report_warns_long_for_large_batch():
    # 20 photos ≈ 4h of sequential animation — flag it as long-running.
    est = estimate(20)
    msg = format_cost_report_ru(est, source_face_count=1, total_targets=20)
    assert "долго" in msg.lower()


def test_format_cost_report_no_long_warning_for_small_batch():
    est = estimate(2)
    msg = format_cost_report_ru(est, source_face_count=1, total_targets=2)
    assert "долго" not in msg.lower()
