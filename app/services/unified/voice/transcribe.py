# -*- coding: utf-8 -*-
"""Transcribe a Telegram voice note (ogg/opus) to Russian text via Whisper.

:func:`transcribe_audio` accepts raw bytes or a file path, optionally runs a
format-conversion hook (``convert_fn`` — ffmpeg lives outside this module so it
stays unit-testable), and calls the OpenAI Whisper API. The OpenAI SDK module
is injectable for tests. Failures never raise — they come back as a
:class:`TranscriptionResult` with ``error`` set so the bot can fall back to a
placeholder.

Cost is billed by audio duration (Whisper: ~$0.006/min). Telegram provides the
voice note's ``duration`` in seconds; pass it via ``duration_sec`` so the cost
can be recorded by the caller.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Union

# OpenAI Whisper API price per minute of audio (USD).
WHISPER_PRICE_PER_MINUTE = 0.006

AudioInput = Union[bytes, str, Path]
ConvertFn = Callable[[bytes], bytes]


@dataclass
class TranscriptionResult:
    """Outcome of :func:`transcribe_audio`."""

    text: str = ""
    cost_usd: float = 0.0
    duration_sec: float = 0.0
    error: str = ""

    @property
    def is_error(self) -> bool:
        return bool(self.error)


def compute_transcription_cost(duration_sec: Optional[float]) -> float:
    """USD cost for ``duration_sec`` seconds of Whisper transcription."""
    if not duration_sec or duration_sec <= 0:
        return 0.0
    return (float(duration_sec) / 60.0) * WHISPER_PRICE_PER_MINUTE


def _read_bytes(audio: AudioInput) -> bytes:
    if isinstance(audio, (bytes, bytearray)):
        return bytes(audio)
    return Path(audio).read_bytes()


def transcribe_audio(
    audio: AudioInput,
    *,
    filename: str = "voice.oga",
    language: str = "ru",
    api_key: Optional[str] = None,
    openai_module: Any = None,
    convert_fn: Optional[ConvertFn] = None,
    duration_sec: Optional[float] = None,
) -> TranscriptionResult:
    """Transcribe ``audio`` to text via OpenAI Whisper.

    Returns a :class:`TranscriptionResult`; ``error`` is set (and nothing
    raises) when the key/SDK is missing, the input is empty, the API fails, or
    no speech is recognised.
    """
    cost = compute_transcription_cost(duration_sec)
    dur = float(duration_sec or 0.0)

    try:
        raw = _read_bytes(audio)
    except Exception as exc:  # noqa: BLE001 - unreadable input → graceful error
        return TranscriptionResult(error=f"Не удалось прочитать аудио: {exc}", duration_sec=dur)

    if not raw:
        return TranscriptionResult(error="Пустой аудиофайл.", duration_sec=dur)

    key = (api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")).strip()
    if not key:
        return TranscriptionResult(
            error="OPENAI_API_KEY не задан — транскрипция недоступна.",
            duration_sec=dur,
        )

    if convert_fn is not None:
        try:
            raw = convert_fn(raw)
        except Exception as exc:  # noqa: BLE001
            return TranscriptionResult(error=f"Конвертация аудио не удалась: {exc}", duration_sec=dur)

    mod = openai_module
    if mod is None:
        try:
            import openai as mod  # type: ignore[no-redef]
        except Exception:  # noqa: BLE001 - missing SDK → graceful error
            return TranscriptionResult(error="openai SDK не установлен.", duration_sec=dur)

    try:
        client = mod.OpenAI(api_key=key)
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=(filename, raw),
            language=language,
        )
        text = (getattr(response, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001 - API failure → graceful error
        return TranscriptionResult(error=f"Ошибка Whisper: {exc}", duration_sec=dur)

    if not text:
        return TranscriptionResult(
            error="Не удалось распознать речь в аудио.", duration_sec=dur
        )

    return TranscriptionResult(text=text, cost_usd=cost, duration_sec=dur)
