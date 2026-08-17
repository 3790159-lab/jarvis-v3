# -*- coding: utf-8 -*-
"""`brief.json` → пять файлов клиента. Каждое правило формата — КОД с причиной.

Спека: `docs/superpowers/specs/2026-08-14-chatter-onboarding-pipeline.md`, §2
(правила R1–R9 и «Что генератор НЕ решает сам»).
План:  `docs/superpowers/plans/2026-08-17-onboard-pipeline-plan.md`.

Почему причина у каждого правила обязана быть В КОДЕ, а не в спеке: спеку через
месяц никто не откроет, а правило без причины «упростят». Все причины ниже —
не рассуждения, а следы уже случившихся поломок: guardrail, зарезавший
собственный прайс клиента; молча выключенный слой эскалации; лоадер, падающий
на переименованном ключе.

ГРАНИЦА МОДУЛЯ. Здесь ноль сети, ноль LLM, ноль xlsx: вход — уже разобранный
`brief.json` (контракт плана), выход — тексты пяти файлов в памяти. Диск и
отчёт не наши: их пишут `__main__.py` и `report.py`.

ЧТО МОДУЛЬ НЕ РЕШАЕТ САМ (спека §2, конец, и §5). `owner_ref` в правильном
падеже, имя персоны, модель, `funnel_gate`, `payments`, `active.yaml`. Всё это
уезжает в `RenderResult.defaults` машиночитаемым списком, из которого
`report.py` собирает раздел 2 отчёта. Дефолты ЖЁСТКИЕ даже против брифа:
`honesty_mode: honest`, `funnel_gate: false`, `strict_knowledge: true`,
payments выключены без реквизитов. Бриф просил другого — это строка отчёта, а
не повод менять дефолт.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace

from chatter.core import guardrails as _guardrails
from chatter.core.brain import EXAMPLES_CHAR_BUDGET
from chatter.core.brand_safety import forbidden_mention
from chatter.core.escalation import _KEYWORD_HEADINGS, parse_escalation_keywords
from chatter.onboard import brief as _brief
from chatter.onboard.vocabulary import (
    DUAL_PURPOSE_FIELDS,
    PROMO_SECTION_UK,
    REQUIRED_FACTS,
    REQUIRED_SECTIONS_UK,
    STUB_TEMPLATE_UK,
    UNKNOWN_SECTION_UK,
)


class RenderError(Exception):
    """Нарушение R1/R2/R6 — ПАДЕНИЕ, а не запись файла.

    Спека §2 требует именно этого: «попытка = ошибка генерации, а не находка на
    приёмке». Записанный на диск конфиг с необеспеченным числом или с
    самоотравлением brand-safety выглядит как успешный прогон и доезжает до
    боевого каталога; красное на приёмке ловит это в лучшем случае, а на
    практике — лид ловит.
    """


FILE_NAMES: tuple[str, ...] = (
    "knowledge.md", "persona.md", "playbook.md", "examples.yaml", "settings.yaml")


@dataclass(frozen=True)
class RenderResult:
    """Пять файлов + всё, из чего `report.py` собирает разделы 2 и 3.

    ШЕСТЬ полей контракта, и каждое отвечает на свой вопрос отчёта:

    `files` — пять файлов клиента в памяти (диск не наш, его пишет `__main__`).

    `defaults` — что подставил генератор вместо решения владельца:
    `{key, value, why, brief_wanted_other, brief_quote}` → раздел 2 отчёта.

    `stubs` — ТОЛЬКО факты из `vocabulary.REQUIRED_FACTS`, ушедшие заглушкой:
    `{fact_id, title, stub_line}` → раздел 3 отчёта и парность C10. Никаких
    синтетических id здесь быть не может: C10 обходит `REQUIRED_FACTS` и сверяет
    их с этим списком, а лишняя запись превращает сверку в поиск факта, которого
    в словаре нет.

    `section_stubs` — та же форма, но про обязательные РАЗДЕЛЫ (R8), для которых
    в брифе не нашлось источника. Отдельным полем именно потому, что C10 их
    сверять не должна, а человеку в отчёте они нужны не меньше.

    `counters` — счётчики артефакта: services, prices, deadlines, stop_words,
    forbidden, example_pairs.

    `dropped` — ВСЁ, что генератор не выпустил наружу, с причиной:
    `{kind, value, reason}`. Поле контрактное, а не служебное, и вот почему:
    единственная альтернатива дропу — тихое исчезновение, а тихо исчезнуть
    может весь `examples.yaml` целиком (каждая пара из брифа может нести число
    вне прайса), и клиент уедет с пустым голосом персоны, ни разу не покраснев.
    Сюда падают: пары примеров, отброшенные по R2 или по
    `brain.EXAMPLES_CHAR_BUDGET`; кандидаты в стоп-слова, отсеянные за коллизию
    с легальным текстом; и ответы брифа с вердиктом `ok`, не доехавшие ни в
    один файл (третье состояние «было и потерялось» — см. `_record_lost_answers`).
    """

    files: dict[str, str]
    defaults: list[dict]
    stubs: list[dict]
    counters: dict
    dropped: list[dict] = field(default_factory=list)
    section_stubs: list[dict] = field(default_factory=list)


# ── R4: заголовок секции ключевых слов ──────────────────────────────────────
#
# `escalation._KEYWORD_HEADINGS` знает РОВНО три формулировки. Любая другая =
# детерминированный слой эскалации молча выключен: `parse_escalation_keywords`
# вернёт `[]`, ошибки не будет, а бот перестанет звать человека на «поверніть
# гроші» — и узнаем мы об этом от лида. Поэтому заголовок здесь не литерал «на
# глаз», а СВЕРЕННЫЙ с живым модулем: расхождение обязано убить импорт, а не
# доехать до клиента.
ESCALATION_HEADING_UK = "Ключові слова ескалації"
if ESCALATION_HEADING_UK.casefold() not in _KEYWORD_HEADINGS:
    raise RuntimeError(
        f"[onboard.render] заголовок секции эскалации «{ESCALATION_HEADING_UK}» "
        f"не входит в escalation._KEYWORD_HEADINGS={list(_KEYWORD_HEADINGS)} — "
        f"сгенерированный playbook имел бы МОЛЧА выключенный слой эскалации")

# ── R1: символы, которые в наших файлах жить не имеют права ─────────────────
#
# NBSP из Google Forms приезжает регулярно, и `guardrails._numbers` снимает
# только `\s`-пробелы... которыми NBSP как раз ЯВЛЯЕТСЯ в Python-регулярках.
# Но человек, читающий файл, NBSP не видит, а `str.split()` и наши собственные
# сравнения по подстроке — видят. Один и тот же прайс в двух вариантах пробела
# = «два числа на одну вещь».
_SPACE_CHARS = "        　   "
# Апострофы: в брифе живут U+02BC («імʼя») и U+2019 («обовʼязково»). Сводим к
# U+0027 — тому же, к которому сводит отпечатки `brief._unify`, чтобы
# forbidden_terms и текст файлов сравнивались в одном алфавите (R6).
_APOSTROPHE_CHARS = "’ʼʻ‘′‵´`"
APOSTROPHE = "'"
# Тире диапазона — U+2013, как в эталоне.
EN_DASH = "–"
# `. ! ? ;` внутри ценовой строки — тот самый нож, которым guardrail режет
# собственный прайс клиента (см. sanitize_price_line).
_SENTENCE_BREAKERS = ".!?;"

# Ссылка — не ценовая строка: её точки часть адреса, а не пунктуация.
_URL_RE = re.compile(r"https?://\S+|(?<![\w.@-])(?:[a-z0-9][\w-]*\.)+[a-z]{2,}(?![\w-])", re.I)

# Внутренний маркер служебной секции playbook: единственное место, где R2
# ослаблен (см. `_assert_numbers_allowed` и комментарий у ICP-секции).
INTERNAL_MARKER_UK = "внутрішнє, вголос не цитувати"

# ── R6: два класса терминов, которые в forbidden_terms стоят ВСЕГДА ─────────
#
# Первый — платёжная лексика чужой юрисдикции. Стоит ноль, а ловит целый класс
# аварии: украинский бот, заговоривший про рубли и российские банки, — это не
# стилистика, это репутация клиента.
_INHERITED_PAYMENT_TERMS: tuple[str, ...] = (
    "рубл", "руб.", "₽", "сбербанк", "тинькофф", "тінькофф", "qiwi", "киви",
    "юмани", "yoomoney", "сбп",
)
# Второй — КОРНИ ролевых слов персон прошлых клиентов. Да, корни, и это не
# нарушение «полными фразами, а не корнями»: правило R6 про корни защищает
# ЛЕГАЛЬНУЮ речь клиента от ложного срабатывания, а здесь термин легальной
# речью быть не может в принципе. «Керівниця» в детейлинге не существует; если
# слово прозвучало — в промпт протёк чужой конфиг, и это надо ловить, а не
# стилизовать. Самоотравление всё равно проверяется ниже: если корень вдруг
# окажется частью собственного текста клиента, генерация упадёт и покажет
# строку, а не выключит защиту молча.
_FOREIGN_PERSONA_TERMS: tuple[str, ...] = ("керівниц",)

# ── R4: из чего НЕ делают ключевые слова ────────────────────────────────────
#
# Ведущие местоимения лида: «Хочу поговорити з менеджером» и «Поговоріть з
# менеджером» — один сигнал, и держать их двумя записями значит завести два
# числа на одну вещь.
_KW_LEADING_FILLER = frozenset({
    "хочу", "хочемо", "мене", "мені", "мій", "моя", "моє", "мої",
    "ви", "буду", "будемо", "я", "ми", "у", "в", "це",
})
_KW_PREPOSITIONS = frozenset({"з", "із", "зі", "до", "в", "у", "на", "по", "про"})
# Местоимение после предлога не сигнал, а шум: «у вас», «з вами» матчат
# половину входящих сообщений. Именно на них обрезка фразы до предложного
# хвоста обязана НЕ срабатывать.
_KW_PRONOUNS = frozenset({
    "вас", "вами", "вам", "нас", "нами", "нам", "мене", "мною", "мені",
    "ним", "нею", "них", "ньому", "тобою", "тебе",
})

# R4: голый корень ОБЯЗАН дорасти до полной фразы, а не быть отброшенным.
#
# Клиент пишет стоп-слова как придётся: у Ярины это готовые фразы («Хочу
# поговорити з менеджером»), но с тем же успехом приезжает голый список
# «менеджер / власник / скарга / суд». Голое «власник» эскалирует лида,
# сказавшего «я власник BMW», то есть каждого второго; голое «суд» сидит
# внутри «посуд». Спека называет лечение прямо: «з власником», а не «власник».
#
# Дорастить = поставить перед корнем ПРЕДЛОГ. Именно предлог, а не падежное
# окончание: склонять программно мы не будем (спека §5), а «з власник» —
# подстрока и «з власником», и «з власника», и «з власницею». Матч у
# `escalation` идёт подстрокой, поэтому усечённый корень ПОСЛЕ предлога ловит
# все падежи сразу и не ловит ни одного законного «я власник».
#
# Ключ — стем (корень без последней гласной), значение — предлог.
_KW_STEM_PREPOSITION: dict[str, str] = {
    # роли человека: без предлога ловят самого лида
    "менеджер": "з", "власник": "з", "керівник": "з", "директор": "з",
    "начальник": "з", "адміністратор": "з", "людин": "з", "майстер": "з",
    "майстр": "з", "оператор": "з", "спеціаліст": "з", "хазя": "з",
    # институции: корень слишком короткий и сидит внутри обычных слов
    "суд": "до", "поліці": "до", "міліці": "до", "прокуратур": "до",
}
_KW_VOWELS = "аеєиіїоуюяы"
# Ниже этой длины «фраза» перестаёт быть однозначной вне контекста и уходит в
# `dropped` с причиной — но именно уходит С ЗАПИСЬЮ, а не исчезает.
_KW_MIN_LEN = 4

# Дефолты, которые генератор НЕ решает сам (спека §2). Значения — рабочие,
# решение — владельца, и каждое уезжает строкой в раздел 2 отчёта.
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_PERSONA_AGE = 26
DEFAULT_LANGUAGE = "uk"
_LANGUAGE_BY_BRIEF = {
    "українська": "uk", "украинский": "uk", "ukrainian": "uk",
    "русский": "ru", "російська": "ru", "russian": "ru",
    "english": "en", "англійська": "en", "английский": "en",
}
_CURRENCY_TOKENS = (
    ("грн", "грн"), ("₴", "грн"), ("uah", "грн"),
    ("$", "USD"), ("usd", "USD"), ("€", "EUR"), ("eur", "EUR"),
)

# Транслитерация slug → имя персоны. Это ДЕФОЛТ и только дефолт: имя персоны —
# решение владельца (спека §2), бриф Ярины в этом поле дал «Джарвис» — наше
# собственное имя. Латиница `i` кладётся в «и», а не в «і», потому что slug
# рождается из уже принятого имени, а не наоборот.
_TRANSLIT = (
    ("shch", "щ"), ("sch", "щ"), ("ya", "я"), ("yu", "ю"), ("ye", "є"),
    ("yi", "ї"), ("zh", "ж"), ("kh", "х"), ("ts", "ц"), ("ch", "ч"),
    ("sh", "ш"), ("iu", "ю"), ("ia", "я"), ("ie", "є"),
    ("a", "а"), ("b", "б"), ("v", "в"), ("h", "г"), ("g", "ґ"), ("d", "д"),
    ("e", "е"), ("z", "з"), ("y", "и"), ("i", "и"), ("j", "й"), ("k", "к"),
    ("l", "л"), ("m", "м"), ("n", "н"), ("o", "о"), ("p", "п"), ("r", "р"),
    ("s", "с"), ("t", "т"), ("u", "у"), ("f", "ф"), ("c", "ц"), ("w", "в"),
    ("x", "кс"), ("q", "к"),
)


# ────────────────────────────────────────────────────────────────────────────
# R1. Одна строка на услугу: «цена + валюта + срок», без точек внутри
# ────────────────────────────────────────────────────────────────────────────

def _unify_chars(text: str) -> str:
    """Пробелы и апострофы к одному виду. Ни одного решения о смысле."""
    out = []
    for ch in text or "":
        if ch in _SPACE_CHARS:
            out.append(" ")
        elif ch in _APOSTROPHE_CHARS:
            out.append(APOSTROPHE)
        else:
            out.append(ch)
    return "".join(out)


_DECIMAL_SENTINEL = "\x00"


def sanitize_price_line(text: str) -> str:
    """Строку, в которой живут ЧИСЛА, привести к форме, безопасной для guardrail.

    ПРИЧИНА, из-за которой эта функция вообще существует.
    `guardrails._context_numbers` режет knowledge по `[\\n.!?;]`, и число
    попадает в обеспеченное множество ТОЛЬКО из фрагмента, где есть валюта или
    ценовое слово. Точка внутри ценовой строки рвёт фрагмент пополам: «Ціна
    5 000–8 000 грн. Тривалість 6–10 годин» отдаёт валюту первой половине, а
    «6» и «10» оставляет без контекста — и **guardrail режет собственный прайс
    клиента**, подавляя ответ, обеспеченный базой на 100%.

    Отсюда четыре правила, и каждое — не стиль:

    1. `. ! ? ;` внутри строки запрещены (сокращения разворачиваются словами);
    2. десятичный разделитель — ЗАПЯТАЯ, один и тот же на весь файл: `_numbers`
       снимает только пробелы, поэтому «1,5» и «1.5» — РАЗНЫЕ токены, и ответ
       с «1.5 години» окажется необеспеченным при живом «1,5» в базе;
    3. разделитель тысяч — обычный пробел (NBSP даёт другой токен);
    4. тире диапазона — U+2013, как в эталоне.

    Функция ИДЕМПОТЕНТНА: повторный прогон уже чистой строки её не меняет.
    """
    s = _unify_chars(text).strip()
    if not s:
        return ""

    # «+» рядом с числом читается как арифметика: «1 день + рекомендований час»
    # выглядит как формула, которую лид попробует сложить. Заменяется ТОЛЬКО
    # здесь, то есть только в строках с числами: в списке без чисел («марка +
    # модель + рік») плюс — нормальный компактный разделитель, и «плюс»
    # словами сделал бы строку хуже.
    s = re.sub(r"(?<=\S)\s\+\s(?=\S)", " плюс ", s)

    # Десятичную запятую прячем ПЕРВОЙ. Иначе нормализация «, » ниже разорвёт
    # «1,5» в «1, 5», и одно число превратится в два — ровно та поломка, от
    # которой правило 2 и защищает.
    s = re.sub(r"(?<=\d)\.(?=\d)", ",", s)            # 1.5 → 1,5
    s = re.sub(r"(?<=\d),(?=\d)", _DECIMAL_SENTINEL, s)

    # Тире диапазона: только МЕЖДУ цифрами. «фари — 2 500» трогать нельзя, это
    # тире пунктуационное, а не диапазонное.
    s = re.sub(rf"(?<=\d)\s*[-—−‒―]\s*(?=\d)", EN_DASH, s)

    # Сначала снимаем ЗАВЕРШАЮЩУЮ пунктуацию, потом переводим оставшиеся
    # разделители предложений в запятую: «Термін: 6–10 годин.» → «6–10 годин»,
    # а «а; б» → «а, б».
    s = s.rstrip(" ,:" + _SENTENCE_BREAKERS)
    s = re.sub(rf"\s*[{re.escape(_SENTENCE_BREAKERS)}]+\s*", ", ", s)
    s = re.sub(r"\s*,\s*", ", ", s)
    s = re.sub(r"(?:,\s*)+", ", ", s)
    s = re.sub(r"\s{2,}", " ", s)
    s = s.strip().strip(",").strip()
    s = s.replace(_DECIMAL_SENTINEL, ",")

    # Самопроверка санитайзера. Если он что-то пропустил, узнать об этом надо
    # здесь, а не на приёмке: молча выпущенная точка = зарезанный прайс.
    _assert_price_line_clean(s, where="sanitize_price_line")
    return s


def _assert_price_line_clean(line: str, *, where: str) -> None:
    for ch in _SENTENCE_BREAKERS:
        if ch in line:
            raise RenderError(
                f"{where}: в строке с числами остался «{ch}» на позиции "
                f"{line.index(ch)} — он разрежет ценовой фрагмент, и guardrail "
                f"зарежет собственный прайс клиента: {line!r}")
    for ch in _SPACE_CHARS:
        if ch in line:
            raise RenderError(
                f"{where}: NBSP/узкий пробел на позиции {line.index(ch)} — "
                f"даёт ДРУГОЙ числовой токен: {line!r}")


def _render_line(text: str) -> str:
    """Одна строка выходного файла.

    Санитайзер R1 применяется к строкам, где ЕСТЬ цифра, — там и только там
    точка ломает разбор. Прозе без чисел точки не мешают, а насильно снятые
    они превратили бы человеческий абзац в телеграмму. Правило узкое
    намеренно: широкое («снять точки везде») пришлось бы обходить руками, а
    обойденное правило — это отсутствующее правило.
    """
    s = _unify_chars(text).strip()
    if not s:
        return ""
    # Строка со ССЫЛКОЙ санитайзеру R1 не отдаётся: точки в домене — часть
    # адреса, и «auto24.ua» превратилось бы в «auto24, ua», то есть в
    # неработающую ссылку. Ценовому разбору это ничем не грозит: в URL нет ни
    # валюты, ни ценового слова, поэтому лишний разрез фрагмента не может
    # оставить цену без контекста — он может только раздробить сам адрес.
    if _URL_RE.search(s):
        return s.rstrip(" ;,")
    if any(ch.isdigit() for ch in s):
        return sanitize_price_line(s)
    return s.rstrip(" ;.,")


# ────────────────────────────────────────────────────────────────────────────
# R2. Публичные границы цены — литералом
# ────────────────────────────────────────────────────────────────────────────

def allowed_numbers(price_block: str) -> set[str]:
    """Множество чисел, которые боту РАЗРЕШЕНО произнести.

    Считается НЕ своим разбором, а `guardrails._context_numbers` — тем самым,
    который в рантайме решает, подавить ответ или пропустить. Свой разбор
    здесь был бы вторым числом на ту же вещь: он разъехался бы с боевым на
    первой же правке морфологии (`_TIME_UNIT` правили дважды — из-за «день» и
    из-за «рік»), и генератор начал бы выпускать «обеспеченные» числа, которые
    рантайм режет.

    Возвращается ОБЪЕДИНЕНИЕ ценового и срочного множеств: R2 говорит про
    любое число, которое бот произносит, а «6–10 годин» — такое же публичное
    обещание, как «5 000 грн».

    Число попадает сюда только из фрагмента, где есть валюта, ценовое слово
    или единица времени. Число из фрагмента «пункт 3 договору» — не попадает,
    и это не придирка: именно так рантайм и считает.
    """
    price, deadline = _guardrails._context_numbers(price_block or "")
    return price | deadline


def _number_tokens(text: str) -> list[str]:
    """Числовые токены в той же нормализации, что у guardrails (`\\s` снят)."""
    return [re.sub(r"\s", "", m.group())
            for m in _guardrails._NUMBER.finditer(text or "")]


def _assert_numbers_allowed(text: str, allowed: set[str], *, where: str) -> None:
    """Ни одного числа вне множества — включая производные («від 5 000»).

    R2 дословно: генератор «не выпускает наружу ни одного числа вне него».
    Проверка стоит на КАЖДОМ куске, который бот может произнести, поэтому
    «до 150 000 грн» в playbook, которого нет в прайсе, роняет генерацию, а не
    доезжает до лида, чтобы там быть подавленным как выдумка.
    """
    for tok in _number_tokens(text):
        if tok not in allowed:
            raise RenderError(
                f"{where}: число «{tok}» отсутствует в прайсе клиента "
                f"(R2: наружу выпускаются только числа из knowledge). Строка: "
                f"{text.strip()!r}")


def _unbacked(text: str, knowledge: str) -> list:
    return _guardrails._findings(text or "", knowledge or "")


# ────────────────────────────────────────────────────────────────────────────
# Мелкие текстовые помощники
# ────────────────────────────────────────────────────────────────────────────

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_QUOTE_CHARS = "«»‹›\"“”„'"


def _strip_quotes(text: str) -> str:
    return (text or "").strip().strip(_QUOTE_CHARS).strip()


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split((text or "").strip()) if s.strip()]


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _upper_first(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]


def _list_items(value: str) -> list[str]:
    """Список из ответа-простыни: перевод строки — разделитель, `;`/`.` — мусор.

    Отдельно разворачиваются ответы, написанные ОДНИМ абзацем через двойной
    пробел (так приехали Q29 и Q41): без этого весь блок стал бы одним
    гигантским пунктом.

    Пункт, заканчивающийся двоеточием, НЕ склеивается со следующим и остаётся
    отдельным элементом. Склейка выглядит соблазнительно («Для хімчистки: фото
    салону»), но отличить подпись-к-одному-значению от заголовка-к-списку можно
    только по смыслу: у Ярины в одном поле живут оба («Для більшості робіт:» с
    одной строкой и «Асистент повинен зібрати:» с девятью). Смысловых суждений
    пайплайн не принимает, поэтому двоеточие рендерится врезкой (`_bullet_block`).
    """
    raw = _unify_chars(value or "")
    chunks: list[str] = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        if "  " in line:
            chunks.extend(part for part in re.split(r"\s{2,}", line) if part.strip())
        else:
            chunks.append(line)
    items: list[str] = []
    for chunk in chunks:
        for sentence in _sentences(chunk):
            item = sentence.strip().strip(";").strip()
            if item:
                items.append(item)
    return items


def _bullet_block(items: list[str], *, lower: bool = True) -> list[str]:
    """Пункты → строки файла. Заголовок с двоеточием — врезка, а не пункт."""
    out: list[str] = []
    for item in items:
        rendered = _render_line(_lower_first(item) if lower else item)
        if not rendered:
            continue
        if item.rstrip().endswith(":"):
            if out and out[-1].strip():
                out.append("")
            out.append(_upper_first(rendered.rstrip(":")) + ":")
            out.append("")
        else:
            out.append(f"- {rendered}")
    while out and not out[-1].strip():
        out.pop()
    return out


def _quoted_or_whole_lines(value: str) -> list[str]:
    """Фразы из ответа-перечисления. Кавычки — если они есть, строка — если нет.

    Форма ответа в спеке НЕ зафиксирована, и оба вида приезжают живьём: бриф
    Ярины дал `«100% гарантія результату»;` (кавычки плюс хвост-условие после
    них), а перечисление без кавычек — просто строка на фразу. Брать только
    закавыченное значило бы, что у клиента, не поставившего кавычки, денилист
    состоит из одних унаследованных терминов, R6 остаётся без источника, а C5
    зеленеет потому, что ей нечего проверять.

    Разбор ПОСТРОЧНЫЙ, а не по всему полю: хвост-условие («…», якщо реальної
    акції немає) обязан отвалиться вместе со своей строкой, а не приклеиться к
    следующей фразе.
    """
    out: list[str] = []
    for line in _unify_chars(value or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        quoted = re.findall(r"«([^»]+)»", line)
        for phrase in (quoted or [line]):
            phrase = phrase.strip().strip(_QUOTE_CHARS + " ;.,:").strip()
            if phrase:
                out.append(phrase)
    return out


def _drop_question_echo(items: list[str], question: str) -> list[str]:
    """Пункт, который является ПЕРЕСКАЗОМ вопроса формы, — не ответ.

    Клиент копирует вопрос в ответ регулярно (у Ярины так пришёл Q39: первая
    строка ответа — сам вопрос со звёздочкой). Мера похожести берётся у T1,
    чтобы во всём пайплайне она была одна.
    """
    q_tokens = _brief.normalize_question(question or "")
    if not q_tokens:
        return items
    kept = []
    for item in items:
        score = _brief.fingerprint_score(_brief.normalize_question(item), q_tokens)
        if score >= _brief.FINGERPRINT_THRESHOLD:
            continue
        kept.append(item)
    return kept


def _field(brief: dict, fid: str) -> dict | None:
    return ((brief or {}).get("fields") or {}).get(fid)


def _value(brief: dict, fid: str) -> str | None:
    """Значение поля, если оно ГОДНОЕ.

    `value` уже равен None при `verdict != ok` (инвариант brief.json), но
    проверка `verdict` здесь стоит явно: контракт держится сторожем T1, а
    молчаливая зависимость от чужого инварианта — это то, как мусор однажды
    доезжает до конфига.
    """
    f = _field(brief, fid)
    if not f or f.get("verdict") != "ok":
        return None
    val = f.get("value")
    if not isinstance(val, str) or not val.strip():
        return None
    return _unify_chars(val).strip()


def _raw_quote(brief: dict, fid: str) -> str | None:
    f = _field(brief, fid)
    if not f:
        return None
    raw = f.get("raw")
    return None if raw is None else str(raw).strip()


# ────────────────────────────────────────────────────────────────────────────
# Разбор прайса (Q22): 14 услуг эталона обязаны получиться 14 услугами
# ────────────────────────────────────────────────────────────────────────────

_SERVICE_HEADER = re.compile(r"^\s*(\d{1,2})\s*\.\s+(\S.+)$")
_LBL_PRICE = ("ціна:", "цена:", "вартість:")
_LBL_TERM = ("термін:", "терміни:", "строк:", "срок:")
_LBL_EXCLUDED = ("не входить",)
_LBL_INCLUDED = ("входить", "включає", "включають", "включає:")
_LBL_APPROX = ("орієнтовно", "приблизно")
_DEPENDS_PREFIXES = ("залежить", "залежно")


@dataclass(frozen=True)
class _Service:
    number: str
    title: str
    price: str
    term: str
    included: str
    included_label: str
    excluded: str
    depends: str
    notes: tuple[str, ...]
    sub_prices: tuple[str, ...]


def _split_label(paragraph: str) -> tuple[str, str]:
    """Абзац «Метка:\\nтело» → (метка, тело). Без метки → ('', абзац).

    Переводы строк в теле СОХРАНЯЮТСЯ: подпрайс «фари — 2 500–4 000 грн» живёт
    построчно, и склейка в одну строку превратила бы пять цен в один пункт —
    ровно те «пять цен в одном фрагменте», из-за которых потом невозможно
    понять, какая именно не обеспечена.
    """
    lines = [ln.strip() for ln in paragraph.split("\n") if ln.strip()]
    if not lines:
        return "", ""
    head = lines[0]
    if head.endswith(":"):
        return head[:-1].strip(), "\n".join(lines[1:]).strip()
    if ":" in head:
        label, _, rest = head.partition(":")
        body = "\n".join([rest.strip()] + lines[1:]).strip()
        return label.strip(), body
    return "", "\n".join(lines).strip()


def _flat(text: str) -> str:
    """Тело метки одной строкой — для мест, где список не подразумевается."""
    return re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()


def _parse_services(price_block: str) -> list[_Service]:
    """Прайс клиента → список услуг. Заголовок «N. Название» ведёт разбор.

    Форма прайса — не наша выдумка: ровно так его пишет клиент в Google-форме,
    и приёмка арки считает услуги (эталон — 14).
    """
    text = _unify_chars(price_block or "")
    blocks: list[tuple[str, str, list[str]]] = []
    current: tuple[str, str, list[str]] | None = None
    for line in text.split("\n"):
        m = _SERVICE_HEADER.match(line)
        if m and not any(line.strip().lower().startswith(p) for p in _LBL_PRICE + _LBL_TERM):
            if current:
                blocks.append(current)
            current = (m.group(1), m.group(2).strip().rstrip(".").strip(), [])
        elif current is not None:
            current[2].append(line)
    if current:
        blocks.append(current)

    services: list[_Service] = []
    for number, title, body_lines in blocks:
        paragraphs = _paragraphs("\n".join(body_lines))
        price = term = included = excluded = depends = included_label = ""
        notes: list[str] = []
        sub_prices: list[str] = []
        pending_label = ""
        for paragraph in paragraphs:
            label, body = _split_label(paragraph)
            if pending_label and not label and not body.endswith(":"):
                label, pending_label = pending_label, ""
            low = label.casefold()
            if not body and label:
                # «Орієнтовно:» отдельным абзацем — тело в следующем.
                pending_label = label
                continue
            if any(low.startswith(p.rstrip(":")) for p in _LBL_PRICE):
                price = _flat(body)
            elif any(low.startswith(p.rstrip(":")) for p in _LBL_TERM):
                term = _flat(body)
            elif any(p in low for p in _LBL_EXCLUDED):
                excluded = _flat(body)
            elif any(p in low for p in _LBL_INCLUDED):
                included, included_label = _flat(body), label.strip()
            elif any(p in low for p in _LBL_APPROX):
                sub_prices.extend(_list_items(body))
            elif label:
                notes.append(f"{label}: {_flat(body)}" if body else label)
            else:
                chunk = _flat(body or paragraph)
                if chunk.casefold().startswith(_DEPENDS_PREFIXES) and not depends:
                    depends = chunk
                else:
                    notes.append(chunk)
        services.append(_Service(
            number=number, title=title, price=price, term=term,
            included=included, included_label=included_label, excluded=excluded,
            depends=depends, notes=tuple(notes), sub_prices=tuple(sub_prices),
        ))
    return services


def _lengthen_titles(services: list[_Service], services_list) -> list[_Service]:
    """Заголовок прайса (Q22) дополняется полным названием из перечня (Q21).

    Решение владельца 17.08. Клиент пишет прайс сокращённо («Локальна
    хімчистка»), а перечень услуг — полностью («Локальна хімчистка окремих
    елементів»). Пока хвоста нет, услуга неотличима от «Комплексна хімчистка
    салону», и лид получает цену НЕ ТОЙ работы: это неверный счёт, а не
    некрасивое слово.

    Обе стороны — ответы САМОГО клиента, ничего не сочиняется. Условия
    замены жёсткие, и каждое закрывает свой способ ошибиться:

    * кандидат обязан НАЧИНАТЬСЯ с заголовка прайса — «похоже» не считается,
      иначе «Нанесення керамічного покриття» переименовало бы «Керамічне
      покриття кузова», то есть генератор начал бы сочинять названия;
    * кандидат обязан быть ДЛИННЕЕ — укорачивать нельзя никогда;
    * кандидат обязан быть ОДИН. Два продолжения одного заголовка — вопрос к
      владельцу («салону» и «салону та багажника» стоят разных денег), а
      молчаливый выбор одного из них ничем не лучше молчаливого дефолта.

    Состав услуг не меняется НИКОГДА: правило трогает текст заголовка, а
    числовые инварианты приёмки (14 услуг) считаются по составу.
    """
    entries = [ln.strip(" -–•\t") for ln in _unify_chars(services_list or "").split("\n")]
    entries = [re.sub(r"^\d+[.)]\s*", "", e).strip() for e in entries if e.strip()]
    out: list[_Service] = []
    for svc in services:
        title = svc.title
        low = title.casefold()
        candidates = [e for e in entries
                      if e.casefold().startswith(low) and len(e) > len(title)]
        out.append(replace(svc, title=candidates[0]) if len(candidates) == 1 else svc)
    return out


def _service_lines(svc: _Service) -> list[str]:
    """Одна услуга → блок строк knowledge.

    Цена, «от чего зависит» и срок собираются в ОДНУ строку намеренно (R1):
    так число, валюта и единица времени попадают в ОДИН фрагмент разбора, и
    `_context_numbers` признаёт обеспеченными и цену, и срок. Разнести их по
    трём строкам — значит вернуть ту самую поломку, ради которой правило и
    написано.
    """
    lines: list[str] = [f"## {svc.number}. {svc.title}", ""]
    extra_notes: list[str] = []

    price_parts: list[str] = []
    if svc.price:
        head, *tail = _sentences(svc.price)
        price_parts.append(f"Ціна {_lower_first(head)}")
        extra_notes.extend(tail)
    if svc.depends:
        head, *tail = _sentences(svc.depends)
        price_parts.append(_lower_first(head))
        extra_notes.extend(tail)
    if svc.term:
        head, *tail = _sentences(svc.term)
        # «1 день + рекомендований час» → «плюс»: знак «+» рядом с числом
        # читается как арифметика, а слово — как текст.
        head = re.sub(r"\s*\+\s*", " плюс ", head)
        price_parts.append(f"тривалість {_lower_first(head)}")
        extra_notes.extend(tail)
    if price_parts:
        lines.append("- " + sanitize_price_line(", ".join(price_parts)))

    if svc.included:
        head, *tail = _sentences(svc.included)
        # Метка берётся КЛИЕНТСКАЯ («Стандартний комплект включає»), а не наша
        # «Входить»: заменив её, мы бы сообщили лиду, что в цену входит то, что
        # клиент назвал стандартным комплектом, — а это разные утверждения.
        label = _upper_first(svc.included_label) if svc.included_label else "Входить"
        lines.append("- " + _render_line(f"{label}: {_lower_first(head)}"))
        extra_notes.extend(tail)
    if svc.excluded:
        head, *tail = _sentences(svc.excluded)
        lines.append("- " + _render_line(f"Не входить: {_lower_first(head)}"))
        extra_notes.extend(tail)
    for item in svc.sub_prices:
        lines.append("- " + sanitize_price_line(_upper_first(item)))
    for note in list(svc.notes) + extra_notes:
        for sentence in _sentences(note):
            rendered = _render_line(sentence)
            if rendered:
                lines.append("- " + rendered)
    lines.append("")
    return lines


# ────────────────────────────────────────────────────────────────────────────
# R9. Неконкретный срок обязан называть, КТО его назовёт
# ────────────────────────────────────────────────────────────────────────────

_VAGUE_TIME_MARKERS = (
    "орієнтовн", "зазвичай", "близько", "залежить", "рекомендован",
    "приблизн", "плюс ", "не менше", "не більше",
    # ДОБАВЛЕНО 17.08 по красному C14 на сгенерированном клиенте. Автоприёмка
    # нашла в knowledge строку «У складних випадках автомобіль МОЖЕ залишатися
    # ДО НАСТУПНОГО ДНЯ»: единица времени есть, числа нет, кто назовёт точное —
    # не сказано. То есть ровно то, что R9 и обязан ловить, но прежний список
    # маркеров этой формы не знал: неопределённость там выражена не наречием
    # («орієнтовно»), а МОДАЛЬНОСТЬЮ и условием.
    #
    # Ошибиться здесь дёшево в одну сторону: лишнее срабатывание всего лишь
    # дописывает строку «Точний час називає …», лида оно не глушит и ответа не
    # подавляет. Пропуск же оставляет лида с обещанием и без адресата.
    # Поэтому список расширен намеренно щедро.
    #
    # Сегмент с числом отбрасывается ДО этой проверки, поэтому «може бути
    # готово за 2 години» сюда не попадает.
    "може ", "можуть ", "можливо", "іноді", "подекуди", "у складних",
    "в складних", "буває",
)
_AUTHORITY_VERBS = ("називає", "підтверджує", "уточнює", "назве", "підтвердить")


def _needs_time_authority(line: str) -> bool:
    """Кусок строки обещает время, но не называет ни числа, ни того, кто его назовёт.

    Числовой guardrail здесь бессилен ПО КОНСТРУКЦИИ: числа нет, подавлять
    нечего. А лид уже получил обещание («плюс час на полімеризацію») и не
    знает, у кого спросить точное. Разбор идёт по сегментам строки, а не по
    строке целиком: «тривалість 1 день плюс рекомендований час на полімеризацію»
    формально содержит число, но обещание без числа сидит во втором сегменте.
    """
    for segment in re.split(r",| плюс ", line):
        if any(ch.isdigit() for ch in segment):
            continue
        if not _guardrails._ANY_TIME_UNIT.search(segment):
            continue
        if any(marker in segment.casefold() for marker in _VAGUE_TIME_MARKERS):
            return True
    return False


def _apply_time_authority(lines: list[str], owner_id: str) -> list[str]:
    out: list[str] = []
    for i, line in enumerate(lines):
        out.append(line)
        if not line.startswith("- ") or not _needs_time_authority(line):
            continue
        neighbourhood = " ".join(lines[i:i + 2]).casefold()
        if any(verb in neighbourhood for verb in _AUTHORITY_VERBS):
            continue
        out.append(f"- Точний час називає {_lower_first(owner_id)}")
    return out


# ────────────────────────────────────────────────────────────────────────────
# R4. Стоп-слова: полными фразами, а не корнями
# ────────────────────────────────────────────────────────────────────────────

def _expand_alternatives(phrase: str) -> list[str]:
    """«з менеджером / власником / людиною» → три фразы, предлог сохраняется.

    Наивный `split('/')` дал бы «власником» без предлога — то есть корень, а
    корень «власник» ловит лида, который сам себе власник авто. Разворачивается
    именно РУН альтернатив внутри фразы, голова и хвост остаются на месте.
    """
    m = re.search(r"(?<!\S)\S+(?:\s*/\s*\S+)+(?!\S)", phrase)
    if not m:
        return [phrase]
    head, tail = phrase[:m.start()], phrase[m.end():]
    return [f"{head}{alt.strip()}{tail}".strip()
            for alt in m.group().split("/") if alt.strip()]


def _root_stem(word: str) -> str:
    """Корень слова: последняя гласная снята, если без неё ещё осталось слово.

    Нужно для подстрочного матча: «скарга» НЕ является подстрокой «скаргу», и
    стоп-слово, взятое из брифа дословно, не сработало бы на самой частой форме
    («хочу залишити скаргУ»). «скарг» ловит все формы сразу.
    """
    low = word.casefold().strip(_QUOTE_CHARS + " .!?;:,")
    if len(low) > 4 and low[-1] in _KW_VOWELS:
        return low[:-1]
    return low


def _keyword_from_phrase(phrase: str) -> str:
    """Фраза клиента из Q34 → ключевое слово детерминированного слоя."""
    text = _strip_quotes(_unify_chars(phrase)).casefold()
    text = text.split(",")[0].strip()            # хвост после запятой — контекст
    text = text.strip(_QUOTE_CHARS + " .!?;:").strip()
    tokens = [t for t in text.split() if t]
    while tokens and tokens[0] in _KW_LEADING_FILLER:
        tokens.pop(0)
    if not tokens:
        return ""

    # ОДНО слово = голый корень. Отбросить его нельзя (это стоп-слово, которое
    # клиент назвал), оставить как есть — тоже (R4: «власник» эскалирует лида,
    # который сам себе власник авто). Значит дорастить: корень плюс предлог,
    # если слово из опасного класса, иначе усечённый корень — он однозначен вне
    # контекста сам по себе («скарг», «претенз», «компенсац»).
    if len(tokens) == 1:
        stem = _root_stem(tokens[0])
        prep = _KW_STEM_PREPOSITION.get(stem)
        return f"{prep} {stem}" if prep else stem

    # Обрезка до предложного хвоста: «поговорити з менеджером» → «з менеджером».
    # Ведущий глагол у лида меняется («хочу поговорити», «покличте», «з'єднайте»),
    # а предлог с объектом — нет. Обрезаем ТОЛЬКО когда объект не местоимение:
    # «у вас», «з вами» матчат половину входящих и сделали бы слой шумом.
    if len(tokens) > 2 and tokens[-2] in _KW_PREPOSITIONS and tokens[-1] not in _KW_PRONOUNS:
        tokens = tokens[-2:]
    return " ".join(tokens).strip()


def _escalation_keywords(stop_words_value: str, forbidden_context: str) -> tuple[list[str], list[dict]]:
    """Q34 → ключевые слова + список отброшенных с причиной.

    Отбрасывается слово, которое является подстрокой ЛЕГАЛЬНОГО текста
    knowledge/examples: такое слово эскалирует не лида, а нас самих на каждой
    второй реплике, и владелец через день попросит выключить эскалацию совсем.
    Причина отброса возвращается — молча похудевший словарь неотличим от
    сломанного парсера.
    """
    kept: list[str] = []
    dropped: list[dict] = []
    seen: set[str] = set()
    haystack = (forbidden_context or "").casefold()
    for raw_line in (stop_words_value or "").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        for variant in _expand_alternatives(line):
            keyword = _keyword_from_phrase(variant)
            if keyword in seen:
                continue
            if len(keyword) < _KW_MIN_LEN:
                # ЗАПИСЬ, а не тишина. «суд» без предлога короче порога, и
                # молча выпав, он исчезает вместе с целым классом эскалации;
                # владелец увидит в отчёте, что стоп-слово брифа не доехало.
                dropped.append({
                    "kind": "escalation_keyword", "value": variant.strip(),
                    "reason": f"после нормализации осталось {keyword!r} — короче "
                              f"{_KW_MIN_LEN} символов, вне контекста неоднозначно",
                })
                continue
            seen.add(keyword)
            if keyword in haystack:
                dropped.append({
                    "kind": "escalation_keyword",
                    "value": keyword,
                    "reason": "матчит легальный текст knowledge/examples — "
                              "слой эскалации срабатывал бы на своей же базе",
                })
                continue
            kept.append(keyword)
    return kept, dropped


def _escalation_section(keywords: list[str], owner_id: str) -> list[str]:
    """Секция ключевых слов. Две грабли R4 закрыты ЗДЕСЬ, а не в тесте.

    Первая: заголовок. `escalation.parse_escalation_keywords` узнаёт РОВНО три
    формулировки; любая другая = слой молча выключен. Заголовок берётся из
    сверенной с модулем константы.

    Вторая: комментарий. Парсер не понимает HTML-комментариев и забирает ЛЮБУЮ
    строку `- ` внутри секции. Первая ручная версия файла так и сделала —
    четыре предложения пояснения уехали в словарь. Поэтому ни одна строка
    комментария ниже НЕ начинается с «- », и это проверяется после сборки.
    """
    lines = [
        f"## {ESCALATION_HEADING_UK}",
        "",
        "<!-- Детермінований шар: одне слово або фразу на рядок, матч",
        "     регістронезалежний по підрядку у ВХІДНОМУ повідомленні ліда.",
        "     Працює, НАВІТЬ ЯКЩО класифікатор ліг — це його єдина причина",
        "     існувати.",
        "",
        "     ЗАГОЛОВОК ЦІЄЇ СЕКЦІЇ розбирає escalation.parse_escalation_keywords",
        f"     і впізнає РІВНО «{ESCALATION_HEADING_UK.casefold()}»; будь-яке інше",
        "     формулювання = шар мовчки вимкнений.",
        "",
        "     РЯДКИ ЦЬОГО КОМЕНТАРЯ НЕ ПОЧИНАЮТЬСЯ З дефіса і пробілу: парсер не",
        "     розуміє HTML-коментарів і забрав би їх у словник як ключові слова.",
        "",
        "     ЧОГО ТУТ НЕМАЄ І ЧОМУ. Контекстні наміри (прохання про знижку,",
        "     торг, готовність платити) судить класифікатор, який бачить усю",
        "     переписку. Голі корені сюди не потрапляють: корінь «власник» ловить",
        f"     ліда, який сам собі власник авто, і кличе {_lower_first(owner_id)}",
        "     на порожньому місці. -->",
    ]
    lines.extend(f"- {kw}" for kw in keywords)
    return lines


# ────────────────────────────────────────────────────────────────────────────
# R7/R8. Обязательные факты и разделы — ТОЛЬКО из vocabulary.py
# ────────────────────────────────────────────────────────────────────────────

_WEEKDAY_WORDS = (
    "понеділ", "вівтор", "серед", "четвер", "п'ятниц", "субот", "неділ",
    "вихідн", "без вихідних", "щодня", "щоденно",
)


def _fact_is_covered(fact_id: str, value: str | None) -> bool:
    """Закрыт ли обязательный факт данными брифа.

    Наличия ГОДНОГО поля мало, и это не педантизм: `q12_hours` у Ярины пришёл
    как «9-18» — часы есть, ВЫХОДНЫХ нет. Считать факт «вихідні дні» закрытым
    потому, что поле-источник заполнено, значит выдать заглушке зелёный свет и
    оставить бота выдумывать выходные. Спека R7 называет этот случай прямо.
    """
    if not value:
        return False
    low = value.casefold()
    if fact_id == "days_off":
        return any(word in low for word in _WEEKDAY_WORDS)
    if fact_id == "links":
        return bool(_URL_RE.search(value))
    return True


def _required_facts(brief: dict, owner_nominative: str) -> tuple[list[str], list[dict]]:
    """Заглушки для «Чого ми НЕ знаємо» + машиночитаемый список для отчёта.

    Формула заглушки ОДНА (`vocabulary.STUB_TEMPLATE_UK`) и на языке клиента:
    `checks` ищет в knowledge именно её, и вторая формулировка сделала бы C10
    слепой. Молча пропустить факт нельзя: либо данные, либо заглушка ПЛЮС
    строка отчёта.
    """
    stub_lines: list[str] = []
    stubs: list[dict] = []
    for fact in REQUIRED_FACTS:
        value = _value(brief, fact.source) if fact.source else None
        if _fact_is_covered(fact.id, value):
            continue
        line = STUB_TEMPLATE_UK.format(title=fact.title_uk, owner=owner_nominative)
        stub_lines.append(line)
        stubs.append({"fact_id": fact.id, "title": fact.title_uk, "stub_line": line})
    return stub_lines, stubs


def _assert_required_sections(knowledge: str) -> None:
    """R8: раздел обязан существовать И иметь хотя бы одну содержательную строку.

    Пустой заголовок считается отсутствующим — иначе «раздел есть» будет
    значить «строка с решёткой напечатана», а бот на вопрос «як записатися»
    будет молчать при формально зелёной проверке.
    """
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in knowledge.splitlines():
        if line.startswith("# "):
            current = line[2:].strip()
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line)
    for title in REQUIRED_SECTIONS_UK:
        if title not in blocks:
            raise RenderError(
                f"knowledge.md: нет обязательного раздела «{title}» "
                f"(vocabulary.REQUIRED_SECTIONS_UK)")
        if not any(ln.strip() and not ln.startswith("#") for ln in blocks[title]):
            raise RenderError(
                f"knowledge.md: раздел «{title}» пуст — пустой заголовок "
                f"считается отсутствующим (R8)")


# ────────────────────────────────────────────────────────────────────────────
# Сборка knowledge.md
# ────────────────────────────────────────────────────────────────────────────

_LIMIT_MARKERS = (
    "але не ", "не захища", "не прибере", "не прибирає", "не вирішує",
    "не гарантує", "остаточно", "попередньо", "не броня", "не повністю",
)
_WORK_HOURS_RE = re.compile(
    r"(\d{1,2})(?::\d{2})?\s*(?:[-–—]|\bдо\b|\bпо\b|\bto\b)\s*(\d{1,2})(?::\d{2})?")
# «5. Скільки часу займає хімчистка?» — номер пункта, приехавший из соседнего
# абзаца поля-простыни. В knowledge он не нужен и ломает счёт чисел.
_NUMBERED_PREFIX = re.compile(r"^\s*\d{1,2}\s*[.)]\s*")


def _limits_lines(services: list[_Service], top_questions: str | None) -> list[str]:
    """Раздел «Межі можливого»: собирается из оговорок клиента, а не сочиняется.

    Источник — примечания прайса и ответы на частые вопросы, то есть места,
    где клиент САМ написал, чего услуга не делает. Сочинять границы ниши за
    клиента генератор не имеет права: это ровно тот случай, где ошибка
    выглядит как знание.
    """
    found: list[str] = []
    pool: list[str] = []
    for svc in services:
        pool.extend(svc.notes)
        if svc.excluded:
            pool.extend(_sentences(svc.excluded)[1:])
    if top_questions:
        # ПО АБЗАЦАМ, а не по всему полю: соседние абзацы Q46 — это разные
        # ответы, и склейка склеивает конец одного с номером следующего
        # («…після огляду кузова.» + «5. Скільки часу…» → «…кузова, » 5»).
        pool.extend(_paragraphs(top_questions))
    for chunk in pool:
        for sentence in _sentences(_strip_quotes(chunk)):
            low = sentence.casefold()
            if not any(marker in low for marker in _LIMIT_MARKERS):
                continue
            clean = _strip_quotes(_NUMBERED_PREFIX.sub("", sentence))
            rendered = _render_line(clean)
            if rendered and rendered not in found:
                found.append(rendered)
    return found


def _build_knowledge(brief: dict, *, services: list[_Service], owner_id: str,
                     owner_ref: str, currency: str,
                     stub_lines: list[str]) -> tuple[str, list[dict]]:
    lines: list[str] = []
    section_stubs: list[dict] = []

    def section(title: str, body: list[str]) -> None:
        body = [ln for ln in body if ln is not None]
        while body and not body[-1].strip():
            body.pop()
        lines.append(f"# {title}")
        lines.append("")
        lines.extend(body)
        lines.append("")

    def fallback(title: str) -> list[str]:
        """Обязательный раздел без источника в брифе — заглушка, а не пустота.

        R8 запрещает пустой раздел, а R7 запрещает молчание. Здесь эти два
        правила встречаются: данных нет, значит раздел получает ту же формулу
        заглушки и ту же строку отчёта, что и обязательный факт.
        """
        line = STUB_TEMPLATE_UK.format(title=title, owner=owner_id)
        section_stubs.append({"fact_id": f"section:{title}", "title": title,
                              "stub_line": line})
        return [f"- {line}"]

    # ── Про студію ──────────────────────────────────────────────────────────
    about: list[str] = []
    company = _value(brief, "q1_company_name")
    industry = _value(brief, "q2_industry")
    offer = _value(brief, "q3_offer")
    if company and industry:
        about.append(_render_line(f"{company} — {_lower_first(industry.rstrip('.'))}"))
    elif company:
        about.append(_render_line(company))
    if offer:
        about.extend(["", offer])
    services_list = _value(brief, "q21_services")
    if services_list:
        names = [_lower_first(item.strip().rstrip(".;")) for item in services_list.split("\n")
                 if item.strip()]
        if names:
            about.extend(["", _render_line("Робимо: " + ", ".join(names))])
    # Город (Q6). Числовой хвост часового пояса («Київ, Україна +2») лиду не
    # говорится: это наша инфраструктурная деталь, а не адрес — но САМ ГОРОД
    # потерять нельзя, это первое, что спрашивает лид. Падежа здесь нет
    # намеренно («Місто: Київ», а не «працюємо в Києві»): склонять не будем.
    city = _strip_timezone_offset(_value(brief, "q6_timezone_city"))
    if city:
        about.extend(["", _render_line(f"Місто: {city}")])
    hours = _value(brief, "q12_hours")
    work_hours = _parse_work_hours(hours)
    if work_hours:
        about.extend(["", _render_line(
            f"Робочий час — з {work_hours[0]}:00 до {work_hours[1]}:00")])
    # Ссылки (Q4) — обязательный факт R7. Если поле годное, ссылка ОБЯЗАНА
    # доехать в knowledge: иначе факт считается обеспеченным (заглушки нет), а
    # сказать ссылку боту нечем — он промолчит на вопрос, ответ на который
    # клиент дал. Строка ставится отдельной: точки внутри домена не имеют права
    # оказаться в одном ряду с ценой.
    links = _value(brief, "q4_links")
    if links and _URL_RE.search(links):
        about.extend(["", _render_line(f"Сайт і соцмережі: {_flat(links)}")])
    section(REQUIRED_SECTIONS_UK[0], about or fallback(REQUIRED_SECTIONS_UK[0]))

    # ── Послуги та ціни ─────────────────────────────────────────────────────
    prices: list[str] = []
    if services:
        # Преамбула без чисел: она объясняет ПРИРОДУ цифр (вилка, а не сумма) и
        # сразу называет, кто подтверждает точную дату — R9 на уровне раздела.
        prices.extend([
            _render_line(f"Усі ціни — у {currency}"),
            "",
            _render_line("Це ВИЛКИ: точна сума в межах вилки залежить від "
                         "конкретного випадку, стану та обсягу робіт"),
            "",
            _render_line(f"Терміни — орієнтовні, точну дату і час підтверджує "
                         f"{_lower_first(owner_id)}"),
            "",
        ])
        for svc in services:
            prices.extend(_apply_time_authority(_service_lines(svc), owner_id))
    # Состав услуги отдельным полем (Q23) — у Ярины он пришёл мусором
    # («заполнил разом»), но поле есть в форме и обязано доезжать, когда
    # заполнено: «не входить» лид должен услышать ДО того, как приехал.
    included = _value(brief, "q23_included_excluded")
    if included:
        prices.extend(["## Що входить і що не входить", ""])
        prices.extend(_bullet_block(_list_items(included), lower=False))
        prices.append("")
    # Сроки отдельным полем (Q25). Здесь же живёт вход R9: фраза с единицей
    # времени и БЕЗ числа («потрібен рекомендований час на полімеризацію»)
    # приезжает обычно именно сюда, и потерять её значит остаться правилом без
    # входа — правило исправно, а обещание висит в воздухе.
    deadlines = _value(brief, "q25_deadlines")
    if deadlines:
        block = ["## Терміни виконання", ""]
        block.extend(_bullet_block(_list_items(deadlines), lower=False))
        prices.extend(_apply_time_authority(block, owner_id))
        prices.append("")
    section(REQUIRED_SECTIONS_UK[1], prices or fallback(REQUIRED_SECTIONS_UK[1]))

    # ── Від чого залежить фінальна ціна ─────────────────────────────────────
    factors = _value(brief, "q24_price_factors")
    factor_lines = _bullet_block(_list_items(factors)) if factors else []
    section(REQUIRED_SECTIONS_UK[2], factor_lines or fallback(REQUIRED_SECTIONS_UK[2]))

    # ── Акція (необязательный раздел: её может законно не быть) ─────────────
    promo = _value(brief, "q28_promo")
    if promo:
        section(PROMO_SECTION_UK, _bullet_block(_list_items(promo), lower=False))

    # ── Оплата та передоплата ───────────────────────────────────────────────
    payment: list[str] = []
    methods = _value(brief, "q30_payment_methods")
    if methods:
        payment.append(_render_line(
            "Способи оплати: " + ", ".join(_lower_first(p) for p in _split_choice(methods))))
    prepay = _value(brief, "q29_prepayment")
    if prepay:
        if payment:
            payment.append("")
        payment.append("Правило передоплати:")
        payment.append("")
        payment.extend(_bullet_block(_list_items(prepay), lower=False))
    section(REQUIRED_SECTIONS_UK[3], payment or fallback(REQUIRED_SECTIONS_UK[3]))

    # ── Гарантії та якість роботи ───────────────────────────────────────────
    guarantees = _value(brief, "q45_guarantees")
    guarantee_lines = [_render_line(s) for s in _sentences(_strip_quotes(guarantees))] \
        if guarantees else []
    section(REQUIRED_SECTIONS_UK[4],
            guarantee_lines or fallback(REQUIRED_SECTIONS_UK[4]))

    # ── Межі можливого ──────────────────────────────────────────────────────
    limits = _limits_lines(services, _value(brief, "q46_top_questions"))
    section(REQUIRED_SECTIONS_UK[5],
            [f"- {ln}" for ln in limits] or fallback(REQUIRED_SECTIONS_UK[5]))

    # ── Чого ми не робимо ───────────────────────────────────────────────────
    #
    # Q38 — поле ДВОЙНОГО НАЗНАЧЕНИЯ (решение владельца 17.08, зафиксировано в
    # vocabulary.DUAL_PURPOSE_FIELDS). Полный текст едет в playbook, а этот
    # раздел собирается ИЗ НЕГО. Разводить содержимое ячейки автоматом мы не
    # будем: отличить «кузовного ремонту не робимо» (публично) от «не беремо
    # клієнтів, які вимагають недосяжного» (внутренний критерий отказа) —
    # смысловое суждение, а пайплайн смысловых суждений не принимает.
    anti_icp = _value(brief, "q38_anti_icp")
    if anti_icp and "q38_anti_icp" not in DUAL_PURPOSE_FIELDS:      # pragma: no cover
        raise RenderError(
            "vocabulary.DUAL_PURPOSE_FIELDS больше не содержит q38_anti_icp, "
            "а раздел «Чого ми не робимо» по-прежнему собирается из него — "
            "C9 покраснеет на КАЖДОМ клиенте")
    not_doing = _bullet_block(_list_items(anti_icp)) if anti_icp else []
    section(REQUIRED_SECTIONS_UK[6], not_doing or fallback(REQUIRED_SECTIONS_UK[6]))

    # ── Чим ми відрізняємось (не обязательный, но почти всегда есть) ────────
    advantages = _value(brief, "q47_advantages")
    if advantages:
        section("Чим ми відрізняємось", _bullet_block(_list_items(advantages)))

    # ── Як записатися ───────────────────────────────────────────────────────
    #
    # Раздел собирается ШАБЛОНОМ, а не переносом Q39/Q40/Q41. Причина не
    # стилистическая: у этих трёх полей `target: playbook`, и их фрагменты в
    # knowledge — ровно то, что ловит C9. Плюс §1.3: knowledge — то, из чего
    # бот ГОВОРИТ лиду, а «збери марку, модель і рік перед передачею» —
    # инструкция боту, которая однажды будет зачитана вслух.
    #
    # Все формулировки ниже держат `owner_id` в ИМЕНИТЕЛЬНОМ падеже
    # («називає X», «підтверджує X», «рахує X») намеренно. Склонять программно
    # мы не будем (спека §5.4), а конструкция «передаємо {X}» требует дательного
    # и на любом клиенте даст сломанную фразу в файле, который читает лид.
    # Правило простое: генератор пишет только те обороты, которые работают с
    # именительным.
    booking = [
        _render_line("Клієнт називає, яка послуга або яка задача цікавить"),
        _render_line("Якщо для оцінки потрібні фото — надсилає фото"),
        _render_line("Ми даємо орієнтир по вартості в межах вилки та по термінах"),
        _render_line(f"Якщо потрібен точний прорахунок — збираємо дані, "
                     f"а точну суму рахує {_lower_first(owner_id)}"),
        _render_line(f"Дату і час підтверджує {_lower_first(owner_id)}"),
    ]
    if prepay:
        tail = ""
        if (_value(brief, "q31_can_send_payment_details") or "").casefold().startswith("ні"):
            tail = f", реквізити надсилає {_lower_first(owner_id)}"
        booking.append(_render_line(
            f"Якщо робота потребує передоплати — умови проговорюємо до запису{tail}"))
    booking_lines = [f"- {ln}" for ln in booking]
    reply_time = _value(brief, "q35_reply_time")
    if reply_time:
        booking_lines.extend(["", _render_line(
            f"{_upper_first(owner_id)} відповідає {_lower_first(reply_time)}")])
    section(REQUIRED_SECTIONS_UK[7], booking_lines)

    # ── Чого ми НЕ знаємо і не називаємо ────────────────────────────────────
    unknown = [
        _render_line("Цих даних у нас немає — вигадувати їх не можна"),
        "",
    ]
    unknown.extend(f"- {_render_line(line)}" for line in stub_lines)
    section(UNKNOWN_SECTION_UK, unknown if stub_lines else fallback(UNKNOWN_SECTION_UK))

    return "\n".join(lines).rstrip() + "\n", section_stubs


_TZ_OFFSET_RE = re.compile(r"\s*(?:utc|gmt)?\s*[+−-]\s*\d{1,2}(?::\d{2})?\s*$", re.I)


def _strip_timezone_offset(value: str | None) -> str:
    """«Київ, Україна +2» → «Київ, Україна».

    Часовой пояс — наша инфраструктурная деталь, лиду он не говорится (решение
    владельца, зафиксированное в эталоне). Снимается ТОЛЬКО хвост-смещение;
    город остаётся, потому что город лид спрашивает первым.
    """
    if not value:
        return ""
    return _TZ_OFFSET_RE.sub("", _flat(value)).strip(" ,;")


def _parse_work_hours(value: str | None) -> tuple[int, int] | None:
    """«9-18» и «з 9:00 до 18:00» — одна и та же пара часов.

    Словесный разделитель распознаётся наравне с тире: клиент пишет часы как
    говорит, и «до» встречается чаще дефиса. Не распознав их, генератор терял
    рабочие часы молча — поле Q12 при этом считалось заполненным.
    """
    if not value:
        return None
    m = _WORK_HOURS_RE.search(value)
    if not m:
        return None
    start, end = int(m.group(1)), int(m.group(2))
    if 0 <= start <= 23 and 0 <= end <= 24:
        return start, end
    return None


# ────────────────────────────────────────────────────────────────────────────
# R3. persona.md — первая строка проза
# ────────────────────────────────────────────────────────────────────────────

def _build_persona(brief: dict, *, persona_name: str, persona_age: int,
                   owner_id: str, company: str, language_label: str) -> str:
    """persona.md. Первая строка — ПРОЗА, и это физическое требование.

    `disclosure.honest_prefix`/`honest_disclosure` берут ПЕРВУЮ строку персоны
    и отдают её лиду на прямой вопрос «ты бот?» (`run.py:_persona_first_line`).
    Заголовок `#` уехал бы в чат как есть. Поэтому файл начинается с фразы
    «Мене звати …», а не с заголовка, и это проверяется после сборки.
    """
    first = (f"Мене звати {persona_name}, мені {persona_age}. "
             f"Я — перший контакт клієнта з {company}.")
    parts: list[str] = [first, ""]
    parts.append(
        f"Рішень за власника я не приймаю: найскладніші питання вирішує "
        f"{_lower_first(owner_id)}.")
    parts.append("")

    tone = _value(brief, "q18_tone")
    if tone:
        parts.append(f"Тон спілкування: {_lower_first(tone)}. "
                     f"Пишу живою мовою, без канцеляриту й роботизованих формулювань.")
        parts.append("")
    parts.append("Відповідаю КОРОТКО і по суті. Полотна тексту не пишу — "
                 "розгорнуте пояснення даю тільки тоді, коли клієнт сам про нього просить.")
    parts.append("")
    emoji = _value(brief, "q19_emoji")
    if emoji:
        if emoji.casefold().startswith("ні"):
            parts.append("Емодзі не використовую.")
        else:
            parts.append("Емодзі використовую доречно й помірно: не більше одного "
                         "на повідомлення і не в кожному. У складній чи конфліктній "
                         "розмові — жодного.")
        parts.append("")
    parts.append(f"Мова відповіді — {_lower_first(language_label)}.")
    parts.append("")
    parts.append("Спершу розбираюся в задачі клієнта, а вже потім називаю послугу "
                 "й ціну — не навпаки. Не продаю дорожче, ніж потрібно. Не тисну і "
                 "не підганяю штучними обмеженнями.")
    return "\n".join(parts).rstrip() + "\n"


def _assert_persona_ok(persona: str, persona_name: str) -> None:
    lines = [ln for ln in persona.splitlines() if ln.strip()]
    if not lines:
        raise RenderError("persona.md: файл пуст — disclosure отдаст лиду пустоту")
    first = lines[0].strip()
    if first.startswith("#"):
        raise RenderError(
            f"persona.md:1 начинается с «#» — этот заголовок уехал бы лиду "
            f"в ответ на «ты бот?» (disclosure берёт первую строку): {first!r}")
    if persona_name.casefold() not in first.casefold():
        raise RenderError(
            f"persona.md:1 не содержит имени персоны «{persona_name}»: {first!r}")


# ────────────────────────────────────────────────────────────────────────────
# playbook.md
# ────────────────────────────────────────────────────────────────────────────

def _build_playbook(brief: dict, *, persona_name: str, owner_id: str, owner_ref: str,
                    company: str, keywords: list[str]) -> str:
    lines: list[str] = []

    def section(title: str, body: list[str], *, level: int = 1) -> None:
        body = [ln for ln in body if ln is not None]
        while body and not body[-1].strip():
            body.pop()
        if not body:
            return
        lines.append(f"{'#' * level} {title}")
        lines.append("")
        lines.extend(body)
        lines.append("")

    speaks_as = _value(brief, "q13_speaks_as")
    role_line = (f"{persona_name} — перший контакт клієнта з {company}. "
                 f"Веде діалог, кваліфікує задачу, дає орієнтири по цінах і термінах "
                 f"З KNOWLEDGE, ескалює ТІЛЬКИ те, що зобов'язана ескалювати "
                 f"(див. «Ескалація»). Найскладніші питання вирішує {owner_id}.")
    role = [role_line, ""]
    if speaks_as:
        role.append(f"Пише від імені: {_lower_first(speaks_as)}.")
        role.append("")
    role.append(f"За замовчуванням {persona_name} САМА відповідає на питання з бази "
                f"знань і НЕ передає діалог без потреби — передача це виняток, а не "
                f"звичайна відповідь.")
    section("Роль", role)

    section("Воронка", [
        "Стадії — внутрішня орієнтація, клієнту їх не називати:",
        "",
        "- new → знайомство: зрозуміти, з чим людина прийшла",
        "- qualifying → кваліфікація: зібрати те, без чого прорахунок неможливий",
        "- hot → цінність плюс орієнтир: вилка і термін З KNOWLEDGE, наступний крок",
        f"- escalated → картка власнику, ТІЛЬКИ на тригери нижче",
        "- closed / dead → клієнт записаний / контакт охолов",
    ])

    can_quote = _value(brief, "q26_can_quote_price")
    autonomous = _value(brief, "q32_autonomous_actions")
    self_answer: list[str] = []
    if can_quote:
        self_answer.append(f"Називати ціну без участі власника: {_lower_first(can_quote)}.")
        self_answer.append("")
    if autonomous:
        self_answer.append("Робить сама, не питаючи власника:")
        self_answer.append("")
        self_answer.extend(_bullet_block(_split_choice(autonomous)))
        self_answer.append("")
    self_answer.append("Конкретні цифри — ЛИШЕ з knowledge, тут вони не дублюються: "
                       "друга копія прайсу розійдеться з першою, і бот почне називати "
                       "ціну, якої немає в базі.")
    self_answer.append("")
    self_answer.append("Вилка НІКОЛИ не йде голою: на будь-яке цінове питання "
                       "відповідь = вилка плюс одне уточнювальне питання.")
    # Политика скидок (Q27) — правило ПОВЕДЕНИЯ, и без него персона либо
    # молчит про действующую акцию, либо начинает торговаться. Поле было
    # пропущено первой версией и найдено обходом «ответ доехал или записан»:
    # ровно тот случай, ради которого обход и стоит.
    discounts = _value(brief, "q27_discounts")
    if discounts:
        self_answer.extend(["", _render_line(f"Знижки: {_lower_first(discounts)}")])
    section(f"Ціни та терміни — {persona_name} відповідає САМА", self_answer)

    gather = _value(brief, "q39_required_questions")
    if gather:
        items = _drop_question_echo(
            _list_items(gather), (_field(brief, "q39_required_questions") or {}).get("question"))
        section("Що зібрати ПЕРЕД ескалацією", _bullet_block(items))

    exact_price = _value(brief, "q40_info_for_exact_price")
    if exact_price:
        items = _drop_question_echo(
            _list_items(exact_price),
            (_field(brief, "q40_info_for_exact_price") or {}).get("question"))
        section("Що потрібно, щоб назвати точну ціну", _bullet_block(items))

    next_step = _value(brief, "q41_next_step")
    if next_step:
        section("Наступний крок — що пропонувати",
                _bullet_block(_list_items(next_step), lower=False))

    voice = _value(brief, "q56_anything_else")
    if voice:
        section("Голос продавця, не довідки",
                _bullet_block([_upper_first(i) for i in _list_items(voice)], lower=False))

    # ── ICP: ТОЛЬКО playbook, и это единственное место с ослабленным R2 ─────
    #
    # §1.3: строка «наш ідеальний клієнт — той, хто готовий інвестувати» в
    # knowledge однажды будет зачитана лиду дословно. В playbook она живёт как
    # внутренняя разметка. Сторож — C9.
    #
    # ОТСТУПЛЕНИЕ от буквы R2 («все числа из playbook — из прайса»), сделанное
    # осознанно: пороги комфортного бюджета клиента («від 25 000 грн») в прайсе
    # НЕ существуют и существовать не могут — это не цена услуги. R2 защищает
    # то, что бот ПРОИЗНОСИТ; этот блок помечен как внутренний и вслух не
    # звучит, а если модель его всё-таки процитирует, число подавит рантайм
    # (strict_knowledge + unbacked_claim). Удалять пороги владельца ради буквы
    # правила значит потерять данные и не купить ничего.
    icp = _value(brief, "q37_icp")
    if icp:
        section(f"Ідеальний клієнт ({INTERNAL_MARKER_UK})", [
            *_bullet_block(_list_items(icp)),
            "",
            "Ці цифри — ОРІЄНТИР ДЛЯ ПЕРСОНИ, а не репліка клієнту. Вголос вони не "
            "звучать НІКОЛИ: сказати людині «наш клієнт починається від такої суми» "
            "— це «ви нам не підходите» її ж словами.",
        ])

    anti_icp = _value(brief, "q38_anti_icp")
    if anti_icp:
        section(f"Кого не беремо в роботу ({INTERNAL_MARKER_UK})", [
            *_bullet_block(_list_items(anti_icp)),
            "",
            "Публічна частина цього ж списку живе в knowledge, розділ "
            f"«{REQUIRED_SECTIONS_UK[6]}»: поле подвійного призначення, розводить "
            "його людина при вичитці, а не пайплайн.",
        ])

    escalate = _value(brief, "q33_must_escalate")
    triggers: list[str] = []
    if escalate:
        triggers.append("Передача — виняток. Перед карткою завжди одна відповідь по "
                        "суті плюс м'який міст. Картка йде власнику, КОЛИ клієнт:")
        triggers.append("")
        triggers.extend(_bullet_block(_split_choice(escalate)))
        triggers.append("")
    triggers.append(f"НЕ ескалація ({persona_name} відповідає САМА): загальне питання про "
                    f"ціни, вилки, «від чого залежить», типові терміни, склад послуги, "
                    f"способи оплати, правило передоплати, чинна акція.")
    triggers.append("")
    triggers.append("Питання про ЦІНУ або стандартний термін саме по собі НЕ привід "
                    "для передачі.")
    section(f"Ескалація — ТІЛЬКИ ці тригери (далі відповідає {owner_id})", triggers)

    section("Формула картки", [
        f"Перед карткою — одна відповідь по суті плюс м'який міст: орієнтир з "
        f"knowledge, далі «точну суму порахує {_lower_first(owner_id)} — передаю деталі».",
        "",
        "Картка НІКОЛИ не згадує реквізити й «як оплатити»: платіжна лексика "
        "з'являється ТІЛЬКИ коли клієнт САМ явно готовий платити.",
        "",
        "Обіцянку участі людини писати через ДІЄСЛОВО зв'язку — «зв'яжу вас з "
        f"{owner_ref}», «напише вам», «передзвонить». Саме дієслово ловить сторож "
        "недоставлених обіцянок; сухе «передам» він не бачить, і обіцянка поїде "
        "ліду без картки власнику. Це не стилістика, це запобіжник.",
        "",
        # ЗАМЕЧАНИЕ ВЛАДЕЛЬЦУ ЗДЕСЬ НЕ МЕСТУ, и это не вкусовщина.
        #
        # Здесь стояла ⚠️-врезка про орудный падеж `owner_ref`. Она адресована
        # человеку, а `playbook.md` уезжает в промпт ЦЕЛИКОМ — то есть модель
        # читала наше внутреннее замечание как правило поведения.
        #
        # И второе, злее: во врезке стояла ссылка «спека §5», а R2 проверяет
        # ВСЕ числа в playbook. Цифра «5» из номера параграфа обязана быть в
        # прайсе клиента — иначе генерация падает. На брифе Ярины это прошло
        # СЛУЧАЙНО: там есть «5 000 грн», и «5» оказалось разрешённым. На любом
        # прайсе без пятёрки сборка клиента падала бы из-за номера параграфа в
        # нашем же комментарии. Зелёное здесь означало не «правильно», а
        # «повезло с прайсом».
        #
        # Само замечание не потеряно: оно уже записано в `defaults` у
        # `owner_ref` вместе с причиной и цитатой брифа — там его место, туда
        # смотрит владелец, и в промпт оно не едет.
    ], level=2)

    section("Ініціатива листування — тільки за клієнтом", [
        f"{persona_name} технічно НЕ вміє писати першою: відповідь запускається ЛИШЕ "
        f"вхідним повідомленням клієнта. Будь-яка обіцянка «я напишу вам завтра», "
        f"«нагадаю», «наберу» гарантовано закінчиться мовчанням — для клієнта це ігнор.",
        "",
        "- НЕ обіцяти написати першою, нагадати чи повернутись з власної ініціативи",
        "- Якщо клієнт просить «напишіть мені завтра» — м'яко перевернути ініціативу",
        f"- Єдиний, хто може написати першим, — {owner_id}, і тільки в межах формули картки",
    ])

    # ── Червоні лінії ───────────────────────────────────────────────────────
    #
    # ЗАПРЕЩЁННЫЕ ФОРМУЛИРОВКИ ЗДЕСЬ НЕ ЦИТИРУЮТСЯ, и это не забывчивость.
    # `forbidden_mention` матчит ПОДСТРОКОЙ и без нормализации: список запретов,
    # выписанный в playbook дословно, делает файл самоотравленным — а playbook
    # едет в промпт, и модель охотно повторяет то, что видит. Ровно этим болен
    # ручной эталон (`chatter/clients/yarina/playbook.md`: «Заборонені
    # формулювання: …» даёт forbidden_mention → «100% гарант»). Запреты живут
    # в settings.yaml как данные и в Q48 как поведение.
    #
    # Секция помечена ВНУТРЕННЕЙ ещё по одной причине, найденной прогоном на
    # живом брифе: красная линия «не гарантувати 100% видалення подряпин»
    # содержит число 100, которого в прайсе нет и быть не может. R2 считает
    # числа, которые боту РАЗРЕШЕНО произнести; ЗАПРЕТ произнести — не
    # обещание, и ронять генерацию на нём значит требовать от клиента убрать
    # из запрета сам запрещаемый оборот. Числа этой секции вслух не звучат, а
    # если модель их всё-таки процитирует, рантайм подавит (strict_knowledge).
    never = _value(brief, "q48_never_promise")
    if never:
        section(f"Червоні лінії ({INTERNAL_MARKER_UK})", [
            "Ніколи, за жодних обставин:",
            "",
            *_bullet_block(_list_items(never)),
            "",
            "Повний перелік заборонених ФОРМУЛЮВАНЬ живе в settings.yaml "
            "(forbidden_terms) і сюди НЕ переписується: playbook їде в промпт, і "
            "виписаний тут запрет модель повторює замість того, щоб уникати.",
        ])

    taboo = _value(brief, "q49_taboo_topics")
    competitors = _value(brief, "q51_can_discuss_competitors")
    if taboo or competitors:
        body: list[str] = []
        if taboo:
            body.extend(_bullet_block(_list_items(taboo)))
        if competitors:
            body.extend(["", _render_line(
                f"Конкурентів обговорювати: {_lower_first(competitors)}. Порівняння по "
                f"обсягу робіт — так, оцінки їхньої якості чи матеріалів — ні.")])
        section("Теми, яких не торкаємось", body)

    lines.extend(_escalation_section(keywords, owner_id))
    return "\n".join(lines).rstrip() + "\n"


def _split_choice(value: str) -> list[str]:
    """Ответ-выбор («A, B, C») → пункты.

    Разделитель — запятая ПЕРЕД заглавной буквой, а не просто запятая. Google
    Forms склеивает отмеченные опции через «, », и каждая опция начинается с
    заглавной; внутри опции запятые тоже есть («Пояснювати, що входить у
    послугу», «Усе, що стосується вже поточних замовлень»). Наивный split по
    запятой разрезал бы опцию пополам и выдал бы боту пункт «що входить у
    послугу» как отдельное разрешённое действие.
    """
    parts = [p.strip().rstrip(".;")
             for p in re.split(r",\s+(?=[А-ЯЄІЇҐA-Z«])", value or "")]
    return [p for p in parts if p]


# ────────────────────────────────────────────────────────────────────────────
# R5. examples.yaml из Q20/Q46 (+ поля возражений)
# ────────────────────────────────────────────────────────────────────────────

_CLIENT_MARKERS = ("клієнт:", "клиент:", "client:")
_REPLY_MARKERS = ("відповідь:", "ответ:", "reply:")


def _pairs_from_dialog_field(value: str) -> list[tuple[str, str]]:
    """Q20: «Клієнт: …» / «Відповідь: …».

    Реплика без явного маркера «Клієнт:» НЕ берётся: у Ярины там сидит
    «Приклад 2 — клієнт питає про хімчистку», то есть ОПИСАНИЕ ситуации, а не
    сообщение лида. Подставить его в поле `client` значит научить персону
    отвечать на фразу, которую живой человек не напишет никогда.
    """
    pairs: list[tuple[str, str]] = []
    pending_client = ""
    lines = [ln.strip() for ln in _unify_chars(value or "").split("\n")]
    i = 0
    while i < len(lines):
        line = lines[i]
        low = line.casefold()
        if any(low.startswith(m) for m in _CLIENT_MARKERS):
            pending_client = line.split(":", 1)[1].strip()
        elif any(low.startswith(m) for m in _REPLY_MARKERS):
            body = line.split(":", 1)[1].strip()
            j = i + 1
            while not body and j < len(lines):
                body = lines[j].strip()
                j += 1
            i = j - 1
            if pending_client and body:
                pairs.append((pending_client, _strip_quotes(body)))
            pending_client = ""
        i += 1
    return pairs


_NUMBERED_QUESTION = re.compile(r"^\s*\d{1,2}\s*[.)]\s*(\S.*\?)\s*$")


def _pairs_from_faq_field(value: str) -> list[tuple[str, str]]:
    """Q46: «N. Вопрос?» + следующий абзац в «кавычках» — ответ.

    Абзац БЕЗ кавычек между вопросом и следующим номером — комментарий клиента
    самому себе («Це навмисно залишаємо так…»), и в голос персоны он не едет.
    """
    pairs: list[tuple[str, str]] = []
    question = ""
    for paragraph in _paragraphs(_unify_chars(value or "")):
        m = _NUMBERED_QUESTION.match(paragraph.split("\n")[0])
        if m:
            question = m.group(1).strip()
            rest = "\n".join(paragraph.split("\n")[1:]).strip()
            if rest.startswith("«"):
                pairs.append((question, _strip_quotes(rest)))
                question = ""
            continue
        if question and paragraph.startswith("«"):
            pairs.append((question, _strip_quotes(paragraph)))
            question = ""
    return pairs


# Реплика лида для полей-возражений. Это НЕ данные клиента, а ярлыки САМОЙ
# формы: вопросы Q42/Q44 дословно называют возражение («Дорого» — що ви
# відповідаєте на це?), а форма зафиксирована как v1 решением владельца.
# Держим фолбэк здесь, потому что текст вопроса в `brief.json` может приехать
# и без кавычек — тогда пара из заполненного поля потерялась бы целиком, а с
# ней и голос персоны на самом частом возражении в продаже.
_OBJECTION_CLIENT_LINE: dict[str, str] = {
    "q42_objection_expensive": "Дорого",
    "q43_objection_think": "Я подумаю",
    "q44_objection_competitors": "У конкурентів дешевше",
}


def _pair_from_objection_field(brief: dict, fid: str) -> tuple[str, str] | None:
    """Q42/Q43/Q44: реплика лида сидит в ТЕКСТЕ ВОПРОСА («Дорого» — что вы…)."""
    value = _value(brief, fid)
    if not value:
        return None
    field_ = _field(brief, fid) or {}
    m = re.search(r"«([^»]+)»", str(field_.get("question") or ""))
    client = m.group(1).strip() if m else _OBJECTION_CLIENT_LINE.get(fid, "")
    reply = _strip_quotes(_paragraphs(value)[0]) if _paragraphs(value) else ""
    return (client, reply) if client and reply else None


def _dedupe_pairs(pairs: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], list[dict]]:
    """Похожие реплики лида схлопываются.

    Мера похожести берётся у T1 (`brief.fingerprint_score` по
    `brief.normalize_question`) с тем же порогом 0.6 — не потому что «удобно»,
    а потому что вторая мера похожести в одном пайплайне это второе число на
    одну вещь.
    """
    kept: list[tuple[str, str]] = []
    dropped: list[dict] = []
    seen: list[set[str]] = []
    for client, reply in pairs:
        tokens = _brief.normalize_question(client)
        twin = next((i for i, prev in enumerate(seen)
                     if _brief.fingerprint_score(tokens, prev) >= _brief.FINGERPRINT_THRESHOLD),
                    None)
        if twin is not None:
            dropped.append({"kind": "example_pair", "value": client,
                            "reason": f"дубль реплики лида «{kept[twin][0]}» "
                                      f"(схожесть ≥ {_brief.FINGERPRINT_THRESHOLD})"})
            continue
        seen.append(tokens)
        kept.append((client, reply))
    return kept, dropped


def _build_examples(brief: dict, *, knowledge: str, allowed: set[str],
                    persona_name: str) -> tuple[str, list[tuple[str, str]], list[dict]]:
    """examples.yaml. Ключи РОВНО {client, olga} — это ХАРДКОД лоадера.

    `config/loader.py:_load_examples` сравнивает `set(item) != {"client","olga"}`
    и падает на старте. Ключ `olga` — не имя персоны, а имя поля, унаследованное
    от первого клиента; переименовать его здесь нельзя, конфиг не поднимется.

    Каждая пара проходит через R2: реплика персоны не имеет права произнести
    число, которого нет в knowledge. Пара, унаследовавшая из брифа выдуманный
    срок («переробляти через місяць», когда в базе про месяцы ни слова), НЕ
    выпускается наружу — иначе guardrail подавит её в рантайме, а голос персоны
    будет учить модель именно тому, что рантайм режет.
    """
    # ОБА разбора применяются к ОБОИМ полям. Форма ответа в спеке не
    # зафиксирована, а R5 называет источником Q20 И Q46 — при этом клиент пишет
    # их как хочет: Ярина дала Q20 диалогом «Клієнт:/Відповідь:», а Q46 —
    # нумерованным «N. Питання?» плюс ответ в кавычках. Разбор, привязанный к
    # полю, а не к форме, теряет целое поле молча: пять готовых пар голоса
    # превращаются в ноль, и никто этого не видит, потому что файл не пуст.
    raw_pairs: list[tuple[str, str]] = []
    for fid in ("q20_real_replies", "q46_top_questions"):
        value = _value(brief, fid) or ""
        raw_pairs.extend(_pairs_from_dialog_field(value))
        raw_pairs.extend(_pairs_from_faq_field(value))
    for fid in ("q42_objection_expensive", "q43_objection_think",
                "q44_objection_competitors"):
        pair = _pair_from_objection_field(brief, fid)
        if pair:
            raw_pairs.append(pair)

    pairs, dropped = _dedupe_pairs(raw_pairs)

    kept: list[tuple[str, str]] = []
    used = 0
    for client, reply in pairs:
        # Реплика ЛИДА через санитайзер R1 НЕ идёт: это чужая речь, а не наш
        # текст. Снятый там знак вопроса («скільки коштує полірування BMW 5?»)
        # превратил бы вопрос в утверждение и научил бы персону отвечать на
        # фразу, которой никто не пишет. Правило R1 живёт в knowledge, где
        # точка режет фрагмент; здесь она ничего не режет.
        client = _unify_chars(client).strip()
        reply = _unify_chars(reply).strip()
        findings = _unbacked(reply, knowledge)
        if findings:
            f = findings[0]
            dropped.append({
                "kind": "example_pair", "value": client,
                "reason": f"R2: «{reply[f.start:f.end]}» не обеспечено knowledge "
                          f"(rule={f.rule}, число={f.number})",
            })
            continue
        outside = [tok for tok in _number_tokens(reply) if tok not in allowed]
        if outside:
            dropped.append({
                "kind": "example_pair", "value": client,
                "reason": f"R2: числа {outside} нет в прайсе клиента",
            })
            continue
        # Бюджет brain.EXAMPLES_CHAR_BUDGET считается ТОЙ ЖЕ формулой, что и в
        # brain._examples_section: сверх бюджета пары молча отбрасываются в
        # рантайме, и голос персоны тихо едет — узнать об этом надо здесь.
        chunk = len(f"\nКлієнт: {client}\nТи: {reply}\n")
        if used + chunk > EXAMPLES_CHAR_BUDGET:
            dropped.append({
                "kind": "example_pair", "value": client,
                "reason": f"сверх brain.EXAMPLES_CHAR_BUDGET={EXAMPLES_CHAR_BUDGET} "
                          f"(набрано {used}) — в рантайме пара молча выпала бы",
            })
            continue
        used += chunk
        kept.append((client, reply))

    lines = [
        "# Еталонні пари «клієнт → персона» — голос на цінових і складних сценаріях.",
        "# Джерело: бриф Q20 (реальні відповіді власника), Q46 (топ питань) і поля",
        "# заперечень, переписані під правила playbook.",
        "#",
        "# КЛЮЧ `olga` — НЕ ім'я персони, а ім'я поля в loader'і",
        "# (`set(item) != {\"client\", \"olga\"}`). Перейменувати його тут не можна:",
        "# конфіг впаде на старті. Це хардкод, успадкований від першого клієнта.",
        "#",
        "# ПРАВИЛО: всі цифри тут існують у knowledge.md — інакше guardrail заріже",
        "# те, чого ми ж самі й навчили персону.",
        "",
    ]
    for client, reply in kept:
        lines.append(f"- client: {_yaml_scalar(client)}")
        lines.append(f"  olga: {_yaml_scalar(reply)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n", kept, dropped


def _yaml_scalar(text: str) -> str:
    """Строка в YAML-скаляре с двойными кавычками.

    JSON — подмножество YAML, поэтому `json.dumps` даёт валидный и корректно
    экранированный скаляр; ручное «обернуть в кавычки» ломается на первой же
    цитате внутри реплики.
    """
    return json.dumps(text, ensure_ascii=False)


# ────────────────────────────────────────────────────────────────────────────
# R6. settings.yaml: forbidden_terms полными фразами + жёсткие дефолты
# ────────────────────────────────────────────────────────────────────────────

def _forbidden_terms(brief: dict, *, owner_id: str, persona_name: str) -> list[str]:
    """Q48/Q50 → forbidden_terms ПОЛНЫМИ фразами.

    Корень «гарант» убил бы легальный раздел «Гарантії та якість роботи»,
    корень «дешев» — легальное «якщо достатньо дешевшого рішення». Цена ложного
    срабатывания — ПОТЕРЯННЫЙ ответ лиду: `forbidden_mention` не смягчает
    ответ, а подавляет его целиком.

    Апостроф даётся в ДВУХ вариантах (U+0027 и U+2019), потому что
    `forbidden_mention` матчит подстрокой БЕЗ нормализации: один вариант молча
    не сработал бы, и запрет выглядел бы включённым, не будучи им.
    """
    terms: list[str] = list(_INHERITED_PAYMENT_TERMS)
    source = _value(brief, "q50_forbidden_phrases") or ""
    for phrase in _quoted_or_whole_lines(source):
        phrase = phrase.casefold()
        if len(phrase) < 3 or phrase in terms:
            continue
        terms.append(phrase)
        # Апостроф — В ДВУХ вариантах. `forbidden_mention` матчит подстрокой
        # БЕЗ нормализации: модель напишет «обов’язково» (U+2019), в денилисте
        # будет «обов'язково» (U+0027), и слой просто не сработает. Заметить
        # это невозможно — молчание слоя выглядит как отсутствие срабатываний.
        for a, b in ((APOSTROPHE, "’"), ("’", APOSTROPHE)):
            if a in phrase:
                twin = phrase.replace(a, b)
                if twin not in terms:
                    terms.append(twin)
    # Детектор протечки чужого конфига НЕ имеет права сработать на самого
    # клиента. Если владельца этого бизнеса и правда зовут «керівниця», корень
    # окажется в его собственных файлах, генерация упадёт самоотравлением, и
    # законный клиент просто не соберётся. Проверка стоит здесь, а не в
    # `_assert_no_self_poison`: там она выглядела бы как поломка, а это
    # нормальная ситуация — совпадение роли, а не протечка.
    own = f"{owner_id} {persona_name}".casefold()
    for term in _FOREIGN_PERSONA_TERMS:
        if term in own or term in terms:
            continue
        terms.append(term)
    return terms


def _assert_no_self_poison(files: dict[str, str], terms) -> None:
    """R6: термин, встречающийся в СОБСТВЕННЫХ файлах, — падение со строкой.

    Самоотравление не теоретическое: brand-safety подавляет ответ, в котором
    термин ВСТРЕТИЛСЯ, а persona/knowledge/playbook/examples едут в промпт
    целиком. Модель, увидевшая запрещённую фразу в своей же базе, повторит её —
    и получит подавленный ответ вместо ответа.
    """
    for name in ("persona.md", "knowledge.md", "playbook.md", "examples.yaml"):
        text = files.get(name) or ""
        hit = forbidden_mention(text, terms)
        if hit is None:
            continue
        line_no = next((i for i, ln in enumerate(text.splitlines(), 1)
                        if hit.casefold() in ln.casefold()), 0)
        raise RenderError(
            f"{name}:{line_no} самоотравление brand-safety: запрещённый термин "
            f"«{hit}» встречается в собственном файле — ответ, повторивший его, "
            f"будет подавлен целиком")


def _build_settings(*, slug: str, brief: dict, persona_name: str, persona_age: int,
                    owner_id: str, owner_ref: str, currency: str, language: str,
                    work_hours: tuple[int, int] | None, terms: list[str],
                    payment_methods: str | None) -> str:
    company = _value(brief, "q1_company_name") or slug
    wh = work_hours or (9, 18)
    methods_text = ", ".join(_lower_first(p) for p in _split_choice(payment_methods or ""))
    safe_reply = (
        f"Оплатити можна так: {methods_text}. "
        f"Реквізити надсилає {owner_ref} — передаю йому ваше питання."
        if payment_methods else
        f"Питання оплати веде {owner_ref} — передаю йому ваше питання."
    )
    terms_yaml = "\n".join(f"  {_yaml_scalar(t)}," for t in terms)
    return f"""# {company} — персона {persona_name}. Клієнт `{slug}`.
