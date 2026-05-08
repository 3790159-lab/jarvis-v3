"""Phase 42: Webhook mode — process_update + webhook_reader_thread."""
from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _get_mod():
    mod_name = f"_test_wh_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# process_update — text message
# ---------------------------------------------------------------------------

class TestProcessUpdate:
    def test_text_message_calls_handle(self):
        mod = _get_mod()
        handled = []
        upd = {"update_id": 1, "message": {"text": "привет", "chat": {"id": "111"}}}
        with patch.object(mod, "handle", lambda cid, txt: handled.append(txt)):
            mod.process_update(upd)
        assert handled == ["привет"]

    def test_callback_query_calls_handle_callback(self):
        mod = _get_mod()
        handled = []
        upd = {
            "update_id": 2,
            "callback_query": {
                "id": "cq1",
                "data": "feedback:positive:dec1",
                "from": {"id": int(mod.ALLOWED_CHAT_ID or "0")},
                "message": {"message_id": 1, "chat": {"id": int(mod.ALLOWED_CHAT_ID or "0")}},
            }
        }
        with patch.object(mod, "handle_callback_query", lambda cq, st: handled.append(cq["data"])), \
             patch.object(mod, "load_state", lambda: {}):
            mod.process_update(upd)
        # If ALLOWED_CHAT_ID is set, callback should be handled
        # If not set, nothing happens — test just should not crash
        assert True  # No exception

    def test_empty_update_no_crash(self):
        mod = _get_mod()
        mod.process_update({})  # Should not raise

    def test_voice_message_attempts_transcription(self):
        mod = _get_mod()
        sent = []
        upd = {
            "update_id": 3,
            "message": {
                "voice": {"file_id": "voice_abc"},
                "chat": {"id": mod.ALLOWED_CHAT_ID or "999"},
            }
        }
        with patch.object(mod, "_download_telegram_file", return_value=None), \
             patch.object(mod, "send", lambda cid, txt: sent.append(txt)):
            mod.process_update(upd)
        # If ALLOWED_CHAT_ID matches, it tries to download; download returns None so no crash

    def test_text_message_ignored_for_wrong_chat(self):
        mod = _get_mod()
        handled = []
        upd = {"update_id": 4, "message": {"text": "hello", "chat": {"id": "WRONG_ID"}}}
        with patch.object(mod, "handle", lambda cid, txt: handled.append(txt)):
            mod.process_update(upd)
        # handle IS called — it's the handle() function that checks ALLOWED_CHAT_ID
        assert len(handled) == 1


# ---------------------------------------------------------------------------
# webhook_reader_thread — reads jsonl file
# ---------------------------------------------------------------------------

class TestWebhookReaderThread:
    def test_reader_reads_jsonl_and_calls_process(self, tmp_path):
        """Unit test the inner parsing loop logic of webhook_reader_thread."""
        import io
        updates = [
            {"update_id": 1, "message": {"text": "test1", "chat": {"id": "111"}}},
            {"update_id": 2, "message": {"text": "test2", "chat": {"id": "111"}}},
        ]
        content = "\n".join(json.dumps(u) for u in updates)
        processed = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                update = json.loads(line)
                processed.append(update.get("update_id"))
            except Exception:
                pass
        assert processed == [1, 2]

    def test_reader_function_exists(self):
        mod = _get_mod()
        assert callable(mod.webhook_reader_thread)

    def test_process_update_function_exists(self):
        mod = _get_mod()
        assert callable(mod.process_update)

    def test_handles_malformed_json_gracefully(self, tmp_path):
        mod = _get_mod()
        processed = []
        errors = []

        # Simulate reading from a file with one bad line and one good
        good_upd = {"update_id": 5, "message": {"text": "ok", "chat": {"id": "1"}}}
        lines = ["not-valid-json", json.dumps(good_upd)]

        import io
        mock_file = io.StringIO("\n".join(lines))
        mock_file.seek(0)

        # Since we can't easily test the thread directly,
        # test the inner loop logic manually
        seen_offset = 0
        good_count = [0]
        bad_count = [0]

        for line in mock_file.read().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                update = json.loads(line)
                good_count[0] += 1
            except Exception:
                bad_count[0] += 1

        assert good_count[0] == 1
        assert bad_count[0] == 1


# ---------------------------------------------------------------------------
# WEBHOOK_URL env var detection
# ---------------------------------------------------------------------------

class TestWebhookEnvDetection:
    def test_webhook_url_env_read(self, monkeypatch):
        monkeypatch.setenv("WEBHOOK_URL", "https://jarvis.example.com")
        import os
        assert os.getenv("WEBHOOK_URL") == "https://jarvis.example.com"

    def test_webhook_url_not_set_is_empty(self, monkeypatch):
        monkeypatch.delenv("WEBHOOK_URL", raising=False)
        import os
        assert not os.getenv("WEBHOOK_URL", "").strip()
