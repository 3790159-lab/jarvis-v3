"""Tests for Phase 26: Voice Input — Whisper transcription."""
from __future__ import annotations

import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.voice_input import (
    is_voice_supported,
    transcribe_voice,
    transcribe_voice_placeholder,
)


# ─── is_voice_supported ──────────────────────────────────────────────────────

class TestIsVoiceSupported:
    def test_true_when_api_key_set(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
            assert is_voice_supported() is True

    def test_false_when_api_key_missing(self):
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        with patch.dict("os.environ", env, clear=True):
            assert is_voice_supported() is False

    def test_false_when_api_key_empty(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            assert is_voice_supported() is False


# ─── transcribe_voice ─────────────────────────────────────────────────────────

def _make_openai_mock(text="Сколько планет в Солнечной системе?"):
    """Inject fake openai module into sys.modules."""
    mock_response = MagicMock()
    mock_response.text = text

    mock_transcriptions = MagicMock()
    mock_transcriptions.create.return_value = mock_response

    mock_audio = MagicMock()
    mock_audio.transcriptions = mock_transcriptions

    mock_client = MagicMock()
    mock_client.audio = mock_audio

    mock_openai_mod = types.ModuleType("openai")
    mock_openai_mod.OpenAI = MagicMock(return_value=mock_client)

    return mock_openai_mod, mock_client


class TestTranscribeVoice:
    def _temp_audio(self, content=b"fake audio data"):
        f = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
        f.write(content)
        f.close()
        return f.name

    def test_raises_on_missing_file(self):
        import pytest
        with pytest.raises(ValueError, match="not found"):
            transcribe_voice("/nonexistent/path/voice.ogg")

    def test_returns_none_without_api_key(self):
        audio = self._temp_audio()
        env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
        try:
            with patch.dict("os.environ", env, clear=True):
                result = transcribe_voice(audio)
        finally:
            Path(audio).unlink(missing_ok=True)
        assert result is None

    def test_returns_transcribed_text(self):
        audio = self._temp_audio()
        mock_mod, mock_client = _make_openai_mock("Привет мир")
        try:
            with patch.dict(sys.modules, {"openai": mock_mod}):
                with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
                    import importlib
                    import app.services.voice_input as vi_mod
                    importlib.reload(vi_mod)
                    result = vi_mod.transcribe_voice(audio)
        finally:
            Path(audio).unlink(missing_ok=True)
        assert result == "Привет мир"

    def test_uses_whisper_1_model(self):
        audio = self._temp_audio()
        mock_mod, mock_client = _make_openai_mock("test")
        try:
            with patch.dict(sys.modules, {"openai": mock_mod}):
                with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
                    import importlib
                    import app.services.voice_input as vi_mod
                    importlib.reload(vi_mod)
                    vi_mod.transcribe_voice(audio)
        finally:
            Path(audio).unlink(missing_ok=True)
        create_call = mock_client.audio.transcriptions.create.call_args
        assert create_call.kwargs.get("model") == "whisper-1" or "whisper-1" in str(create_call)

    def test_returns_none_on_api_error(self):
        audio = self._temp_audio()
        mock_mod = types.ModuleType("openai")
        mock_mod.OpenAI = MagicMock(side_effect=Exception("API error"))
        try:
            with patch.dict(sys.modules, {"openai": mock_mod}):
                with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
                    import importlib
                    import app.services.voice_input as vi_mod
                    importlib.reload(vi_mod)
                    result = vi_mod.transcribe_voice(audio)
        finally:
            Path(audio).unlink(missing_ok=True)
        assert result is None

    def test_returns_none_for_empty_transcription(self):
        audio = self._temp_audio()
        mock_mod, _ = _make_openai_mock("")  # empty transcription
        try:
            with patch.dict(sys.modules, {"openai": mock_mod}):
                with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
                    import importlib
                    import app.services.voice_input as vi_mod
                    importlib.reload(vi_mod)
                    result = vi_mod.transcribe_voice(audio)
        finally:
            Path(audio).unlink(missing_ok=True)
        assert result is None

    def test_strips_whitespace_from_result(self):
        audio = self._temp_audio()
        mock_mod, _ = _make_openai_mock("  Привет мир  ")
        try:
            with patch.dict(sys.modules, {"openai": mock_mod}):
                with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
                    import importlib
                    import app.services.voice_input as vi_mod
                    importlib.reload(vi_mod)
                    result = vi_mod.transcribe_voice(audio)
        finally:
            Path(audio).unlink(missing_ok=True)
        assert result == "Привет мир"


# ─── transcribe_voice_placeholder ────────────────────────────────────────────

class TestPlaceholder:
    def test_contains_emoji(self):
        f = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
        f.write(b"data")
        f.close()
        try:
            result = transcribe_voice_placeholder(f.name)
        finally:
            Path(f.name).unlink(missing_ok=True)
        assert "🎤" in result

    def test_suggests_text_input(self):
        f = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
        f.write(b"data")
        f.close()
        try:
            result = transcribe_voice_placeholder(f.name)
        finally:
            Path(f.name).unlink(missing_ok=True)
        assert "текст" in result.lower() or "напиши" in result.lower()

    def test_handles_nonexistent_file_gracefully(self):
        result = transcribe_voice_placeholder("/tmp/nonexistent.ogg")
        assert "🎤" in result
