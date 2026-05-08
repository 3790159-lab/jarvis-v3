import logging
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI
import openai

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class LLMResult:
    ok: bool
    text: Optional[str] = None
    error: Optional[str] = None


class LLMClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("OPENAI_MODEL", "gpt-5.2").strip()
        self.enabled = bool(self.api_key)

        self.client: Optional[OpenAI] = None
        if self.enabled:
            self.client = OpenAI(
                api_key=self.api_key,
                timeout=20.0,
                max_retries=2,
            )

    def chat(
        self,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int = 500,
    ) -> LLMResult:
        if not self.enabled or self.client is None:
            return LLMResult(
                ok=False,
                error="OPENAI_API_KEY не найден или LLMClient не инициализирован."
            )

        try:
            response = self.client.responses.create(
                model=self.model,
                instructions=system_prompt,
                input=user_prompt,
                max_output_tokens=max_output_tokens,
            )

            text = (response.output_text or "").strip()
            if not text:
                return LLMResult(
                    ok=False,
                    error="Модель вернула пустой ответ."
                )

            return LLMResult(ok=True, text=text)

        except openai.RateLimitError as exc:
            logger.exception("Rate limit from OpenAI")
            return LLMResult(ok=False, error=f"Rate limit: {exc}")

        except openai.APIConnectionError as exc:
            logger.exception("OpenAI connection error")
            return LLMResult(ok=False, error=f"Connection error: {exc}")

        except openai.APIStatusError as exc:
            logger.exception("OpenAI API status error")
            return LLMResult(
                ok=False,
                error=f"API status error {exc.status_code}: {exc}"
            )

        except Exception as exc:
            logger.exception("Unexpected LLM error")
            return LLMResult(ok=False, error=f"Unexpected error: {exc}")