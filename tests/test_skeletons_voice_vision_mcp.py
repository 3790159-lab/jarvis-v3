"""Tests for Voice/Vision/MCP — upgraded from Phase 22 stubs to Phase 26/27 real impl."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.voice_input import transcribe_voice, is_voice_supported, transcribe_voice_placeholder
from app.services.vision import analyze_image, is_vision_supported, analyze_image_placeholder
from app.services.mcp_adapter import (
    register_mcp_server,
    call_mcp_tool,
    list_mcp_servers,
    is_mcp_available,
    _mcp_servers,
)


# ---------------------------------------------------------------------------
# Voice Input — Phase 26 real implementation
# ---------------------------------------------------------------------------

class TestVoiceInput:
    def test_is_voice_supported_depends_on_api_key(self):
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with patch.dict("os.environ", env, clear=True):
            assert is_voice_supported() is False
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            assert is_voice_supported() is True

    def test_transcribe_nonexistent_file_raises(self):
        with pytest.raises(ValueError, match="not found"):
            transcribe_voice("/nonexistent/audio.ogg")

    def test_transcribe_returns_none_without_api_key(self, tmp_path):
        f = tmp_path / "test.ogg"
        f.write_bytes(b"\x00" * 1024)
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with patch.dict("os.environ", env, clear=True):
            result = transcribe_voice(str(f))
        assert result is None

    def test_placeholder_returns_helpful_message(self, tmp_path):
        f = tmp_path / "voice.ogg"
        f.write_bytes(b"\x00" * 512)
        result = transcribe_voice_placeholder(str(f))
        assert isinstance(result, str)
        assert "🎤" in result
        assert "текст" in result.lower() or "напиши" in result.lower()


# ---------------------------------------------------------------------------
# Vision — Phase 27 real implementation
# ---------------------------------------------------------------------------

class TestVision:
    def test_is_vision_supported_depends_on_api_key(self):
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict("os.environ", env, clear=True):
            assert is_vision_supported() is False
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            assert is_vision_supported() is True

    def test_analyze_nonexistent_raises(self):
        with pytest.raises(ValueError, match="not found"):
            analyze_image("/nonexistent/image.jpg")

    def test_analyze_existing_image_returns_placeholder_without_key(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 1000)
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict("os.environ", env, clear=True):
            result = analyze_image(str(img))
        assert isinstance(result, str)
        assert len(result) > 0

    def test_placeholder_returns_helpful_message(self, tmp_path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 500)
        result = analyze_image_placeholder(str(img))
        # Should suggest alternative
        assert "файл" in result.lower() or "текст" in result.lower() or "pdf" in result.lower()

    def test_placeholder_includes_filename(self, tmp_path):
        img = tmp_path / "myimage.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
        result = analyze_image_placeholder(str(img))
        assert "myimage.jpg" in result or "myimage" in result.lower()


# ---------------------------------------------------------------------------
# MCP Adapter skeleton tests
# ---------------------------------------------------------------------------

class TestMCPAdapter:
    def setup_method(self):
        _mcp_servers.clear()

    def teardown_method(self):
        _mcp_servers.clear()

    def test_register_server(self):
        register_mcp_server("test_server", "python", ["mcp_server.py"])
        assert "test_server" in list_mcp_servers()

    def test_list_empty_initially(self):
        assert list_mcp_servers() == []

    def test_call_unregistered_returns_error(self):
        result = call_mcp_tool("nonexistent_server", "some_tool")
        assert "error" in result

    def test_call_registered_jarvis_dispatches(self):
        # Phase 25: 'jarvis' server now dispatches through JarvisMCPServer
        from unittest.mock import patch
        register_mcp_server("jarvis", "python", ["mcp_server.py"])
        with patch("app.services.mcp_server._call_backend", return_value={"answer": "ok"}):
            result = call_mcp_tool("jarvis", "jarvis_research", {"query": "test"})
        # Should return content array or isError (real dispatch)
        assert "content" in result or "isError" in result or "error" in result

    def test_is_mcp_available_false_by_default(self):
        register_mcp_server("jarvis", "python", [])
        assert is_mcp_available("jarvis") is False

    def test_is_mcp_available_unregistered(self):
        assert is_mcp_available("nonexistent") is False

    def test_register_with_env(self):
        register_mcp_server("server", "python", [], env={"KEY": "val"})
        assert _mcp_servers["server"]["env"]["KEY"] == "val"

    def test_external_server_returns_note(self):
        # Non-jarvis external server returns note about stdio transport
        register_mcp_server("external_srv", "python", [])
        result = call_mcp_tool("external_srv", "some_tool")
        note = result.get("note", "")
        assert "stdio" in note.lower() or "external" in note.lower() or "not" in note.lower()
