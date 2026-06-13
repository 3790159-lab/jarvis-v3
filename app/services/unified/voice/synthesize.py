# -*- coding: utf-8 -*-
"""Synthesize text into a Telegram-compatible voice note (TTS).

:func:`synthesize_speech` turns text into audio bytes via a pluggable provider
(``JARVIS_TTS_PROVIDER``: ``openai`` | ``elevenlabs``). The OpenAI SDK module
and the ElevenLabs HTTP call are injectable so tests make no network call.
Failures never raise — they return a :class:`SynthesisResult` with ``error``
set, and the bot simply skips the voice reply.

Voice replies are opt-in: :func:`voice_reply_enabled` reads
``JARVIS_VOICE_REPLY_ENABLED`` (default ``0``).

Cost is billed per output character (OpenAI ``tts-1`` ≈ $15/1M chars;
ElevenLabs is an estimate — credit pricing varies by plan).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

# USD per output character.
OPENAI_TTS_PRICE_PER_CHAR = 15.0 / 1_000_000
# Estimate — ElevenLabs bills in credits and varies by plan (~$0.30/1k chars).
ELEVENLABS_PRICE_PER_CHAR = 0.30 / 1_000

# Telegram voice notes want an Ogg/Opus container; map the requested codec.
_FORMAT_CONTAINER = {"opus": "ogg", "mp3": "mp3", "aac": "aac", "wav": "wav", "flac": "flac"}

# elevenlabs_synth_fn: (text, *, api_key, voice) -> bytes
ElevenLabsSynthFn = Callable[..., bytes]


@dataclass
class SynthesisResult:
    """Outcome of :func:`synthesize_speech`."""

    audio: bytes = b""
    audio_format: str = "ogg"
    cost_usd: float = 0.0
    provider: str = ""
    error: str = ""

    @property
    def is_error(self) -> bool:
        return bool(self.error)


def resolve_tts_provider(provider: Optional[str] = None) -> str:
    """The configured TTS provider, normalised to lower-case."""
    val = provider if provider is not None else os.getenv("JARVIS_TTS_PROVIDER", "")
    return (val or "openai").strip().lower() or "openai"


def voice_reply_enabled() -> bool:
    """Whether spoken replies are turned on (``JARVIS_VOICE_REPLY_ENABLED=1``)."""
    return os.getenv("JARVIS_VOICE_REPLY_ENABLED", "0").strip() == "1"


def _synthesize_openai(
    text: str,
    *,
    api_key: Optional[str],
    openai_module: Any,
    voice: str,
    audio_format: str,
    model: str,
) -> SynthesisResult:
    key = (api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")).strip()
    if not key:
        return SynthesisResult(provider="openai", error="OPENAI_API_KEY (ключ) не задан.")

    mod = openai_module
    if mod is None:
        try:
            import openai as mod  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001
            return SynthesisResult(provider="openai", error="openai SDK не установлен.")

    try:
        client = mod.OpenAI(api_key=key)
        response = client.audio.speech.create(
            model=model,
            voice=voice,
            input=text,
            response_format=audio_format,
        )
        audio = _extract_audio_bytes(response)
    except Exception as exc:  # noqa: BLE001
        return SynthesisResult(provider="openai", error=f"Ошибка OpenAI TTS: {exc}")

    if not audio:
        return SynthesisResult(provider="openai", error="TTS вернул пустой аудиопоток.")
    return SynthesisResult(
        audio=audio,
        audio_format=_FORMAT_CONTAINER.get(audio_format, audio_format),
        cost_usd=len(text) * OPENAI_TTS_PRICE_PER_CHAR,
        provider="openai",
    )


def _synthesize_elevenlabs(
    text: str,
    *,
    api_key: Optional[str],
    synth_fn: Optional[ElevenLabsSynthFn],
    voice: str,
) -> SynthesisResult:
    key = (api_key if api_key is not None else os.getenv("ELEVENLABS_API_KEY", "")).strip()
    if not key:
        return SynthesisResult(provider="elevenlabs", error="ELEVENLABS_API_KEY (ключ) не задан.")
    if synth_fn is None:
        return SynthesisResult(
            provider="elevenlabs", error="ElevenLabs backend не подключён."
        )
    try:
        audio = synth_fn(text, api_key=key, voice=voice)
    except Exception as exc:  # noqa: BLE001
        return SynthesisResult(provider="elevenlabs", error=f"Ошибка ElevenLabs: {exc}")
    if not audio:
        return SynthesisResult(provider="elevenlabs", error="ElevenLabs вернул пустой аудиопоток.")
    return SynthesisResult(
        audio=bytes(audio),
        audio_format="mp3",
        cost_usd=len(text) * ELEVENLABS_PRICE_PER_CHAR,
        provider="elevenlabs",
    )


def _extract_audio_bytes(response: Any) -> bytes:
    """Pull raw bytes out of whatever shape the SDK returns."""
    if hasattr(response, "read"):
        return response.read()
    if hasattr(response, "content"):
        return response.content
    return bytes(response)


def synthesize_speech(
    text: str,
    *,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    openai_module: Any = None,
    elevenlabs_synth_fn: Optional[ElevenLabsSynthFn] = None,
    voice: str = "alloy",
    audio_format: str = "opus",
    model: str = "tts-1",
) -> SynthesisResult:
    """Synthesize ``text`` to audio bytes via the selected TTS provider."""
    text = (text or "").strip()
    name = resolve_tts_provider(provider)
    if not text:
        return SynthesisResult(provider=name, error="Пустой текст для синтеза.")

    if name == "openai":
        return _synthesize_openai(
            text,
            api_key=api_key,
            openai_module=openai_module,
            voice=voice,
            audio_format=audio_format,
            model=model,
        )
    if name == "elevenlabs":
        return _synthesize_elevenlabs(
            text, api_key=api_key, synth_fn=elevenlabs_synth_fn, voice=voice
        )
    return SynthesisResult(provider=name, error=f"Неизвестный TTS-провайдер: {name}")
