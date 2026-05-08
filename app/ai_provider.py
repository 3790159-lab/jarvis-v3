from openai import OpenAI

from app import settings
from app.services.identity_core import get_system_prompt


class AIProvider:
    def __init__(self) -> None:
        if settings.LLM_MODE != "openai":
            raise RuntimeError(f"Unsupported LLM_MODE: {settings.LLM_MODE}")

        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is empty")

        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_MODEL

    def ask(self, user_text: str, system_prompt: str | None = None) -> dict:
        instructions = system_prompt or get_system_prompt("supervisor")

        response = self.client.responses.create(
            model=self.model,
            instructions=instructions,
            input=user_text,
        )

        text = getattr(response, "output_text", "") or ""

        return {
            "text": text.strip(),
            "mode": "ai",
            "source": "openai",
            "confidence": 0.95,
        }