#
# Файл СГЕНЕРОВАНО пайплайном онбордингу з брифу. Рішення, які пайплайн НЕ
# приймає сам, стоять дефолтами і продубльовані в розділі 2 звіту: модель,
# ім'я персони, відмінок owner_ref, funnel_gate, payments.

model: {DEFAULT_MODEL}
language: {language}

# ── Хто є хто ────────────────────────────────────────────────────────────────
# owner_id — як власника звати (бриф Q16). persona_name — ІНША людина: лоадер
# падає, якщо вони збігаються, і це правильно (лід не має говорити сам із собою).
owner_id: {_yaml_scalar(owner_id)}
# Як НАЗВАТИ власника ліду. Шаблони фолбеків підставляють ref ПІСЛЯ прийменника
# («зв'яжу вас з {{ref}}»), тому тут потрібен ОРУДНИЙ відмінок. Пайплайн
# відмінків не змінює (спека §5, пункт про відмінки) — значення взято з брифу
# як є, і його правильна форма — рішення власника.
owner_ref: {_yaml_scalar(owner_ref)}
persona_name: {_yaml_scalar(persona_name)}
persona_age: {persona_age}

currency: {_yaml_scalar(currency)}

# ── Brand-safety: згадка у ВІДПОВІДІ → відповідь подавляється плюс картка ────
# Два класи. Перший — платіжна лексика чужої юрисдикції (успадковано, коштує
# нуль). Другий — заборонені формулювання з брифу Q50, і вони ЗУМИСНЕ вписані
# ПОВНИМИ фразами, а не корінням: корінь «гарант» убив би легальну розмову про
# гарантії, корінь «дешев» — легальне «достатньо дешевшого рішення».
# Апостроф даний у ДВОХ варіантах (U+0027 і U+2019): матч іде підрядком без
# нормалізації, і один варіант мовчки не спрацював би.
forbidden_terms: [
{terms_yaml}
]
# Чим замінити подавлену відповідь про оплату: подавлення ≠ тиша.
safe_payment_reply: {_yaml_scalar(safe_reply)}

