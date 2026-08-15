from __future__ import annotations
from dataclasses import dataclass
import re
from chatter.storage.db import Store

_NUMBER = re.compile(r"\d[\d\s.,]*\d|\d")

_CURRENCY = r"(?:руб\w*|₽|\$|€|грн\w*|тыс\w*|евро\w*|доллар\w*)"
_PRICE_WORD = r"(?:цен\w*|сто[ий]\w*|стоимост\w*|скидк\w*|предоплат\w*)"
# P20 (а): формы «день»/«тиждень»/«місяць» ОБЯЗАНЫ распознаваться. Инцидент
# 2026-07-28/29: knowledge содержал «21 календарний день», но `дн(?:я|ей|ь)?\w*`
# построен на беглой гласной и матчит только дня/днів/дней — номинативная форма
# «день» (и русская тоже) выпадала, фрагмент не признавался срочным, «21» не
# попадало в deadline_numbers, и весь ответ с четырьмя обеспеченными ценами
# подавлялся из-за одного числа.
#
# ⚠️ `\b` ОБЯЗАТЕЛЕН (см. test_price_words_containing_den_do_not_leak_into_deadline):
# без границы слова стем «ден» матчит «ве-ДЕН-ня» и «ай-ДЕН-тики», из-за чего
# ЦЕНЫ (700/750/900) уехали бы в СРОЧНОЕ множество и обеспечивали бы выдуманные
# сроки. Граница нужна и старым стемам: «дн» без неё сидит внутри «однак».
# ГОДЫ — тот же класс дыры, что P20 «день», только на другой морфологии:
# «Гарантія — 1 рік» не признавалось срочным фрагментом, и обеспеченный базой
# ответ подавлялся целиком. Здесь окончания перечислены ЯВНО, а не через `\w*`:
# иначе «год»→«ГОДный», «рок»→«РОК-гурт», «лет»→«ЛЕТний» утащили бы ЦЕНЫ из
# таких строк в срочное множество (та же грабля границы слова, что у «ден»).
# Голое «рок» без окончания намеренно НЕ единица: номинатив украинского — «рік».
_TIME_UNIT = (
    r"(?:\b(?:дн(?:я|ей|ь)?|ден|тижн|тижд|місяц)\w*|"
    r"\b(?:недел|час|месяц)\w*|"
    r"\b(?:рік|рок(?:у|и|ів|ах|ам|ом|ами)|роц(?:і|ях)|"
    r"год(?:а|у|е|ом|ы|ах|ам|ов|ами)?|лет)\b|"
    r"\b(?:январ|феврал|март|апрел|ма[йя]|июн|июл|"
    r"август|сентябр|октябр|ноябр|декабр)\w*)"
)
_WEEKDAY = (
    r"(?:понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресен\w*)"
)

# Stems used to check whether a *bare* (number-less) temporal claim is backed
# by knowledge.md, sorted longest-first so a specific stem wins over a
# shorter one that happens to be a substring of it.
_TIME_UNIT_STEMS = sorted(
    [
        "недел", "месяц", "январ", "феврал", "август", "сентябр", "октябр",
        "ноябр", "декабр", "апрел", "март", "июн", "июл", "час", "дн",
        # P20 (а): украинские формы + номинативный «день» (см. _TIME_UNIT).
        "ден", "тижн", "тижд", "місяц",
        # Годы (см. _TIME_UNIT): «рік/роки/років», «год/года/лет».
        "рік", "рок", "год", "лет",
        # NOTE: no bare 2-char "ма" stem -- it was a substring of unrelated
        # words (e.g. "формат", "информация") and could hide a real unbacked
        # May-deadline claim behind an accidental knowledge-base match. Use
        # the specific inflections of "май" instead.
        "май", "мая",
    ],
    key=len, reverse=True,
)
_WEEKDAY_STEMS = [
    "понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскресен",
]

_PRICE_CONTEXT = re.compile(
    rf"(?:{_CURRENCY}|{_PRICE_WORD}|%|\bза\s+\d)", re.IGNORECASE
)
_DEADLINE_NUM = re.compile(
    rf"\b(?:за|через|к)\s+(\d[\d\s.,]*\d|\d)\s*{_TIME_UNIT}", re.IGNORECASE
)
_DEADLINE_NO_NUM = re.compile(
    rf"\b(?:за|через)\s+({_TIME_UNIT})", re.IGNORECASE
)
_WEEKDAY_DEADLINE = re.compile(rf"\bк\s+({_WEEKDAY})", re.IGNORECASE)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Любая единица времени где-либо во фрагменте — чтобы пометить его числа как
# «срочные» при разборе knowledge (M1: контекстное подтверждение).
_ANY_TIME_UNIT = re.compile(_TIME_UNIT, re.IGNORECASE)

