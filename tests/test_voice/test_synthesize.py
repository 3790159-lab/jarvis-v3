# -*- coding: utf-8 -*-
"""Unit tests for ``app.services.unified.voice.synthesize``.

Provider backends are injected (OpenAI SDK module / ElevenLabs callable) so no
network call is made. We cover provider selection, the voice-reply feature
flag, a successful OpenAI synthesis with per-character cost, the injected
ElevenLabs path, and graceful failures (no key, empty text, unknown provider).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.unified.voice.synthesize import (
    OPENAI_TTS_PRICE_PER_CHAR,
    SynthesisResult,
    resolve_tts_provider,
    synthesize_speech,
    voice_reply_enabled,
)


class _FakeSpeech:
    def __init__(self, audio: bytes = b"OPUS-AUDIO") -> None:
        self._audio = audio
        self.received: dict | None = None

    def create(self, *, model, voice, input, response_format, **kw):
        self.received = {
            "model": model,
            "voice": voice,
            "input": input,
            "response_format": response_format,
        }
        return SimpleNamespace(read=lambda: self._audio)


class _FakeOpenAIModule:
    def __init__(self, audio: bytes = b"OPUS-AUDIO") -> None:
        self.speech = _FakeSpeech(audio)
        self.constructed_with: dict | None = None

    def OpenAI(self, *, api_key):  # noqa: N802 - mirrors SDK surface
        self.constructed_with = {"api_key": api_key}
        return SimpleNamespace(audio=SimpleNamespace(speech=self.speech))


# ── provider selection + feature flag ────────────────────────────────────────


def test_resolve_tts_provider_defaults_to_openai(monkeypatch):
    monkeypatch.delenv("JARVIS_TTS_PROVIDER", raising=False)
    assert resolve_tts_provider() == "openai"


def test_resolve_tts_provider_from_env(monkeypatch):
    monkeypatch.setenv("JARVIS_TTS_PROVIDER", "ElevenLabs")
    assert resolve_tts_provider() == "elevenlabs"  # normalised


def test_voice_reply_disabled_by_default(monkeypatch):
    monkeypatch.delenv("JARVIS_VOICE_REPLY_ENABLED", raising=False)
    assert voice_reply_enabled() is False
    monkeypatch.setenv("JARVIS_VOICE_REPLY_ENABLED", "1")
    assert voice_reply_enabled() is True


# ── synthesis ────────────────────────────────────────────────────────────────


def test_synthesize_openai_success_with_cost():
    mod = _FakeOpenAIModule(audio=b"OGG-OPUS-BYTES")
    result = synthesize_speech(
        "привет", provider="openai", api_key="sk-x", openai_module=mod
    )
    assert isinstance(result, SynthesisResult)
    assert not result.is_error
    assert result.audio == b"OGG-OPUS-BYTES"
    assert result.provider == "openai"
    assert result.cost_usd == pytest.approx(len("привет") * OPENAI_TTS_PRICE_PER_CHAR)
    assert mod.speech.received["input"] == "привет"


def test_synthesize_elevenlabs_uses_injected_backend():
    seen = []

    def fake_eleven(text, *, api_key, voice):
        seen.append((text, api_key, voice))
        return b"ELEVEN-AUDIO"

    result = synthesize_speech(
        "hello",
        provider="elevenlabs",
        api_key="el-key",
        elevenlabs_synth_fn=fake_eleven,
    )
    assert not result.is_error
    assert result.provider == "elevenlabs"
    assert result.audio == b"ELEVEN-AUDIO"
    assert seen and seen[0][0] == "hello"


def test_synthesize_missing_key_fails_gracefully(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = synthesize_speech("текст", provider="openai", openai_module=_FakeOpenAIModule())
    assert result.is_error
    assert "ключ" in result.error.lower() or "key" in result.error.lower()


def test_synthesize_empty_text_is_an_error():
    result = synthesize_speech("   ", provider="openai", api_key="sk-x")
    assert result.is_error


def test_synthesize_unknown_provider_is_an_error():
    result = synthesize_speech("текст", provider="martian", api_key="x")
    assert result.is_error
    assert "martian" in result.error
