from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx

from app.models.ai_router import ProviderName
from app.services.ai.provider_base import BaseAIProvider


class OllamaProvider(BaseAIProvider):
    provider_name = ProviderName.OLLAMA

    def __init__(self, timeout_seconds: int = 120) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.base_url = os.getenv("AI_ROUTER_OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        self.model = os.getenv("AI_ROUTER_OLLAMA_MODEL", "llama3.1:latest").strip()
        self.enabled_flag = os.getenv("AI_ROUTER_ENABLE_OLLAMA", "true").strip().lower() == "true"

    def is_enabled(self) -> bool:
        return self.enabled_flag

    def is_configured(self) -> bool:
        return bool(self.base_url) and bool(self.model)

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.is_enabled():
            raise RuntimeError("Ollama provider is disabled")
        if not self.is_configured():
            raise RuntimeError("Ollama provider is not configured")

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\nUser request:\n{prompt}"

        payload = {
            "model": self.model,
            "prompt": full_prompt,
            "stream": False,
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/api/generate",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        content = data.get("response", "").strip()
        if not content:
            raise RuntimeError("Empty Ollama response")

        return {
            "provider": self.provider_name.value,
            "model": self.model,
            "text": content,
            "raw": data,
        }
