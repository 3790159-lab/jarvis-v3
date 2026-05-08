from __future__ import annotations


class AnthropicPayloadGuard:
    @staticmethod
    def build_candidates(model: str, system: str, prompt: str, safe_mode: bool = False) -> list[dict]:
        out = []
        if not safe_mode:
            out.append({
                "model": model,
                "max_tokens": 1600,
                "temperature": 0.2,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            })
        out.append({
            "model": model,
            "max_tokens": 1200 if safe_mode else 1600,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": f"{system}\n\n{prompt}"}],
        })
        return out