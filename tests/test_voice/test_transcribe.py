# -*- coding: utf-8 -*-
"""Unit tests for ``app.services.unified.voice.transcribe``.

The OpenAI Whisper client is *injected* (``openai_module=``) so these tests
never make a network call. We cover: a successful transcription with cost from
duration, a missing key, an API failure, empty input, an empty result, and the
optional format-conversion hook (so no real ffmpeg is required).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.voice.transcribe import (
    TranscriptionResult,
    compute_transcription_cost,
    transcribe_audio,
)


class _FakeTranscriptions:
    def __init__(self, text: str = "", raises: bool = False) -> None:
        self._text = text
        self._raises = raises
        self.received: dict | None = None

    def create(self, *, model, file, language, **kw):
        if self._raises:
            raise RuntimeError("whisper down")
        self.received = {"model": model, "file": file, "language": language}
        return SimpleNamespace(text=self._text)


class _FakeOpenAIModule:
    """Stand-in for the ``openai`` package exposing ``OpenAI``."""

    def __init__(self, text: str = "привет мир", raises: bool = False) -> None:
        self.transcriptions = _FakeTranscriptions(text, raises)
        self.constructed_with: dict | None = None

    def OpenAI(self, *, api_key):  # noqa: N802 - mirrors SDK surface
        self.constructed_with = {"api_key": api_key}
        audio = SimpleNamespace(transcriptions=self.transcriptions)
        return SimpleNamespace(audio=audio)


def test_transcribe_success_returns_text_and_cost():
    mod = _FakeOpenAIModule(text="  привет, как дела  ")
    result = transcribe_audio(
        b"OggS-fake-bytes", api_key="sk-x", openai_module=mod, duration_sec=60
    )
    assert isinstance(result, TranscriptionResult)
    assert not result.is_error
    assert result.text == "привет, как дела"  # trimmed
    # 60s = 1 min × $0.006/min
    assert result.cost_usd == pytest.approx(0.006)
    # Russian language + whisper-1 were requested.
    assert mod.transcriptions.received["language"] == "ru"
    assert mod.transcriptions.received["model"] == "whisper-1"


def test_transcribe_missing_key_fails_gracefully(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = transcribe_audio(b"bytes", openai_module=_FakeOpenAIModule())
    assert result.is_error
    assert "OPENAI_API_KEY" in result.error


def test_transcribe_api_error_fails_gracefully():
    mod = _FakeOpenAIModule(raises=True)
    result = transcribe_audio(b"bytes", api_key="sk-x", openai_module=mod)
    assert result.is_error
    assert "whisper down" in result.error


def test_transcribe_empty_audio_fails_without_calling_api():
    mod = _FakeOpenAIModule()
    result = transcribe_audio(b"", api_key="sk-x", openai_module=mod)
    assert result.is_error
    assert mod.transcriptions.received is None  # API never called


def test_transcribe_empty_result_is_an_error():
    mod = _FakeOpenAIModule(text="   ")
    result = transcribe_audio(b"bytes", api_key="sk-x", openai_module=mod, duration_sec=5)
    assert result.is_error
    assert "распозна" in result.error.lower()


def test_transcribe_applies_conversion_hook():
    converted = []

    def fake_convert(raw: bytes) -> bytes:
        converted.append(raw)
        return b"CONVERTED-WAV"

    mod = _FakeOpenAIModule(text="ок")
    result = transcribe_audio(
        b"OGG-RAW",
        api_key="sk-x",
        openai_module=mod,
        convert_fn=fake_convert,
        duration_sec=3,
    )
    assert not result.is_error
    assert converted == [b"OGG-RAW"]
    # The converted bytes (not the raw ogg) were sent to Whisper.
    sent_file = mod.transcriptions.received["file"]
    assert b"CONVERTED-WAV" in (sent_file[1] if isinstance(sent_file, tuple) else sent_file)


def test_compute_transcription_cost_per_minute():
    assert compute_transcription_cost(120) == pytest.approx(0.012)
    assert compute_transcription_cost(0) == 0.0
    assert compute_transcription_cost(None) == 0.0
