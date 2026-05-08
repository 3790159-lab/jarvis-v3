from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx

from app.models.ai_router import ProviderName
from app.services.ai.provider_base import BaseAIProvider


class AnthropicProvider(BaseAIProvider):
    provider_name = ProviderName.ANTHROPIC

    def __init__(self, timeout_seconds: int = 120) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        self.model = os.getenv("AI_ROUTER_ANTHROPIC_MODEL", "claude-sonnet-4-20250514").strip()
        self.enabled_flag = os.getenv("AI_ROUTER_ENABLE_ANTHROPIC", "true").strip().lower() == "true"

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
            raise RuntimeError("Anthropic provider is disabled")
        if not self.is_configured():
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")

        payload = {
            "model": self.model,
            "max_tokens": 2000,
            "temperature": 0.2,
            "messages": [
                {"role": "user", "content": prompt}
            ],
        }
        if system_prompt:
            payload["system"] = system_prompt

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

        content_parts = data.get("content", [])
        text_parts = []
        for item in content_parts:
            if item.get("type") == "text":
                text_parts.append(item.get("text", ""))

        content = "\n".join(part for part in text_parts if part).strip()
        if not content:
            raise RuntimeError("Empty Anthropic response content")

        return {
            "provider": self.provider_name.value,
            "model": self.model,
            "text": content,
            "raw": data,
        }
