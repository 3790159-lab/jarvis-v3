# -*- coding: utf-8 -*-
"""B-51: Telegram media_group buffer dedupe by file_unique_id.

Transport-layer fix for the ~2x target-photo replication documented in
docs/B-51_INVESTIGATION.md. The engine + orchestrator stay clean (B-50
path-level dedupe is defence-in-depth); this guards the source.

All Telegram / network / handler boundaries are mocked — no real bot token,
no HTTP, no pods.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _get_mod():
    # Pattern from tests/test_webhook_mode.py:18-26 — load the bot module by
    # path under a unique name so each test gets a fresh module object.
    spec = importlib.util.spec_from_file_location(
        f"_test_b51_{id(object())}",
        ROOT / "tools" / "jarvis_smart_telegram_control.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _photo_msg(
    file_unique_id: str,
    file_id: str,
    *,
    chat_id: int = 12345,
    media_group_id: str = "mg1",
) -> dict:
    """A Telegram photo message carrying a single PhotoSize."""
    return {
        "chat": {"id": chat_id},
        "media_group_id": media_group_id,
        "photo": [
            {
                "file_id": file_id,
                "file_unique_id": file_unique_id,
                "file_size": 1000,
            },
        ],
    }


def _run_album(mod, msgs, chat_id: str = "12345"):
    """Invoke the real _swapbatch_album_intercept with mocked boundaries.

    Returns (consumed: bool, paths: list) — the paths handed to
    consume_targets_album. _download_telegram_file returns a deterministic
    path per file_id, so distinct file_ids would yield distinct paths.
    """
    captured: dict = {"paths": None}
    handler = MagicMock()
    orch = MagicMock()
    orch.is_waiting_for_targets.return_value = True

    def _consume(chat_id_int, paths):
        captured["paths"] = list(paths)
        return MagicMock()

    handler.consume_targets_album.side_effect = _consume

    with patch.object(mod, "_swapbatch_get_handler", return_value=(handler, orch)), \
         patch.object(
             mod, "_download_telegram_file",
             side_effect=lambda fid, fn: f"/tmp/{fid}.jpg",
         ), \
         patch.object(mod, "_swapbatch_apply_reply", lambda *a, **k: None):
        consumed = mod._swapbatch_album_intercept(chat_id, msgs)
    return consumed, captured["paths"]


# ── §7.1 unit: _largest_photo_unique_id ─────────────────────────────────────


def test_largest_photo_unique_id_returns_largest_size_unique_id():
    mod = _get_mod()
    msg = {
        "photo": [
            {"file_id": "s", "file_unique_id": "U_small", "file_size": 100},
            {"file_id": "b", "file_unique_id": "U_big", "file_size": 9000},
            {"file_id": "m", "file_unique_id": "U_mid", "file_size": 1500},
        ]
    }
    assert mod._largest_photo_unique_id(msg) == "U_big"


def test_largest_photo_unique_id_none_when_no_photo():
    mod = _get_mod()
    assert mod._largest_photo_unique_id({"text": "hi"}) is None


# ── §7.2 integration: dedupe-on-append collapses duplicate deliveries ────────


def test_album_dedupes_repeated_unique_id_to_unique_paths():
    """Prod-evidence pattern: 2 unique photos delivered as A A B B A → 2.

    Mirrors the multiplicity in tests/test_swapbatch_orchestrator.py:148.
    """
    mod = _get_mod()
    raw = [
        _photo_msg("U_A", "fid_a1"),
        _photo_msg("U_A", "fid_a2"),
        _photo_msg("U_B", "fid_b1"),
        _photo_msg("U_B", "fid_b2"),
        _photo_msg("U_A", "fid_a3"),
    ]
    buffer: dict = {}
    for m in raw:
        mod._buffer_media_group_msg(buffer, "mg1", m)

    # Dedupe-on-append collapsed 5 deliveries → 2 unique photos.
    assert len(buffer["mg1"]["msgs"]) == 2

    consumed, paths = _run_album(mod, buffer["mg1"]["msgs"])
    assert consumed is True
    assert len(paths) == 2


def test_dedupe_keys_on_unique_id_not_file_id():
    """Same physical photo (file_unique_id 'U') redelivered under two DIFFERENT
    file_ids must still collapse to one path.

    This is the case B-50's path-level dedupe cannot catch: distinct file_ids
    map to distinct staged filenames → distinct Paths. Keying on the stable
    file_unique_id collapses them at the source.
    """
    mod = _get_mod()
    raw = [
        _photo_msg("U", "fid_first"),
        _photo_msg("U", "fid_second"),
    ]
    buffer: dict = {}
    for m in raw:
        mod._buffer_media_group_msg(buffer, "mg1", m)

    assert len(buffer["mg1"]["msgs"]) == 1

    consumed, paths = _run_album(mod, buffer["mg1"]["msgs"])
    assert consumed is True
    assert len(paths) == 1


# ── §7.3 flush re-entry safety: pop-before-process ──────────────────────────


def test_flush_pops_group_before_processing():
    """A mid-flush exception must not leave the group buffered.

    Otherwise the next poll-loop iteration re-flushes the partially-processed
    group → duplicate submission (mechanism §3.4.1). Popping before processing
    guarantees the group is gone even when processing raises.
    """
    mod = _get_mod()
    buffer: dict = {
        "mg1": {
            "msgs": [_photo_msg("U_A", "fid_a")],
            "seen_uids": {"U_A"},
            "last_seen": 0.0,
        }
    }
    with patch.object(mod, "ALLOWED_CHAT_ID", "12345"), \
         patch.object(
             mod, "_swapbatch_album_intercept",
             side_effect=RuntimeError("download boom"),
         ):
        with pytest.raises(RuntimeError, match="download boom"):
            mod._flush_media_group(buffer, "mg1")

    assert "mg1" not in buffer  # popped before the raise → no re-flush


# ── B-51 diagnostic logging (Task 5, Day 8) ─────────────────────────────────
#
# Pure observability: every transport-level transition emits a greppable INFO
# line so the next "sent N, received <N" album loss can be root-caused. These
# assert on the log text only — dedupe/flush behaviour is unchanged and is
# covered by the §7.x tests above.


def test_dedupe_logs_accepted_for_new_photo(caplog):
    mod = _get_mod()
    buffer: dict = {}
    with caplog.at_level(logging.INFO):
        mod._buffer_media_group_msg(buffer, "mg1", _photo_msg("U_A", "fid_a"))
    assert "media_group: accepted photo" in caplog.text
    assert "file_unique_id=U_A" in caplog.text


def test_dedupe_logs_skipped_for_duplicate(caplog):
    mod = _get_mod()
    buffer: dict = {}
    mod._buffer_media_group_msg(buffer, "mg1", _photo_msg("U_A", "fid_a1"))
    with caplog.at_level(logging.INFO):
        mod._buffer_media_group_msg(buffer, "mg1", _photo_msg("U_A", "fid_a2"))
    assert "media_group: SKIPPED duplicate photo" in caplog.text
    # The duplicate must NOT have grown the buffer (behaviour unchanged).
    assert len(buffer["mg1"]["msgs"]) == 1


def test_flush_logs_with_timing_info(caplog):
    mod = _get_mod()
    buffer: dict = {}
    mod._buffer_media_group_msg(buffer, "mg1", _photo_msg("U_A", "fid_a"))
    mod._buffer_media_group_msg(buffer, "mg1", _photo_msg("U_B", "fid_b"))
    with caplog.at_level(logging.INFO), \
         patch.object(mod, "_swapbatch_album_intercept", return_value=True):
        mod._flush_media_group(buffer, "mg1")
    assert "media_group: flushing" in caplog.text
    assert "photo_count=2" in caplog.text
    assert "elapsed_since_first=" in caplog.text
    assert "elapsed_since_last=" in caplog.text


def test_new_media_group_id_logged(caplog):
    """A second album arriving before the first flushes is flagged — this is
    the Telegram-split-album hypothesis from the B-51 investigation."""
    mod = _get_mod()
    buffer: dict = {}
    mod._buffer_media_group_msg(buffer, "mg1", _photo_msg("U_A", "fid_a", media_group_id="mg1"))
    with caplog.at_level(logging.INFO):
        mod._buffer_media_group_msg(buffer, "mg2", _photo_msg("U_B", "fid_b", media_group_id="mg2"))
    assert "media_group: NEW media_group_id" in caplog.text
    assert "previous=mg1" in caplog.text
    assert "new=mg2" in caplog.text


def test_missing_file_unique_id_does_not_crash(caplog):
    """A PhotoSize with no file_unique_id must log '(no file_unique_id)' and
    still be buffered (can't dedupe an unkeyable photo)."""
    mod = _get_mod()
    buffer: dict = {}
    msg = {
        "chat": {"id": 12345},
        "media_group_id": "mg1",
        "photo": [{"file_id": "fid_x", "file_size": 1000}],  # no file_unique_id
    }
    with caplog.at_level(logging.INFO):
        mod._buffer_media_group_msg(buffer, "mg1", msg)  # must not raise
    assert "(no file_unique_id)" in caplog.text
    assert len(buffer["mg1"]["msgs"]) == 1
