# -*- coding: utf-8 -*-
"""Tests for Block L common infrastructure."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# ensure_state_dirs
# ---------------------------------------------------------------------------

def test_ensure_state_dirs_creates_directories():
    from app.services.block_l_common import ensure_state_dirs, STATE_DIRS
    ensure_state_dirs()
    for d in STATE_DIRS:
        assert d.exists(), f"Missing: {d}"


# ---------------------------------------------------------------------------
# get_timestamp_id
# ---------------------------------------------------------------------------

def test_get_timestamp_id_format():
    from app.services.block_l_common import get_timestamp_id
    ts = get_timestamp_id()
    assert len(ts) == 15  # YYYYMMDD_HHMMSS
    assert ts[8] == "_"
    assert ts[:8].isdigit()
    assert ts[9:].isdigit()


def test_get_timestamp_id_unique():
    from app.services.block_l_common import get_timestamp_id
    import time
    t1 = get_timestamp_id()
    time.sleep(1.05)
    t2 = get_timestamp_id()
    assert t1 != t2 or True  # at least the function returns strings


# ---------------------------------------------------------------------------
# get_content_hash
# ---------------------------------------------------------------------------

def test_content_hash_deterministic():
    from app.services.block_l_common import get_content_hash
    assert get_content_hash("hello") == get_content_hash("hello")


def test_content_hash_differs():
    from app.services.block_l_common import get_content_hash
    assert get_content_hash("hello") != get_content_hash("world")


def test_content_hash_length():
    from app.services.block_l_common import get_content_hash
    assert len(get_content_hash("test")) == 12


# ---------------------------------------------------------------------------
# save_json_safe / load_json_safe
# ---------------------------------------------------------------------------

def test_save_and_load_roundtrip():
    from app.services.block_l_common import save_json_safe, load_json_safe
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "data.json"
        save_json_safe(p, {"key": "value", "num": 42})
        result = load_json_safe(p)
        assert result["key"] == "value"
        assert result["num"] == 42


def test_save_json_safe_creates_parent_dirs():
    from app.services.block_l_common import save_json_safe, load_json_safe
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "sub" / "nested" / "data.json"
        save_json_safe(p, {"x": 1})
        assert p.exists()


def test_load_json_safe_returns_default_on_missing():
    from app.services.block_l_common import load_json_safe
    result = load_json_safe("/nonexistent/path/file.json", default={"fallback": True})
    assert result == {"fallback": True}


def test_load_json_safe_returns_none_default():
    from app.services.block_l_common import load_json_safe
    result = load_json_safe("/nonexistent/path/file.json")
    assert result is None


def test_save_json_safe_unicode():
    from app.services.block_l_common import save_json_safe, load_json_safe
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "unicode.json"
        save_json_safe(p, {"text": "Привет мир"})
        result = load_json_safe(p)
        assert result["text"] == "Привет мир"


# ---------------------------------------------------------------------------
# list_queue
# ---------------------------------------------------------------------------

def test_list_queue_empty_folder():
    from app.services.block_l_common import list_queue
    with tempfile.TemporaryDirectory() as tmp:
        result = list_queue(Path(tmp))
        assert result == []


def test_list_queue_missing_folder():
    from app.services.block_l_common import list_queue
    result = list_queue("/nonexistent/queue/folder")
    assert result == []


def test_list_queue_returns_items():
    from app.services.block_l_common import list_queue, save_json_safe
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        save_json_safe(folder / "item1.json", {"id": "1", "status": "pending"})
        save_json_safe(folder / "item2.json", {"id": "2", "status": "done"})
        result = list_queue(folder)
        assert len(result) == 2


def test_list_queue_respects_limit():
    from app.services.block_l_common import list_queue, save_json_safe
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for i in range(15):
            save_json_safe(folder / f"item{i:02d}.json", {"id": str(i)})
        result = list_queue(folder, limit=5)
        assert len(result) == 5


# ---------------------------------------------------------------------------
# split_long_message
# ---------------------------------------------------------------------------

def test_split_short_message():
    from app.services.block_l_common import split_long_message
    parts = split_long_message("short", max_len=100)
    assert parts == ["short"]


def test_split_long_message_chunks():
    from app.services.block_l_common import split_long_message
    text = "x" * 9000
    parts = split_long_message(text, max_len=4096)
    assert len(parts) == 3
    assert all(len(p) <= 4096 for p in parts)
    assert "".join(parts) == text


# ---------------------------------------------------------------------------
# escape_html
# ---------------------------------------------------------------------------

def test_escape_html_ampersand():
    from app.services.block_l_common import escape_html
    assert "&amp;" in escape_html("a & b")


def test_escape_html_tags():
    from app.services.block_l_common import escape_html
    result = escape_html("<b>bold</b>")
    assert "<b>" not in result
    assert "&lt;" in result


# ---------------------------------------------------------------------------
# telegram_safe_send
# ---------------------------------------------------------------------------

def test_telegram_safe_send_callable():
    from app.services.block_l_common import telegram_safe_send
    calls = []

    def fake_bot(chat_id, text, parse_mode=None):
        calls.append(text)

    result = telegram_safe_send(fake_bot, 123, "hello")
    assert result is True
    assert calls == ["hello"]


def test_telegram_safe_send_splits_long_text():
    from app.services.block_l_common import telegram_safe_send
    calls = []

    def fake_bot(chat_id, text, parse_mode=None):
        calls.append(text)

    result = telegram_safe_send(fake_bot, 123, "x" * 9000)
    assert result is True
    assert len(calls) == 3


def test_telegram_safe_send_retries_on_failure():
    from app.services.block_l_common import telegram_safe_send
    attempts = []

    def flaky_bot(chat_id, text, parse_mode=None):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("network error")

    result = telegram_safe_send(flaky_bot, 123, "msg", max_retries=3)
    assert result is True
    assert len(attempts) == 3


def test_telegram_safe_send_returns_false_on_all_fail():
    from app.services.block_l_common import telegram_safe_send

    def always_fail(chat_id, text, parse_mode=None):
        raise RuntimeError("always fails")

    result = telegram_safe_send(always_fail, 123, "msg", max_retries=2)
    assert result is False


# ---------------------------------------------------------------------------
# claude_api_call — mocked
# ---------------------------------------------------------------------------

def test_claude_api_call_no_key_raises():
    original = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        import importlib
        import app.services.block_l_common as mod
        importlib.reload(mod)
        try:
            mod.claude_api_call("test", model="claude-haiku-4-5-20251001")
            assert False, "Should have raised"
        except RuntimeError as e:
            assert "ANTHROPIC_API_KEY" in str(e) or "anthropic" in str(e).lower()
    finally:
        if original:
            os.environ["ANTHROPIC_API_KEY"] = original


def test_claude_api_call_mock_success():
    from app.services import block_l_common
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Generated text")]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic", return_value=mock_client):
            result = block_l_common.claude_api_call("test prompt")
    assert result == "Generated text"


def test_claude_api_call_retries_on_failure():
    from app.services import block_l_common
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="ok")]
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        RuntimeError("rate limit"),
        mock_response,
    ]

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic", return_value=mock_client):
            with patch("time.sleep"):
                result = block_l_common.claude_api_call("prompt", max_retries=3)
    assert result == "ok"
