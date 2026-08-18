# -*- coding: utf-8 -*-
"""T4 — автоприёмка онбординга: C1–C15 по готовому каталогу клиента.

Спека: `docs/superpowers/specs/2026-08-14-chatter-onboarding-pipeline.md`, §4
(таблица C1–C14), §5 (чего не автоматизируем), §6 (приёмка арки).
C15:   `docs/superpowers/specs/2026-08-17-c15-numbers-must-match-the-brief.md`
       (числа knowledge обязаны совпадать с числами брифа).
План:  `docs/superpowers/plans/2026-08-17-onboard-pipeline-plan.md`.

ГРАНИЦА МОДУЛЯ. Ноль сети, ноль LLM, ноль записи на диск: вход — каталог из
пяти файлов, уже собранный `report.json` и `brief.json` того же прогона, выход
— ровно 15 вердиктов. Каталог читается и НЕ правится ни при каких условиях:
`--check` ходит в том числе по боевому `chatter/clients/<slug>` (спека §6,
калибровка), а проверка, которая чинит то, что проверяет, ничего не доказывает.

ТРИ ПРАВИЛА, которым подчинён каждый кусок ниже.

1. **Проверок ВСЕГДА пятнадцать.** Проверка, которая молча исчезла из списка,
   неотличима от пройденной — это ровно та «зелёная ширма», о которой спека
   предупреждает в §7. Поэтому исключение внутри проверки не убирает её из
   результата, а делает `blocked` (см. `CheckResult.blocked` и `verdict`).

2. **Красное называет файл:строку и виновника.** «Проверка не прошла» стоит
   владельцу второго прогона и чтения кода; `knowledge.md:47 «…» → 8000 не
   обеспечено (rule=price)` стоит одной правки.

3. **Проверяем ПОСЛЕДСТВИЕ настоящим кодом, а не свою копию правила.** Цены
   гоняются через живой `guardrails._findings`, слой эскалации — через живой
   `escalation.parse_escalation_keywords`, клиент поднимается живым
   `load_config`. Собственная копия правила зеленеет ровно тогда, когда
   разъезжается с рантаймом, — а разъезжается она молча.

── РЕШЕНИЯ, ГДЕ СПЕКА МОЛЧАЛА (каждое названо здесь и в отчёте прогона) ──
* `verdict` возвращает 2, если хоть одна проверка `blocked`. «Не состоялось»
  перевешивает «есть красное»: прогон, часть которого не выполнялась, ничего
  не доказал, и выдавать его за «нашли одно красное» нельзя.
* Каталог существует и читается → прогон СОСТОЯЛСЯ, даже если внутри нет
  файлов: отсутствующий `knowledge.md` — это красное C1 с текстом ConfigError
  (так требует §4), а не «прогон не состоялся».
* C4 и C10 опираются на `report.json`. Битый/непереданный отчёт делает
  `blocked` РОВНО эти две проверки, а не все четырнадцать: остальные двенадцать
  читают файлы и доказывают ровно столько же, сколько доказали бы с отчётом.
* C15 опирается на `brief.json` — по той же логике `blocked` достаётся ей
  одной. Сверять числа файла НЕ С ЧЕМ, если брифа рядом нет; молча зеленеть
  при этом нельзя (спека C15, §4).
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from chatter.config.loader import ConfigError, load_config
from chatter.core import guardrails as _g
from chatter.core.brain import EXAMPLES_CHAR_BUDGET
from chatter.core.brand_safety import forbidden_mention
from chatter.core.drill import (
    DrillScenarioError, parse_scenario, vacuous_expectations)
from chatter.core.escalation import _KEYWORD_HEADINGS, parse_escalation_keywords
from chatter.core.obligations import DEFAULT_PROMISE_TERMS, unbacked_promise
from chatter.onboard.report import TARGET_FILES as _TARGET_FILES
from chatter.onboard.vocabulary import (
    DUAL_PURPOSE_FIELDS,
    PROMO_SECTION_UK,
    REQUIRED_FACTS,
    REQUIRED_SECTIONS_UK,
    STUB_TEMPLATE_UK,
    UNKNOWN_SECTION_UK,
)
from chatter.payments.drill_gate import DRILL_CONTACTS as PROD_DRILL_CONTACTS

__all__ = [
    "CheckResult", "CHECK_IDS", "FLAG_IDS", "REVIEWED_FILENAME",
    "is_reviewed", "run_checks", "verdict",
]

CHECK_IDS: tuple[str, ...] = tuple(f"C{i}" for i in range(1, 16))

# C12/C13 — ФЛАГИ, а не красное (спека §4, подтверждено владельцем 17.08).
# Красный статус здесь означал бы, что пайплайн знает намерение клиента лучше
# владельца: «Старший майстер відповідає протягом години» — законная строка из
# Q35, и блокировать её нельзя.
FLAG_IDS: frozenset[str] = frozenset({"C12", "C13"})

# Решение владельца (вопрос 3 спеки, ответ 17.08): без метки вычитки `--check`
# НЕ зелёный. Причина в §7: отчёт может стать зелёной ширмой — если разделы 2 и
# 3 не читать, дефолты доедут до прода как «решения». Метку ставит человек.
REVIEWED_FILENAME = "REVIEWED"

CLIENT_FILES: tuple[str, ...] = (
    "knowledge.md", "persona.md", "playbook.md", "examples.yaml", "settings.yaml")

# Корень репозитория — КОНСТАНТА модуля, а не выражение внутри `_Ctx`: C7
# ходит отсюда в `docs/chatter/drills/` и в `scripts/drill_reset.py`, и без
# подменяемой точки сторож происхождения сценария (DEV-36) пришлось бы писать
# либо на живой репозиторий, либо на приватную функцию в обход `run_checks`.
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]

# Разобранный бриф ТОГО ЖЕ прогона. Имя фиксировано в `__main__.BRIEF_FILENAME`
# — здесь оно повторено строкой, потому что импорт `__main__` из проверяемого
# модуля означал бы запуск CLI ради константы.
BRIEF_FILENAME = "brief.json"

# Коды выхода (спека, §0 и §4).
RC_GREEN, RC_RED, RC_NOT_RUN = 0, 1, 2

# C9: длина фрагмента, ниже которой совпадение не считается протечкой. Спека
# называет число прямо. Короткие куски («не беремо в роботу») совпадают у
# playbook и knowledge законно и постоянно.
C9_MIN_FRAGMENT = 40


# ─────────────────────────────────────────────────────────────────────────────
# Результат
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CheckResult:
    """Один вердикт. `ok=False` при `is_flag=True` — это ФЛАГ, а не красное.

    `blocked` — седьмое поле сверх публичного контракта, и оно НЕ дублирует
    `ok`. «Проверка отработала и нашла дефект» и «проверка не отработала» —
    разные состояния с разными кодами выхода (1 и 2), и склеить их в один `ok`
    значит объявить непроведённую проверку пройденной. Поле стоит последним и
    имеет умолчание, поэтому конструктор шести полей публичного API остаётся
    рабочим как есть.
    """

    id: str
    ok: bool
    is_flag: bool
    message: str
    file: str | None = None
    line: int | None = None
    blocked: bool = False


def _ok(cid: str, message: str, *, file: str | None = None, line: int | None = None) -> CheckResult:
    return CheckResult(cid, True, cid in FLAG_IDS, message, file, line)


def _red(cid: str, message: str, *, file: str | None = None, line: int | None = None) -> CheckResult:
    return CheckResult(cid, False, cid in FLAG_IDS, message, file, line)


def _blocked(cid: str, message: str, *, file: str | None = None, line: int | None = None) -> CheckResult:
    return CheckResult(cid, False, cid in FLAG_IDS, f"НЕ СОСТОЯЛАСЬ: {message}",
                       file, line, True)


def is_reviewed(client_dir) -> bool:
    """Стоит ли метка вычитки. Файл, а не флаг в коде: ставит её человек."""
    return (Path(client_dir) / REVIEWED_FILENAME).exists()


# ─────────────────────────────────────────────────────────────────────────────
# Нормализация и разбор текста
# ─────────────────────────────────────────────────────────────────────────────
#
# Один алфавит на весь модуль. В брифе живут U+02BC и U+2019, из Google Forms
# регулярно приезжает NBSP, тире диапазона у нас U+2013 — сравнение по сырому
# тексту хрупко ровно здесь, и половина проверок молча не находила бы того, что
# ищет («два числа на одну вещь» в виде двух написаний одной строки).

_SPACE_CHARS = "              　"
_INVISIBLE_CHARS = "​‌‍﻿"
_APOSTROPHE_CHARS = "’ʼʻ‘′‵´`"
_DASH_CHARS = "–—−‒"

# NBSP и невидимки ловим ОТДЕЛЬНО (C8): человек их не видит, а `str.split()`,
# сравнение по подстроке и наши собственные якоря — видят.
_INVISIBLE_NAMES = {
    " ": "NBSP", " ": "NNBSP", " ": "FIGURE SPACE",
    " ": "THIN SPACE", " ": "HAIR SPACE", " ": "4-PER-EM SPACE",
    "　": "IDEOGRAPHIC SPACE", "​": "ZERO WIDTH SPACE",
    "‌": "ZWNJ", "‍": "ZWJ", "﻿": "BOM/ZWNBSP",
}

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_URL_RE = re.compile(r"https?://\S+|(?<![\w.@-])(?:[a-z0-9][\w-]*\.)+[a-z]{2,}(?![\w-])", re.I)
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
# Дата и время — не десятичная дробь: «30.09.2026» и «9:00» точкой и двоеточием
# разделяют не целую и дробную часть.
_DATE_RE = re.compile(r"\b\d{1,4}[./]\d{1,2}(?:[./]\d{2,4})?\b|\b\d{1,2}:\d{2}\b")


def _unify(text: str) -> str:
    """Casefold + один пробел + один апостроф + одно тире."""
    out = []
    for ch in text or "":
        if ch in _SPACE_CHARS:
            out.append(" ")
        elif ch in _INVISIBLE_CHARS:
            continue
        elif ch in _APOSTROPHE_CHARS:
            out.append("'")
        elif ch in _DASH_CHARS:
            out.append("-")
        else:
            out.append(ch)
    return " ".join("".join(out).casefold().split())


def _strip_html_comments(text: str) -> str:
    """Комментарии убираем, но переводы строк СОХРАНЯЕМ — иначе номера строк в
    красном поедут, а номер строки здесь и есть половина ценности сообщения."""
    def _blank(m: re.Match) -> str:
        return "".join(ch if ch == "\n" else " " for ch in m.group())
    return _HTML_COMMENT_RE.sub(_blank, text or "")


@dataclass(frozen=True)
class _Unit:
    """«Пункт» файла: пункт списка или предложение абзаца, с номером строки.

    Почему не строка. Эталон переносит абзацы по ширине, и предложение
    «Питання повернення передоплати в інших випадках вирішує старший майстер»
    живёт на ДВУХ строках. Проверка, работающая построчно, увидела бы половину
    предложения и вынесла бы вердикт по ней — то есть по тексту, которого в
    файле нет.
    """

    file: str
    line: int
    section: str
    text: str
    # Пункт списка или обычный абзац. Разница нужна там, где список — это
    # ПЕРЕЧЕНЬ сущностей, а абзац над ним — вводная фраза («Цих даних у нас
    # немає — їх не можна вигадувати»). Считать вводную фразу сущностью значит
    # искать в файле то, чего клиент не называл.
    bullet: bool = False


def _units(text: str, filename: str) -> list[_Unit]:
    clean = _strip_html_comments(text or "")
    lines = clean.splitlines()
    units: list[_Unit] = []
    section = ""
    block: list[tuple[int, str]] = []
    block_bullet = False

    def flush() -> None:
        if not block:
            return
        joined = ""
        marks: list[tuple[int, int]] = []   # (offset в joined, номер строки)
        for lineno, raw in block:
            if joined:
                joined += " "
            marks.append((len(joined), lineno))
            joined += raw.strip()
        joined = joined.strip()
        if joined:
            pos = 0
            first_sentence = True
            for part in _SENTENCE_SPLIT_RE.split(joined):
                if not part.strip():
                    continue
                start = joined.find(part, pos)
                if start < 0:
                    start = pos
                pos = start + len(part)
                lineno = block[0][0]
                for off, ln in marks:
                    if off <= start:
                        lineno = ln
                    else:
                        break
                # `bullet` стоит только на ПЕРВОМ предложении пункта: второе и
                # дальше — пояснение («Він залежить від матеріалу, експлуатації,
                # частоти мийок»), и сущность там уже не называется, а
                # раскрывается. Ключ, взятый из пояснения, ищет в файле общие
                # слова и находит их.
                units.append(_Unit(filename, lineno, section, part.strip(),
                                   bullet=block_bullet and first_sentence))
                first_sentence = False
        block.clear()

    for i, raw in enumerate(lines, 1):
        stripped = raw.strip()
        head = _HEADING_RE.match(raw)
        if head:
            flush()
            block_bullet = False
            section = head.group(2).strip()
            continue
        if not stripped:
            flush()
            block_bullet = False
            continue
        if _BULLET_RE.match(raw):
            flush()
            block_bullet = True
            block.append((i, _BULLET_RE.sub("", raw, count=1)))
            continue
        block.append((i, raw))
    flush()
    return units


def _headings(text: str) -> dict[str, int]:
    """{нормализованный заголовок: номер строки}. Отдельно от `_sections`,
    потому что заголовок БЕЗ содержимого не даёт ни одного юнита — а для
    владельца «раздела нет» и «раздел пуст» это разные правки: во втором случае
    он будет искать глазами то, что видит на экране."""
    out: dict[str, int] = {}
    for i, line in enumerate(_strip_html_comments(text or "").splitlines(), 1):
        m = _HEADING_RE.match(line)
        if m:
            out.setdefault(_unify(m.group(2)), i)
    return out


def _sections(text: str) -> dict[str, list[_Unit]]:
    out: dict[str, list[_Unit]] = {}
    for u in _units(text, "knowledge.md"):
        out.setdefault(_unify(u.section), []).append(u)
    return out


def _stem(word: str, size: int = 5) -> str:
    """Корень слова для нестрогого сравнения форм.

    Склонять программно мы не будем (спека §5), поэтому сравниваем по началу
    слова. `size` подобран так, чтобы «майстер/майстру/майстрові» сошлись, а
    «час»/«частина» — нет: короткий корень ловит чужие слова, и это ровно та
    грабля, которой guardrails объясняет свой `\\b` у стема «ден».
    """
    w = _unify(word)
    return w[:size] if len(w) > size else w


# Служебные слова: в ключ-сущность не идут, но и не мешают ему совпасть.
_FUNCTION_WORDS = frozenset({
    "або", "чи", "та", "і", "й", "на", "в", "у", "з", "із", "зі", "до", "для",
    "по", "від", "при", "як", "що", "це", "цих", "їх", "не", "ні", "та", "яка",
    "який", "яке", "які", "буде", "поки", "ще", "вже", "наш", "наша", "наші",
})


def _key_stem(word: str) -> str:
    """Корень ключа-сущности: слово минус ОДНА буква окончания.

    Пять символов (как у ролей владельца) здесь слишком коротко: «орієнтир»
    усечённый до «орієнт» матчит «терміни орієнтовні» — один корень, разные
    смыслы, и C13 даёт флаг на пустом месте. Одна буква снимает падеж
    («адреса/адресу/адреси» → «адрес», «сайт» → «сайт») и не пускает чужое
    слово: «орієнтир» → «орієнти», а «орієнтовні» с этого не начинается.
    """
    w = _unify(word)
    return w if len(w) < 6 else w[:-1]


def _phrase_stems(phrase: str) -> list[str]:
    return [_key_stem(w) for w in _WORD_RE.findall(_unify(phrase))
            if len(w) >= 4 and w not in _FUNCTION_WORDS]


def _contains_all_stems(text: str, stems: list[str]) -> bool:
    if not stems:
        return False
    low = _unify(text)
    return all(re.search(rf"\b{re.escape(s)}", low) for s in stems)


# ── Единицы времени: СВОЙ узкий список, а не `guardrails._TIME_UNIT` ─────────
#
# `_TIME_UNIT` построен под другую задачу: он ищет ЧИСЛО в срочном контексте,
# и лишний матч там стоит одного лишнего сравнения. Здесь матч сам по себе
# становится флагом/красным, и `\bчас\w*` даёт «ЧАСтина», а `ма[йя]\w*` —
# «МАЙстру». Замер на реальном эталоне: сплошной обход по `_TIME_UNIT` дал 12
# срабатываний из 12 ложных. Поэтому окончания перечислены ЯВНО — тем же
# приёмом, которым сам `guardrails` лечил «рок»→«РОК-гурт» и «год»→«ГОДный».
_TIME_WORDS: tuple[str, ...] = (
    # uk
    "хвилина", "хвилини", "хвилину", "хвилин", "хвилинами",
    "година", "години", "годину", "годин", "годині", "годинами",
    "день", "дня", "дні", "днів", "дню", "днем", "днями", "дні",
    "доба", "доби", "добу", "діб", "добою",
    "тиждень", "тижня", "тижні", "тижнів", "тижнем", "тижнями",
    "місяць", "місяця", "місяці", "місяців", "місяцем",
    "рік", "року", "роки", "років", "роком", "роках",
    "час", "часу", "часи", "часів", "часом",
    # ru
    "минута", "минуты", "минут", "минуту",
    "часа", "часов", "часы",
    "дней", "дни",
    "неделя", "недели", "недель", "неделю", "неделями",
    "месяц", "месяца", "месяцев", "месяцы",
    "год", "года", "годов", "году", "лет",
    "сутки", "суток",
)
_TIME_WORD_RE = re.compile(
    r"\b(?:" + "|".join(sorted(set(_TIME_WORDS), key=len, reverse=True)) + r")\b",
    re.IGNORECASE)

# «Кто назовёт точное» (R9/C14): роль владельца ИЛИ глагол назначения.
_AUTHORITY_VERBS = re.compile(
    r"назива|назве|підтвердж|підтвердит|уточн|узгодж|погодж|порахує|рахує|"
    r"назыв|подтвержд|соглас|уточня", re.IGNORECASE)

# Отрицание сущности: пункт, который сам говорит «этого у нас нет».
_DENIAL_RE = re.compile(
    r"нема\b|немає|не знаємо|не називаємо|не вгадуємо|не даємо|не можна|"
    r"відсутн|не маємо|не знаем|не называем|отсутств", re.IGNORECASE)

# Предлоги длительности: «протягом години» — интервал, «точний час» — нет.
_DURATION_PREPS = re.compile(
    r"\b(протягом|впродовж|упродовж|межах|течение|течении|за|через)\b", re.IGNORECASE)


def _time_hits(text: str) -> list[re.Match]:
    return list(_TIME_WORD_RE.finditer(text or ""))


def _neighbour_words(text: str, start: int, end: int, count: int = 2) -> tuple[str, str]:
    """Окрестность единицы времени. Окно СПРАВА шире окна слева — намеренно.

    Слева число УПРАВЛЯЕТ единицей и стоит вплотную: «тривалість 1 день»,
    «5–10 робочих днів» — не больше двух слов между. Широкое левое окно съело бы
    ровно тот случай, ради которого C14 и написана: «тривалість 1 день плюс
    рекомендований час на полімеризацію» — «час» тут висит, хотя число в пункте
    есть.

    Справа число приходит после связки: «Робочий час студії — з 9:00 до 18:00».
    Узкое правое окно (три слова) дало на ручном эталоне ложное красное ровно
    на этой строке, где срок назван полностью.
    """
    before = " ".join((text[max(0, start - 40):start]).split()[-count:])
    after = text[end:end + 40]
    return before, after


def _has_interval(text: str) -> re.Match | None:
    """Интервал времени: единица времени ПРИ числе или при предлоге длительности.

    Голое «час» интервалом не считается намеренно: «точний час називає старший
    майстер» — это отказ называть срок, а не обещание за третье лицо, и C12,
    краснеющая на нём, была бы фоном, а не сторожем.
    """
    for m in _time_hits(text):
        before, _ = _neighbour_words(text, m.start(), m.end())
        if re.search(r"\d", before) or _DURATION_PREPS.search(before):
            return m
    return None


def _hanging_time_hits(text: str) -> list[re.Match]:
    """Единицы времени, при которых НЕТ числа ни до, ни после (R9).

    Число ищется по обе стороны намеренно. Слева стоит «тривалість 1 день»,
    справа — «Робочий час — з 9:00 до 18:00»: во втором случае срок назван
    полностью, и красное на нём было бы ложным.
    """
    out = []
    for m in _time_hits(text):
        before, after = _neighbour_words(text, m.start(), m.end())
        if re.search(r"\d", before) or re.search(r"\d", after):
            continue
        out.append(m)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Контекст прогона
# ─────────────────────────────────────────────────────────────────────────────

class _Ctx:
    """Всё, что читается один раз: файлы, конфиг, отчёт, пары примеров.

    Читаем ОДИН раз и держим ошибки чтения рядом со значениями: проверка,
    которая сама решает, что делать при отсутствии файла, обязана видеть
    причину, а не пустую строку (пустая строка неотличима от пустого файла).
    """

    def __init__(self, client_dir, report_document, *, slug: str):
        self.dir = Path(client_dir)
        self.slug = slug
        self.report = report_document if isinstance(report_document, dict) else None
        self.report_error: str | None = None
        if self.report is None:
            self.report_error = (
                f"report.json не передан или не словарь "
                f"(получено {type(report_document).__name__})")
        elif "sections" not in self.report:
            self.report, self.report_error = None, "в report.json нет ключа 'sections'"

        self.dir_error: str | None = None
        if not self.dir.is_dir():
            self.dir_error = f"каталог {self.dir} не существует или не читается"

        self.files: dict[str, str] = {}
        self.file_errors: dict[str, str] = {}
        if not self.dir_error:
            for name in CLIENT_FILES:
                path = self.dir / name
                try:
                    self.files[name] = path.read_text(encoding="utf-8")
                except OSError as exc:
                    self.file_errors[name] = str(exc)

        # Конфиг поднимаем ЖИВЫМ загрузчиком: C1 обязана падать ровно там же,
        # где упадёт старт раннера, и тем же текстом.
        self.config = None
        self.config_error: str | None = None
        if self.dir_error:
            self.config_error = self.dir_error
        else:
            try:
                self.config = load_config(self.dir.parent, self.dir.name)
            except ConfigError as exc:
                self.config_error = str(exc)
            except Exception as exc:                     # noqa: BLE001
                self.config_error = f"{type(exc).__name__}: {exc}"

        self._raw_settings: dict | None = None
        raw = self.files.get("settings.yaml")
        if raw:
            try:
                loaded = yaml.safe_load(raw)
                if isinstance(loaded, dict):
                    self._raw_settings = loaded
            except yaml.YAMLError:
                self._raw_settings = None

        # `brief.json` — вход C15. Читается ОТДЕЛЬНО от пяти файлов клиента:
        # это артефакт прогона, а не конфиг, и его отсутствие означает не
        # «клиент собран плохо», а «сверять числа не с чем» (rc «не состоялось»
        # РОВНО у C15, как отсутствующий отчёт — у C4 и C10).
        self.brief: dict | None = None
        self.brief_error: str | None = None
        if self.dir_error:
            self.brief_error = self.dir_error
        else:
            path = self.dir / BRIEF_FILENAME
            if not path.is_file():
                self.brief_error = f"{BRIEF_FILENAME} рядом с файлами нет"
            else:
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    self.brief_error = f"{BRIEF_FILENAME} не читается или не разбирается: {exc}"
                else:
                    if not isinstance(loaded, dict) or not isinstance(loaded.get("fields"), dict):
                        self.brief_error = (
                            f"{BRIEF_FILENAME}: ожидался словарь с ключом 'fields', "
                            f"получено {type(loaded).__name__}")
                    else:
                        self.brief = loaded

        self._units_cache: dict[str, list[_Unit]] = {}
        self.repo_root = _REPO_ROOT

    # -- файлы ---------------------------------------------------------------

    def text(self, name: str) -> str:
        return self.files.get(name, "")

    def units(self, name: str) -> list[_Unit]:
        if name not in self._units_cache:
            self._units_cache[name] = _units(self.text(name), name)
        return self._units_cache[name]

    # -- настройки (конфиг, иначе сырой YAML) --------------------------------

    def _setting(self, key: str, default=None):
        if self.config is not None:
            return getattr(self.config.settings, key, default)
        if self._raw_settings is not None:
            return self._raw_settings.get(key, default)
        return default

    @property
    def persona_name(self) -> str:
        return str(self._setting("persona_name") or "")

    @property
    def owner_id(self) -> str:
        return str(self._setting("owner_id") or "")

    @property
    def owner_ref(self) -> str:
        return str(self._setting("owner_ref") or "")

    @property
    def forbidden_terms(self) -> tuple[str, ...]:
        value = self._setting("forbidden_terms", ()) or ()
        return tuple(str(x) for x in value)

    @property
    def owner_stems(self) -> list[str]:
        """Корни ролевых слов владельца из ОБОИХ значений конфига.

        `owner_id` («Старший майстер») и `owner_ref` («нашим старшим майстром»)
        — два разных значения на одну роль, и текст файлов употребляет то одно,
        то другое, да ещё и в падежах. Ищем по корням обоих; служебные «нашим»
        и подобные отсеиваются длиной.
        """
        stems: list[str] = []
        for value in (self.owner_id, self.owner_ref):
            for word in _WORD_RE.findall(_unify(value)):
                if len(word) >= 5:
                    s = _stem(word)
                    if s not in stems:
                        stems.append(s)
        return stems

    def mentions_owner(self, text: str) -> bool:
        low = _unify(text)
        return any(re.search(rf"\b{re.escape(s)}", low) for s in self.owner_stems)

    # -- пары примеров -------------------------------------------------------

    @property
    def example_pairs(self) -> list[tuple[str, str]]:
        if self.config is not None:
            return [(c, o) for c, o in self.config.examples]
        raw = self.text("examples.yaml")
        if not raw:
            return []
        try:
            loaded = yaml.safe_load(raw)
        except yaml.YAMLError:
            return []
        if not isinstance(loaded, list):
            return []
        out = []
        for item in loaded:
            if isinstance(item, dict) and "client" in item and "olga" in item:
                out.append((str(item["client"]), str(item["olga"])))
        return out

    def example_pair_line(self, index: int) -> int | None:
        """Номер строки пары #index (1-based) в examples.yaml."""
        starts = [i for i, line in enumerate(self.text("examples.yaml").splitlines(), 1)
                  if re.match(r"^\s*-\s+client\s*:", line)]
        if 1 <= index <= len(starts):
            return starts[index - 1]
        return None

    # -- отчёт ---------------------------------------------------------------

    def report_rows(self, section: str) -> list[dict]:
        if self.report is None:
            return []
        rows = (self.report.get("sections") or {}).get(section) or []
        return [r for r in rows if isinstance(r, dict)]

    def report_counter(self, key: str):
        if self.report is None:
            return None
        return (self.report.get("counters") or {}).get(key)

    # -- бриф ----------------------------------------------------------------

    def brief_field(self, field_id: str) -> dict | None:
        if self.brief is None:
            return None
        field = (self.brief.get("fields") or {}).get(field_id)
        return field if isinstance(field, dict) else None


