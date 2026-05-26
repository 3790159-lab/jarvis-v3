# -*- coding: utf-8 -*-
"""Tests for the per-batch video quality settings parser/validator (Task C)."""
from __future__ import annotations

import pytest

from app.services.block_m2_face_swap.quality_settings import (
    DURATION_MAX,
    DURATION_MIN,
    FPS_MAX,
    FPS_NATIVE,
    QualityError,
    QualitySettings,
    parse_quality_args,
    validate_quality,
)


# ── parsing ──────────────────────────────────────────────────────────────────


def test_parse_duration_and_fps():
    assert parse_quality_args("duration=10 fps=42") == {"duration": 10, "fps": 42}


def test_parse_single_key():
    assert parse_quality_args("duration=7") == {"duration": 7}


def test_parse_empty_returns_empty():
    assert parse_quality_args("") == {}
    assert parse_quality_args("   ") == {}


def test_parse_extra_whitespace_tolerant():
    assert parse_quality_args("  duration=5   fps=84 ") == {
        "duration": 5,
        "fps": 84,
    }


def test_parse_unknown_key_raises():
    with pytest.raises(QualityError):
        parse_quality_args("bitrate=5000")


def test_parse_non_int_value_raises():
    with pytest.raises(QualityError):
        parse_quality_args("duration=ten")


def test_parse_token_without_equals_raises():
    with pytest.raises(QualityError):
        parse_quality_args("duration 10")


# ── validation: duration ─────────────────────────────────────────────────────


def test_validate_happy_path():
    qs = validate_quality(10, 21, fps_enabled=False)
    assert qs == QualitySettings(duration_sec=10, fps=21)


def test_validate_duration_below_min_raises():
    with pytest.raises(QualityError):
        validate_quality(DURATION_MIN - 1, 21, fps_enabled=True)


def test_validate_duration_above_max_raises():
    with pytest.raises(QualityError):
        validate_quality(DURATION_MAX + 1, 21, fps_enabled=True)


# ── validation: fps + feature flag gating ────────────────────────────────────


def test_validate_fps_arbitrary_in_range_allowed():
    # Exact-fps RIFE (ComfyUI-VFI) supports any integer fps in
    # [FPS_NATIVE, FPS_MAX] — not just multiples of 21.
    for fps in (30, 45, FPS_MAX):
        qs = validate_quality(5, fps, fps_enabled=True)
        assert qs.fps == fps


def test_validate_fps_above_max_raises():
    with pytest.raises(QualityError):
        validate_quality(5, FPS_MAX + 1, fps_enabled=True)


def test_validate_fps_below_native_raises():
    with pytest.raises(QualityError):
        validate_quality(5, FPS_NATIVE - 1, fps_enabled=True)


def test_validate_fps_boost_rejected_when_flag_off():
    with pytest.raises(QualityError, match="not yet verified"):
        validate_quality(5, 42, fps_enabled=False)


def test_validate_fps_boost_allowed_when_flag_on():
    qs = validate_quality(5, 42, fps_enabled=True)
    assert qs.fps == 42


def test_validate_fps_native_allowed_when_flag_off():
    qs = validate_quality(5, 21, fps_enabled=False)
    assert qs.fps == 21
