from __future__ import annotations

import re


MOJIBAKE_HINTS = [
    "â€™",
    "â€œ",
    "â€\x9d",
    "â€“",
    "â€”",
    "â€¦",
    "Ã",
    "Ð",
    "Ñ",
    "â",
]


def _looks_mojibake(text: str) -> bool:
    return any(token in text for token in MOJIBAKE_HINTS)


def _repair_mojibake(text: str) -> str:
    if not text or not _looks_mojibake(text):
        return text
    try:
        candidate = text.encode("latin-1", errors="ignore").decode("utf-8", errors="ignore")
        if candidate and candidate.count("�") <= text.count("�"):
            return candidate
    except Exception:
        pass
    return text


def normalize_model_text(text: str | None) -> str:
    if text is None:
        return ""

    value = str(text)
    value = _repair_mojibake(value)

    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)

    # last-mile common artifacts
    value = value.replace("Jarvisâs", "Jarvis's")
    value = value.replace("â€™", "'")
    value = value.replace("â€œ", '"').replace("â€\x9d", '"')
    value = value.replace("â€“", "–").replace("â€”", "—")
    value = value.replace("â€¦", "...")
    value = value.replace("Â·", "·")

    return value.strip()