# Б1: число (или диапазон «5–10»), стоящее ПРИ единице времени, — это СРОК, а не
# цена. Допускаем до двух слов между числом и единицей («5–10 РОБОЧИХ ДНІВ»,
# «7 дней», «1.5–2 часа»). Валюта отсекается сама: `\s+\w+` не матчит « $»/« грн»
# через неслововые символы, поэтому «300 $ за 5 днів» не пометит 300 срочным.
_NUM_IN_TIME_CONTEXT = re.compile(
    rf"(\d[\d\s.,]*\d|\d)(?:\s*[–—-]\s*(\d[\d\s.,]*\d|\d))?"
    rf"(?:\s+\w+){{0,2}}\s*{_TIME_UNIT}",
    re.IGNORECASE,
)


def _numbers(text: str) -> set[str]:
    return {re.sub(r"\s", "", m.group()) for m in _NUMBER.finditer(text or "")}


def _context_numbers(knowledge: str) -> tuple[set[str], set[str]]:
    """Числа knowledge по КОНТЕКСТУ (M1): (ценовые, срочные). Число «обеспечено»
    для ценового/срочного обещания, только если стоит в ТОМ ЖЕ контексте в
    knowledge, а не просто где-то в файле (плоское known_numbers пускало
    выдуманный срок, переиспользующий цену/номер как «известное» число).

    knowledge режем по строкам и границам предложений; фрагмент с ценовым
    словом/валютой отдаёт свои числа в ценовые, фрагмент с единицей времени —
    в срочные (один фрагмент, напр. «15000 грн за съёмку (2 часа)», может дать
    в оба)."""
    price: set[str] = set()
    deadline: set[str] = set()
    for frag in re.split(r"[\n.!?;]", knowledge or ""):
        nums = _numbers(frag)
        if not nums:
            continue
        if _PRICE_CONTEXT.search(frag):
            price |= nums
        if _ANY_TIME_UNIT.search(frag):
            deadline |= nums
    return price, deadline


def _time_context_numbers(text: str) -> set[str]:
    """Числа фрагмента, стоящие в СРОЧНОМ контексте («5–10 робочих днів», «7
    дней», «1.5–2 часа»). Нужны, чтобы в предложении, где есть И цена И срок,
    срочное число сверялось со срочным множеством knowledge, а не с ценовым."""
    out: set[str] = set()
    for m in _NUM_IN_TIME_CONTEXT.finditer(text or ""):
        for g in m.groups():
            if g:
                out.add(re.sub(r"\s", "", g))
    return out


def _stem_of(word: str, stems: list[str]) -> str | None:
    low = word.lower()
    return next((s for s in stems if s in low), None)


def _mentions_stem(knowledge_lower: str, stem: str) -> bool:
    """Упомянута ли единица времени/день недели в knowledge — с ГРАНИЦЕЙ СЛОВА.

    P20 (а): голая подстрока делала базу «обеспечивающей» что угодно — стем
    «ден» сидит внутри «ведення», поэтому выдуманное «через день» считалось
    подтверждённым прайс-листом, где о днях не сказано ни слова."""
    return re.search(rf"\b{re.escape(stem)}", knowledge_lower) is not None


@dataclass(frozen=True)
class _Finding:
    """Одно необеспеченное место в ответе: где стоит, какое число, какое правило."""
    start: int
    end: int
    number: str | None
    rule: str          # deadline | price | weekday | bare_unit | large_number


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """Границы предложений с ОФСЕТАМИ — нужны, чтобы знать позицию найденного
    числа в исходном ответе (для точечной редакции). Прежний `_split_sentences`
    отдавал только тексты и офсеты терял, поэтому снят."""
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _SENTENCE_SPLIT.finditer(text or ""):
        spans.append((start, m.start()))
        start = m.end()
    if start < len(text or ""):
        spans.append((start, len(text or "")))
    return [(s, e) for s, e in spans if (text or "")[s:e].strip()]