# ── Жорсткі дефолти (спека §2). Бриф просив іншого → рядок звіту, не правка ──
# honesty_mode: honest — на «ти бот?» персона розкривається чесно. Єдина
#   альтернатива — ПОВНЕ значення 'free_owner_liability'; вмикати його =
#   свідомо взяти на себе відповідальність за те, що бот видає себе за людину.
# strict_knowledge: true — персона говорить лише з knowledge.md; вигадані
#   ЦІНИ/ТЕРМІНИ подавляються і ескалуються. Межа цього захисту — DEV-28:
#   перевіряються ЧИСЛА, а не УТВЕРДЖЕННЯ.
honesty_mode: honest
strict_knowledge: true

# Робочі години бізнесу (бриф Q12). Це НЕ розклад бота: work_hours впливають
# ЛИШЕ на темп друку — humanizer сповільнює відповіді вночі.
work_hours: {{start: {wh[0]}, end: {wh[1]}}}
timings:
  read_delay_min: 1.5
  read_delay_max: 4.0
  cps_min: 4.0
  cps_max: 7.0
  jitter_min: 0.9
  jitter_max: 1.2
  split_pause_min: 0.6
  split_pause_max: 1.8
  split_max_len: 160
  night_multiplier: 2.5
  debounce_window: 3.0
  debounce_max: 15.0
