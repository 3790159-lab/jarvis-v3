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
    # ВЕЖЛИВАЯ ФОРМА «ви» — не косметика, а закрытие дыры в гарантии §6.
    # H3 (2026-07-18) добавил «ти», но адъяцентную «ви»-форму пропустил, а
    # шаблон с 1-3 словами между требует слов МЕЖДУ — «Ви бот?» вплотную не
    # ловилось ничем. Персоны говорят на «ви», лид зеркалит обращение, так что
    # это САМАЯ вероятная формулировка (живой прогон volska 2026-07-21: прошло
    # мимо гарантии, честно ответила сама модель — то есть держалось на удаче).
    # `\b` после «робот» обязателен: без него «ви роботУ шукаєте?» (работа!)
    # давало бы ложное срабатывание.
    r"\b(?:ти|ви)\s+бот\b", r"\b(?:ти|ви)\s+робот\b",
    r"\bце\s+(?:бот|робот)\b", r"\bз\s+ботом\b",
    r"\bжива\s+людина\b", r"\b(?:ти|ви)\s+справжн\w*", r"\bреальн\w*\s+людин\w*\b",
    r"\bлюдина\s+чи\s+бот\b", r"\bбот\s+чи\s+людина\b",
    r"\bшту\w*\s+інтелект\b", r"\bші\b",
    # "ти"/"ви" с 1-3 словами до бот/робот — зеркало RU-gap-паттерна выше
    # ("ти часом не бот?", "а ти взагалі бот?").
    r"\b(?:ти|ви)\b(?:\s+\S+){1,3}\s+(?:бот|робот)\b",
    # Офлайн-замер volska 2026-07-21 (40 диалогов на --llm real): лид спрашивает
    # про личность НЕ только словом «бот». Из пяти живых формулировок ловилась
    # ОДНА («ви бот?»), а «ви жива?», «це справжня людина?», «ви Ольга особисто?»
    # и «з ким спілкуюсь» уходили в LLM. На «ви Ольга особисто?» honest-режим
    # ответил «Так, мене звати Ольга, я менеджерка агенції» — прямое выдавание
    # себя за человека ПРИ ВКЛЮЧЁННОЙ гарантии. Спека §6 обещает гарантию без
    # тумблера, поэтому дыра здесь = дыра в главном инварианте продукта.
    #
    # Ложное срабатывание тут дороже обычного (лид получает шаблон раскрытия
    # вместо ответа), поэтому формы узкие: «жива/живий», но НЕ «живете»;
    # «особисто/лично» — только если рядом есть обращение «ти/ви», иначе это
    # про встречу («хочу зустрітись особисто»), а не про собеседника.
    r"\b(?:ти|ви)\s+жив(?:а|ий)\b",
    r"\bсправжн\w*\s+людин\w*\b",
    r"\b(?:ти|ви)\b(?:\s+\S+){0,2}\s+особисто\b",
    r"\bз\s+ким\s+(?:я\s+)?(?:спілкуюсь|спілкуюся|розмовляю|говорю)\b",
    # RU-зеркала тех же четырёх форм (demo-персона русскоязычная).
    r"\b(?:ты|вы)\s+жив(?:ая|ой|ые)\b",
    r"\bнастоящ\w+\s+человек\w*\b",
    r"\b(?:ты|вы)\b(?:\s+\S+){0,2}\s+лично\b",
    r"\bс\s+кем\s+(?:я\s+)?(?:общаюсь|разговариваю|говорю)\b",
    # EN: «who am I talking to?» — адресный вопрос о собеседнике без слова bot,
    # который существующий "am i talking to"-паттерн не ловит (тот требует
    # цели bot/human/ai после обращения).
    r"\bwho\s+am\s+i\s+(?:talking|speaking|chatting)\s+(?:to|with)\b",
    # Хвост второго захода. «ти людина?» — самая частая форма вопроса о
    # личности — не ловилась ничем, включая расширение выше: паттерны с
    # «людина» требовали соседа («жива людина», «людина чи бот»).
    #
    # «хто це пише» требует «це» ОБЯЗАТЕЛЬНО: голое «хто пише» — это вопрос про
    # услугу («хто пише тексти для постів?»), а не про собеседника. Ровно та же
    # причина у «автоматичн\w* відповідь»: без слова «відповідь» это попало бы
    # на «автоматичну оплату».
    r"\b(?:ти|ви)\s+людин\w*\b", r"\b(?:ти|ви)\s+реальн\w*\b",
    r"\b(?:ти|ви)\s+програма\b", r"\bхто\s+це\s+пише\b",
    r"\bавтоматичн\w*\s+відповід\w*\b",
    r"\b(?:ти|ви)\b(?:\s+\S+){0,2}\s+існу\w*\b",
    # RU-зеркала хвоста.
    r"\b(?:ты|вы)\s+человек\b", r"\b(?:ты|вы)\s+программа\b",
    r"\bкто\s+это\s+пишет\b", r"\bавтоматическ\w*\s+ответ\w*\b",
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
# Клауза «не человек» вынесена из _CORES отдельно, потому что её переиспользует
# honest_prefix() — честный минимум БЕЗ предложения владельца, который нужен
# аварийному фоллбеку H2 (карточка не дошла → контакт владельца обещать нельзя,
# но честность обязана уцелеть). Один источник правды на обе формы.
_NOT_HUMAN = {
    "ru": "а не живой человек",
    "en": "not a human",
    "uk": "а не жива людина",
}

_CORES = {
    "ru": (
        "{marker}, {not_human}. "
        "Помогаю с вопросами и подсказываю по услугам. "
        "Если хотите, подключу владельца — {owner_id} ответит лично."
    ),
    "en": (
        "{marker}, {not_human}. "
        "I help answer questions and point you toward the right service. "
        "If you'd like, I can bring in the owner — {owner_id} will reply personally."
    ),
    "uk": (
        "{marker}, {not_human}. "
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
    core = _CORES.get(language, _CORES["ru"]).format(
        marker=marker, not_human=_NOT_HUMAN.get(language, _NOT_HUMAN["ru"]),
        owner_id=owner_id)
    if tone:
        tone = tone.rstrip(" .!?…")  # absorb the tone's trailing break into the connective
        return f"{tone} — {mid}{core}"
    return f"{lead}{core}"


def honest_prefix(language: str = "ru") -> str:
    """Честный факт БЕЗ предложения подключить владельца.

    Нужен аварийному фоллбеку H2: если карточка эскалации до владельца НЕ
    дошла, обещать его контакт нельзя (иначе лид ждёт того, кто не в курсе) —
    но честность от этого зависеть не должна. Замер volska 2026-07-21 показал
    цену прежнего поведения: H2 заменял сочинённое моделью раскрытие отпиской
    «уточню детали и вернусь», и лид, спросивший «ви Ольга особисто?», получал
    ответ, из которого следовало, что он говорит с человеком.

    Маркер попадает сюда ДОСЛОВНО (как в honest_disclosure): факт не
    перефразируется, а только переоформляется.
    """
    marker = HONESTY_MARKERS.get(language, HONESTY_MARKER)
    not_human = _NOT_HUMAN.get(language, _NOT_HUMAN["ru"])
    _, lead = _CONNECTIVES.get(language, _CONNECTIVES["ru"])
    return f"{lead}{marker}, {not_human}."
