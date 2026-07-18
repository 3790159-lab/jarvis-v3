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
    # EN: "are you" / "am I talking|chatting|speaking to|with" / "is this"
    # addressing the assistant, with 0-3 words in between, followed by the
    # target (bot/robot/human/ai/real person) -- catches natural phrasings
    # like "are you actually a bot?", "am I chatting with a real person or
    # a bot?" that the plain adjacency patterns above miss. Requires one of
    # the address phrasings to actually be present, so it does NOT fire on
    # e.g. "I need a bot for my telegram channel" (no "are you"/"am I ...
    # to"/"is this") or "can a human review this later?" (asking to involve
    # a human, not "are YOU human").
    r"\b(?:are\s+you|am\s+i\s+(?:talking|chatting|speaking)\s+(?:to|with)|is\s+this)\b"
    r"(?:\s+\S+){0,3}\s+(?:a\s+)?(?:real\s+person|bot|human|robot|ai)\b",
    # UK: рынок продукта украинский, а RU-паттерны выше цепляются за "ты" —
    # украинское "ти" их не матчит, поэтому "ти бот?"/"ти жива людина?"
    # проваливали гарантию честности насквозь (аудит 2026-07-18, H3).
    r"\bти\s+бот\b", r"\bти\s+робот\b", r"\bце\s+бот\b", r"\bз\s+ботом\b",
    r"\bжива\s+людина\b", r"\bти\s+справжн\w*", r"\bреальн\w*\s+людин\w*\b",
    r"\bлюдина\s+чи\s+бот\b", r"\bбот\s+чи\s+людина\b",
    r"\bшту\w*\s+інтелект\b", r"\bші\b",
    # "ти"/"ви" с 1-3 словами до бот/робот — зеркало RU-gap-паттерна выше
    # ("ти часом не бот?", "а ти взагалі бот?").
    r"\b(?:ти|ви)\b(?:\s+\S+){1,3}\s+(?:бот|робот)\b",
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
#
# The honest core embeds `{marker}` mid-sentence (lowercase, after a
# connective), so the per-language honesty marker stays a verbatim substring --
# the FACT is never rephrased, only reframed. This is what keeps the disclosure
# one flowing phrase instead of two glued templates.
_CORES = {
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

# Connective that fuses the persona's tone line into the honest core as ONE
# phrase. `mid` (lowercase) follows a tone lead-in; `lead` (capitalized) starts
# the phrase when there is no tone. Both keep the marker lowercase mid-sentence.
_CONNECTIVES = {
    "ru": ("честно говоря, ", "Честно говоря, "),
    "en": ("to be honest, ", "To be honest, "),
    "uk": ("чесно кажучи, ", "Чесно кажучи, "),
}


def honest_disclosure(*, owner_id: str, persona_line: str, language: str = "ru") -> str:
    """Always honest, no exceptions. `persona_line` only sets TONE — it can
    never suppress or replace the honest fact (the per-language marker).
    `language` selects which honesty template/marker is used (safety: the
    disclosure must be understandable in the user's own language); unknown
    languages fall back to `ru`.

    The tone line and the honest core are woven into a SINGLE natural phrase:
    the tone's trailing sentence break is absorbed into a connective ("... —
    честно говоря, я — виртуальный ассистент, а не живой человек.") rather than
    butted against the core as a second intro. This fixes the live "bio.
    honesty" glue (lowercase 'я' after a period, two templates stuck together)
    without altering the honest fact itself."""
    tone = (persona_line or "").strip()
    marker = HONESTY_MARKERS.get(language, HONESTY_MARKER)
    mid, lead = _CONNECTIVES.get(language, _CONNECTIVES["ru"])
    core = _CORES.get(language, _CORES["ru"]).format(marker=marker, owner_id=owner_id)
    if tone:
        tone = tone.rstrip(" .!?…")  # absorb the tone's trailing break into the connective
        return f"{tone} — {mid}{core}"
    return f"{lead}{core}"
