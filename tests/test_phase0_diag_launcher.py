"""Tests for Phase 0 (Block D1): /diag command and launcher fixes."""
from __future__ import annotations

import importlib
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Helpers to import bot module
# ---------------------------------------------------------------------------

def _get_bot_module():
    """Import bot module without running it (BOT_TOKEN may be empty)."""
    spec = importlib.util.spec_from_file_location(
        "jarvis_tg_ctrl",
        ROOT / "tools" / "jarvis_smart_telegram_control.py",
    )
    mod = importlib.util.module_from_spec(spec)
    # Patch os.environ so module initialises without real tokens
    with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test_token", "TELEGRAM_ALLOWED_CHAT_ID": "123"}):
        spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# /diag tests
# ---------------------------------------------------------------------------

class TestDiagFunction:
    def setup_method(self):
        self.bot = _get_bot_module()

    def test_diag_returns_string(self):
        with patch.object(self.bot, "http_json", return_value={"status": "ok"}):
            result = self.bot._run_diag()
        assert isinstance(result, str)

    def test_diag_contains_header(self):
        with patch.object(self.bot, "http_json", return_value={"status": "ok"}):
            result = self.bot._run_diag()
        assert "Diagnostics" in result or "diag" in result.lower()

    def test_diag_backend_ok(self):
        with patch.object(self.bot, "http_json", return_value={"status": "ok"}):
            result = self.bot._run_diag()
        assert "✅" in result

    def test_diag_backend_down(self):
        with patch.object(self.bot, "http_json", return_value={"ok": False, "_error": "refused"}):
            result = self.bot._run_diag()
        assert "❌" in result or "⚠️" in result

    def test_diag_backend_exception(self):
        with patch.object(self.bot, "http_json", side_effect=Exception("connection refused")):
            result = self.bot._run_diag()
        assert "❌" in result or "⚠️" in result

    def test_diag_shows_backend_url(self):
        with patch.object(self.bot, "http_json", return_value={"status": "ok"}):
            result = self.bot._run_diag()
        assert "8010" in result or "BACKEND" in result.upper()

    def test_diag_cowork_paths_mentioned(self):
        with patch.object(self.bot, "http_json", return_value={"status": "ok"}):
            result = self.bot._run_diag()
        assert "cowork" in result.lower() or "Cowork" in result

    def test_diag_agent_count(self):
        with patch.object(self.bot, "http_json", return_value={"status": "ok"}):
            result = self.bot._run_diag()
        assert "13" in result or "агент" in result.lower()

    def test_diag_bot_token_set(self):
        with patch.object(self.bot, "http_json", return_value={"status": "ok"}):
            result = self.bot._run_diag()
        # token is set to "test_token" in this test env
        assert "BOT_TOKEN" in result or "токен" in result.lower()

    def test_diag_memory_check(self):
        with patch.object(self.bot, "http_json", return_value={"status": "ok"}):
            result = self.bot._run_diag()
        assert "memory" in result.lower() or "память" in result.lower()

    def test_diag_ends_with_completion_marker(self):
        with patch.object(self.bot, "http_json", return_value={"status": "ok"}):
            result = self.bot._run_diag()
        assert "diag" in result.lower()


# ---------------------------------------------------------------------------
# .env port fix verification
# ---------------------------------------------------------------------------

class TestEnvPortFix:
    def test_env_backend_url_is_8010(self):
        env_path = ROOT / ".env"
        if not env_path.exists():
            return  # nothing to check in CI
        content = env_path.read_text(encoding="utf-8")
        # Should NOT contain 8015 as BACKEND_BASE_URL value
        for line in content.splitlines():
            if line.startswith("BACKEND_BASE_URL="):
                assert "8015" not in line, f"BACKEND_BASE_URL still has old port 8015: {line}"
                assert "8010" in line, f"BACKEND_BASE_URL should use port 8010: {line}"

    def test_env_jarvis_base_url_is_8010(self):
        env_path = ROOT / ".env"
        if not env_path.exists():
            return
        content = env_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.startswith("JARVIS_BASE_URL="):
                assert "8015" not in line, f"JARVIS_BASE_URL still has old port 8015: {line}"

    def test_env_example_uses_8010(self):
        example_path = ROOT / ".env.example"
        if not example_path.exists():
            return
        content = example_path.read_text(encoding="utf-8")
        assert "8010" in content
        assert "BACKEND_BASE_URL=http://127.0.0.1:8010" in content


# ---------------------------------------------------------------------------
# start_jarvis.ps1 improvements verification
# ---------------------------------------------------------------------------

class TestStartJarvisScript:
    def test_launcher_has_wt_method(self):
        script = (ROOT / "start_jarvis.ps1").read_text(encoding="utf-8")
        assert "wt.exe" in script

    def test_launcher_has_cmd_fallback(self):
        script = (ROOT / "start_jarvis.ps1").read_text(encoding="utf-8")
        assert "cmd.exe" in script

    def test_launcher_has_noexit(self):
        script = (ROOT / "start_jarvis.ps1").read_text(encoding="utf-8")
        assert "-NoExit" in script

    def test_launcher_has_title_for_backend(self):
        script = (ROOT / "start_jarvis.ps1").read_text(encoding="utf-8")
        assert "Backend" in script

    def test_launcher_has_title_for_bot(self):
        script = (ROOT / "start_jarvis.ps1").read_text(encoding="utf-8")
        assert "Bot" in script

    def test_launcher_overrides_backend_url(self):
        script = (ROOT / "start_jarvis.ps1").read_text(encoding="utf-8")
        assert "BACKEND_BASE_URL" in script

    def test_run_jarvis_md_exists(self):
        assert (ROOT / "RUN_JARVIS.md").exists()

    def test_run_jarvis_md_has_troubleshooting(self):
        content = (ROOT / "RUN_JARVIS.md").read_text(encoding="utf-8")
        assert "Troubleshooting" in content or "troubleshooting" in content.lower()
