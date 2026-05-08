"""Phase 26: Voice Input — real Whisper transcription via OpenAI API.

Upgraded from Phase 22 skeleton (stub) to real implementation.
Falls back to user-friendly placeholder when OPENAI_API_KEY is not set.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def is_voice_supported() -> bool:
    """Return True when OpenAI Whisper API key is configured."""
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def transcribe_voice(audio_path: str) -> Optional[str]:
    """Transcribe a voice message to text via OpenAI Whisper API.

    Returns transcribed text on success, or None if transcription fails so
    the caller can show a placeholder. Raises ValueError for missing file.
    """
    path = Path(audio_path)
    if not path.exists():
        raise ValueError(f"Audio file not found: {audio_path}")

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.warning("OPENAI_API_KEY not set — returning placeholder")
        return None

    try:
        import openai

        client = openai.OpenAI(api_key=api_key)
        with open(audio_path, "rb") as f:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="ru",
            )
        text = (response.text or "").strip()
        return text if text else None
    except Exception as exc:
        logger.warning("Whisper transcription failed: %s", exc)
        return None


def transcribe_voice_placeholder(audio_path: str) -> str:
    """Return a user-friendly message when Whisper is not configured."""
    path = Path(audio_path)
    size_kb = path.stat().st_size // 1024 if path.exists() else 0
    return (
        f"🎤 Голосовые сообщения пока не поддерживаются.\n"
        f"(Получен файл: {path.name}, {size_kb}KB)\n\n"
        "Напиши текстом — отвечу сразу!"
    )