# ─────────────────────────────────────────────────────────────────────────────
# C1 — клиент поднимается
# ─────────────────────────────────────────────────────────────────────────────

def _c1(ctx: _Ctx) -> CheckResult:
    if ctx.config_error:
        # Текст ConfigError отдаётся КАК ЕСТЬ (спека §4): он уже называет файл и
        # ключ, а пересказ своими словами теряет ровно ту деталь, ради которой
        # загрузчик его и формулировал.
        return _red("C1", f"load_config упал: {ctx.config_error}", file="settings.yaml")
    cfg = ctx.config
    return _ok("C1", f"клиент '{cfg.slug}' поднимается: persona_name={cfg.settings.persona_name!r}, "
                     f"owner_id={cfg.settings.owner_id!r}, пар примеров {len(cfg.examples)}")


# ─────────────────────────────────────────────────────────────────────────────
# C2 — все цены прайса обеспечены
# ─────────────────────────────────────────────────────────────────────────────

def _price_units(ctx: _Ctx) -> list[_Unit]:
    """Пункты knowledge, которые САМИ являются ценовым утверждением."""
    out = []
    for u in ctx.units("knowledge.md"):
        if not _g._NUMBER.search(u.text):
            continue
        if not _g._PRICE_CONTEXT.search(u.text):
            continue
        out.append(u)
    return out


