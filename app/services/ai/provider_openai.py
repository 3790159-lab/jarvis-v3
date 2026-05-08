from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx

from app.models.ai_router import ProviderName
from app.services.ai.provider_base import BaseAIProvider


class OpenAIProvider(BaseAIProvider):
    provider_name = ProviderName.OPENAI

    def __init__(self, timeout_seconds: int = 120) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("AI_ROUTER_OPENAI_MODEL", "gpt-4.1").strip()
        self.enabled_flag = os.getenv("AI_ROUTER_ENABLE_OPENAI", "true").strip().lower() == "true"

    def is_enabled(self) -> bool:
        return self.enabled_flag

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.is_enabled():
            raise RuntimeError("OpenAI provider is disabled")
        if not self.is_configured():
            raise RuntimeError("OPENAI_API_KEY is not configured")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

        content = ""
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception as exc:
            raise RuntimeError(f"Unexpected OpenAI response format: {exc}") from exc

        return {
            "provider": self.provider_name.value,
            "model": self.model,
            "text": content,
            "raw": data,
        }
