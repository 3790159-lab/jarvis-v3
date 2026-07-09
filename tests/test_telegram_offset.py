# -*- coding: utf-8 -*-
"""Persisted getUpdates offset (Этап 1 — добор пропущенных апдейтов).

The poll loop's ``offset`` was an in-memory ``0`` reset on every restart. These
tests pin the small, injectable persistence layer that lets a restarted bot
resume from the last update_id it actually processed, so updates that arrived in
the kill→start window (held 24h on Telegram's servers) are re-delivered instead
of silently skipped past.

Contract:
  * ``load_last_update_id(path)`` → the highest processed ``update_id``, or the
    sentinel ``-1`` when nothing has been persisted yet (fresh bot / missing /
    corrupt file). ``-1`` makes ``offset = last + 1`` collapse to ``0`` (fetch
    everything pending) for a fresh bot, and keeps an id-less update (``0``)
    dispatchable via the ``uid <= last`` dedup guard.
  * ``save_last_update_id(path, uid)`` → atomic write (tmp + os.replace), so a
    crash mid-write never leaves a truncated offset file.

$0, no network, no Telegram. Pure fs on tmp_path.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.services import telegram_offset as off


def test_load_missing_file_returns_sentinel(tmp_path):
    """A fresh bot (no persisted offset) reports -1 → offset resolves to 0."""
    assert off.load_last_update_id(tmp_path / "telegram_offset.json") == -1


def test_save_then_load_roundtrips(tmp_path):
    p = tmp_path / "telegram_offset.json"
    off.save_last_update_id(p, 4242)
    assert off.load_last_update_id(p) == 4242


def test_save_is_atomic_replace_not_partial(tmp_path):
    """The write must land as a single os.replace of a temp file, never a
    truncated in-place write — otherwise a crash mid-write loses the offset."""
    p = tmp_path / "telegram_offset.json"
    off.save_last_update_id(p, 100)
    # No leftover temp artifact after a successful write.
    assert list(tmp_path.glob("*.tmp")) == []
    # File is valid JSON carrying the id.
    data = json.loads(p.read_text(encoding="utf-8"))
    assert int(data["last_update_id"]) == 100


def test_load_corrupt_file_returns_sentinel(tmp_path):
    """A truncated / garbage file must not crash startup — treat as fresh."""
    p = tmp_path / "telegram_offset.json"
    p.write_text("{not json", encoding="utf-8")
    assert off.load_last_update_id(p) == -1


def test_load_non_int_value_returns_sentinel(tmp_path):
    p = tmp_path / "telegram_offset.json"
    p.write_text(json.dumps({"last_update_id": "oops"}), encoding="utf-8")
    assert off.load_last_update_id(p) == -1


def test_save_overwrites_previous_value(tmp_path):
    p = tmp_path / "telegram_offset.json"
    off.save_last_update_id(p, 10)
    off.save_last_update_id(p, 25)
    assert off.load_last_update_id(p) == 25


def test_save_creates_parent_dir(tmp_path):
    p = tmp_path / "nested" / "dir" / "telegram_offset.json"
    off.save_last_update_id(p, 7)
    assert off.load_last_update_id(p) == 7