def _fragment_of(knowledge: str, number: str) -> str | None:
    """Фрагмент knowledge (так, как его режет `_context_numbers`), где живёт число."""
    for frag in re.split(r"[\n.!?;]", knowledge or ""):
        if number in {re.sub(r"\s", "", m.group()) for m in _g._NUMBER.finditer(frag)}:
            return frag.strip()
    return None


def _breaker_position(line: str) -> int | None:
    """Позиция `. ! ? ;` ВНУТРИ строки — того самого ножа, которым guardrail
    режет собственный прайс клиента. Точки ссылок не считаются: они часть
    адреса, а не пунктуация. Последний символ строки тоже не считается: он
    фрагмент завершает, а не разрезает."""
    spans = [m.span() for m in _URL_RE.finditer(line)]
    for i, ch in enumerate(line):
        if ch not in ".!?;":
            continue
        if i >= len(line.rstrip()) - 1:
            continue
        if any(s <= i < e for s, e in spans):
            continue
        return i + 1
    return None


def _is_wrapped(knowledge: str, unit: _Unit) -> bool:
    """Живёт ли пункт на нескольких физических строках файла."""
    return not any(unit.text in line for line in (knowledge or "").splitlines())


def _c2(ctx: _Ctx) -> CheckResult:
    knowledge = ctx.text("knowledge.md")
    if not knowledge.strip():
        return _red("C2", "knowledge.md пуст или не читается — обеспеченность цен "
                          "не проверена ни на одной строке", file="knowledge.md")
    units = _price_units(ctx)
    if not units:
        return _red("C2", "в knowledge.md нет ни одной ценовой строки — прайса нет, "
                          "и проверять нечего (это не «всё чисто»)", file="knowledge.md")
    dirty = [u for u in units if _g._findings(u.text, knowledge)]
    for u in dirty[:1]:
        findings = _g._findings(u.text, knowledge)
        f = findings[0]
        why = []
        frag = _fragment_of(knowledge, f.number) if f.number else None
        if f.number and frag is None:
            why.append("числа нет в knowledge вообще")
        elif frag is not None and not _g._PRICE_CONTEXT.search(frag):
            why.append("в фрагменте нет валюты")
        pos = _breaker_position(u.text)
        if pos is not None:
            why.append(f"точка на поз. {pos} разрезала строку")
        elif _is_wrapped(knowledge, u):
            # `_context_numbers` режет knowledge и по `\n` тоже, поэтому
            # предложение, перенесённое по ширине, теряет валюту ровно так же,
            # как от точки внутри строки. Бот при этом скажет его ОДНОЙ строкой —
            # и получит свой же прайс зарезанным.
            why.append("предложение перенесено по строкам, и перенос разрезал "
                       "фрагмент так же, как это сделала бы точка")
        if not why:
            why.append(f"число не попало в обеспеченное множество (rule={f.rule})")
        # Остаток называется вслух: владелец, починивший одну строку и увидевший
        # красное снова, обязан знать, что их было шесть, а не что фикс не помог.
        tail = f"; всего ценовых строк с findings: {len(dirty)}" if len(dirty) > 1 else ""
        return _red(
            "C2",
            f"knowledge.md:{u.line} «{u.text}» → {f.number or u.text[f.start:f.end]} "
            f"не обеспечено (rule={f.rule}): " + ", ".join(why) + tail,
            file="knowledge.md", line=u.line)
    return _ok("C2", f"{len(units)} ценовых строк knowledge.md прогнаны через "
                     f"guardrails._findings — findings пусто")


# ─────────────────────────────────────────────────────────────────────────────
# C3 — examples чисты
# ─────────────────────────────────────────────────────────────────────────────

def _c3(ctx: _Ctx) -> CheckResult:
    knowledge = ctx.text("knowledge.md")
    pairs = ctx.example_pairs
    if not pairs:
        return _red("C3", "examples.yaml не дал ни одной пары — голос персоны пуст "
                          "(лоадер молча отдаёт пустой кортеж, если файла нет)",
                    file="examples.yaml")
    for i, (_client, olga) in enumerate(pairs, 1):
        line = ctx.example_pair_line(i)
        findings = _g._findings(olga, knowledge)
        if findings:
            f = findings[0]
            near = _nearest_line(knowledge, f.number) if f.number else None
            tail = f"; ближайшая строка knowledge.md:{near}" if near else \
                   "; в knowledge такого числа нет вовсе"
            return _red("C3", f"examples.yaml пара #{i} → {f.number or olga[f.start:f.end]} "
                              f"(rule={f.rule}){tail}", file="examples.yaml", line=line)
        term = forbidden_mention(olga, ctx.forbidden_terms)
        if term:
            return _red("C3", f"examples.yaml пара #{i} → запрещённый термин «{term}» "
                              f"в ответе персоны (forbidden_mention)",
                        file="examples.yaml", line=line)
        promise = unbacked_promise(olga, knowledge, DEFAULT_PROMISE_TERMS)
        if promise:
            return _red("C3", f"examples.yaml пара #{i} → необеспеченное обещание "
                              f"«{promise}»: в knowledge этого утвердительно нет "
                              f"(unbacked_promise)", file="examples.yaml", line=line)
    return _ok("C3", f"{len(pairs)} пар примеров чисты: _findings пусто, "
                     f"forbidden_mention None, unbacked_promise None")


