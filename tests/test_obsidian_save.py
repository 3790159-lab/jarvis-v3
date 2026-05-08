"""Phase H1.3: Obsidian save endpoint tests."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.routers.obsidian_save_router import obsidian_save, obsidian_health, ObsidianSaveRequest


# ---------------------------------------------------------------------------
# obsidian_health
# ---------------------------------------------------------------------------

class TestObsidianHealth:
    def test_not_configured_when_no_env(self, monkeypatch):
        monkeypatch.delenv("JARVIS_OBSIDIAN_VAULT_PATH", raising=False)
        result = obsidian_health()
        assert result["configured"] is False
        assert result["vault_exists"] is False

    def test_configured_but_not_exists(self, monkeypatch):
        monkeypatch.setenv("JARVIS_OBSIDIAN_VAULT_PATH", "/nonexistent/vault")
        result = obsidian_health()
        assert result["configured"] is True
        assert result["vault_exists"] is False

    def test_configured_and_exists(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JARVIS_OBSIDIAN_VAULT_PATH", str(tmp_path))
        result = obsidian_health()
        assert result["configured"] is True
        assert result["vault_exists"] is True


# ---------------------------------------------------------------------------
# obsidian_save
# ---------------------------------------------------------------------------

class TestObsidianSave:
    def test_error_when_no_vault_path(self, monkeypatch):
        monkeypatch.delenv("JARVIS_OBSIDIAN_VAULT_PATH", raising=False)
        req = ObsidianSaveRequest(content="test", title="My Note")
        result = obsidian_save(req)
        assert result["ok"] is False
        assert "JARVIS_OBSIDIAN_VAULT_PATH" in result["error"]

    def test_error_when_vault_not_exists(self, monkeypatch):
        monkeypatch.setenv("JARVIS_OBSIDIAN_VAULT_PATH", "/nonexistent/vault/path")
        req = ObsidianSaveRequest(content="test")
        result = obsidian_save(req)
        assert result["ok"] is False
        assert "not found" in result["error"].lower()

    def test_saves_file_to_vault(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JARVIS_OBSIDIAN_VAULT_PATH", str(tmp_path))
        req = ObsidianSaveRequest(content="Test content", title="Test Note", folder="Jarvis")
        result = obsidian_save(req)
        assert result["ok"] is True
        assert "path" in result
        assert (tmp_path / result["path"]).exists()

    def test_creates_folder_if_not_exists(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JARVIS_OBSIDIAN_VAULT_PATH", str(tmp_path))
        req = ObsidianSaveRequest(content="Content", title="Note", folder="SubFolder/Deep")
        result = obsidian_save(req)
        assert result["ok"] is True
        assert (tmp_path / "SubFolder" / "Deep").is_dir()

    def test_sanitizes_title(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JARVIS_OBSIDIAN_VAULT_PATH", str(tmp_path))
        req = ObsidianSaveRequest(content="c", title='Bad/Title:With*Chars?', folder="Jarvis")
        result = obsidian_save(req)
        assert result["ok"] is True
        filename = Path(result["absolute_path"]).name
        assert "/" not in filename
        assert ":" not in filename
        assert "*" not in filename

    def test_default_folder_is_jarvis(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JARVIS_OBSIDIAN_VAULT_PATH", str(tmp_path))
        req = ObsidianSaveRequest(content="content")
        result = obsidian_save(req)
        assert result["ok"] is True
        assert result["path"].startswith("Jarvis")

    def test_file_content_correct(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JARVIS_OBSIDIAN_VAULT_PATH", str(tmp_path))
        content = "# My Note\nHello world\n\nSecond paragraph"
        req = ObsidianSaveRequest(content=content, title="TestContent")
        result = obsidian_save(req)
        assert result["ok"] is True
        saved = Path(result["absolute_path"]).read_text(encoding="utf-8")
        assert saved == content

    def test_empty_title_defaults_to_note(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JARVIS_OBSIDIAN_VAULT_PATH", str(tmp_path))
        req = ObsidianSaveRequest(content="hello", title="")
        result = obsidian_save(req)
        assert result["ok"] is True
        assert "Note" in result["filename"]


# ---------------------------------------------------------------------------
# Router endpoints
# ---------------------------------------------------------------------------

class TestObsidianRouterEndpoints:
    def test_save_route_registered(self):
        from app.routers.obsidian_save_router import router
        paths = [r.path for r in router.routes]
        assert "/api/jarvis/tools/obsidian/save" in paths

    def test_health_route_registered(self):
        from app.routers.obsidian_save_router import router
        paths = [r.path for r in router.routes]
        assert "/api/jarvis/tools/obsidian/health" in paths
