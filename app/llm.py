from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class LLMResult:
    ok: bool
    text: str
    raw: dict[str, Any] | None = None
    error: str | None = None


class OpenAICompatibleLLM:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"
        self.enabled = bool(self.api_key)

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.2,
        use_web: bool = False,
    ) -> LLMResult:
        if not self.enabled:
            return LLMResult(
                ok=False,
                text="",
                error="OPENAI_API_KEY is not set",
            )

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # На будущее: если ты захочешь web-search, это лучше делать отдельной логикой,
        # а не пытаться "магически" включить его здесь через неизвестный payload.
        # Пока use_web просто остаётся параметром маршрутизации.
        _ = use_web

        try:
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=(10, 25),  # connect timeout, read timeout
            )
        except requests.Timeout:
            return LLMResult(
                ok=False,
                text="",
                error="OpenAI request timed out",
            )
        except Exception as exc:
            return LLMResult(
                ok=False,
                text="",
                error=str(exc),
            )

        try:
            data = resp.json()
        except Exception:
            return LLMResult(
                ok=False,
                text="",
                error=f"OpenAI returned non-JSON response with status {resp.status_code}",
            )

        if resp.status_code >= 400:
            return LLMResult(
                ok=False,
                text="",
                raw=data if isinstance(data, dict) else None,
                error=str(data),
            )

        text = self._extract_text(data)
        if not text:
            return LLMResult(
                ok=False,
                text="",
                raw=data if isinstance(data, dict) else None,
                error="OpenAI returned empty text",
            )

        return LLMResult(
            ok=True,
            text=text,
            raw=data if isinstance(data, dict) else None,
            error=None,
        )

    def _extract_text(self, data: dict[str, Any]) -> str:
        try:
            choices = data.get("choices", [])
            if not choices:
                return ""

            message = choices[0].get("message", {})
            content = message.get("content", "")
            if isinstance(content, str):
                return content.strip()

            return ""
        except Exception:
            return ""