def _nearest_line(knowledge: str, number: str) -> int | None:
    for i, line in enumerate((knowledge or "").splitlines(), 1):
        if number in {re.sub(r"\s", "", m.group()) for m in _g._NUMBER.finditer(line)}:
            return i
    return None


# ─────────────────────────────────────────────────────────────────────────────
# C4 — слой эскалации жив
# ─────────────────────────────────────────────────────────────────────────────
#
# ⚠️ ПОЧЕМУ ПРОВЕРКА КОЛЛИЗИЙ СУЖЕНА. Спека требует: «ни одно слово не является
# подстрокой легального текста knowledge/examples». Сплошной обход на ручном
# эталоне даёт 13 красных из 35 слов — и все 13 ложные: эталон ДУБЛИРУЕТ список
# поводов эскалации разделом knowledge «Коли передаємо старшому майстру», плюс
# отдельным предложением «Питання повернення передоплати… вирішує старший
# майстер». Это не легальная самостоятельная речь бота, это ТО ЖЕ САМОЕ правило,
# записанное во втором файле; совпадение с ним ничего не говорит о том, поймает
# ли слово невинный вопрос лида.
#
# Поэтому из корпуса «легального текста» вычитаются:
#   1. раздел, чей ЗАГОЛОВОК объявляет передачу человеку («передаємо»,
#      «ескалація», «escalation») — раздел о хендовере по определению состоит из
#      поводов хендовера;
#   2. отдельный пункт, который САМ маршрутизирует тему на человека: упоминает
#      роль владельца И глагол назначения («вирішує старший майстер»).
# Замер: 13 ложных → 1 → 0 на эталоне, 0 → 0 на сгенерированном клиенте.
# Сигнал, всегда красный при законной работе, — это не сторож, а фон.

_HANDOVER_HEADINGS = ("передаєм", "передаем", "передач", "ескалац", "эскалац", "escalation")


def _legal_corpus(ctx: _Ctx) -> list[_Unit]:
    out = []
    for u in ctx.units("knowledge.md"):
        low = _unify(u.section)
        if any(marker in low for marker in _HANDOVER_HEADINGS):
            continue
        if ctx.mentions_owner(u.text) and _AUTHORITY_VERBS.search(u.text):
            continue
        out.append(u)
    for i, (client, olga) in enumerate(ctx.example_pairs, 1):
        line = ctx.example_pair_line(i) or 0
        out.append(_Unit("examples.yaml", line, f"пара #{i}", olga))
        out.append(_Unit("examples.yaml", line, f"пара #{i} (клиент)", client))
    return out


def _c4(ctx: _Ctx) -> CheckResult:
    playbook = ctx.text("playbook.md")
    keywords = parse_escalation_keywords(playbook)
    if not keywords:
        found = [m.group(2) for m in
                 (_HEADING_RE.match(l) for l in playbook.splitlines()) if m]
        return _red("C4", "слой пуст — заголовок секции не из _KEYWORD_HEADINGS "
                          f"({list(_KEYWORD_HEADINGS)}); заголовки playbook.md: "
                          f"{found}", file="playbook.md")

    if ctx.report is None:
        return _blocked("C4", f"{ctx.report_error} — сверить список со списком в "
                              f"отчёте нечем (слой жив: {len(keywords)} слов)",
                        file="playbook.md")
    counter = ctx.report_counter("stop_words")
    if counter is None:
        return _red("C4", f"в playbook.md {len(keywords)} ключевых слов, а счётчик "
                          f"stop_words в отчёте не посчитан (None) — сверка списка "
                          f"со списком отчёта не состоялась", file="playbook.md")
    if int(counter) != len(keywords):
        return _red("C4", f"список разошёлся с отчётом: playbook.md даёт "
                          f"{len(keywords)} слов, отчёт считает {counter}",
                    file="playbook.md")

    corpus = _legal_corpus(ctx)
    for kw in keywords:
        needle = _unify(kw)
        if not needle:
            continue
        for u in corpus:
            if needle in _unify(u.text):
                return _red("C4", f"«{kw}» матчит {u.file}:{u.line} «{u.text}» — "
                                  f"ключевое слово сидит внутри легального текста и "
                                  f"будет звать человека на пустом месте",
                            file=u.file, line=u.line)
    return _ok("C4", f"слой эскалации жив: {len(keywords)} слов, список сходится с "
                     f"отчётом, коллизий с легальным текстом нет")


# ─────────────────────────────────────────────────────────────────────────────
# C5 — нет самоотравления brand-safety
# ─────────────────────────────────────────────────────────────────────────────

def _c5(ctx: _Ctx) -> CheckResult:
    terms = ctx.forbidden_terms
    if not terms:
        return _red("C5", "forbidden_terms пуст — слой brand-safety выключен, и "
                          "проверять самоотравление не на чем", file="settings.yaml")
    for name in ("persona.md", "knowledge.md", "playbook.md", "examples.yaml"):
        text = ctx.text(name)
        term = forbidden_mention(text, terms)
        if term:
            line = next((i for i, l in enumerate(text.splitlines(), 1)
                         if term.casefold() in l.casefold()), None)
            return _red("C5", f"«{term}» встречается в {name}:{line} — собственный "
                              f"файл клиента отравлен запрещённым термином: любой "
                              f"ответ, процитировавший эту строку, будет подавлен",
                        file=name, line=line)
    return _ok("C5", f"самоотравления нет: {len(terms)} терминов не встречаются "
                     f"ни в persona/knowledge/playbook/examples")


# ─────────────────────────────────────────────────────────────────────────────
# C6 — persona
# ─────────────────────────────────────────────────────────────────────────────

def _c6(ctx: _Ctx) -> CheckResult:
    persona = ctx.text("persona.md")
    first_line, first_no = None, None
    for i, line in enumerate(persona.splitlines(), 1):
        if line.strip():
            first_line, first_no = line.strip(), i
            break
    if not first_line:
        return _red("C6", "persona.md пуст — `disclosure`/`honest_prefix` отдадут "
                          "лиду пустую строку на вопрос «ты бот?»", file="persona.md")
    if first_line.startswith("#"):
        return _red("C6", f"persona.md:{first_no} первая строка — заголовок "
                          f"«{first_line}»: он уехал бы в чат целиком, `#` и всё",
                    file="persona.md", line=first_no)
    name = ctx.persona_name
    if not name:
        return _red("C6", "persona_name в settings.yaml пуст — сверить первую строку "
                          "персоны не с чем", file="settings.yaml")
    if _unify(name) not in _unify(first_line):
        return _red("C6", f"persona.md:{first_no} первая строка не называет персону "
                          f"«{name}»: «{first_line}»", file="persona.md", line=first_no)
    return _ok("C6", f"persona.md:{first_no} проза и называет «{name}»: «{first_line}»",
               file="persona.md", line=first_no)


# ─────────────────────────────────────────────────────────────────────────────
# C7 — дрил-сценарий
# ─────────────────────────────────────────────────────────────────────────────

def _scripts_drill_contacts(repo_root: Path) -> frozenset[str] | None:
    """Второй список DRILL_CONTACTS — из `scripts/drill_reset.py`, БЕЗ импорта.

    Читаем исходник через `ast`: модуль скриптов исполнять ради константы
    нельзя (у него свои побочные эффекты), а прод-пакет не импортирует
    `scripts/` по направлению зависимости. Списка два, и спека требует, чтобы
    контакт был в ОБОИХ: одна копия из трёх уже отставала месяц.
    """
    path = repo_root / "scripts" / "drill_reset.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(getattr(t, "id", None) == "DRILL_CONTACTS" for t in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and value.args:
            value = value.args[0]
        try:
            return frozenset(ast.literal_eval(value))
        except (ValueError, TypeError, SyntaxError):
            return None
    return None


def _drill_scenarios(ctx: _Ctx) -> tuple[list[Path], str]:
    """Найденные сценарии И ОТКУДА они взяты: `build` или `manual`.

    DEV-36: происхождение возвращается рядом с путями, а не восстанавливается
    потом по имени файла. Отбор в фолбэке идёт по ПРЕФИКСУ слага, поэтому
    «файл называется как наш» и «файл собран для нас» — разные утверждения, и
    сложить их обратно из одного лишь пути нельзя.
    """
    local = [ctx.dir / "drill.yaml", ctx.dir / "drill_scenario.yaml",
             ctx.dir / f"{ctx.slug}-drill.yaml", ctx.dir / f"drill_{ctx.slug}.yaml"]
    found = [p for p in local if p.is_file()]
    if found:
        return found, "build"
    # Ручной эталон держит сценарии не в каталоге клиента, а в `docs/chatter/
    # drills/`. Ищем и там: иначе калибровка §6 краснела бы на файле, который
    # существует и отработал живой дрил.
    drills = ctx.repo_root / "docs" / "chatter" / "drills"
    if not drills.is_dir():
        return [], "manual"
    return sorted(drills.glob(f"{ctx.slug}*.yaml")), "manual"


