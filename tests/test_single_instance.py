# -*- coding: utf-8 -*-
"""OS-level single-instance lock — real kernel lock on tmp, $0, no network."""
import pytest

from app.services import single_instance as si


def test_acquire_returns_handle_and_creates_file(tmp_path):
    lock = tmp_path / "bot.lock"
    handle = si.acquire(lock)
    assert handle is not None
    assert lock.exists()


def test_second_acquire_same_path_raises(tmp_path):
    # The whole point: a second live holder cannot take the lock. This is what
    # stops a duplicate bot process from polling alongside the first.
    lock = tmp_path / "bot.lock"
    si.acquire(lock)                                 # first holds it (for the test's life)
    with pytest.raises(si.AlreadyRunning):
        si.acquire(lock)


def test_release_allows_reacquire(tmp_path):
    # A clean shutdown (or crash → OS auto-release) must let the next start in.
    lock = tmp_path / "bot.lock"
    handle = si.acquire(lock)
    si.release(handle)
    si.acquire(lock)                                 # must not raise


def test_distinct_paths_are_independent(tmp_path):
    # Two different lock files never contend (guards against a hardcoded path).
    si.acquire(tmp_path / "a.lock")
    si.acquire(tmp_path / "b.lock")                  # must not raise