limits: {{max_reply_tokens: 20000, per_contact_hourly: 20, daily_cap: 500}}

telegram:
  # allowlist = override «ВІДПОВІДАТИ ЗАВЖДИ». Порожній: жодного перевіреного
  # id бриф не дав, а вписати сюди чуже — значить відповідати не тому.
  allowlist: []
  # ФАКТ, не ціль. Гардіан деплоїть з робочого дерева, тому ввімкнений у файлі
  # гейт піднявся б на ребуті БЕЗ команди власника, а catch_up_missed на старті
  # прогнав би через нього непрочитане за добу. Вмикати ТІЛЬКИ командою пульта.
  funnel_gate: false
control:
  # ⚠️ СВОЯ env-змінна, НЕ спільна з іншими клієнтами. Тут ІМ'Я змінної, НЕ
  # токен. Два раннери з ОДНАКОВИМ ім'ям читали б ОДИН токен і билися б за
  # getUpdates: Telegram віддає long-poll рівно одному споживачу.
  control_bot_token_env: CHATTER_CONTROL_BOT_TOKEN_{slug.upper()}
  classifier_enabled: true
  # УГОВІР 2026-07-23: алерт три дні поспіль → лагодимо КОРІНЬ класифікатора,
  # а НЕ піднімаємо поріг.
  classifier_error_threshold: 2
  profile_stale_threshold: 3
  snooze_seconds: 3600