def _c7(ctx: _Ctx) -> CheckResult:
    paths, origin = _drill_scenarios(ctx)
    if not paths:
        return _red("C7", f"дрил-сценария нет ни в {ctx.dir}, ни в "
                          f"docs/chatter/drills/{ctx.slug}*.yaml — сценарий не "
                          f"разбирается, потому что его нет")
    second = _scripts_drill_contacts(ctx.repo_root)
    for path in paths:
        name = path.name
        try:
            scenario = parse_scenario(path.read_text(encoding="utf-8"))
        except (DrillScenarioError, OSError) as exc:
            return _red("C7", f"{name}: сценарий не разбирается — {exc}", file=name)
        # `before` пуст намеренно: живой БД у автоприёмки нет и быть не должно
        # (§4 «чего автоприёмка НЕ делает»). Эта нога доказывает, что ни одно
        # ожидание не самоподтверждается на ЧИСТОМ слоте; самоподтверждение по
        # осадку прошлого прогона ловит уже `drill_runner` перед стартом.
        vacuous = vacuous_expectations(scenario, {})
        if vacuous:
            return _red("C7", f"{name}: самоподтверждающиеся ожидания — "
                              f"{'; '.join(vacuous)}", file=name)
        contact = scenario.contact
        expected_suffix = f":{ctx.slug}"
        if not contact:
            return _red("C7", f"{name}: contact пуст — дрил пошёл бы по неизвестно "
                              f"какому контакту", file=name)
        if not contact.endswith(expected_suffix):
            return _red("C7", f"{name}: contact «{contact}» не вида <id>{expected_suffix} "
                              f"— суффикс это ПЕРСОНА, и чужой суффикс уводит прогон "
                              f"к другому клиенту", file=name)
        if contact not in PROD_DRILL_CONTACTS:
            return _red("C7", f"{name}: контакт «{contact}» не в "
                              f"payments/drill_gate.DRILL_CONTACTS "
                              f"({sorted(PROD_DRILL_CONTACTS)}) — тестовые активы ему "
                              f"не выдадут", file=name)
        if second is None:
            return _red("C7", f"{name}: вторую копию DRILL_CONTACTS "
                              f"(scripts/drill_reset.py) прочитать не удалось — "
                              f"сверка двух списков не состоялась", file=name)
        if contact not in second:
            return _red("C7", f"{name}: контакт «{contact}» есть в drill_gate, но нет "
                              f"в scripts/drill_reset.py ({sorted(second)}) — копии "
                              f"канона разошлись", file=name)
    names = ", ".join(p.name for p in paths)
    if origin != "build":
        # DEV-36. Сценарий разобран, ожидания не вакуумны, контакт законный —
        # придраться НЕ К ЧЕМУ, и всё же зелёным это быть не может: файл нашли
        # по совпадению имени со слагом, а не потому, что его собрал пайплайн.
        # Ночью 17.08 так зеленел сперва чужой `drill.yaml` из каталога сборки,
        # потом ручной эталон месячной давности под другой прайс.
        #
        # Поэтому ФЛАГ, а не красное (решение владельца по C12/C13 тем же
        # рассуждением): ручной эталон — законное состояние, и красное здесь
        # значило бы, что пайплайн знает про клиента больше владельца. Но и
        # зелёным он не притворяется: живой дрил стоит денег и времени
        # человека, и «по какому файлу» обязано стоять в отчёте, а не в
        # памяти того, кто собирал.
        return CheckResult(
            "C7", False, True,
            f"сценарий взят из docs/chatter/drills/ ({names}): это РУЧНОЙ "
            f"сценарий, а не выход пайплайна — его нашли по совпадению имени "
            f"со слагом «{ctx.slug}». Разбор чистый: ожидания не вакуумны, "
            f"контакт есть в обоих списках DRILL_CONTACTS. Проверить глазами, "
            f"что он про ЭТУ сборку (услуги, прайс, контакт), прежде чем "
            f"звать человека на живой прогон",
            paths[0].name, None)
    return _ok("C7", f"сценариев разобрано {len(paths)} "
                     f"({names}): ожидания не вакуумны, "
                     f"контакт есть в обоих списках DRILL_CONTACTS")


# ─────────────────────────────────────────────────────────────────────────────
# C8 — формат-гигиена
# ─────────────────────────────────────────────────────────────────────────────

