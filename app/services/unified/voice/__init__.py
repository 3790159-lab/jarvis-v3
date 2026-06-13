# -*- coding: utf-8 -*-
"""Unified-Jarvis voice I/O — Phase-4 Step 2.

Two thin, injectable layers over external speech APIs:

- :mod:`~app.services.unified.voice.transcribe` — Telegram voice note
  (ogg/opus) → Russian text via OpenAI Whisper.
- :mod:`~app.services.unified.voice.synthesize` — text → voice note via a
  pluggable TTS provider (OpenAI or ElevenLabs).

Both degrade gracefully (return an error result, never raise) when the API key
or SDK is missing so the bot can fall back to text-only behaviour.
"""
from app.services.unified.voice.synthesize import (
    SynthesisResult,
    resolve_tts_provider,
    synthesize_speech,
    voice_reply_enabled,
)
from app.services.unified.voice.transcribe import (
    TranscriptionResult,
    compute_transcription_cost,
    transcribe_audio,
)

__all__ = [
    "TranscriptionResult",
    "transcribe_audio",
    "compute_transcription_cost",
    "SynthesisResult",
    "synthesize_speech",
    "resolve_tts_provider",
    "voice_reply_enabled",
]
