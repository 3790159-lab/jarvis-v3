from __future__ import annotations

from typing import Any, Dict


class Assistant:
    def __init__(self) -> None:
        self.name = "Jarvis Assistant"

    def health(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "assistant": self.name,
        }

    def respond(self, message: str) -> str:
        text = (message or "").strip()
        if not text:
            return "Empty message received."
        return f"Received: {text}"


assistant = Assistant()