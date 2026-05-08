from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class FormattedResponse:
    text: str
    mode: str


class ResponseFormatter:
    def finalize(self, text: str, *, mode: str) -> FormattedResponse:
        cleaned = self._cleanup(text)
        if mode == "verified_answer":
            cleaned = self._prefer_compact(cleaned)
        return FormattedResponse(text=cleaned, mode=mode)

    def split_for_telegram(self, text: str, limit: int = 3500) -> list[str]:
        if len(text) <= limit:
            return [text]
        parts: list[str] = []
        remaining = text
        while len(remaining) > limit:
            chunk = remaining[:limit]
            cut = chunk.rfind("\n\n")
            if cut < 1200:
                cut = chunk.rfind("\n")
            if cut < 600:
                cut = chunk.rfind(". ")
            if cut < 300:
                cut = limit
            parts.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
        if remaining:
            parts.append(remaining)
        return parts

    def _prefer_compact(self, text: str) -> str:
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text[:3500].strip()

    def _cleanup(self, text: str) -> str:
        out = (text or "").strip()
        replacements = {
            "temperatures are quite pleasant": "температура довольно комфортная",
            "seems": "кажется",
            "varies depending on the time of year": "зависит от сезона",
            "however": "однако",
            "details": "детали",
        }
        for old, new in replacements.items():
            out = out.replace(old, new)
        out = re.sub(r"\bnone\b", "нет", out, flags=re.I)
        out = re.sub(r"\bbackend\b", "backend", out, flags=re.I)
        out = re.sub(r"[ \t]+\n", "\n", out)
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out