def _c8(ctx: _Ctx) -> CheckResult:
    # 1. Невидимые пробелы во всех пяти файлах.
    for name in CLIENT_FILES:
        for i, line in enumerate(ctx.text(name).splitlines(), 1):
            for pos, ch in enumerate(line, 1):
                if ch in _INVISIBLE_NAMES:
                    return _red("C8", f"{name}:{i} {_INVISIBLE_NAMES[ch]} на поз. {pos} "
                                      f"— человек его не видит, а сравнение по "
                                      f"подстроке и `str.split()` видят",
                                file=name, line=i)
    # 2. Точка внутри ценовой строки — по ФИЗИЧЕСКИМ строкам файла, а не по
    # «пунктам». R1 — правило о том, как строка НАПИСАНА, и `_context_numbers`
    # режет knowledge ровно по этим же символам. Разбор по предложениям здесь
    # слеп по конструкции: он сам режет строку по точке и потом честно
    # докладывает, что точки внутри нет.
    for i, line in enumerate(ctx.text("knowledge.md").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if not (_g._NUMBER.search(s) and _g._PRICE_CONTEXT.search(s)):
            continue
        pos = _breaker_position(s)
        if pos is not None:
            return _red("C8", f"knowledge.md:{i} «{s}» — разделитель предложения на "
                              f"поз. {pos} внутри ценовой строки: фрагмент рвётся, и "
                              f"цена теряет валюту", file="knowledge.md", line=i)
    # 3. Один десятичный разделитель на файл.
    for name in CLIENT_FILES:
        text = ctx.text(name)
        # ДАТЫ И ВРЕМЯ — не десятичные дроби. «Остання дата дії акції —
        # 30.09.2026» дало ложное красное на ручном эталоне: точка там
        # разделяет день и месяц, а не целую и дробную часть, и требовать от
        # клиента писать дату запятой — это проверка, спорящая с календарём.
        skip = [m.span() for m in _URL_RE.finditer(text)]
        skip += [m.span() for m in _DATE_RE.finditer(text)]
        seen: dict[str, int] = {}
        for m in re.finditer(r"\d([.,])\d", text):
            if any(s <= m.start() < e for s, e in skip):
                continue
            seen.setdefault(m.group(1), text.count("\n", 0, m.start()) + 1)
        if len(seen) > 1:
            where = ", ".join(f"«{sep}» на строке {ln}" for sep, ln in sorted(seen.items()))
            return _red("C8", f"{name}: два десятичных разделителя в одном файле "
                              f"({where}) — `_numbers` снимает только пробелы, "
                              f"поэтому «1,5» и «1.5» это РАЗНЫЕ токены",
                        file=name)
    # 4. Бюджет примеров.
    pairs = ctx.example_pairs
    used, dropped = 0, 0
    for client, olga in pairs:
        chunk = f"\nКлієнт: {client}\nТи: {olga}\n"
        if used + len(chunk) > EXAMPLES_CHAR_BUDGET:
            dropped += 1
            continue
        used += len(chunk)
    if dropped:
        return _red("C8", f"examples.yaml: {dropped} пар(ы) из {len(pairs)} не влезают "
                          f"в brain.EXAMPLES_CHAR_BUDGET={EXAMPLES_CHAR_BUDGET} "
                          f"(занято {used}) — brain отбросит их, и голос персоны "
                          f"тихо поедет", file="examples.yaml")
    return _ok("C8", f"гигиена чиста: невидимых пробелов нет, точек внутри ценовых "
                     f"строк нет, разделитель один на файл, примеры {used} симв. из "
                     f"{EXAMPLES_CHAR_BUDGET}")


# ─────────────────────────────────────────────────────────────────────────────
# C9 — маршрут ICP соблюдён
# ─────────────────────────────────────────────────────────────────────────────

def _playbook_fragments(ctx: _Ctx) -> tuple[list[tuple[str, str]], str]:
    """([(field_id, фрагмент)], каким источником взято) для полей `target: playbook`.

    Источник по убыванию точности: `brief.json` рядом с файлами (полное
    значение поля) → раздел 1 отчёта (цитата, обрезанная до 200 символов).
    Второй источник беднее, и это названо вслух в сообщении проверки: молча
    проверить четверть текста и сказать «чисто» — та же зелёная ширма.

    Бриф читает `_Ctx` — один раз на прогон и одним способом. Своя вторая
    попытка открыть тот же файл жила здесь до C15: она молча возвращала пустоту
    на битом JSON и всё равно печатала «источник: brief.json», то есть отчёт о
    прогоне называл не тот источник, которым прогон пользовался.
    """
    out: list[tuple[str, str]] = []
    values: list[tuple[str, str]] = []
    source = BRIEF_FILENAME
    if ctx.brief is not None:
        for fid, field in (ctx.brief.get("fields") or {}).items():
            if not isinstance(field, dict) or field.get("target") != "playbook":
                continue
            values.append((fid, str(field.get("value") or field.get("raw") or "")))
    if not values:
        source = "раздел 1 отчёта (цитаты обрезаны до 200 симв.)"
        for row in ctx.report_rows("taken"):
            if row.get("target_file") != "playbook.md":
                continue
            values.append((str(row.get("field_id") or "?"), str(row.get("quote") or "")))

    for fid, value in values:
        if fid in DUAL_PURPOSE_FIELDS:
            # Q38 — поле ДВОЙНОГО назначения (решение владельца 17.08). Полный
            # текст едет в playbook, раздел knowledge «Чого ми не робимо»
            # собирается ИЗ НЕГО же. Маршрут по источнику здесь законно двойной,
            # и C9 обязана это знать: иначе она красная на КАЖДОМ клиенте.
            continue
        for piece in re.split(r"\n|\s\|\s|(?<=[.!?;])\s+", value):
            # Хвостовая пунктуация снимается ОБЯЗАТЕЛЬНО: генератор переносит
            # текст поля в playbook пунктом списка и точку в конце не ставит, а
            # бриф её ставит. Фрагмент, сравниваемый вместе с точкой, не
            # находится нигде — и C9 зеленеет ровно на том тексте, который она
            # обязана ловить (проверено мутацией: ICP, вручную положенный в
            # knowledge, не находился).
            frag = _unify(piece).strip(" -•.,;:!?…")
            if len(frag) >= C9_MIN_FRAGMENT:
                out.append((fid, frag))
    return out, source


def _c9(ctx: _Ctx) -> CheckResult:
    fragments, source = _playbook_fragments(ctx)
    if not fragments:
        return _red("C9", "не нашлось ни одного фрагмента полей с target=playbook "
                          "(ни brief.json рядом с файлами, ни строк раздела 1 отчёта) "
                          "— маршрут ICP не проверен ни на чём")
    knowledge = ctx.text("knowledge.md")
    flat = _unify(knowledge)
    for fid, frag in fragments:
        if frag not in flat:
            continue
        line = None
        for u in ctx.units("knowledge.md"):
            if frag[:60] in _unify(u.text):
                line = u.line
                break
        return _red("C9", f"{fid} → knowledge.md:{line}: «{frag[:80]}…» — поле с "
                          f"target=playbook доехало в knowledge, откуда бот говорит "
                          f"ЛИДУ; ICP/анти-ICP/ЦА едут только в playbook",
                    file="knowledge.md", line=line)
    return _ok("C9", f"{len(fragments)} фрагментов ≥{C9_MIN_FRAGMENT} симв. полей "
                     f"target=playbook в knowledge.md не встречаются "
                     f"(источник: {source}; Q38 исключён как поле двойного назначения)")


# ─────────────────────────────────────────────────────────────────────────────
# C10 — обязательные факты закрыты
# ─────────────────────────────────────────────────────────────────────────────

def _unknown_section_units(ctx: _Ctx) -> list[_Unit]:
    """Пункты раздела «Чого ми НЕ знаємо». Пункты списка, а не вводная фраза.

    В обоих вариантах — и в сгенерированном, и в ручном — раздел устроен
    одинаково: абзац-предисловие («Цих даних у нас немає — вигадувати їх не
    можна») и список сущностей. Предисловие сущностью не является; C13,
    принявшая его за сущность, ищет в файле фразу, которой клиент не называл, и
    находит её же — то есть даёт флаг на саму себя.
    """
    want = _unify(UNKNOWN_SECTION_UK)
    units = [u for u in ctx.units("knowledge.md") if _unify(u.section) == want]
    bullets = [u for u in units if u.bullet]
    return bullets or units


def _stub_for(fact, stubs: list[_Unit]) -> tuple[_Unit | None, bool]:
    """(пункт-заглушка, по формуле ли словаря).

    Две ступени, и вторая нужна не для красоты. Первая — ОБЩАЯ формула словаря
    (`STUB_TEMPLATE_UK`), и она единственно верна для выхода генератора: своя
    копия формулы здесь ослепила бы C10 ровно в день, когда формулу поправят в
    одном месте. Но `--check` ходит и по каталогу, собранному РУКАМИ (спека §6),
    где та же заглушка написана человеческим языком: «Адреса студії, орієнтир,
    паркування та умови під'їзду». Требовать от неё формулу значит краснеть на
    файле, который отработал живой дрил, — то есть спорить с фактом.

    Поэтому вторая ступень: пункт раздела «чего не знаем», в котором есть ВСЕ
    корни первой фразы названия факта. Отступление от формулы при этом не
    проглатывается — оно называется вслух в сообщении проверки.
    """
    prefix = _unify(STUB_TEMPLATE_UK.format(title=fact.title_uk, owner="")).strip()
    for u in stubs:
        if _unify(u.text).startswith(prefix):
            return u, True
    head = fact.title_uk.split(",")[0].strip()
    stems = _phrase_stems(head)
    for u in stubs:
        if _contains_all_stems(u.text, stems):
            return u, False
    return None, False


def _c10(ctx: _Ctx) -> CheckResult:
    if ctx.report is None:
        return _blocked("C10", f"{ctx.report_error} — парность «заглушка ⇔ строка "
                               f"раздела 3» проверить нечем")
    stubs = _unknown_section_units(ctx)
    missing_rows = ctx.report_rows("missing")
    taken_ids = {str(r.get("field_id")) for r in ctx.report_rows("taken")}
    by_field: list[str] = []
    off_formula: list[str] = []

    for fact in REQUIRED_FACTS:
        stub_unit, canonical = _stub_for(fact, stubs)
        has_row = any(r.get("fact_id") == fact.id for r in missing_rows)
        covered = bool(fact.source) and fact.source in taken_ids

        if stub_unit is not None:
            if not has_row:
                return _red("C10", f"факт «{fact.title_uk}»: заглушка в knowledge.md:"
                                   f"{stub_unit.line} есть, а строки в разделе 3 отчёта "
                                   f"нет — владелец не узнает, что спросить у клиента",
                            file="knowledge.md", line=stub_unit.line)
            if not canonical:
                off_formula.append(f"{fact.id}@knowledge.md:{stub_unit.line}")
            continue
        if covered:
            by_field.append(fact.id)
            continue
        return _red("C10", f"факт «{fact.title_uk}» ({fact.id}): ни данных "
                           f"(источник {fact.source or 'в форме отсутствует'}), ни "
                           f"заглушки в разделе «{UNKNOWN_SECTION_UK}» — бот выдумает",
                    file="knowledge.md")

    # ⚠️ ИЗВЕСТНАЯ ГРАНИЦА, названная вслух. «Поле отвечено» и «факт закрыт» —
    # разные вещи: `days_off` берётся из `q12_hours`, где стоит «9-18», и часы
    # там есть, а выходных нет. Решение «поле не закрывает факт» принимает
    # ГЕНЕРАТОР (он ставит заглушку), и C10 проверяет ПОСЛЕДСТВИЕ этого решения
    # — парность «заглушка ⇔ строка отчёта». Проверить смысл ответа она не может
    # и не пытается: это ровно то суждение, которое спека §5 оставила человеку.
    tail = f"; заглушки не по формуле словаря: {off_formula}" if off_formula else ""
    return _ok("C10", f"все {len(REQUIRED_FACTS)} обязательных фактов закрыты: "
                      f"{len(REQUIRED_FACTS) - len(by_field)} заглушкой + строкой "
                      f"раздела 3, {len(by_field)} полем брифа ({by_field}) — по "
                      f"последним проверено, что ПОЛЕ отвечено, а не что ФАКТ закрыт "
                      f"по смыслу{tail}")


# ─────────────────────────────────────────────────────────────────────────────
# C11 — обязательные разделы на месте
# ─────────────────────────────────────────────────────────────────────────────

def _section_span(text: str, title: str) -> list[tuple[int, str, bool]]:
    """Строки раздела `title`: [(номер строки, текст, это ли заголовок)].

    Раздел кончается на заголовке ТОГО ЖЕ ИЛИ БОЛЕЕ ВЫСОКОГО уровня; более
    глубокий — его собственная часть (почему именно так — см. `_section_has_content`,
    который на этом обходе и построен). Пустой список означает «раздела нет».

    Обход ОДИН на две проверки (C11 и C15) намеренно: «где кончается раздел» —
    это ровно то правило, две копии которого разъезжаются молча, и вторая копия
    зеленела бы, спрашивая не про тот кусок файла.
    """
    target = _unify(title)
    level: int | None = None
    out: list[tuple[int, str, bool]] = []
    for i, line in enumerate(_strip_html_comments(text or "").splitlines(), 1):
        head = _HEADING_RE.match(line)
        if head:
            depth = len(head.group(1))
            if level is None:
                if _unify(head.group(2)) == target:
                    level = depth
                continue
            if depth <= level:
                break
            out.append((i, head.group(2), True))
            continue
        if level is not None:
            out.append((i, line, False))
    return out


def _section_has_content(text: str, title: str) -> bool:
    """Есть ли в разделе хоть одна содержательная строка — С УЧЁТОМ ПОДРАЗДЕЛОВ.

    Считать иначе нельзя, и это не мелочь. `_units` приписывает строку
    БЛИЖАЙШЕМУ заголовку любого уровня, поэтому раздел, который целиком состоит
    из подразделов, выглядит пустым:

        # Послуги та ціни        ← по прежнему счёту «пусто»
        ## 1. Детейлінг-мийка
        - Ціна 1 200–2 000 грн   ← а вот и содержимое, и оно ГЛАВНОЕ в файле

    Именно так генератор и пишет прайс — заголовок, сразу подразделы. То есть
    C11 краснела бы на каждом клиенте, у которого прайс оформлен списком услуг.

    Ручной эталон проходил проверку ПО СЛУЧАЙНОСТИ: между «# Послуги та ціни» и
    первым «## 1. …» там оказался вводный абзац из трёх строк. Убери его — и
    калибровка §6 покраснела бы на файле, отработавшем живой дрил. Зелёное
    означало не «правильно», а «повезло с вёрсткой».

    Раздел кончается на заголовке ТОГО ЖЕ ИЛИ БОЛЕЕ ВЫСОКОГО уровня; более
    глубокий — это его собственная часть.

    Заголовок подраздела содержательной строкой НЕ считается: раздел, целиком
    состоящий из пустых подразделов, — это пустой раздел.
    """
    return any(line.strip()
               for _, line, is_heading in _section_span(text, title)
               if not is_heading)


def _c11(ctx: _Ctx) -> CheckResult:
    sections = _sections(ctx.text("knowledge.md"))
    # Все недостающие разделы разом, а не первый: их собирают человек и
    # генератор из разных полей, и узнать о втором пропущенном на следующем
    # прогоне — это ещё один прогон.
    headings = _headings(ctx.text("knowledge.md"))
    absent = [t for t in REQUIRED_SECTIONS_UK if _unify(t) not in headings]
    knowledge = ctx.text("knowledge.md")
    empty_titles = [t for t in REQUIRED_SECTIONS_UK
                    if _unify(t) in headings and not _section_has_content(knowledge, t)]
    empty = [f"{t} (knowledge.md:{headings[_unify(t)]})" for t in empty_titles]
    if absent or empty:
        parts = []
        if absent:
            parts.append("нет разделов: " + ", ".join(f"«{t}»" for t in absent))
        if empty:
            parts.append("пусты (заголовок есть, содержательной строки нет, а пустой "
                         "заголовок считается отсутствующим разделом): "
                         + ", ".join(f"«{t}»" for t in empty))
        # Строка обязательна: `file` без `line` — это ПОЛОВИНА адреса, и она
        # хуже отсутствующего, потому что выглядит названным местом. Для
        # пустого раздела указываем его заголовок, для отсутствующего — конец
        # файла: туда его и дописывать.
        line = (headings[_unify(empty_titles[0])] if empty_titles
                else max(1, len((knowledge or "").splitlines())))
        return _red("C11", "knowledge.md — " + "; ".join(parts),
                    file="knowledge.md", line=line)
    return _ok("C11", f"все {len(REQUIRED_SECTIONS_UK)} обязательных разделов на "
                      f"месте и непусты")


# ─────────────────────────────────────────────────────────────────────────────
# C12 ⚠️ — обещание за третье лицо (ФЛАГ)
# ─────────────────────────────────────────────────────────────────────────────

def _c12(ctx: _Ctx) -> CheckResult:
    if not ctx.owner_stems:
        return _red("C12", "owner_id/owner_ref в settings.yaml пусты — роль владельца "
                           "искать нечем", file="settings.yaml")
    hits: list[tuple[_Unit, str]] = []
    for u in ctx.units("knowledge.md"):
        if not ctx.mentions_owner(u.text):
            continue
        interval = _has_interval(u.text)
        if interval is not None:
            hits.append((u, interval.group()))
    for i, (_client, olga) in enumerate(ctx.example_pairs, 1):
        if not ctx.mentions_owner(olga):
            continue
        interval = _has_interval(olga)
        if interval is not None:
            hits.append((_Unit("examples.yaml", ctx.example_pair_line(i) or 0,
                               f"пара #{i}", olga), interval.group()))
    if not hits:
        return _ok("C12", "обещаний за третье лицо не найдено (роль владельца в одном "
                          "предложении с интервалом времени)")
    u, word = hits[0]
    more = f" (+ ещё {len(hits) - 1})" if len(hits) > 1 else ""
    return _red("C12", f"⚠️ {u.file}:{u.line} «{u.text}» — обещание за человека "
                       f"(интервал «{word}» рядом с ролью владельца), законно или "
                       f"нет решает владелец{more}", file=u.file, line=u.line)


# ─────────────────────────────────────────────────────────────────────────────
# C13 ⚠️ — сущность и утверждается, и «не знаем» (ФЛАГ)
# ─────────────────────────────────────────────────────────────────────────────

def _c13(ctx: _Ctx) -> CheckResult:
    unknown = _unknown_section_units(ctx)
    if not unknown:
        return _ok("C13", f"раздела «{UNKNOWN_SECTION_UK}» нет или он пуст — "
                          f"противопоставлять нечего (наличие раздела проверяет C11)")
    want = _unify(UNKNOWN_SECTION_UK)
    # УТВЕРЖДАЮЩИЕ разделы — те, где сущность именно утверждается. Пункт, который
    # сам отрицает («Точну адресу називає старший майстер — у базі цих даних поки
    # немає»), — это то же «не знаємо», записанное в другом разделе, а не вторая
    # правда о сущности. Флаг на нём говорит владельцу «ты написал это дважды»,
    # а не «у тебя противоречие», и в блоке ФЛАГИ он чистый фон.
    asserting = [u for u in ctx.units("knowledge.md")
                 if _unify(u.section) != want and not _DENIAL_RE.search(u.text)]
    hits: list[tuple[str, _Unit, _Unit]] = []
    for u in unknown:
        # Ключ-сущность — ФРАЗА, а не слово. Пункт «Телефон студії, посилання на
        # сайт чи будь-яку форму — цих контактів немає» перечисляет НЕСКОЛЬКО
        # сущностей, и «сайт» в нём не первая: брать только первую фразу значит
        # пропустить ровно пример спеки («сайт»: одна строка даёт ссылку, другая
        # говорит «посилань немає»).
        #
        # Но и словами по отдельности брать нельзя: «орієнтир» из «Адреса,
        # орієнтир, паркування» матчит легальное «даємо орієнтир по вартості» —
        # один корень, разные смыслы. Поэтому фраза требует ВСЕХ своих корней в
        # одном пункте: «канал запису» не матчит «підтвердження запису».
        #
        # Режем ТОЛЬКО по запятой. Запятая в этих пунктах разделяет РАВНЫХ
        # («Адреса, орієнтир, паркування»), а «або»/«чи» — уточняют одну
        # сущность: «строк служби керамічного покриття АБО PPF-плівки» после
        # разреза даёт голое «PPF-плівки», которое матчит каждое упоминание
        # услуги. Замер на ручном эталоне: с разрезом по «або/чи» — 5 флагов, все
        # ложные; без него — ни одного.
        head = u.text.split(" — ")[0]
        for phrase in head.split(","):
            stems = _phrase_stems(phrase)
            if not stems:
                continue
            other = next((o for o in asserting if _contains_all_stems(o.text, stems)), None)
            if other is not None:
                hits.append((phrase.strip(), u, other))
                break
    if not hits:
        return _ok("C13", f"{len(unknown)} пунктов «{UNKNOWN_SECTION_UK}» не находят "
                          f"себе пары в утверждающих разделах")
    head, u, other = hits[0]
    more = f" (+ ещё {len(hits) - 1})" if len(hits) > 1 else ""
    return _red("C13", f"⚠️ «{head}»: knowledge.md:{other.line} утверждает «{other.text}», "
                       f"knowledge.md:{u.line} говорит «{u.text}» — одна сущность или "
                       f"разные, решает владелец{more}",
                file="knowledge.md", line=other.line)


# ─────────────────────────────────────────────────────────────────────────────
# C14 — висящий срок
# ─────────────────────────────────────────────────────────────────────────────

def _c14(ctx: _Ctx) -> CheckResult:
    units = ctx.units("knowledge.md")
    for i, u in enumerate(units):
        hanging = _hanging_time_hits(u.text)
        if not hanging:
            continue
        neighbourhood = [u]
        if i + 1 < len(units):
            neighbourhood.append(units[i + 1])
        if any(_AUTHORITY_VERBS.search(n.text) or ctx.mentions_owner(n.text)
               for n in neighbourhood):
            continue
        m = hanging[0]
        return _red("C14", f"knowledge.md:{u.line} «{u.text}» — единица времени "
                           f"«{m.group()}» без числа, и ни в этом, ни в следующем "
                           f"пункте не сказано, кто назовёт точное; числовой "
                           f"guardrail здесь бессилен по конструкции: числа нет",
                    file="knowledge.md", line=u.line)
    total = sum(len(_hanging_time_hits(u.text)) for u in units)
    return _ok("C14", f"висящих сроков нет: {total} упоминаний времени без числа, и у "
                      f"каждого рядом назван тот, кто назовёт точное")


# ─────────────────────────────────────────────────────────────────────────────
# C15 — числа knowledge совпадают с числами брифа
# ─────────────────────────────────────────────────────────────────────────────
#
# Спека: `docs/superpowers/specs/2026-08-17-c15-numbers-must-match-the-brief.md`.
# Дыра, которую C15 закрывает, видна на C12: «відповідає протягом години» и
# «відповідає протягом трьох годин» неотличимы для всех четырнадцати прежних
# проверок — они смотрят ФОРМУ. Лид ждёт час, человек отвечает через три, и
# виноват бот, который «пообещал».
#
# C15 — КРАСНОЕ, а не флаг (спека §4): флаг означает «законно или нет, решает
# владелец», а здесь решать нечего — число либо то, либо не то.
#
# ── КАКОЙ ВОПРОС ЗАДАЁТСЯ И КАКОЙ НЕ ЗАДАЁТСЯ ───────────────────────────────
# Задаётся: «число, которое стоит в собранном файле НА МЕСТЕ этого поля брифа,
# — то же, что в поле?» Направление сверки одно, и оно не симметрично:
#
#   * число в РАЗДЕЛЕ файла, которого нет в поле брифа  → КРАСНОЕ (разъехалось);
#   * число в ПОЛЕ БРИФА, которого нет в разделе файла  → НЕ красное.
#
# Второе — прямое требование спеки §3 («не требуем полноты в обратную сторону»)
# и сторож C15-4: пайплайн сознательно не выпускает наружу часть ответов, и они
# уезжают в раздел 3 отчёта. Проверка, требующая полноты, краснела бы на каждом
# законном прогоне — то есть была бы фоном, а не сторожем.
#
# НЕ задаётся: «верен ли этот срок по жизни» — на это отвечает вопрос клиенту
# (`vocabulary.SLA_REALITY_QUESTION_UK`), и машине он недоступен. И не
# сверяется ТЕКСТ: человек вправе переписать фразу, он не вправе изменить число.

# Где оседает поле брифа (спека C15, §2). Заголовки берутся из `vocabulary`,
# то есть из ТОГО ЖЕ кортежа, которым `render` эти разделы пишет: своя копия
# строки «Оплата та передоплата» разъехалась бы с генератором молча, и C15
# стала бы зелёной не потому, что числа сошлись, а потому, что раздел «не
# нашёлся».
#
# ⚠️ ОСТАТОЧНЫЙ РИСК, названный вслух: САМА ПРИВЯЗКА поля к разделу живёт
# только здесь — `render` выбирает раздел по месту в коде. Если поле переедет в
# другой раздел, C15 перестанет находить его числа и промолчит. Дешевле этого
# сегодня нет: вынести привязку в `vocabulary` можно только вместе с
# перестройкой `render`, а сверять «где оседает» по отчёту нельзя — якорь
# раздела 1 у `q12_hours` на живом прогоне равен «(якір не знайдено)».
_C15_SOURCES: tuple[tuple[str, str], ...] = (
    ("q35_reply_time", REQUIRED_SECTIONS_UK[7]),   # «Як записатися» — SLA ответа
    ("q29_prepayment", REQUIRED_SECTIONS_UK[3]),   # «Оплата та передоплата» — %
    ("q22_price_list", REQUIRED_SECTIONS_UK[1]),   # «Послуги та ціни» — вилки и сроки
    ("q12_hours", REQUIRED_SECTIONS_UK[0]),        # «Про студію» — часы работы
    ("q28_promo", PROMO_SECTION_UK),               # «Акція» — дата окончания
)

# Группа из одних нулей числом не является: она приезжает МИНУТАМИ времени суток
# («9:00»), где `_NUMBER` видит «9», «00» и «18», а в брифе стоит «9-18». Без
# этого C15 краснела бы на законных часах работы каждого клиента. Значением «0»
# при этом ничего не обещают, так что потери сигнала здесь нет.
_C15_ZERO_RE = re.compile(r"^[0.,]+$")


def _c15_numbers(text: str) -> set[str]:
    """Числа так, как их видит РАНТАЙМ: `guardrails._numbers` и ничего своего.

    Своя копия ответа на вопрос «что такое число» разъехалась бы с guardrail'ом
    молча — и C15 зеленела бы ровно тогда, когда разъехалась.

    Сверху ровно два шага, и оба названы спекой §3:
      * десятичный разделитель к одному виду («1,5» и «1.5» — одно число;
        внутри `_numbers` это сделать нельзя, там от формы записи зависит
        поведение guardrail'а у четырёх живых клиентов);
      * нулевые группы вон (см. `_C15_ZERO_RE`).
    """
    return {n.replace(",", ".") for n in _g._numbers(text)
            if not _C15_ZERO_RE.match(n)}


# ── Единица и множитель: спека §3, «бесчисловая формулировка» ────────────────
#
# «Протягом години» числа не содержит вовсе, а смысл несёт: одна година. Значит
# сверять надо ЕДИНИЦУ и МНОЖИТЕЛЬ, иначе «протягом години» против «протягом
# трьох годин» проходит чисто — а это ровно тот случай, из-за которого спека и
# написана.
#
# Классы строятся ИЗ `_TIME_WORDS` (одного словаря единиц на весь модуль), а не
# вторым списком слов. Порядок префиксов значим: «годин» обязан проверяться до
# «год», иначе украинские ЧАСЫ станут русскими ГОДАМИ — той же граблёй, которую
# `guardrails` уже ловил у себя трижды.
_C15_UNIT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("хвилин", "хвилина"), ("минут", "хвилина"),
    ("годин", "година"), ("час", "година"),
    ("тижд", "тиждень"), ("тижн", "тиждень"), ("недел", "тиждень"),
    ("місяц", "місяць"), ("месяц", "місяць"),
    ("рік", "рік"), ("рок", "рік"), ("год", "рік"), ("лет", "рік"),
    ("доб", "доба"), ("діб", "доба"), ("сут", "доба"),
    ("день", "день"), ("дн", "день"),
)


