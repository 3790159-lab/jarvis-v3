from __future__ import annotations
import re

# The honest fact that can never be turned off by config, persona, or any
# other input. Spec §6: there is no toggle for this — it is hardcoded.
HONESTY_MARKER = "я — виртуальный ассистент"

_PATTERNS = [
    r"\bты\s+бот\b", r"\bты\s+робот\b", r"\bэто\s+бот\b", r"\bс\s+ботом\b",
    r"\bбот\s+или\s+человек\b",
    r"\bживой\s+человек\b", r"\bреальный\s+человек\b", r"\bавтоответчик\b",
    r"\bчеловек\s+или\s+ии\b", r"\bреальный.*или.*ии\b",
    r"\bare\s+you\s+a?\s*bot\b", r"\bis\s+this\s+a?\s*bot\b", r"\breal\s+person\b",
    # "ты"/"вы" addressing the assistant, with 1-3 words in between, followed
    # by бот/робот -- catches natural phrasings like "а ты вообще бот?",
    # "ты что, бот?", "ты не бот?", "ты случайно не бот?" that the plain
    # adjacency patterns above miss. Requires "ты"/"вы" to actually be
    # present, so it does NOT fire on e.g. "работаю с ботами в телеграме"
    # (no "ты") or "сколько стоит бот для рассылки?" (no "ты"/"вы" either).
    r"\b(?:ты|вы)\b(?:\s+\S+){1,3}\s+(?:бот|робот)\b",
]
_RE = re.compile("|".join(_PATTERNS), re.IGNORECASE)


def is_bot_question(text: str) -> bool:
    return bool(_RE.search(text or ""))


def honest_disclosure(*, owner_id: str, persona_line: str) -> str:
    """Always honest, no exceptions. `persona_line` only sets TONE — it can
    never suppress or replace the honest fact (HONESTY_MARKER)."""
    tone = (persona_line or "").strip()
    core = (
        f"{HONESTY_MARKER}, а не живой человек. "
        f"Помогаю с вопросами и подсказываю по услугам. "
        f"Если хотите — позову {owner_id} лично, ответит вживую."
    )
    return f"{tone} {core}".strip() if tone else core
