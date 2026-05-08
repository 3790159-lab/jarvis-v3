from __future__ import annotations

import re

from .intent_gate import IntentDecision


class AmbiguityResolver:
    def resolve(self, text: str, initial: IntentDecision) -> IntentDecision:
        lower = (text or "").lower().strip()

        if "корень" in lower and "уравнен" not in lower and "квадрат" not in lower:
            return IntentDecision("general", 0.78, "root_word_non_math_context")

        if any(x in lower for x in ["дерева", "дерево", "корневая система", "корень дерева"]):
            return IntentDecision("general", 0.88, "tree_root_context")

        if re.search(r"\bсторон(ами|ы)?\b", lower) and re.search(r"\b\d+[,\.\d]*\b", lower):
            return IntentDecision("math_exact", 0.9, "triangle_sides_context")

        if any(x in lower for x in ["фото", "скрин", "картинк"]) and "что думаешь" in lower:
            return IntentDecision("photo_vision", 0.86, "followup_about_image")

        return initial