# ── Оплата ───────────────────────────────────────────────────────────────────
# ВИМКНЕНО, бо реквізитів немає: requisites.yaml не заповнений, а ввімкнена
# фіча, якій нічого сказати, роняє клієнта на старті (assert_startable).
payments:
  enabled: false
"""


# ────────────────────────────────────────────────────────────────────────────
# Публичный вход
# ────────────────────────────────────────────────────────────────────────────

def _transliterate(slug: str) -> str:
    text = (slug or "").replace("-", " ").replace("_", " ").strip().casefold()
    out = text
    for lat, cyr in _TRANSLIT:
        out = out.replace(lat, cyr)
    return _upper_first(out) or _upper_first(slug)


def _detect_currency(price_block: str) -> str:
    low = (price_block or "").casefold()
    for token, name in _CURRENCY_TOKENS:
        if token in low:
            return name
    return ""


def render_all(brief: dict, *, slug: str) -> RenderResult:
    """`brief.json` → пять файлов клиента + машиночитаемые списки для отчёта.

    Порядок сборки не произвольный:
      1. knowledge — из него строится множество допустимых чисел (R2);
      2. examples — фильтруются ПО этому множеству и по живому guardrail;
      3. playbook — его слой эскалации отсеивает слова, матчащие уже готовые
         knowledge/examples (C4);
      4. settings — forbidden_terms проверяются на самоотравление уже собранных
         файлов (R6).
    Собрать их в другом порядке значит проверять правило по файлу, которого ещё
    нет, то есть не проверять вовсе.
    """
    if not isinstance(brief, dict) or not isinstance(brief.get("fields"), dict):
        raise RenderError(
            "brief.json: ожидался словарь с ключом 'fields' (контракт T1) — "
            "без него генератор не знает, что разбирать")
    if not slug or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", slug):
        raise RenderError(f"slug {slug!r}: ожидался [a-z0-9][a-z0-9_-]*")

    defaults: list[dict] = []

    def default(key, value, why, *, wanted_other=False, quote=None):
        defaults.append({"key": key, "value": value, "why": why,
                         "brief_wanted_other": bool(wanted_other),
                         "brief_quote": quote})
        return value

    price_block = _value(brief, "q22_price_list")
    if not price_block:
        raise RenderError(
            "q22_price_list пуст или забракован мусор-детектором — без прайса "
            "knowledge не собрать, а собранный без него клиент будет выдумывать "
            "цены. Это rc «не состоялось», а не «сгенерировали как смогли»")

    services = _lengthen_titles(_parse_services(price_block),
                                _value(brief, "q21_services"))
    if not services:
        raise RenderError(
            "прайс не разобран ни на одну услугу: ожидались строки вида "
            "«1. Назва послуги» (форма прайса Google-формы)")

    # ── Решения, которые генератор НЕ принимает сам (спека §2) ──────────────
    company = _value(brief, "q1_company_name") or slug
    persona_from_brief = _value(brief, "q14_persona_name")
    persona_name = default(
        "persona_name", persona_from_brief or _transliterate(slug),
        "имя персоны — решение владельца, а не поле формы",
        wanted_other=persona_from_brief is None,
        quote=_raw_quote(brief, "q14_persona_name"))
    persona_age = default("persona_age", DEFAULT_PERSONA_AGE,
                          "возраста в форме нет; влияет на тон, решает владелец")
    default("model", DEFAULT_MODEL,
            "живая воронка с квалификацией и торгом по вилке — haiku мало")

    owner_id = _value(brief, "q16_owner_ref") or "власник"
    owner_id = _upper_first(owner_id.strip())
    if owner_id.casefold() == persona_name.casefold():
        raise RenderError(
            f"owner_id и persona_name совпали ({owner_id!r}): loader.load_config "
            f"падает на этом намеренно — лид не должен говорить сам с собой")
    # ДВА слота, ОДНО поле брифа — и это неустранимо без склонения.
    # `owner_id` стоит в подлежащем («підтверджує {owner_id}»), `owner_ref` —
    # после предлога («зв'яжу вас з {owner_ref}»), то есть нужны именительный и
    # орудный. Форма Q16 одна, какая — решает клиент: Ярина написала «старший
    # мастер» (именительный), другой напишет «нашим старшим майстром»
    # (орудный). Склонять программно нам запрещено (спека §5), поэтому оба
    # слота получают то, что дал бриф, и ОБА уезжают в раздел 2 отчёта. Молча
    # подставить одну форму в оба места значит отдать владельцу файл, в котором
    # одна из двух фраз читается с ошибкой, и не сказать, какая.
    default("owner_id", owner_id,
            "поле Q16 одно, а падежей нужно два (owner_id — називний, "
            "owner_ref — орудний); сверь ОБЕ формы",
            wanted_other=False, quote=_raw_quote(brief, "q16_owner_ref"))
    owner_ref = default(
        "owner_ref", _lower_first(owner_id),
        "падеж программно не склоняем (спека §5, пункт про падежи): шаблон "
        "подставляет ref после предлога («зв'яжу вас з {ref}»), нужен орудный",
        wanted_other=False, quote=_raw_quote(brief, "q16_owner_ref"))

    language_raw = (_value(brief, "q17_language") or "").casefold()
    language = default("language", _LANGUAGE_BY_BRIEF.get(language_raw, DEFAULT_LANGUAGE),
                       "язык общения из брифа Q17", quote=_raw_quote(brief, "q17_language"))
    language_label = _value(brief, "q17_language") or "українська"
    currency = _detect_currency(price_block)
    if not currency:
        raise RenderError(
            "в прайсе не найдено ни одной валюты — guardrail не признает "
            "обеспеченным НИ ОДНО число (фрагмент без валюты и ценового слова "
            "не отдаёт числа в ценовое множество)")
    default("currency", currency, "определена по прайсу клиента")

    # honesty_mode/funnel_gate/strict_knowledge/payments — ЖЁСТКИЕ дефолты.
    disclose = _value(brief, "q15_disclose_ai")
    default("honesty_mode", "honest",
            "жёсткий дефолт: на «ты бот?» персона раскрывается честно; "
            "выключение требует полного значения free_owner_liability и берёт "
            "ответственность на владельца",
            wanted_other=bool(disclose and not disclose.casefold().startswith("так")),
            quote=_raw_quote(brief, "q15_disclose_ai"))
    default("strict_knowledge", True,
            "жёсткий дефолт: персона говорит только из knowledge.md")
    default("telegram.funnel_gate", False,
            "жёсткий дефолт: гардиан деплоит из рабочего дерева, включённый в "
            "файле гейт поднялся бы на ребуте БЕЗ команды владельца")
    default("payments.enabled", False,
            "реквизитов в брифе нет; включённая фича без реквизитов роняет "
            "клиента на старте (assert_startable)")
    default("telegram.allowlist", [],
            "проверенного telegram id бриф не дал",
            wanted_other=False, quote=_raw_quote(brief, "q8_accounts"))
    default("control.owner_chat_id", None,
            "канал уведомлений владельцу в брифе не дан",
            wanted_other=False, quote=_raw_quote(brief, "q36_notify_channel"))

    # ── R7: обязательные факты → заглушки ──────────────────────────────────
    stub_lines, stubs = _required_facts(brief, owner_id)

    # ── knowledge ──────────────────────────────────────────────────────────
    # `section_stubs` держится ОТДЕЛЬНО от `stubs` намеренно: C10 обходит
    # `vocabulary.REQUIRED_FACTS` и сверяет их с `.stubs`, а заглушка РАЗДЕЛА
    # (R8) фактом словаря не является. Сложив их в один список, мы заставили бы
    # C10 искать факт с id «section:Гарантії…», которого в словаре нет, —
    # проверка стала бы красной на ровном месте или, что хуже, научилась бы
    # игнорировать незнакомые id и вместе с ними настоящие расхождения.
    knowledge, section_stubs = _build_knowledge(
        brief, services=services, owner_id=owner_id, owner_ref=owner_ref,
        currency=currency, stub_lines=stub_lines)
    _assert_required_sections(knowledge)
    _assert_format_hygiene(knowledge, "knowledge.md", price_lines=True)

    allowed = allowed_numbers(knowledge)
    _assert_knowledge_self_backed(knowledge)

    # ── examples ───────────────────────────────────────────────────────────
    examples, pairs, dropped = _build_examples(
        brief, knowledge=knowledge, allowed=allowed, persona_name=persona_name)

    # ── playbook ───────────────────────────────────────────────────────────
    stop_words_value = _value(brief, "q34_stop_words")
    keywords, kw_dropped = _escalation_keywords(
        stop_words_value or "", knowledge + "\n" + examples)
    dropped.extend(kw_dropped)
    if not keywords:
        # Пустой детерминированный слой = бот не зовёт человека на «поверніть
        # гроші», даже когда классификатор лёг. Это не «клиент без стоп-слов»,
        # это клиент без последней линии обороны, и собирать его молча нельзя.
        why = ("поле q34_stop_words пусто или забраковано мусор-детектором"
               if not stop_words_value else
               f"все кандидаты отброшены: {[d['value'] for d in kw_dropped]}")
        raise RenderError(
            f"детерминированный слой эскалации собрался ПУСТЫМ ({why}). "
            f"Слой существует ровно для случая «классификатор лёг»; пустым он "
            f"молча выключен, и лид с претензией останется наедине с ботом")
    playbook = _build_playbook(
        brief, persona_name=persona_name, owner_id=owner_id, owner_ref=owner_ref,
        company=company, keywords=keywords)
    _assert_format_hygiene(playbook, "playbook.md")
    _assert_escalation_layer_alive(playbook, keywords)
    _assert_playbook_numbers(playbook, knowledge, allowed)

    # ── persona ────────────────────────────────────────────────────────────
    persona = _build_persona(
        brief, persona_name=persona_name, persona_age=persona_age,
        owner_id=owner_id, company=company, language_label=language_label)
    _assert_persona_ok(persona, persona_name)
    _assert_format_hygiene(persona, "persona.md")

    # ── settings ───────────────────────────────────────────────────────────
    terms = _forbidden_terms(brief, owner_id=owner_id, persona_name=persona_name)
    settings = _build_settings(
        slug=slug, brief=brief, persona_name=persona_name, persona_age=persona_age,
        owner_id=owner_id, owner_ref=owner_ref, currency=currency, language=language,
        work_hours=_parse_work_hours(_value(brief, "q12_hours")), terms=terms,
        payment_methods=_value(brief, "q30_payment_methods"))

    files = {
        "knowledge.md": knowledge,
        "persona.md": persona,
        "playbook.md": playbook,
        "examples.yaml": examples,
        "settings.yaml": settings,
    }
    _assert_no_self_poison(files, terms)
    dropped.extend(_record_lost_answers(brief, files))

    price_numbers, deadline_numbers = _guardrails._context_numbers(knowledge)
    counters = {
        "services": len(services),
        "prices": len(price_numbers),
        "deadlines": len(deadline_numbers),
        "stop_words": len(keywords),
        "forbidden": len(terms),
        "example_pairs": len(pairs),
    }
    return RenderResult(files=files, defaults=defaults, stubs=stubs,
                        counters=counters, dropped=dropped,
                        section_stubs=section_stubs)


# ────────────────────────────────────────────────────────────────────────────
# Сторожа генератора: то, что обязано упасть ЗДЕСЬ, а не на приёмке
# ────────────────────────────────────────────────────────────────────────────

_DECIMAL_DOT = re.compile(r"\d\.\d")


def _assert_format_hygiene(text: str, name: str, *, price_lines: bool = False) -> None:
    """C8 на стороне генератора: NBSP, точки в числовых строках, один разделитель.

    Проверять это только автоприёмкой поздно: файл уже записан, а «один
    десятичный разделитель на файл» — свойство ФАЙЛА, а не строки, и собрать
    его можно только зная весь текст.

    `price_lines=True` включается ТОЛЬКО для knowledge.md, и это не послабление
    для остальных. `_context_numbers` разбирает по фрагментам ИМЕННО knowledge —
    там точка внутри ценовой строки разрезает фрагмент и лишает число валюты.
    В persona.md запрет точки означал бы «Мене звати Ярина, мені 26, я …»
    вместо человеческой фразы, а разбору guardrail этот файл вообще не
    подвергается. Правило, применённое там, где его причина не действует,
    перестаёт быть правилом и становится придиркой, которую однажды снимут
    вместе с настоящей проверкой.
    """
    for i, line in enumerate(text.splitlines(), 1):
        if line.startswith("#") or line.lstrip().startswith(("<!--", "-->")):
            continue      # заголовки и комментарии `_context_numbers` не разбирает
        if not any(ch.isdigit() for ch in line) or _URL_RE.search(line):
            continue     # ссылка — не ценовая строка, её точки часть адреса
        if price_lines:
            _assert_price_line_clean(line, where=f"{name}:{i}")
        else:
            for ch in _SPACE_CHARS:
                if ch in line:
                    raise RenderError(
                        f"{name}:{i}: NBSP/узкий пробел на позиции "
                        f"{line.index(ch)} — даёт ДРУГОЙ числовой токен: {line!r}")
    # Ссылки маскируются ПЕРЕД поиском второго десятичного разделителя: точка
    # в «site.24.ua» — не разделитель дробной части, и падать на ней значит
    # запретить клиенту его собственный домен.
    m = _DECIMAL_DOT.search(_URL_RE.sub(" ", text))
    if m:
        line_no = _URL_RE.sub(" ", text).count("\n", 0, m.start()) + 1
        raise RenderError(
            f"{name}:{line_no} десятичная ТОЧКА «{m.group()}» при запятой в файле — "
            f"guardrails._numbers снимает только пробелы, поэтому «1,5» и «1.5» "
            f"РАЗНЫЕ токены, и один из них окажется необеспеченным")


def _assert_knowledge_self_backed(knowledge: str) -> None:
    """C2 на стороне генератора: каждая строка прайса обеспечена САМА СОБОЙ.

    Если строка knowledge, прогнанная через боевой `_findings` против того же
    knowledge, даёт finding — значит точка (или NBSP, или другой десятичный
    разделитель) разрезала фрагмент, и в рантайме бот не сможет назвать
    собственную цену. Это единственная проверка, которая ловит R1 по существу,
    а не по признакам.
    """
    for i, line in enumerate(knowledge.splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        for f in _unbacked(line, knowledge):
            raise RenderError(
                f"knowledge.md:{i} «{line[f.start:f.end].strip()}» → "
                f"{f.number} не обеспечено (rule={f.rule}) собственным же "
                f"knowledge: фрагмент разрезан или лишён валюты. Строка: {line!r}")


def _assert_playbook_numbers(playbook: str, knowledge: str, allowed: set[str]) -> None:
    """R2 для playbook: наружу не выпускается ни одно число вне прайса.

    Исключение ровно одно и оно помечено в самом файле: секции с маркером
    «внутрішнє, вголос не цитувати» (ICP). Причина — в комментарии у ICP-секции.
    """
    internal = False
    for i, line in enumerate(playbook.splitlines(), 1):
        if line.startswith("#"):
            internal = INTERNAL_MARKER_UK in line
            continue
        if internal or not line.strip():
            continue
        for f in _unbacked(line, knowledge):
            raise RenderError(
                f"playbook.md:{i} «{line[f.start:f.end].strip()}» не обеспечено "
                f"knowledge (rule={f.rule})")
        _assert_numbers_allowed(line, allowed, where=f"playbook.md:{i}")


_TRACE_WORD = re.compile(r"[\w’'-]{5,}")
# Поля, чьё значение превращается в СТРУКТУРУ, а не в текст: «Українська» →
# `language: uk`, «Так, цілодобово» → отсутствие ограничения, «Ні» → флаг.
# Искать их дословный след в файлах бессмысленно — они и не должны там быть,
# и такая запись превратила бы `dropped` в шум, в котором настоящая потеря
# утонет. За их судьбу отвечает `defaults`, а не этот обход.
_TRACE_SKIP_TARGETS = frozenset({"report_only", "settings"})


def _record_lost_answers(brief: dict, files: dict[str, str]) -> list[dict]:
    """Третье состояние: «клиент ответил, детектор пропустил, генератор потерял».

    Отчёт знает ровно два состояния: «ВЗЯТО ИЗ БРИФА» (раздел 1) и «В БРИФЕ
    НЕТ» (раздел 3). Ответ, который клиент написал, мусор-детектор признал
    чистым, а генератор не положил никуда, не попадёт НИ В ОДИН раздел —
    владелец прочитает отчёт и решит, что всё на месте. Дороже всего это стоит
    там, где ответ закрывает обязательный факт: факт считается обеспеченным,
    заглушки нет, а сказать боту нечего, и он молчит на вопрос, ответ на
    который у клиента был.

    След ищется МЯГКО и по ВСЕМ файлам: текст законно переписывается под язык
    клиента и законно уезжает не в тот файл, где лежит `target` (тон из
    playbook-поля живёт в persona.md). Требовать дословности или точного файла
    значит наплодить ложных записей — а список, которому не верят, читать
    перестанут.
    """
    haystack = "\n".join(files.values()).casefold()
    lost: list[dict] = []
    for fid, rec in (brief.get("fields") or {}).items():
        if rec.get("target") in _TRACE_SKIP_TARGETS or rec.get("verdict") != "ok":
            continue
        value = rec.get("value")
        if not isinstance(value, str) or not value.strip():
            continue
        words = [w for w in _TRACE_WORD.findall(value) if not w.isdigit()]
        numbers = re.findall(r"\d+", value)
        if any(w.casefold() in haystack for w in words):
            continue
        if any(n in haystack for n in numbers):
            continue
        lost.append({
            "kind": "brief_answer", "value": f"{fid}: {value[:80]}",
            "reason": f"ответ с вердиктом ok не оставил следа ни в одном файле "
                      f"(target={rec.get('target')}) — генератору некуда его "
                      f"положить, и в отчёт он попал бы как «в брифе нет»",
        })
    return lost


def _assert_escalation_layer_alive(playbook: str, expected: list[str]) -> None:
    """R4: слой эскалации ЖИВ и содержит ровно то, что мы в него положили.

    Проверяется не глазами, а боевым парсером `parse_escalation_keywords`. Он
    ловит обе грабли сразу: чужой заголовок секции (список окажется пустым) и
    строку комментария, начатую с «- » (в списке появится лишнее). Сравнение
    именно РАВЕНСТВОМ: «непусто» пропустило бы четыре предложения пояснения,
    уехавшие в словарь, — ровно то, что случилось с первой ручной версией.
    """
    parsed = parse_escalation_keywords(playbook)
    if not parsed:
        raise RenderError(
            "playbook.md: parse_escalation_keywords вернул пусто — "
            "детерминированный слой эскалации МОЛЧА выключен (заголовок секции "
            f"не из escalation._KEYWORD_HEADINGS={list(_KEYWORD_HEADINGS)})")
    want = [k.casefold() for k in expected]
    if parsed != want:
        extra = [k for k in parsed if k not in want]
        missing = [k for k in want if k not in parsed]
        raise RenderError(
            f"playbook.md: слой эскалации разобрался НЕ так, как собран. "
            f"Лишнее (вероятно, строка комментария начинается с «- »): {extra}. "
            f"Потеряно: {missing}")
