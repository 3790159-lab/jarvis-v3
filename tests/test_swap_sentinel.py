# -*- coding: utf-8 -*-
"""Tests for the cross-process video-swap sentinel.

The bot marks ``state/swap_active`` while a long generation runs; the backend
watchdog reads it to avoid restarting a legitimately-busy bot.
"""
from __future__ import annotations

import time

import pytest

from app.services.block_m2_video.swap_sentinel import (
    is_swap_active,
    mark_swap_start,
    mark_swap_end,
)


def test_inactive_when_file_absent(tmp_path):
    sentinel = tmp_path / "swap_active"
    assert is_swap_active(sentinel) is False


def test_active_after_mark_start(tmp_path):
    sentinel = tmp_path / "swap_active"
    mark_swap_start(sentinel)
    assert sentinel.exists()
    assert is_swap_active(sentinel) is True


def test_inactive_after_mark_end(tmp_path):
    sentinel = tmp_path / "swap_active"
    mark_swap_start(sentinel)
    mark_swap_end(sentinel)
    assert not sentinel.exists()
    assert is_swap_active(sentinel) is False


def test_mark_end_is_idempotent_when_absent(tmp_path):
    sentinel = tmp_path / "swap_active"
    # must not raise even though the file was never created
    mark_swap_end(sentinel)
    assert is_swap_active(sentinel) is False


def test_stale_sentinel_is_inactive(tmp_path, monkeypatch):
    """A bot that crashed mid-swap leaves a frozen sentinel; once it ages past
    WATCHDOG_SWAP_MAX_SEC the watchdog must treat the bot as restartable."""
    sentinel = tmp_path / "swap_active"
    monkeypatch.setenv("WATCHDOG_SWAP_MAX_SEC", "1800")
    old = int(time.time()) - 3600  # 1h ago
    sentinel.write_text(str(old), encoding="utf-8")
    assert is_swap_active(sentinel) is False


def test_fresh_sentinel_within_window_is_active(tmp_path, monkeypatch):
    sentinel = tmp_path / "swap_active"
    monkeypatch.setenv("WATCHDOG_SWAP_MAX_SEC", "1800")
    recent = int(time.time()) - 60  # 1 min ago
    sentinel.write_text(str(recent), encoding="utf-8")
    assert is_swap_active(sentinel) is True


def test_corrupt_sentinel_falls_back_to_mtime(tmp_path, monkeypatch):
    """Unparseable contents must not permanently wedge the watchdog: fall back
    to the file's mtime so a fresh-but-corrupt sentinel still reads busy and an
    old one reads idle."""
    sentinel = tmp_path / "swap_active"
    monkeypatch.setenv("WATCHDOG_SWAP_MAX_SEC", "1800")
    sentinel.write_text("not-a-timestamp", encoding="utf-8")
    # just written → mtime is now → busy
    assert is_swap_active(sentinel) is True
