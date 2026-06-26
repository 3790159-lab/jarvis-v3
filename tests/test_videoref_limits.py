"""Tests for reference-video limit validation (Веха B, Task 2).

Pure function over Telegram metadata numbers (file_size bytes, duration s).
No I/O, no download — these gates run *before* the file is fetched (Task 3).
"""

from __future__ import annotations

from app.services.block_m2_video.video_frames import validate_videoref

_MB = 1024 * 1024


def test_ok_case_returns_true_empty_reason():
    ok, reason = validate_videoref(file_size=5 * _MB, duration=8)
    assert ok is True
    assert reason == ""


def test_oversize_rejected():
    ok, reason = validate_videoref(file_size=21 * _MB, duration=5)
    assert ok is False
    assert "20" in reason  # mentions the 20 MB limit


def test_too_long_rejected():
    ok, reason = validate_videoref(file_size=1 * _MB, duration=16)
    assert ok is False
    assert "15" in reason  # mentions the 15 s limit


def test_exactly_at_size_limit_passes():
    ok, _ = validate_videoref(file_size=20 * _MB, duration=5)
    assert ok is True


def test_exactly_at_duration_limit_passes():
    ok, _ = validate_videoref(file_size=1 * _MB, duration=15)
    assert ok is True


def test_size_checked_before_duration_when_both_exceeded():
    # Deterministic order: size is reported first.
    ok, reason = validate_videoref(file_size=21 * _MB, duration=16)
    assert ok is False
    assert "20" in reason
    assert "15" not in reason


def test_just_over_boundaries_rejected():
    ok_size, _ = validate_videoref(file_size=20 * _MB + 1, duration=5)
    ok_dur, _ = validate_videoref(file_size=1 * _MB, duration=15.001)
    assert ok_size is False
    assert ok_dur is False


def test_custom_limits_respected():
    ok, reason = validate_videoref(
        file_size=11 * _MB, duration=5, max_size_mb=10
    )
    assert ok is False
    assert "10" in reason
