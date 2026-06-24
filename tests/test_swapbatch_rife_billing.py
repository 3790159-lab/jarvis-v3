"""Задача 2 (TDD): RIFE interpolation billing.

Mirrors the animate billing contract (_bill_completed_videos): bills ONLY for
successful videos, best-effort (a ledger failure never breaks the batch).
Formula: succeeded × max(1, seconds) × rate, rate from SWAPBATCH_RIFE_USD_PER_SEC
(default 0.01). seconds = input video duration, already known from the animate
phase (sess.duration_sec) — not re-measured.
"""
import pytest

from app.handlers.face_swap_handler import FaceSwapHandler
from app.services.audit import cost_tracker as _cost


def _handler():
    # Orchestrator is unused by the billing method; None is fine for unit test.
    return FaceSwapHandler(orchestrator=None)


@pytest.fixture
def recorded(monkeypatch):
    calls = []
    monkeypatch.setattr(_cost, "record_cost",
                        lambda uid, uname, amount: calls.append((uid, uname, amount)))
    return calls


def test_two_successful_videos_billed_per_second(recorded, monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    _handler()._bill_interpolated_videos(2, 10, user_id=42, username="art")
    assert len(recorded) == 1
    uid, uname, amount = recorded[0]
    assert uid == 42 and uname == "art"
    assert amount == pytest.approx(2 * 10 * 0.01)   # $0.20


def test_only_successful_count_is_billed(recorded, monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    # 1 success + 1 RIFE failure -> caller passes succeeded=1
    _handler()._bill_interpolated_videos(1, 5, user_id=7, username="u")
    assert recorded[0][2] == pytest.approx(1 * 5 * 0.01)   # $0.05, not $0.10


def test_zero_successes_records_nothing(recorded):
    _handler()._bill_interpolated_videos(0, 10, user_id=7, username="u")
    assert recorded == []


def test_rate_read_from_env(recorded, monkeypatch):
    monkeypatch.setenv("SWAPBATCH_RIFE_USD_PER_SEC", "0.02")
    _handler()._bill_interpolated_videos(2, 10, user_id=7, username="u")
    assert recorded[0][2] == pytest.approx(2 * 10 * 0.02)   # $0.40


def test_rate_defaults_to_one_cent_when_unset(recorded, monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    _handler()._bill_interpolated_videos(1, 1, user_id=7, username="u")
    assert recorded[0][2] == pytest.approx(0.01)


def test_invalid_rate_falls_back_to_default(recorded, monkeypatch):
    monkeypatch.setenv("SWAPBATCH_RIFE_USD_PER_SEC", "not-a-number")
    _handler()._bill_interpolated_videos(1, 10, user_id=7, username="u")
    assert recorded[0][2] == pytest.approx(0.10)   # default 0.01


def test_billing_failure_does_not_raise(monkeypatch):
    def boom(uid, uname, amount):
        raise RuntimeError("ledger down")
    monkeypatch.setattr(_cost, "record_cost", boom)
    # Must NOT propagate — batch delivery continues regardless.
    _handler()._bill_interpolated_videos(2, 10, user_id=7, username="u")


def test_min_one_second_charge(recorded, monkeypatch):
    # WaveSpeed bills a minimum of 1s; never under-bill below what we paid.
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    _handler()._bill_interpolated_videos(2, 0, user_id=7, username="u")
    assert recorded[0][2] == pytest.approx(2 * 1 * 0.01)   # seconds floored to 1


def test_no_user_id_skips_billing(recorded):
    _handler()._bill_interpolated_videos(2, 10, user_id=None, username=None)
    assert recorded == []
