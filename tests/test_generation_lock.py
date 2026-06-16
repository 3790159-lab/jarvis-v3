# -*- coding: utf-8 -*-
"""Tests for ``GenerationLock``."""
from __future__ import annotations

import pytest

from app.services.block_m2_video.generation_lock import (
    GenerationLock,
    GenerationLockBusy,
    LockToken,
)


def test_acquire_returns_token():
    lock = GenerationLock()
    token = lock.acquire(42)
    assert isinstance(token, LockToken)
    assert token.chat_id == 42
    assert token.nonce


def test_double_acquire_raises_busy():
    lock = GenerationLock()
    lock.acquire(1)
    with pytest.raises(GenerationLockBusy):
        lock.acquire(1)


def test_acquire_different_chats_independent():
    lock = GenerationLock()
    t1 = lock.acquire(1)
    t2 = lock.acquire(2)
    assert t1.chat_id == 1
    assert t2.chat_id == 2


def test_release_clears_lock():
    lock = GenerationLock()
    token = lock.acquire(7)
    assert lock.is_busy(7)
    lock.release(token)
    assert not lock.is_busy(7)
    # second acquire now succeeds
    lock.acquire(7)


def test_release_with_stale_token_is_noop():
    lock = GenerationLock()
    stale = lock.acquire(9)
    lock.release(stale)
    fresh = lock.acquire(9)
    # releasing the old token must not free the new holder
    lock.release(stale)
    assert lock.is_busy(9)
    lock.release(fresh)
    assert not lock.is_busy(9)


def test_release_unknown_chat_is_noop():
    lock = GenerationLock()
    lock.release(LockToken(chat_id=999, nonce="deadbeef"))
    assert not lock.is_busy(999)


def test_is_busy_reflects_state():
    lock = GenerationLock()
    assert not lock.is_busy(5)
    token = lock.acquire(5)
    assert lock.is_busy(5)
    lock.release(token)
    assert not lock.is_busy(5)


def test_session_context_releases_on_exit():
    lock = GenerationLock()
    with lock.session(12):
        assert lock.is_busy(12)
    assert not lock.is_busy(12)


def test_session_context_releases_on_exception():
    lock = GenerationLock()
    with pytest.raises(RuntimeError):
        with lock.session(13):
            assert lock.is_busy(13)
            raise RuntimeError("boom")
    assert not lock.is_busy(13)


def test_session_double_entry_raises_busy():
    lock = GenerationLock()
    with lock.session(20):
        with pytest.raises(GenerationLockBusy):
            with lock.session(20):
                pass
    assert not lock.is_busy(20)


# ── Sentinel callbacks (ref-counted busy signal for the watchdog) ────────────

def test_first_acquire_fires_on_first_acquire():
    events = []
    lock = GenerationLock(
        on_first_acquire=lambda: events.append("start"),
        on_last_release=lambda: events.append("end"),
    )
    lock.acquire(1)
    assert events == ["start"]


def test_last_release_fires_only_when_all_released():
    events = []
    lock = GenerationLock(
        on_first_acquire=lambda: events.append("start"),
        on_last_release=lambda: events.append("end"),
    )
    t1 = lock.acquire(1)
    t2 = lock.acquire(2)  # second chat: no extra "start"
    lock.release(t1)      # still one holder: no "end" yet
    assert events == ["start"]
    lock.release(t2)      # last holder gone: "end"
    assert events == ["start", "end"]


def test_callbacks_are_optional():
    # default construction (no callbacks) must behave exactly as before
    lock = GenerationLock()
    token = lock.acquire(1)
    lock.release(token)
    assert not lock.is_busy(1)


def test_callback_exception_does_not_break_locking():
    def boom():
        raise RuntimeError("sentinel io failed")

    lock = GenerationLock(on_first_acquire=boom, on_last_release=boom)
    # a failing sentinel hook must never break the in-memory lock contract
    token = lock.acquire(1)
    assert lock.is_busy(1)
    lock.release(token)
    assert not lock.is_busy(1)
