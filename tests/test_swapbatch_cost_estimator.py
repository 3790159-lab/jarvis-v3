# -*- coding: utf-8 -*-
"""Tests for ``app.services.block_m2_face_swap.cost_estimator``."""
from __future__ import annotations

import pytest

from app.services.block_m2_face_swap.cost_estimator import (
    CostEstimate,
    _LONG_BATCH_MINUTES,
    animate_enabled,
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
    assert "2 нечитаемых" in msg
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


# ── FIX C: animate_enabled flag & format_cost_report_ru(animate_enabled=False) ─


def test_animate_enabled_default_is_false(monkeypatch):
    """SWAPBATCH_ANIMATE_ENABLED unset (or "0") → animate_enabled() returns False."""
    monkeypatch.delenv("SWAPBATCH_ANIMATE_ENABLED", raising=False)
    assert animate_enabled() is False


def test_animate_enabled_reads_env_true(monkeypatch):
    """SWAPBATCH_ANIMATE_ENABLED="1" → animate_enabled() returns True."""
    monkeypatch.setenv("SWAPBATCH_ANIMATE_ENABLED", "1")
    assert animate_enabled() is True


def test_animate_enabled_accepts_true_variants(monkeypatch):
    """Truthy string variants ("true", "yes", "on") are all accepted."""
    for val in ("true", "True", "TRUE", "yes", "Yes", "on", "ON"):
        monkeypatch.setenv("SWAPBATCH_ANIMATE_ENABLED", val)
        assert animate_enabled() is True, f"expected True for {val!r}"


def test_animate_enabled_rejects_zero(monkeypatch):
    """"0" and empty string → False."""
    for val in ("0", ""):
        monkeypatch.setenv("SWAPBATCH_ANIMATE_ENABLED", val)
        assert animate_enabled() is False, f"expected False for {val!r}"


def test_format_cost_report_animate_disabled_omits_animate_line():
    """When animate_enabled=False the report must not contain an Animate line."""
    est = estimate(5, skipped_count=0)
    msg = format_cost_report_ru(
        est,
        source_face_count=1,
        total_targets=5,
        animate_enabled=False,
    )
    assert "Animate:" not in msg


def test_format_cost_report_animate_disabled_total_is_swap_only():
    """With animate_enabled=False, Всего uses swap_usd not total_usd."""
    est = estimate(5)
    msg = format_cost_report_ru(
        est,
        source_face_count=1,
        total_targets=5,
        animate_enabled=False,
    )
    # swap_usd for 5 photos: 5×0.02 + 0.05 = 0.15
    assert f"${est.swap_usd:.2f}" in msg
    # total_usd (swap + animate) must NOT appear as "Всего" line
    total_str = f"${est.total_usd:.2f}"
    # The total_usd (e.g. "$1.72") should not appear after "Всего:" in the report
    # (it could appear coincidentally, so assert the *swap* value is in the Всего line)
    vsego_line = next(
        (line for line in msg.splitlines() if "Всего" in line), None
    )
    assert vsego_line is not None
    assert f"${est.swap_usd:.2f}" in vsego_line


def test_format_cost_report_animate_disabled_time_is_swap_only():
    """With animate_enabled=False, Время uses swap_minutes only."""
    est = estimate(5)
    msg = format_cost_report_ru(
        est,
        source_face_count=1,
        total_targets=5,
        animate_enabled=False,
    )
    # The word "animate" must not appear in the Время line (or at all, re: label)
    time_line = next(
        (line for line in msg.splitlines() if "Время" in line), None
    )
    assert time_line is not None
    assert "animate" not in time_line.lower()


def test_format_cost_report_animate_disabled_no_long_warning():
    """With animate_enabled=False, the долго warning is never shown (swap alone is fast)."""
    # 20 photos: swap only ~7 min, total (with animate) ~245 min — flag must be absent.
    est = estimate(20)
    msg = format_cost_report_ru(
        est,
        source_face_count=1,
        total_targets=20,
        animate_enabled=False,
    )
    assert "долго" not in msg.lower()


def test_format_cost_report_animate_enabled_true_unchanged():
    """Passing animate_enabled=True (the explicit default) produces the same
    output as the legacy call without the param."""
    est = estimate(8, skipped_count=2)
    legacy = format_cost_report_ru(est, source_face_count=1, total_targets=10)
    explicit = format_cost_report_ru(
        est, source_face_count=1, total_targets=10, animate_enabled=True
    )
    assert legacy == explicit


# ── animate cost/time overrides (caps-based WaveSpeed estimate) ───────────────


def test_animate_overrides_used_when_provided():
    """When both overrides are passed, the Animate cost + Время reflect the
    caps-based (WaveSpeed) numbers — NOT the stale RunPod 720s/0.27 estimate."""
    # 1 photo with default env: animate_usd=0.32, animate_minutes ≈ 14.0
    est = estimate(1)
    # Sanity: the stale path would render "~14 мин".
    assert est.animate_minutes == pytest.approx(14.0, abs=0.1)
    msg = format_cost_report_ru(
        est,
        source_face_count=1,
        total_targets=1,
        animate_usd_override=0.50,
        animate_minutes_override=1.0,
    )
    # WaveSpeed numbers present.
    assert "$0.50" in msg
    assert "~1 мин" in msg
    # Combined total reflects swap + override (0.07 + 0.50 = 0.57).
    assert f"${est.swap_usd + 0.50:.2f}" in msg
    # Stale RunPod animate time must be gone.
    assert "~14 мин" not in msg
    # And the stale animate cost too.
    assert f"${est.animate_usd:.2f}" not in msg


def test_no_overrides_keeps_legacy_output():
    """With no overrides, output is byte-for-byte identical to legacy."""
    est = estimate(8, skipped_count=2)
    legacy = format_cost_report_ru(est, source_face_count=1, total_targets=10)
    with_none = format_cost_report_ru(
        est,
        source_face_count=1,
        total_targets=10,
        animate_usd_override=None,
        animate_minutes_override=None,
    )
    assert legacy == with_none
    # Legacy still uses est.animate_usd / est.animate_minutes verbatim.
    assert f"Animate: ${est.animate_usd:.2f}" in legacy
    assert f"animate ~{est.animate_minutes:.0f} мин" in legacy


def test_long_batch_warning_uses_overridden_minutes():
    """When overrides push the total below the long-batch threshold, the
    'долго' warning is absent even though est.total_minutes is high."""
    # 20 photos: est.total_minutes ≈ 245 (well past 120) → stale path warns.
    est = estimate(20)
    assert est.total_minutes >= _LONG_BATCH_MINUTES
    msg = format_cost_report_ru(
        est,
        source_face_count=1,
        total_targets=20,
        animate_usd_override=2.00,
        animate_minutes_override=5.0,
    )
    assert "долго" not in msg.lower()