def _c15_unit(word: str) -> str | None:
    low = _unify(word)
    for prefix, unit in _C15_UNIT_PREFIXES:
        if low.startswith(prefix):
            return unit
    return None


# Числительные СЛОВАМИ — только для бесчисловой формы. Цифры сюда не ходят: их
# читает `_c15_numbers` живым `guardrails._numbers`, и второй разбор цифр был бы
# как раз той второй правдой, которой в этом модуле быть не должно.
_C15_NUMERAL_WORDS: dict[str, float] = {
    "один": 1, "одна": 1, "одну": 1, "одного": 1, "однієї": 1, "одної": 1,
    "одной": 1, "одного": 1,
    "півтори": 1.5, "півтора": 1.5, "полтора": 1.5, "полторы": 1.5,
    "два": 2, "дві": 2, "двох": 2, "две": 2, "двух": 2,
    "три": 3, "трьох": 3, "трех": 3, "трёх": 3,
    "чотири": 4, "чотирьох": 4, "четыре": 4, "четырёх": 4, "четырех": 4,
    "п'ять": 5, "п'яти": 5, "пять": 5, "пяти": 5,
    "шість": 6, "шести": 6, "шесть": 6,
    "сім": 7, "семи": 7, "семь": 7,
    "вісім": 8, "восьми": 8, "восемь": 8,
    "дев'ять": 9, "дев'яти": 9, "девять": 9, "девяти": 9,
    "десять": 10, "десяти": 10,
}


def _fmt_multiplier(value) -> str:
    number = float(value)
    return str(int(number)) if number == int(number) else str(number)


def _word_multiplier(before: str) -> str:
    """Множитель ИЗ СЛОВ слева. Ничего не нашли — «години» без числа = 1 (§3)."""
    for word in reversed(_WORD_RE.findall(_unify(before))):
        if word in _C15_NUMERAL_WORDS:
            return _fmt_multiplier(_C15_NUMERAL_WORDS[word])
    return "1"


def _c15_durations(text: str) -> set[tuple[str, str]]:
    """Все сроки текста как (единица, множитель) — и цифрами, и словами.

    Это сторона БРИФА: она обязана быть широкой, иначе «1 година» в брифе и
    «протягом години» в файле разошлись бы на ровном месте (одно и то же,
    записанное двумя способами).
    """
    out: set[tuple[str, str]] = set()
    for m in _time_hits(text):
        unit = _c15_unit(m.group())
        if unit is None:
            continue
        before, _ = _neighbour_words(text, m.start(), m.end())
        digits = _c15_numbers(before)
        for mult in (digits or {_word_multiplier(before)}):
            out.add((unit, mult))
    return out


