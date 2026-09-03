from __future__ import annotations
from dataclasses import dataclass
import re
from chatter.core.langdetect import detect_language
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
# ЧАСЫ ПО-УКРАИНСКИ — та же дыра третий раз, и она была НЕВИДИМА: стем «годин»
# начинается на «год», а «год» уже занят РУССКИМ годом выше по группе, где после
# него стоит `\b`. Для «годин» ветка «год» проходит буквы, но упирается в границу
# слова («год» есть, дальше «ин») — и НИ ОДНА ветка не матчила. Замер до правки:
# «Займе 40 годин.» → findings=[] (сорок часов работы уезжали лиду молча), при
# том что русское «за 3 часа» ловилось. У детейлинга сроки в часах — основная
# форма («тривалість 6–10 годин»), так что молчала как раз главная единица.
# Окончания перечислены ЯВНО, а не через `\w*`, по той же причине, что у годов:
# `годин\w*` утащил бы ГОДИННИК (часы-прибор) в единицы времени и пометил бы его
# числа срочными. Ветка стоит ПЕРЕД «год», чтобы более длинный стем выигрывал
# явно, а не за счёт того, что у соседа не сработала граница слова.
# Негативный контроль (проверено): годинник, годиться/годяться, годування/
# годувати — единицей времени НЕ считаются.
_TIME_UNIT = (
    r"(?:\b(?:дн(?:я|ей|ь)?|ден|тижн|тижд|місяц)\w*|"
    r"\b(?:недел|час|месяц)\w*|"
    r"\b(?:рік|рок(?:у|и|ів|ах|ам|ом|ами)|роц(?:і|ях)|"
    r"годин(?:а|и|у|ою|і|ах|ам|ами)?|"
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
        # Украинские ЧАСЫ. Стем ровно «годин», и это осознанный выбор в обе
        # стороны, потому что здесь цена ошибки ДРУГАЯ, чем в _TIME_UNIT: этот
        # список решает, обеспечено ли БЕСЧИСЛОВОЕ «за годину» текстом базы, и
        # слишком широкий стем даёт ЛОЖНОЕ «обеспечено» — то есть ПРЯЧЕТ дефект,
        # а не поднимает ложную тревогу.
        #  • Короче нельзя: «год» уже в списке, а `_stem_of` берёт САМЫЙ ДЛИННЫЙ
        #    подходящий стем, поэтому без «годин» слово «години» съезжало бы на
        #    «год», и `\bгод` в базе матчил бы «ГОДування», «ГОДиться»,
        #    «ГОДинник» — выдуманный час считался бы подтверждённым прайсом,
        #    где о часах не сказано ни слова (та же грабля, что «ден» в «ведення»).
        #  • Длиннее нельзя: «годин» — общий префикс ВСЕХ требуемых форм
        #    (година/години/годину/годиною/годин/годині/годинах/годинам/годинами),
        #    любой более длинный стем перестал бы находить часть из них в базе.
        # Остаточный риск назван честно: `\bгодин` матчит ещё и «годинник», то
        # есть база, где упомянуты только часы-ПРИБОР, признает бесчисловое «за
        # годину» обеспеченным. Сузить нечем — проверка стема префиксная (см.
        # `_mentions_stem`), а «годин» — настоящая словоформа; в knowledge
        # детейлинга «годинник» не встречается.
        "годин",
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

# DEV-35. Предложение вправе нести НЕСКОЛЬКО видов чисел разом, и дата — один
# из них.
#
# Замер на живом конфиге Ярины (17.08): «Акція діє до 30 вересня 2026 року: при
# замовленні двоетапного полірування (8 000–13 000 грн)…» давало
# `_Finding(number='2026', rule='price')`, и ответ подавлялся. Короткая форма
# («Так, акція діє до 30 вересня 2026 року») была чиста — то есть **чем полнее
# бот пересказывал акцию, тем вернее его ответ глушился**: дефект наказывал за
# правильное поведение.
#
# Корень не в дате как таковой. `_NUM_IN_TIME_CONTEXT` ищет число ПЕРЕД
# единицей времени и допускает до двух слов между ними — в «30 вересня 2026
# року» год попадал ровно в эти «слова-заполнители»: захватывалось «30», а
# «2026» оставалось незамеченным и уезжало сверяться с ЦЕНОВЫМ множеством,
# где года законно нет.
#
# Поэтому ниже — не «год никогда не цена» (это запретило бы законную цену
# «2026 грн»), а «число, стоящее В ДАТЕ, — срочное». Дата опознаётся по
# соседству, которого у цены не бывает:
#   • год при словах года: «2026 року», «2026 р.», «2026 году»;
#   • день и год при названии месяца: «30 вересня», «30 вересня 2026».
# Украинские месяцы живут ЗДЕСЬ, а не в `_TIME_UNIT`: тот список решает ещё и
# «обеспечена ли бесчисловая единица базой», и расширять его значит менять
# поведение сразу трёх проверок у четырёх живых клиентов.
_YEAR_WORD = r"(?:рок(?:у|и|ів|ах|ам|ом|ами)|рік|роц(?:і|ях)|год(?:а|у|ом|ы)?|р\.|г\.)"
_MONTH_NAME = (
    r"(?:січн|лют|берез|квітн|травн|червн|липн|серпн|вересн|жовтн|листопад|груд|"
    r"январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*"
)
_DATE_NUMBERS = re.compile(
    rf"\b((?:19|20)\d{{2}})\s*{_YEAR_WORD}"          # 2026 року / 2026 г.
    rf"|\b(\d{{1,2}})\s+{_MONTH_NAME}\s+((?:19|20)\d{{2}})\b"   # 30 вересня 2026
    rf"|\b(\d{{1,2}})\s+{_MONTH_NAME}",              # 30 вересня
    re.IGNORECASE,
)


def _numbers(text: str) -> set[str]:
    return {re.sub(r"\s", "", m.group()) for m in _NUMBER.finditer(text or "")}


# --------------------------------------------------------------------------
# C-1 / D2-1: число, названное лидом, — не выдумка бота
# --------------------------------------------------------------------------
#
# `_findings` видит только `reply` и `knowledge`, поэтому бюджет самого лида
# ловился правилом `large_number` как необеспеченный, клауза с ним заменялась
# на «узгоджуємо індивідуально», а при неполной чистке ответ подавлялся
# целиком. Пока это так, ни один playbook не заставит бота считать по вводным
# клиента (спека sales-competence §1.2).
#
# ГРАНИЦА (§4 D2-1) реализована ДВУМЯ условиями, и оба обязательны:
#   * число обязано ЗВУЧАТЬ ОТ ЛИДА — атрибуция в той же фразе («ви називали»,
#     «ваш бюджет»); притяжательного местоимения самого по себе НЕ достаточно;
#   * во фразе не должно быть утверждения О НАШЕЙ ЦЕНЕ («ціна», «вартість»,
#     «коштує») — иначе лид продиктует «$100 за айдентику», а бот повторит это
#     как нашу цену (Риск 5 спеки).
# Нет атрибуции → поведение остаётся ДОСЛОВНО прежним. Это fail-closed:
# отсутствие улики оставляет гардрейл включённым.
_LEAD_ATTRIBUTION = re.compile(
    r"(?:\bви\s+(?:називали|назвали|казали|говорили|згадували|дали|маєте)"
    r"|\bвы\s+(?:называли|назвали|говорили|сказали|упоминали|дали)"
    r"|\byou\s+(?:mentioned|said|named|gave|have)"
    r"|\bваш\w*\s+(?:бюджет\w*|мет\w*|цел\w*|ціл\w*|орієнтир\w*|ориентир\w*)"
    r"|\byour\s+(?:budget|goal|target)"
    r"|\bза\s+ваш\w*\s+(?:дан\w*|цифр\w*|вступн\w*|вводн\w*)"
    r")",
    re.IGNORECASE,
)

# Утверждение о НАШЕЙ цене. Ветирует пропуск даже при живой атрибуции.
_OUR_PRICE_ASSERTION = re.compile(
    r"(?:ц[іе]н\w*|варт[іо]ст\w*|стоимост\w*|сто[ий]мост\w*|кошту\w*"
    r"|обійд\w*|обойд\w*|прайс\w*|\bprice\b|\bcost\b)",
    re.IGNORECASE,
)


def lead_numbers(texts) -> frozenset[str]:
    """Числа, произнесённые ЛИДОМ в окне истории.

    Нормализация та же, что у `_numbers` (пробелы внутри числа снимаются),
    иначе «1 000» у лида и «1000» в ответе не встретятся, и правило не
    сработает ровно там, где лид пишет по-человечески."""
    out: set[str] = set()
    for t in texts or ():
        out |= _numbers(t or "")
    return frozenset(out)


def _lead_backed(fragment: str, number: str | None,
                 lead_nums: frozenset[str]) -> bool:
    """Обеспечено ли ЧИСЛОМ ЛИДА это конкретное место ответа."""
    if not number or number not in lead_nums:
        return False
    if _OUR_PRICE_ASSERTION.search(fragment or ""):
        return False
    return _LEAD_ATTRIBUTION.search(fragment or "") is not None


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
    for pattern in (_NUM_IN_TIME_CONTEXT, _DATE_NUMBERS):
        for m in pattern.finditer(text or ""):
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


def _findings(reply: str, knowledge: str, *,
              lead_nums: frozenset[str] = frozenset()) -> list[_Finding]:
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
        if num in deadline_numbers:
            continue
        if _lead_backed(text, num, lead_nums):
            continue
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
            if num in expected:
                continue
            if _lead_backed(sentence, num, lead_nums):
                continue
            out.append(_Finding(s + m.start(), s + m.end(), num,
                                "deadline" if is_time else "price"))

    for m in _NUMBER.finditer(text):
        num = re.sub(r"\s", "", m.group())
        digits = re.sub(r"\D", "", num)
        if (digits and int(digits) >= 100
                and num not in known_numbers
                and num not in lead_nums):
            out.append(_Finding(m.start(), m.end(), num, "large_number"))

    return out


def contains_unbacked_claim(reply: str, knowledge: str, *,
                            lead_numbers: frozenset[str] = frozenset()) -> bool:
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
    return bool(_findings(reply, knowledge, lead_nums=lead_numbers))


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


_TRAILING_DELIM = re.compile(r"\s*[,;]\s*$")
_LEADING_DELIM = re.compile(r"^\s*[,;]\s*")


def _drop_trailing_delim(chunk: str) -> str:
    """Снять ОДИН разделитель, которым удаляемая клауза крепилась слева."""
    return _TRAILING_DELIM.sub("", chunk)


def _drop_leading_delim(chunk: str) -> str:
    """То же справа — для клаузы, у которой слева разделителя не было."""
    return _LEADING_DELIM.sub(" " if chunk[:1].isspace() else "", chunk)


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


def redact_unbacked(reply: str, knowledge: str, *, language: str = "ru",
                    lead_numbers: frozenset[str] = frozenset()) -> RedactionResult:
    """Вырезать необеспеченные утверждения, сохранив всё остальное.

    `clean=False` означает «редакцией не спаслось» — вызывающий обязан
    подавить ответ целиком. Проверка финальная и честная: результат
    прогоняется через тот же `contains_unbacked_claim`, поэтому выдуманная
    цена/срок не может уехать лиду через эту дверь."""
    text = reply or ""
    # D2-4: формула говорит на языке ОТВЕТА. `language` (скалярка клиента)
    # остаётся фолбэком — нет сигнала, нет и перемены поведения.
    reply_language = detect_language(text, default=language)
    findings = _findings(text, knowledge, lead_nums=lead_numbers)
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
    # D2-3: одна формула — один раз на ответ. Сравниваем БАЗОВУЮ константу, а не
    # написание: первая вставка в начале предложения приходит с большой буквы, и
    # наивное сравнение строк пропустило бы повтор.
    used: set[str] = set()
    drop_next_delim = False
    for cs, ce, rule in spans:
        clause = text[cs:ce]
        lead_ws = clause[:len(clause) - len(clause.lstrip())]
        tail_ws = clause[len(clause.rstrip()):]
        table = _REDACTION_DEADLINE if rule == "deadline" else _REDACTION_PRICE
        phrase = base = table.get(reply_language, table["ru"])
        gap = text[cursor:cs]
        if drop_next_delim:
            gap = _drop_leading_delim(gap)
            drop_next_delim = False
        if base in used:
            # Повтор: клауза УДАЛЯЕТСЯ вместе с формулой, а не заменяется на неё.
            # Забираем разделитель, которым она крепилась к соседу, иначе
            # остаётся «текст, , текст».
            trimmed = _drop_trailing_delim(gap)
            drop_next_delim = trimmed == gap
            out.append(trimmed)
        else:
            used.add(base)
            if _starts_sentence(text, cs + len(lead_ws)):
                phrase = phrase[0].upper() + phrase[1:]
            out.append(gap)
            out.append(f"{lead_ws}{phrase}{tail_ws}")
        cursor = ce
        for f in findings:
            if cs <= f.start < ce:
                records.append(Redaction(f.number, f.rule, len(clause.strip())))
    tail = text[cursor:]
    out.append(_drop_leading_delim(tail) if drop_next_delim else tail)

    redacted = "".join(out)
    return RedactionResult(redacted, tuple(records),
                           not contains_unbacked_claim(
                               redacted, knowledge, lead_numbers=lead_numbers))


def within_hourly_limit(store: Store, contact_id: str, *, now: float, limit: int) -> bool:
    sent = store.count_messages_since(contact_id, role="assistant", since_ts=now - 3600.0)
    return sent < limit


def within_daily_cap(store: Store, *, now: float, cap: int) -> bool:
    day_start = now - (now % 86400.0)
    sent = store.count_outbound_between(day_start, day_start + 86400.0)
    return sent < cap