def _findings(reply: str, knowledge: str) -> list[_Finding]:
    """Все необеспеченные места ответа. `contains_unbacked_claim` — булев
    фасад над этим же разбором (поведение правил не меняется, добавились только
    позиции и причина, без которых невозможна точечная редакция)."""
    text = reply or ""
    known_numbers = _numbers(knowledge)
    price_numbers, deadline_numbers = _context_numbers(knowledge)
    knowledge_lower = (knowledge or "").lower()
    out: list[_Finding] = []

    for m in _WEEKDAY_DEADLINE.finditer(text):
        stem = _stem_of(m.group(1), _WEEKDAY_STEMS)
        if stem and not _mentions_stem(knowledge_lower, stem):
            out.append(_Finding(m.start(), m.end(), None, "weekday"))

    for m in _DEADLINE_NUM.finditer(text):
        num = re.sub(r"\s", "", m.group(1))
        if num not in deadline_numbers:
            out.append(_Finding(m.start(), m.end(), num, "deadline"))

    for m in _DEADLINE_NO_NUM.finditer(text):
        stem = _stem_of(m.group(1), _TIME_UNIT_STEMS)
        if stem and not _mentions_stem(knowledge_lower, stem):
            out.append(_Finding(m.start(), m.end(), None, "bare_unit"))

    for s, e in _sentence_spans(text):
        sentence = text[s:e]
        if not _PRICE_CONTEXT.search(sentence):
            continue
        time_nums = _time_context_numbers(sentence)
        for m in _NUMBER.finditer(sentence):
            num = re.sub(r"\s", "", m.group())
            is_time = num in time_nums
            expected = deadline_numbers if is_time else price_numbers
            if num not in expected:
                out.append(_Finding(s + m.start(), s + m.end(), num,
                                    "deadline" if is_time else "price"))

    for m in _NUMBER.finditer(text):
        num = re.sub(r"\s", "", m.group())
        digits = re.sub(r"\D", "", num)
        if digits and int(digits) >= 100 and num not in known_numbers:
            out.append(_Finding(m.start(), m.end(), num, "large_number"))

    return out


def contains_unbacked_claim(reply: str, knowledge: str) -> bool:
    """Safety-net guardrail (spec §7): flags `reply` only when it makes a
    concrete, unbacked PRICE/DISCOUNT or DEADLINE claim, or states a large
    bare number, that `knowledge` does not support:

    1. Price/discount context (currency token, price word, '%', or a number
       preceded by "за") with a number not present in knowledge.
    2. Deadline context: "за/через/к <N> <time-unit>" with N not in
       knowledge, OR a bare "за/через <time-unit>" / "к <weekday>" whose
       unit/weekday isn't mentioned anywhere in knowledge at all.
    3. Bare-number backstop: any number >= 100 not present in knowledge.

    Small incidental counts (< 100) with no price/deadline wording are left
    alone. This is a NET for whatever the system prompt's "don't invent
    prices/deadlines" instruction missed -- not the primary defense.
    """
    return bool(_findings(reply, knowledge))


# --------------------------------------------------------------------------
# P20 (D): точечная редакция вместо подавления всего ответа
# --------------------------------------------------------------------------
#
# Замер инцидента 2026-07-29: одно необеспеченное число убивало ответ целиком,
# вместе с четырьмя обеспеченными ценами. Здесь мы вырезаем не ЧИСЛО (осталась
# бы дыра в предложении), а КЛАУЗУ — грамматически цельный кусок между
# запятыми/скобками/границами предложения — и ставим на её место нейтральную
# формулу.
#
# ⚠️ Формула НЕ упоминает владельца намеренно: `mentions_owner_contact`
# посчитала бы это обещанием контакта, и H2-гейт при недоставленной карточке
# затёр бы аккуратную редакцию обратно в заглушку (run.py). О пробеле в базе
# владелец узнаёт карточкой эскалации, а не текстом лиду.
_REDACTION_DEADLINE = {
    "ru": "точный срок согласовываем индивидуально",
    "uk": "точний термін узгоджуємо індивідуально",
    "en": "the exact timeline is agreed individually",
}
_REDACTION_PRICE = {
    "ru": "точную стоимость согласовываем индивидуально",
    "uk": "точну вартість узгоджуємо індивідуально",
    "en": "the exact price is agreed individually",
}
_DEADLINE_RULES = frozenset({"deadline", "weekday", "bare_unit"})

