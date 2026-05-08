"""Phase 27: Vision Input — Claude Vision for image analysis.

Upgraded from Phase 22 skeleton (stub) to real implementation using Claude Haiku.
Photos without captions are analyzed and described. Photos with a caption/question
are analyzed in context of that question.
"""
from __future__ import annotations

import base64
import logging
import mimetypes
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _auto_rotate(image_path: str) -> str:
    """Auto-rotate image based on EXIF orientation tag. Returns path to use."""
    try:
        from PIL import Image, ExifTags
        img = Image.open(image_path)
        exif = img._getexif() if hasattr(img, '_getexif') else None
        if exif is None:
            return image_path
        for tag, value in exif.items():
            if ExifTags.TAGS.get(tag) == 'Orientation':
                rotations = {3: 180, 6: 270, 8: 90}
                if value in rotations:
                    img = img.rotate(rotations[value], expand=True)
                    stem = Path(image_path).stem
                    suffix = Path(image_path).suffix
                    rotated_path = str(Path(image_path).parent / f"{stem}_rotated{suffix}")
                    img.save(rotated_path)
                    return rotated_path
                break
    except Exception as e:
        logger.warning("Auto-rotate failed: %s", e)
    return image_path

_DEFAULT_PROMPT = (
    "Опиши что на этом изображении. "
    "Если это документ — извлеки ключевую информацию. "
    "Отвечай на том же языке, на котором написан вопрос или документ."
)


def is_vision_supported() -> bool:
    """Return True when Anthropic API key is configured."""
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def _detect_media_type(image_path: str) -> str:
    ext = Path(image_path).suffix.lower()
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    return mapping.get(ext, "image/jpeg")


def analyze_image(image_path: str, question: str = "") -> str:
    """Analyze an image via Claude Vision.

    Returns analysis text. Falls back to placeholder if API unavailable.
    Raises ValueError if the file does not exist.
    """
    path = Path(image_path)
    if not path.exists():
        raise ValueError(f"Image file not found: {image_path}")

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — returning placeholder")
        return analyze_image_placeholder(image_path)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        rotated_path = _auto_rotate(image_path)
        with open(rotated_path, "rb") as f:
            image_data = base64.standard_b64encode(f.read()).decode("utf-8")

        media_type = _detect_media_type(image_path)
        prompt = question.strip() or _DEFAULT_PROMPT

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_data,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        logger.warning("Vision analysis failed: %s", exc)
        return analyze_image_placeholder(image_path)


def analyze_image_placeholder(image_path: str) -> str:
    """Return a user-friendly message when Vision API is not configured."""
    path = Path(image_path)
    size_kb = path.stat().st_size // 1024 if path.exists() else 0
    suffix = path.suffix.upper()
    return (
        f"🖼 Анализ изображений пока не поддерживается.\n"
        f"(Получен файл: {path.name}, {size_kb}KB, {suffix})\n\n"
        "Отправь файл (PDF/DOCX) или текстовый запрос — обработаю!"
    )
