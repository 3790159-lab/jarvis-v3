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


# Per-language honesty markers. RU stays equal to HONESTY_MARKER so existing
# RU-only callers/tests keep working unchanged.
HONESTY_MARKERS = {
    "ru": HONESTY_MARKER,
    "en": "I'm a virtual assistant",
    "uk": "я — віртуальний асистент",
}

# Owner name sits in apposition (nominative case) in every language so it
# reads correctly regardless of the name -- e.g. RU "... владельца — Дмитрий
# ответит лично." never declines "Дмитрий", unlike the old "позову Дмитрий
# лично" (wrong case).
_TEMPLATES = {
    "ru": (
        "{marker}, а не живой человек. "
        "Помогаю с вопросами и подсказываю по услугам. "
        "Если хотите, подключу владельца — {owner_id} ответит лично."
    ),
    "en": (
        "{marker}, not a human. "
        "I help answer questions and point you toward the right service. "
        "If you'd like, I can bring in the owner — {owner_id} will reply personally."
    ),
    "uk": (
        "{marker}, а не жива людина. "
        "Допомагаю з питаннями і підказую щодо послуг. "
        "Якщо хочете, підключу власника — {owner_id} відповість особисто."
    ),
}


def honest_disclosure(*, owner_id: str, persona_line: str, language: str = "ru") -> str:
    """Always honest, no exceptions. `persona_line` only sets TONE — it can
    never suppress or replace the honest fact (the per-language marker).
    `language` selects which honesty template/marker is used (safety: the
    disclosure must be understandable in the user's own language); unknown
    languages fall back to `ru`."""
    tone = (persona_line or "").strip()
    marker = HONESTY_MARKERS.get(language, HONESTY_MARKER)
    template = _TEMPLATES.get(language, _TEMPLATES["ru"])
    core = template.format(marker=marker, owner_id=owner_id)
    return f"{tone} {core}".strip() if tone else core