# Границы клаузы. Тире НЕ разделитель: оно живёт внутри диапазонов («750–900»)
# и как связка («Стратегія - 600–800 $»). Точка — разделитель только если не
# стоит между цифрами («1.5 години» не должно рваться пополам).
_CLAUSE_DELIMS = ",;()[]\n!?…:"


def _is_delim(text: str, i: int) -> bool:
    ch = text[i]
    if ch in _CLAUSE_DELIMS:
        return True
    if ch == ".":
        before = text[i - 1] if i > 0 else ""
        after = text[i + 1] if i + 1 < len(text) else ""
        return not (before.isdigit() and after.isdigit())
    return False


def _clause_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Границы клаузы, накрывающей [start:end)."""
    i = start
    while i > 0 and not _is_delim(text, i - 1):
        i -= 1
    j = end
    while j < len(text) and not _is_delim(text, j):
        j += 1
    return i, j


def _starts_sentence(text: str, i: int) -> bool:
    """Клауза открывает предложение → формулу надо писать с большой буквы."""
    k = i - 1
    while k >= 0 and text[k].isspace():
        k -= 1
    return k < 0 or text[k] in ".!?…\n"


@dataclass(frozen=True)
class Redaction:
    """След одной редакции. PII-free by construction: ни текста лида, ни текста
    ответа — только число, правило и длина вырезанной клаузы."""
    number: str | None
    rule: str
    clause_chars: int


@dataclass(frozen=True)
class RedactionResult:
    text: str
    records: tuple[Redaction, ...]
    clean: bool        # True ⇔ в `text` не осталось необеспеченных чисел


def redact_unbacked(reply: str, knowledge: str, *, language: str = "ru") -> RedactionResult:
    """Вырезать необеспеченные утверждения, сохранив всё остальное.

    `clean=False` означает «редакцией не спаслось» — вызывающий обязан
    подавить ответ целиком. Проверка финальная и честная: результат
    прогоняется через тот же `contains_unbacked_claim`, поэтому выдуманная
    цена/срок не может уехать лиду через эту дверь."""
    text = reply or ""
    findings = _findings(text, knowledge)
    if not findings:
        return RedactionResult(text, (), True)

    # Клауза может накрыть несколько находок — схлопываем пересечения, иначе
    # вторая замена резала бы уже подменённый текст по устаревшим офсетам.
    spans: list[tuple[int, int, str]] = []
    for f in sorted(findings, key=lambda f: f.start):
        cs, ce = _clause_bounds(text, f.start, f.end)
        rule = "deadline" if f.rule in _DEADLINE_RULES else "price"
        if spans and cs <= spans[-1][1]:
            ps, pe, prule = spans[-1]
            spans[-1] = (ps, max(pe, ce), prule if prule == "deadline" else rule)
        else:
            spans.append((cs, ce, rule))

    records: list[Redaction] = []
    out: list[str] = []
    cursor = 0
    for cs, ce, rule in spans:
        clause = text[cs:ce]
        lead_ws = clause[:len(clause) - len(clause.lstrip())]
        tail_ws = clause[len(clause.rstrip()):]
        table = _REDACTION_DEADLINE if rule == "deadline" else _REDACTION_PRICE
        phrase = table.get(language, table["ru"])
        if _starts_sentence(text, cs + len(lead_ws)):
            phrase = phrase[0].upper() + phrase[1:]
        out.append(text[cursor:cs])
        out.append(f"{lead_ws}{phrase}{tail_ws}")
        cursor = ce
        for f in findings:
            if cs <= f.start < ce:
                records.append(Redaction(f.number, f.rule, len(clause.strip())))
    out.append(text[cursor:])

    redacted = "".join(out)
    return RedactionResult(redacted, tuple(records),
                           not contains_unbacked_claim(redacted, knowledge))


def within_hourly_limit(store: Store, contact_id: str, *, now: float, limit: int) -> bool:
    sent = store.count_messages_since(contact_id, role="assistant", since_ts=now - 3600.0)
    return sent < limit


def within_daily_cap(store: Store, *, now: float, cap: int) -> bool:
    day_start = now - (now % 86400.0)
    sent = store.count_outbound_between(day_start, day_start + 86400.0)
    return sent < cap