def _c15_bare_promises(text: str) -> list[tuple[str, str, str]]:
    """Сторона ФАЙЛА: (слово, единица, множитель) для БЕСЧИСЛОВЫХ обещаний срока.

    Узко и намеренно. Сроки, записанные цифрами, уже сверены множеством чисел;
    здесь ловится ровно то, чего множество чисел поймать не может, — «протягом
    години» без единой цифры.

    Два условия сверх «единица без числа рядом»:
      * предлог длительности слева («протягом», «за», «через») — иначе «Робочий
        ЧАС студії» и «точний ЧАС» станут обещанием одного часа;
      * в предложении не назван тот, кто скажет точное («підтверджує старший
        мастер») — это отказ называть срок, а не обещание. Ровно это различие
        уже проведено в C12/C14, и второе правило здесь было бы третьей копией.
    """
    if _AUTHORITY_VERBS.search(text or ""):
        return []
    out: list[tuple[str, str, str]] = []
    for m in _hanging_time_hits(text):
        unit = _c15_unit(m.group())
        if unit is None:
            continue
        before, _ = _neighbour_words(text, m.start(), m.end())
        if not _DURATION_PREPS.search(before):
            continue
        out.append((m.group(), unit, _word_multiplier(before)))
    return out


def _fmt_durations(durations) -> str:
    """Сроки человеку, а не кортежами: «1 × година, 3 × день»."""
    return ", ".join(f"{mult} × «{unit}»"
                     for unit, mult in sorted(durations)) or "срока нет"


def _c15_skip_reason(field_id: str, field: dict | None) -> tuple[str | None, bool]:
    """(причина не сверять, надо ли это считать «не состоялось»).

    `blocked` достаётся только тому случаю, который назван спекой §4: клиент
    ЧТО-ТО написал, а мусор-детектор это забраковал. Сверять тогда не с чем, и
    зелёное здесь было бы утверждением «числа сошлись», которого никто не
    проверял (сторож C15-6).

    ПУСТОЕ поле — другой случай, и склеивать их нельзя. Из пустой ячейки
    пайплайн не взял НИ ОДНОГО числа, разъезжаться нечему, а `q28_promo` в
    схеме `required: false` — акции может законно не быть. `blocked` на нём
    означал бы rc «не состоялось» на каждом клиенте без акции: сигнал, всегда
    красный при законной работе, — это не сторож, а фон.
    """
    if field is None:
        return (f"{field_id}: поля нет в brief.json (схема формы разошлась со "
                f"списком C15 — сверять нечего, и это надо чинить)"), False
    verdict_ = str(field.get("verdict") or "")
    if verdict_ != "ok":
        raw = "" if field.get("raw") is None else str(field.get("raw")).strip()
        reason = str(field.get("reason") or "причина не названа")
        if raw:
            return (f"{field_id}: ответ клиента забракован ({reason}) — сверять "
                    f"числа не с чем"), True
        return f"{field_id}: поле пусто ({reason}) — числа взять неоткуда", False
    if _TARGET_FILES.get(str(field.get("target"))) != "knowledge.md":
        return (f"{field_id}: target={field.get('target')!r} — спека C15 §2 ждёт "
                f"knowledge.md, маршрут поля изменился"), False
    return None, False


def _c15(ctx: _Ctx) -> CheckResult:
    if ctx.brief is None:
        return _blocked("C15", f"{ctx.brief_error} — числа собранных файлов "
                               f"сверять НЕ С ЧЕМ")
    knowledge = ctx.text("knowledge.md")

    checked: list[str] = []
    notes: list[str] = []
    blockers: list[str] = []
    comparable: list[tuple[str, str, str]] = []   # (поле, раздел, значение)
    total_numbers = 0

    # Проход первый — ЧТО вообще подлежит сверке. Отдельно от сверки намеренно:
    # «не состоялось» перевешивает «есть красное» (см. `verdict`), а при сверке
    # в один проход первое же найденное красное вернулось бы раньше, чем стало
    # известно о забракованном поле, и прогон, часть которого не выполнялась,
    # выдал бы себя за «нашли один дефект».
    for field_id, section_title in _C15_SOURCES:
        field = ctx.brief_field(field_id)
        reason, blocking = _c15_skip_reason(field_id, field)
        if reason is not None:
            (blockers if blocking else notes).append(reason)
            continue
        if not _section_span(knowledge, section_title):
            notes.append(f"{field_id}: раздела «{section_title}» в knowledge.md нет "
                         f"(наличие разделов сторожит C11)")
            continue
        comparable.append((field_id, section_title, str(field.get("value") or "")))

    if blockers:
        return _blocked("C15", "; ".join(blockers))
    if not comparable:
        return _blocked("C15", "ни одно из пяти полей-источников не сверено — "
                               + ("; ".join(notes) or "причина не названа"))

    for field_id, section_title, value in comparable:
        span = _section_span(knowledge, section_title)
        brief_numbers = _c15_numbers(value)
        brief_durations = _c15_durations(value)
        # Бесчисловая форма брифа несёт множитель (§3), и он обязан считаться
        # числом: иначе «Протягом години» в брифе против «протягом 1 години» в
        # файле дало бы красное на ровном месте (сторож C15-2).
        brief_numbers |= {mult for _, mult in brief_durations}

        for line_no, line, _is_heading in span:
            # 🔴 Нумерацию подраздела пишет ГЕНЕРАТОР («## 3. Локальна
            # хімчистка»), в брифе её нет и быть не должно. Считать её числом
            # прайса значит краснеть на каждом клиенте, чей прайс не
            # пронумерован его собственной рукой.
            #
            # Найдено разнесённой парой авторов 18.08: на живом каталоге Ярины
            # проверка была ЗЕЛЁНОЙ — но лишь потому, что клиентка сама
            # пронумеровала свой прайс, и цифры случайно совпали. Ровно тот
            # класс «зелёное по везению», ради которого сторожа и пишет другой
            # автор.
            #
            # Срезается ТОЛЬКО ведущая нумерация: число внутри названия
            # («## 7. PPF 200 мкм») остаётся под проверкой — оно из брифа.
            # Решётки здесь уже срезаны: `_section_span` отдаёт текст
            # заголовка, а не строку файла. Поэтому `#` в шаблоне
            # необязательны — с ними правка не срабатывала вовсе.
            scan = re.sub(r"^\s*#*\s*\d+[.)]\s*", "", line) if _is_heading else line
            for number in sorted(_c15_numbers(scan)):
                total_numbers += 1
                if number in brief_numbers:
                    continue
                return _red(
                    "C15",
                    f"knowledge.md:{line_no} «{line.strip()}» → число {number} "
                    f"в разделе «{section_title}» не совпадает ни с одним числом "
                    f"поля {field_id} ({sorted(brief_numbers) or 'чисел нет'}): "
                    f"число разъехалось с брифом, и лид получит НЕ ТО",
                    file="knowledge.md", line=line_no)
            for word, unit, mult in _c15_bare_promises(line):
                total_numbers += 1
                if (unit, mult) in brief_durations:
                    continue
                return _red(
                    "C15",
                    f"knowledge.md:{line_no} «{line.strip()}» → срок «{word}» "
                    f"читается как {mult} × «{unit}», а поле {field_id} брифа "
                    f"даёт {_fmt_durations(brief_durations)} "
                    f"(«{value.strip()[:80]}»): бесчисловая форма несёт множитель, "
                    f"и он разъехался",
                    file="knowledge.md", line=line_no)
        checked.append(field_id)

    tail = f"; НЕ сверено: {'; '.join(notes)}" if notes else ""
    return _ok("C15", f"числа сходятся с брифом по {len(checked)} полям "
                      f"({', '.join(checked)}), сверено значений: {total_numbers}"
                      + tail)


# ─────────────────────────────────────────────────────────────────────────────
# Прогон и вердикт
# ─────────────────────────────────────────────────────────────────────────────

_CHECKS = (
    ("C1", _c1), ("C2", _c2), ("C3", _c3), ("C4", _c4), ("C5", _c5),
    ("C6", _c6), ("C7", _c7), ("C8", _c8), ("C9", _c9), ("C10", _c10),
    ("C11", _c11), ("C12", _c12), ("C13", _c13), ("C14", _c14), ("C15", _c15),
)


def run_checks(client_dir, report_document: dict, *, slug: str) -> list[CheckResult]:
    """Ровно 15 вердиктов по каталогу клиента, всегда и в порядке C1…C15.

    Каталог только ЧИТАЕТСЯ: `--check` ходит в том числе по боевому
    `chatter/clients/<slug>`, где живут деньги клиента.
    """
    try:
        ctx = _Ctx(client_dir, report_document, slug=slug)
    except Exception as exc:                              # noqa: BLE001
        return [_blocked(cid, f"контекст прогона не собрался: {type(exc).__name__}: {exc}")
                for cid in CHECK_IDS]

    if ctx.dir_error:
        # Каталога нет — прогон не состоялся ЦЕЛИКОМ. Это не «15 красных»:
        # красное утверждает, что проверка отработала и нашла дефект.
        return [_blocked(cid, ctx.dir_error) for cid in CHECK_IDS]

    results: list[CheckResult] = []
    for cid, fn in _CHECKS:
        try:
            res = fn(ctx)
        except Exception as exc:                          # noqa: BLE001
            # Исключение НЕ убирает проверку из списка: исчезнувшая проверка
            # неотличима от пройденной. Падение делает её `blocked` — то есть
            # прогон не состоялся, а не «флаг не сработал».
            res = _blocked(cid, f"{type(exc).__name__}: {exc}")
        if res.id != cid:                                 # защита от опечатки в _CHECKS
            res = _blocked(cid, f"проверка вернула чужой id {res.id!r}")
        results.append(res)
    return results


def verdict(results, *, reviewed: bool) -> int:
    """0 — всё зелёное И отчёт вычитан; 1 — есть красное; 2 — прогон не состоялся.

    Порядок именно такой:

    * **2 перевешивает всё.** Прогон, часть которого не выполнялась, ничего не
      доказал; выдавать его за «нашли одно красное» значит объявить остальные
      проверки пройденными. Сюда же попадает недостача проверок в списке.
    * **C12/C13 на код не влияют** — они флаги (решение владельца): законная
      формулировка клиента и дефект выглядят одинаково, и красный статус
      означал бы, что пайплайн знает намерение клиента лучше владельца.
    * **Невычитанный отчёт при всех зелёных — это 1, а не 0** (решение
      владельца, вопрос 3 спеки). Отчёт может стать зелёной ширмой: если
      разделы 2 и 3 не читать, дефолты доедут до прода как «решения».
    """
    rows = list(results or [])
    seen = [r.id for r in rows]
    if sorted(seen) != sorted(CHECK_IDS) or len(seen) != len(CHECK_IDS):
        return RC_NOT_RUN
    if any(r.blocked for r in rows):
        return RC_NOT_RUN
    if any((not r.ok) and (not r.is_flag) for r in rows):
        return RC_RED
    if not reviewed:
        return RC_RED
    return RC_GREEN
