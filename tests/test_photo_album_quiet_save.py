# -*- coding: utf-8 -*-
"""Fix: photo-album clobber + Vision-spam.

Two prod bugs, both triggered by sending a plain album of photos (no command):

1. Filename collision — ``_extract_file_from_msg`` named every photo
   ``photo_{file_id[:8]}.jpg``, but ALL Telegram photo file_ids share the
   ``AgACAgIA`` prefix, so an N-photo album downloaded to ONE path, each frame
   overwriting the last (only the final photo survived on disk). Fix: name by
   the stable, per-media ``file_unique_id`` + a non-overwrite guard.

2. Vision-spam — a no-command album flushed each photo through
   ``_handle_file_message`` → one paid Vision call PER frame + one reply per
   frame. Fix: a no-caption album with no active collection session is saved
   quietly (unique names, no Vision, no backend) with a single "Принял N" reply.
   Captioned albums and active me_seed/photo_studio sessions are unchanged.

All Telegram / network / handler boundaries are mocked — no token, no HTTP.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _get_mod():
    # Fresh module object per test (pattern from tests/test_b51_media_group_dedupe.py)
    # so the in-memory SeedCollector singleton never leaks across tests.
    spec = importlib.util.spec_from_file_location(
        f"_test_album_{id(object())}",
        ROOT / "tools" / "jarvis_smart_telegram_control.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _photo_msg(unique_id: str, file_id: str, *, caption: str | None = None,
               chat_id: int = 12345, media_group_id: str = "mg1") -> dict:
    m = {
        "chat": {"id": chat_id},
        "media_group_id": media_group_id,
        "photo": [{"file_id": file_id, "file_unique_id": unique_id, "file_size": 500}],
    }
    if caption is not None:
        m["caption"] = caption
    return m


# ── Fix 1: unique filenames from file_unique_id ─────────────────────────────

def test_two_distinct_photos_get_distinct_filenames():
    """The clobber regression: two photos whose file_ids share the AgACAgIA
    prefix must still map to DISTINCT filenames (keyed on file_unique_id)."""
    mod = _get_mod()
    a = {"photo": [{"file_id": "AgACAgIAaaaaXXXX", "file_unique_id": "UQ_A", "file_size": 500}]}
    b = {"photo": [{"file_id": "AgACAgIAbbbbYYYY", "file_unique_id": "UQ_B", "file_size": 500}]}
    _, name_a, _ = mod._extract_file_from_msg(a)
    _, name_b, _ = mod._extract_file_from_msg(b)
    assert name_a != name_b
    assert "UQ_A" in name_a
    assert "UQ_B" in name_b


def test_photo_filename_uses_largest_size_unique_id():
    mod = _get_mod()
    msg = {"photo": [
        {"file_id": "AgACAgIAsmall", "file_unique_id": "UQ_small", "file_size": 100},
        {"file_id": "AgACAgIAbig", "file_unique_id": "UQ_big", "file_size": 9000},
    ]}
    _, name, _ = mod._extract_file_from_msg(msg)
    assert "UQ_big" in name


def test_document_keeps_real_file_name():
    """Documents with an original file_name keep it (distinct → no clobber)."""
    mod = _get_mod()
    msg = {"document": {"file_id": "BQACabc", "file_unique_id": "DUQ", "file_name": "vera_07.jpg"}}
    _, name, _ = mod._extract_file_from_msg(msg)
    assert name == "vera_07.jpg"


def test_document_without_file_name_uses_unique_id():
    """A file_name-less document falls back to its file_unique_id, not the
    always-colliding file_id[:8]."""
    mod = _get_mod()
    msg = {"document": {"file_id": "BQACAgIAzzz", "file_unique_id": "DUQ_1", "mime_type": "image/jpeg"}}
    _, name, _ = mod._extract_file_from_msg(msg)
    assert "DUQ_1" in name


# ── Fix 1b: anti-clobber non-overwrite guard ───────────────────────────────

def test_uniquify_dest_returns_path_unchanged_when_free(tmp_path):
    mod = _get_mod()
    p = tmp_path / "photo_UQ.jpg"
    assert mod._uniquify_dest(p) == p


def test_uniquify_dest_suffixes_when_taken(tmp_path):
    mod = _get_mod()
    p = tmp_path / "photo_UQ.jpg"
    p.write_bytes(b"existing")
    out = mod._uniquify_dest(p)
    assert out != p
    assert not out.exists()
    assert out.stem.startswith("photo_UQ")
    assert out.suffix == ".jpg"


# ── Fix 2: no-command album → quiet save, one summary, NO Vision ────────────

def _flush_env(mod, *, session_active=False):
    """Common patch set for _flush_media_group flush tests."""
    return [
        patch.object(mod, "ALLOWED_CHAT_ID", "12345"),
        patch.object(mod, "_swapbatch_get_handler", return_value=(None, None)),
        patch.object(mod, "save_state", lambda s: None),
        patch.object(mod, "load_state", lambda: mod.default_state()),
        patch.object(mod, "_album_has_active_session", return_value=session_active),
    ]


def test_no_caption_album_quiet_saves_without_vision():
    mod = _get_mod()
    msgs = [_photo_msg(f"U_{i}", f"AgACAgIA_{i}") for i in range(3)]
    sends: list = []
    downloads: list = []
    ctx = _flush_env(mod)
    ctx += [
        patch.object(mod, "_download_telegram_file",
                     side_effect=lambda fid, fn: (downloads.append(fn), f"/inc/{fn}")[1]),
        patch.object(mod, "send", side_effect=lambda cid, txt, **k: sends.append(txt)),
        # If quiet-save leaks into the per-frame path, this makes the test fail loudly.
        patch.object(mod, "_handle_file_message",
                     side_effect=AssertionError("no-caption album must NOT go per-frame")),
    ]
    from contextlib import ExitStack
    with ExitStack() as stack:
        for c in ctx:
            stack.enter_context(c)
        buffer = {"mg1": {"msgs": msgs, "seen_uids": set(), "last_seen": 0.0}}
        mod._flush_media_group(buffer, "mg1")

    assert len(downloads) == 3
    assert len(set(downloads)) == 3           # three distinct filenames (no clobber)
    summaries = [s for s in sends if "Принял" in s]
    assert len(summaries) == 1                # exactly ONE summary reply
    assert "3" in summaries[0]


def test_captioned_album_still_processed_per_frame():
    mod = _get_mod()
    msgs = [_photo_msg(f"U_{i}", f"AgACAgIA_{i}",
                       caption=("суммируй" if i == 0 else None)) for i in range(3)]
    per_frame: list = []
    ctx = _flush_env(mod)
    ctx += [
        patch.object(mod, "send", lambda cid, txt, **k: None),
        patch.object(mod, "_handle_file_message",
                     side_effect=lambda cid, m, s: per_frame.append(m)),
    ]
    from contextlib import ExitStack
    with ExitStack() as stack:
        for c in ctx:
            stack.enter_context(c)
        buffer = {"mg1": {"msgs": msgs, "seen_uids": set(), "last_seen": 0.0}}
        mod._flush_media_group(buffer, "mg1")
    assert len(per_frame) == 3                # captioned album keeps per-frame routing


def test_album_has_active_session_false_when_none():
    """No me_seed session and no photo-studio conv for the chat → quiet-save
    eligible. Uses a chat id with no on-disk conversation file."""
    mod = _get_mod()
    assert mod._album_has_active_session("999999999") is False


def test_no_caption_album_with_active_session_stays_per_frame():
    """A no-caption album must still feed an ACTIVE me_seed/photo_studio session
    per-frame — quiet-save must not swallow collection flows."""
    mod = _get_mod()
    msgs = [_photo_msg(f"U_{i}", f"AgACAgIA_{i}") for i in range(3)]
    per_frame: list = []
    ctx = _flush_env(mod, session_active=True)
    ctx += [
        patch.object(mod, "send", lambda cid, txt, **k: None),
        patch.object(mod, "_handle_file_message",
                     side_effect=lambda cid, m, s: per_frame.append(m)),
    ]
    from contextlib import ExitStack
    with ExitStack() as stack:
        for c in ctx:
            stack.enter_context(c)
        buffer = {"mg1": {"msgs": msgs, "seen_uids": set(), "last_seen": 0.0}}
        mod._flush_media_group(buffer, "mg1")
    assert len(per_frame) == 3
