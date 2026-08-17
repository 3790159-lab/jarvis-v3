# -*- coding: utf-8 -*-
"""T3 — отчёт онбординга: `brief.json` + результат генерации → REPORT.md + report.json.

Спека §3, контракт `report.json` — план от 17.08.

Зачем отчёт вообще существует. Генератор экономит время; отчёт решает, узнает ли
владелец, что бот собран на дефолтах и заглушках. Спека же называет главный риск
арки прямо: **отчёт может стать зелёной ширмой** — если разделы 2 и 3 не читать,
дефолты доедут до прода как «решения». Отсюда два правила, которым подчинён
каждый кусок этого модуля:

1. **Ничто не теряется молча.** Каждое поле брифа обязано оказаться ровно в одном
   месте: либо в разделе 1 (взято), либо в разделе 3 (не взято). Поле, которого
   нет ни там, ни там, — это дыра, которую отчёт не покажет никогда.
2. **Молчание и отсутствие проверки обязаны различаться.** Пустой блок печатается
   словами («флагів немає»), а не пустотой; несосчитанный счётчик печатается как
   «не порахований», а не как `0`. Ноль вместо «не считали» — это классические
   «два числа на одну вещь»: меньшее гасит большее молча.

Модуль НЕ импортирует `render`. Он пишется параллельно, и завязка на его
внутренности означала бы, что код и сторож сойдутся на общем допущении о том,
как выглядит вывод генератора. Отсюда берутся ровно четыре атрибута по именам,
зафиксированным интегратором: `.files`, `.defaults`, `.stubs`, `.counters`;
их форма разбирается терпимо, а всё, чего не передали, называется вслух.

── ОТСТУПЛЕНИЯ от буквы плана (осознанные, каждое названо в коде по месту) ──
* `document["meta"]` — пятый ключ сверх четырёх из плана. Причина механическая:
  `render_markdown(report)` по контракту принимает ТОЛЬКО документ, а шапка
  отчёта обязана назвать slug, бриф и `schema_version` (риск §7 спеки — дрейф
  схемы). Четыре ключа плана не тронуты.
* `verdict: "absent"` в разделе 3. У брифа три вердикта (ok/suspect/garbage), и
  все три — про ЯЧЕЙКУ. Обязательный факт, для которого в форме нет вопроса
  (адрес, телефон, канал записи), — четвёртый случай, и выдавать его за
  `garbage` нельзя: это утверждение, что клиент написал мусор там, где его не
  спрашивали.
* Пустой блок флагов печатается двумя формулировками сразу — см. `EMPTY_FLAGS_LINE`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from chatter.onboard.vocabulary import (
    DUAL_PURPOSE_REPORT_LINE_UK,
    REQUIRED_FACTS,
    ROLE_WORDING_QUESTION_UK,
    SLA_REALITY_QUESTION_UK,
    UNKNOWN_SECTION_UK,
)

SCHEMA_VERSION = 1

# Маршрут поля объявлен в схеме формы (`target`), а имя файла — здесь. Это
# единственная связь отчёта с генератором, и она проходит через контракт схемы,
# а не через чужой код: якорь раздела 1 выводится из `target`, а не из того, что
# render решил сделать внутри.
TARGET_FILES: dict[str, str | None] = {
    "knowledge": "knowledge.md",
    "playbook": "playbook.md",
    "settings": "settings.yaml",
    "examples": "examples.yaml",
    "persona": "persona.md",
    "report_only": None,
}

# Место для строки раздела 1, у которой места нет. Словами, а не `None`:
# «None» печатается человеку как обычный текст и читается как имя файла.
NO_FILE_PLACE = "(жодного файлу)"
REPORT_ONLY_ANCHOR = "тільки у звіт"
# Файл известен, а строка в нём не нашлась. Это ДРУГОЙ случай, чем «файла нет»,
# и склеивать их нельзя: первый значит «генератор переписал ответ до
# неузнаваемости», второй — «поле никуда не едет по конструкции».
ANCHOR_NOT_FOUND = "(якір не знайдено)"

# Шесть счётчиков раздела 1 — те самые, что вчера считали руками (спека §3).
COUNTER_KEYS: tuple[str, ...] = (
    "services", "prices", "deadlines", "stop_words", "forbidden", "example_pairs",
)

# Имена счётчиков зафиксированы планом; синонимы — дешёвая страховка на случай,
# если генератор назовёт их чуть иначе. Страховка сводит к ОДНОМУ каноническому
# имени и никогда не выдумывает значение: непереданный счётчик остаётся None.
_COUNTER_ALIASES: dict[str, str] = {
    "service_lines": "services", "services_count": "services",
    "price_numbers": "prices", "prices_count": "prices",
    "deadline_numbers": "deadlines", "durations": "deadlines",
    "stopwords": "stop_words", "escalation_keywords": "stop_words",
    "forbidden_terms": "forbidden", "forbidden_phrases": "forbidden",
    "pairs": "example_pairs", "examples": "example_pairs",
}

_COUNTER_TITLES_RU: dict[str, str] = {
    "services": "услуг",
    "prices": "ценовых чисел",
    "deadlines": "сроков",
    "stop_words": "стоп-слов эскалации",
    "forbidden": "запрещённых фраз",
    "example_pairs": "пар примеров",
}

# ОТСТУПЛЕНИЕ. Спека §3 пишет сторожевую строку по-русски («флагов нет»),
# задание исполнителю — по-украински («флагів немає»). Это одна и та же вещь под
# двумя именами, и строка, которую БУДУТ ИСКАТЬ глазами и проверкой, не имеет
# права зависеть от того, какую формулировку запомнил читатель. Печатаем обе:
# скобка стоит копейку, разошедшиеся сторожа — прогон, который ничего не доказал.
EMPTY_FLAGS_LINE = "флагів немає (флагов нет)"

# Флаги не передали вовсе — это НЕ «флагов нет». Проверки C12/C13 могли не
# запускаться, и пустой список по умолчанию обязан сказать об этом вслух.
FLAGS_NOT_RUN_LINE = (
    "⚠️ результат C12/C13 в отчёт не передавали — список пуст по умолчанию, "
    "а не потому, что проверки прошли чисто"
)

_APOSTROPHES = "ʼʻ’‘′´`‵'"
_SPACES = "       \t\r"
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_YAML_KEY_RE = re.compile(r"^([A-Za-z_][\w.\-]*)\s*:")
_TOKEN_RE = re.compile(r"[\w’ʼ']+", re.UNICODE)

_QUOTE_LIMIT = 200  # длина цитаты в ОДНОСТРОЧНОМ виде (раздел 1); раздел 3 цитирует дословно


# ─────────────────────────────────────────────────────────────────────────────
# Результат
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReportResult:
    """Отчёт в двух видах: машине (`document`) и человеку (`markdown`).

    Оба вида собираются из ОДНОГО документа: `markdown` — это буквально
    `render_markdown(document)`. Разойтись они не могут по конструкции, а
    расхождение здесь означало бы отчёт, где проверка читает одно, а владелец
    видит другое.
    """

    document: dict
    markdown: str

    @property
    def counters(self) -> dict:
        return self.document.get("counters", {})

    @property
    def sections(self) -> dict:
        return self.document.get("sections", {})


# ─────────────────────────────────────────────────────────────────────────────
# Мелкая нормализация
# ─────────────────────────────────────────────────────────────────────────────

def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _unify(text: str) -> str:
    """Casefold + один апостроф + один пробел.

    Тот же приём, что в `brief.normalize_question`: в брифе живут `U+02BC` и
    `U+2019`, а генератор пишет свой вариант. Сравнение по сырому тексту хрупко
    ровно здесь, и якорь раздела 1 молча не находился бы на половине полей.
    """
    text = "".join(" " if ch in _SPACES else ch for ch in text)
    text = "".join("'" if ch in _APOSTROPHES else ch for ch in text)
    return " ".join(text.casefold().split())


def _one_line(value, limit: int = _QUOTE_LIMIT) -> str:
    """Значение в одну строку для табличной части отчёта.

    Переводы строк становятся ` | `: строка раздела 1 обязана оставаться ОДНОЙ
    строкой, иначе прайс из 14 услуг развалит формат и отчёт перестанут читать.
    Полный текст при этом никуда не девается — он лежит в `brief.json`, а в
    разделе 3 цитируется дословно.
    """
    text = " | ".join(part.strip() for part in _text(value).split("\n") if part.strip())
    text = " ".join(text.split())
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(_unify(text))


# ─────────────────────────────────────────────────────────────────────────────
# Чтение результата генерации — ТОЛЬКО по именам атрибутов
# ─────────────────────────────────────────────────────────────────────────────

def _attr(obj, name):
    """Атрибут ИЛИ ключ — по имени, без импорта чужого модуля.

    `render_result` пишется параллельно. Единственное, на что здесь можно
    опереться, — четыре имени, зафиксированные интегратором. Всё остальное
    (dataclass это, namedtuple или словарь) разбирается терпимо, а недостающее
    называется вслух вместо того, чтобы стать нулём.
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _file_texts(render_result) -> dict[str, str]:
    """`.files` → {имя файла: текст}.

    Терпим к трём формам: {имя: текст}, {имя: путь} и список путей. Текст нужен
    ровно для одного — найти якорь раздела 1. Не нашли текст — якорь будет
    `None`, и отчёт скажет об этом словами, а не подставит правдоподобное.
    """
    files = _attr(render_result, "files")
    out: dict[str, str] = {}
    if not files:
        return out

    def _put(name: str, value) -> None:
        name = Path(_text(name)).name
        if not name:
            return
        text = _text(value)
        if "\n" not in text and len(text) < 300:
            path = Path(text)
            try:
                if path.is_file():
                    out[name] = path.read_text(encoding="utf-8")
                    return
            except OSError:
                pass  # не прочиталось — ниже ляжет как есть, якорь просто не найдётся
        out[name] = text

    if isinstance(files, dict):
        for name, value in files.items():
            _put(name, value)
    elif isinstance(files, (list, tuple, set)):
        for item in files:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                _put(item[0], item[1])
            else:
                _put(item, item)
    return out


def _entries(value) -> list[dict]:
    """Список записей генератора (`.defaults` / `.stubs`) → список словарей.

    Строка тоже запись: генератор вправе отдать заглушку одной строкой текста —
    она и есть то, что дословно уехало в knowledge.
    """
    if not value:
        return []
    if isinstance(value, dict):
        # {key: value} — форма «дефолт: значение» без пояснений
        return [{"key": k, "value": v} for k, v in value.items()]
    out: list[dict] = []
    for item in value if isinstance(value, (list, tuple)) else [value]:
        if isinstance(item, dict):
            out.append(dict(item))
        elif isinstance(item, str):
            out.append({"text": item})
        else:
            out.append({
                name: getattr(item, name)
                for name in dir(item)
                if not name.startswith("_") and not callable(getattr(item, name, None))
            })
    return out


def _pick(entry: dict, *names, default=None):
    for name in names:
        if name in entry and entry[name] not in (None, ""):
            return entry[name]
    return default


# ─────────────────────────────────────────────────────────────────────────────
# Якоря раздела 1
# ─────────────────────────────────────────────────────────────────────────────

def _find_anchor(text: str, value: str, *, markdown: bool = True) -> str | None:
    """Где в сгенерированном файле осел ответ клиента.

    Точное вхождение искать нельзя: R1 переписывает ценовую строку, R5 — ответы
    примеров, и дословный текст брифа в файле почти не встречается. Поэтому три
    захода от точного к грубому, и если не сошлось ни одним — честный `None`.
    Придуманный якорь хуже отсутствующего: он посылает читателя не туда, и
    ошибку он спишет на себя.

    `markdown=False` для `.yaml`: там `#` начинает КОММЕНТАРИЙ, а не заголовок.
    Найдено живьём — якорь поля `q20_real_replies` приехал строкой комментария
    «guardrail заріже те, чого ми ж самі й навчили», то есть отчёт указывал на
    рассуждение вместо места, где лежит ответ клиента.
    """
    value = _text(value).strip()
    if not text or not value:
        return None

    lines = text.split("\n")
    norm_lines = [_unify(line) for line in lines]

    probe_source = next((line for line in value.split("\n") if line.strip()), "")
    probe = _unify(probe_source)

    hit = None
    for size in (60, 32, 18):
        if len(probe) < size:
            continue
        needle = probe[:size]
        for idx, line in enumerate(norm_lines):
            if needle and needle in line:
                hit = idx
                break
        if hit is not None:
            break

    if hit is None and len(probe) >= 3:
        # Заход для КОРОТКИХ ответов. Лестница выше начинается с 18 символов,
        # и всё короче проваливалось мимо всех заходов сразу: «Київ» (4),
        # «гарантія» (8), «хочу з власником» (16) получали `None` и уходили из
        # раздела 1 без места.
        #
        # Это не край случая, а класс: стоп-слова эскалации (R4) и запрещённые
        # фразы (R6) КОРОТКИ ПО НАЗНАЧЕНИЮ — их и режут до полных фраз именно
        # затем, чтобы они были короткими. Город, телефон, имя — тоже.
        # То есть порог молча терял ровно те поля, у которых значение и должно
        # быть коротким.
        #
        # Для короткого значения дословное вхождение как раз ОСМЫСЛЕННО: R1/R5
        # переписывают предложения, но отдельное слово в файл едет как есть.
        # Ищем по границам слова, иначе «гарант» поймает «гарантія результату»
        # в чужой строке. Порог 3 символа оставлен: на одно-двухбуквенном
        # значении совпадение случайно по определению, и честный `None` лучше
        # якоря, посылающего читателя не туда.
        for idx, line in enumerate(norm_lines):
            if re.search(rf"(?<!\w){re.escape(probe)}(?!\w)", line):
                hit = idx
                break

    if hit is None:
        # Грубый заход: самые длинные токены ответа. Два совпадения в одной
        # строке — это уже не случайность на текстах такого размера.
        probe_tokens = sorted({t for t in _tokens(probe_source) if len(t) >= 5}, key=len, reverse=True)[:4]
        if len(probe_tokens) >= 2:
            best, best_score = None, 0
            for idx, line in enumerate(norm_lines):
                score = sum(1 for t in probe_tokens if t in line)
                if score > best_score:
                    best, best_score = idx, score
            if best_score >= 2:
                hit = best

    if hit is None:
        return None

    for idx in range(hit, -1, -1):
        if markdown:
            heading = _HEADING_RE.match(lines[idx])
            if heading:
                return heading.group(1)
        key = _YAML_KEY_RE.match(lines[idx])
        if key:
            return key.group(1)
    return f"рядок {hit + 1}"


# ─────────────────────────────────────────────────────────────────────────────
# Сборка документа
# ─────────────────────────────────────────────────────────────────────────────

def _fact_by_source(source_id: str):
    for fact in REQUIRED_FACTS:
        if fact.source == source_id:
            return fact
    return None


def _stub_index(render_result) -> tuple[dict[str, str], list[str]]:
    """`.stubs` → ({fact_id: текст заглушки}, [заглушки без известного факта]).

    Второй список — не мусор, а самое интересное. У Ярины руками написаны
    заглушки, которых нет ни в форме, ни в `REQUIRED_FACTS`: срок службы
    керамики, марка материалов, наличие на складе. Отчёт обязан донести их до
    владельца ровно так же, как факты из словаря, иначе он расскажет о том, что
    знает словарь, вместо того, что уехало клиенту.
    """
    by_fact: dict[str, str] = {}
    loose: list[str] = []
    # `section_stubs` — заглушки ОБЯЗАТЕЛЬНЫХ РАЗДЕЛОВ (R8), вынесенные из
    # `.stubs` отдельным решением интегратора: там они шли с синтетическим
    # `fact_id: "section:…"`, и C10 пошла бы сверять факты, которых в словаре
    # нет. Но выносить их из ОТЧЁТА нельзя: раздел без данных — это ровно то,
    # о чём владелец обязан узнать. Читаем оба поля, иначе следующий клиент
    # потеряет их молча — а на брифе Ярины это поле пустое, то есть потеря не
    # всплыла бы и на приёмке.
    entries = list(_entries(_attr(render_result, "stubs")))
    entries += list(_entries(_attr(render_result, "section_stubs")))
    for entry in entries:
        # `stub_line` ПЕРВЫМ — это имя, под которым генератор кладёт ПОЛНУЮ
        # строку заглушки. Раньше ключ не спрашивался, поиск проваливался до
        # `title`, и владелец видел в разделе 3 «Адреса, орієнтир, паркування»
        # вместо «Адреса, орієнтир, паркування — називає Старший мастер».
        # Раздел 3 существует, чтобы показать то, что ДОСЛОВНО уехало в
        # knowledge; обрезанная строка сверке не поддаётся, а выглядит как
        # полноценная запись. Расхождение нашёл третий автор (T4) — ни
        # генератор, ни отчёт своей половиной контракта его увидеть не могли.
        text = _text(_pick(entry, "stub_line", "text", "line", "stub", "title",
                           "value", default=""))
        fact_id = _pick(entry, "fact_id", "fact", "id")
        if fact_id and any(f.id == fact_id for f in REQUIRED_FACTS):
            by_fact[str(fact_id)] = text or str(fact_id)
            continue
        matched = None
        norm = _unify(text)
        for fact in REQUIRED_FACTS:
            if norm and _unify(fact.title_uk) in norm:
                matched = fact
                break
        if matched is not None:
            by_fact[matched.id] = text
        elif text:
            loose.append(text)
    return by_fact, loose


def _generic_question_uk(field_id: str, question: str, raw) -> str:
    """Вопрос клиенту по полю, для которого готового вопроса в словаре нет.

    Раздел 3 существует ради текста, который владелец КОПИРУЕТ и отправляет.
    Строка «q54_launch_date: garbage» не копируется — копируется вопрос. Берём
    формулировку самого брифа: она уже на языке клиента и уже им прочитана.
    """
    question = _one_line(question, 160) or field_id
    shown = _one_line(raw, 60)
    tail = f" (зараз стоїть «{shown}»)" if shown else ""
    return f"Уточніть, будь ласка: {question}{tail}"


def _stub_question_uk(stub_text: str) -> str:
    title = _text(stub_text).split(" — ")[0].strip() or _one_line(stub_text, 80)
    return f"Уточніть, будь ласка: {title}."


def _counters(render_result) -> dict:
    """Шесть счётчиков. Непереданный — `None`, а не `0`.

    Ноль здесь означал бы «услуг ноль» — то есть громкую находку. «Не считали»
    означает «мы не знаем». Склеить эти два состояния в одно число — ровно тот
    способ, которым проверка зеленеет, не проверив ничего.
    """
    raw = _attr(render_result, "counters") or {}
    if not isinstance(raw, dict):
        raw = {name: _attr(raw, name) for name in COUNTER_KEYS}
    canon: dict[str, int | None] = {key: None for key in COUNTER_KEYS}
    for name, value in raw.items():
        key = name if name in COUNTER_KEYS else _COUNTER_ALIASES.get(str(name))
        if key is None:
            continue
        try:
            canon[key] = int(value)
        except (TypeError, ValueError):
            canon[key] = None
    return canon


def _defaults(brief_fields: dict, render_result) -> list[dict]:
    """Раздел 2: что подставили и противоречил ли бриф.

    Конфликт — не техническая деталь, а решение владельца: Q15 просил
    «спілкуватись як людина» против `honesty_mode: honest`, Q14 дал наше
    собственное имя вместо имени персоны. Оба раза правильным оказался дефолт,
    и оба раза узнать об этом владелец мог только отсюда.
    """
    out: list[dict] = []
    for entry in _entries(_attr(render_result, "defaults")):
        key = _text(_pick(entry, "key", "name", "id", default="?"))
        value = _pick(entry, "value", "default", "resolved")
        why = _text(_pick(entry, "why", "reason", "because", default=""))
        source_id = _pick(entry, "brief_field", "field", "field_id", "conflicts_with")
        quote = _pick(entry, "brief_quote", "quote")
        wanted_other = _pick(entry, "brief_wanted_other", "conflict", "brief_conflict")

        if quote is None and source_id:
            source = brief_fields.get(str(source_id)) or {}
            quote = source.get("raw")
        if wanted_other is None:
            wanted_other = bool(quote) and bool(source_id)

        out.append({
            "key": key,
            "value": value,
            "why": why,
            "brief_wanted_other": bool(wanted_other),
            "brief_quote": _one_line(quote) if quote is not None else None,
            "brief_field": str(source_id) if source_id else None,
        })
    return out


def _flags(flags) -> list[dict]:
    """C12/C13 — флаги, а не красное.

    Красный статус здесь означал бы, что пайплайн знает намерение клиента лучше
    владельца. «Старший майстер відповідає протягом години» — законная строка из
    Q35, и блокировать её нельзя.
    """
    out: list[dict] = []
    for entry in _entries(flags):
        line = _pick(entry, "line", "lineno", "row")
        try:
            line = int(line)
        except (TypeError, ValueError):
            line = None
        out.append({
            "check": _text(_pick(entry, "check", "id", "rule", default="?")),
            "file": _text(_pick(entry, "file", "path", default="")) or None,
            "line": line,
            "quote": _one_line(_pick(entry, "quote", "text", "fragment", default="")) or None,
            "question": _text(_pick(entry, "question", "ask", default="")) or None,
        })
    return out


def build_report(brief: dict, render_result, *, slug: str, flags: list[dict] | None = None) -> ReportResult:
    """`brief.json` + результат генерации → отчёт (документ + markdown).

    `flags=None` и `flags=[]` — РАЗНЫЕ состояния: «проверки не передали
    результат» против «проверки прошли, флагов нет». Спека требует различать
    молчание и отсутствие проверки, и различие обязано дожить до документа, а не
    сгореть в приведении к пустому списку.
    """
    if not isinstance(brief, dict) or "fields" not in brief:
        # DEV-18: тихого фолбэка тут быть не может. Отчёт, собранный из «почти
        # брифа», выглядит как настоящий и ровно поэтому опаснее ошибки.
        raise ValueError(
            "[onboard] report: на вход нужен brief.json с ключом 'fields', "
            f"получено: {type(brief).__name__}"
        )

    fields: dict = brief.get("fields") or {}
    file_texts = _file_texts(render_result)
    stub_by_fact, loose_stubs = _stub_index(render_result)

    taken: list[dict] = []
    missing: list[dict] = []

    for field_id, field in fields.items():
        verdict = _text(field.get("verdict"))
        target = _text(field.get("target"))
        target_file = TARGET_FILES.get(target, None)

        if verdict == "ok":
            value = field.get("value")
            if value in (None, ""):
                value = field.get("raw")
            if target_file:
                anchor = _find_anchor(
                    file_texts.get(target_file, ""), _text(value),
                    markdown=target_file.endswith(".md"),
                ) or ANCHOR_NOT_FOUND
            else:
                # `report_only` — ответ клиента взят, но НИ В ОДИН файл не
                # едет. Формат раздела 1 требует место, а места нет, и раньше
                # сюда клался `None` — который человеку печатался словом
                # «None», то есть ВЫГЛЯДЕЛ адресом. Отсутствие адреса обязано
                # читаться как отсутствие, а не как строка «None»: иначе
                # владелец пойдёт искать файл с таким именем.
                target_file, anchor = NO_FILE_PLACE, REPORT_ONLY_ANCHOR
            taken.append({
                "field_id": field_id,
                "target_file": target_file,
                "anchor": anchor,
                "quote": _one_line(value),
            })
            continue

        # Не «ok» — значит в конфиг не поехало ни при каких условиях (§1.2).
        fact = _fact_by_source(field_id)
        missing.append({
            "field_id": field_id,
            "raw": field.get("raw"),
            "verdict": verdict,
            "reason": field.get("reason"),
            "stub": stub_by_fact.get(fact.id) if fact else None,
            "question_for_client": (
                fact.question_for_client_uk if fact
                else _generic_question_uk(field_id, field.get("question"), field.get("raw"))
            ),
            "fact_id": fact.id if fact else None,
        })

    # Обязательные факты, у которых нет своей строки выше. Два случая:
    # источника в форме нет вообще (адрес, телефон, канал записи) — либо
    # источник есть и он «ok», но факта в нём нет (Q12 даёт часы, а выходных не
    # даёт), и понять это может только генератор, поставивший заглушку. Молча
    # пропустить факт нельзя (R7): либо данные, либо заглушка плюс строка.
    named = {row.get("fact_id") for row in missing}
    for fact in REQUIRED_FACTS:
        if fact.id in named:
            continue
        source = fields.get(fact.source) if fact.source else None
        stub = stub_by_fact.get(fact.id)
        if source is not None and _text(source.get("verdict")) == "ok" and stub is None:
            continue  # факт закрыт полем брифа — он в разделе 1
        missing.append({
            "field_id": f"fact:{fact.id}",
            "raw": None,
            "verdict": "absent",
            "reason": (
                f"у формі немає питання про це — «{fact.title_uk}»" if not fact.source
                else f"поле {fact.source} відповіді на цей факт не дало — «{fact.title_uk}»"
            ),
            "stub": stub,
            "question_for_client": fact.question_for_client_uk,
            "fact_id": fact.id,
        })

    # Порядок раздела 3 объявлен в `vocabulary` дословно («порядок значим»), и
    # объявлен он ТАМ не случайно: словарь — единственная разметка на три слоя.
    # Здесь он разъезжался, потому что факт, привязанный к бракованному полю,
    # выезжал вместе с полем (по номеру колонки), а безымянные факты
    # дописывались в конец. Две правды об одном порядке — и сравнить два отчёта
    # построчно уже нельзя.
    #
    # Сортировка устойчивая, поэтому НЕ-фактовые строки сохраняют порядок
    # колонок брифа. Факты подняты наверх намеренно: это самые дорогие строки
    # раздела — ровно то, что бот выдумает, если владелец не спросит клиента.
    fact_order = {fact.id: i for i, fact in enumerate(REQUIRED_FACTS)}
    missing.sort(key=lambda row: (
        (0, fact_order[row["fact_id"]]) if row.get("fact_id") in fact_order else (1, 0)
    ))

    # Заглушки, которых нет ни в форме, ни в словаре: срок службы керамики, марка
    # материалов, наличие на складе. Их придумал не пайплайн — их написал
    # генератор, и владелец узнаёт о них отсюда.
    for text in loose_stubs:
        missing.append({
            "field_id": None,
            "raw": text,
            "verdict": "absent",
            "reason": f"заглушка в knowledge «{UNKNOWN_SECTION_UK}», поля в формі немає",
            "stub": text,
            "question_for_client": _stub_question_uk(text),
            "fact_id": None,
        })

    notes: list[str] = []
    if "q38_anti_icp" in fields:
        # Решение владельца 17.08: развести публичное и внутреннее автоматом
        # нельзя — это смысловое суждение. Вычитку делает человек, и строка
        # ниже — единственное место, где он об этом узнаёт.
        notes.append(DUAL_PURPOSE_REPORT_LINE_UK)

    sla_value = _text((fields.get("q35_reply_time") or {}).get("value")).strip()

    document = {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "slug": slug,
            "source": brief.get("source"),
            "brief_schema_version": brief.get("schema_version"),
            "files": sorted(file_texts),
            "notes": notes,
            # Различие «проверок не было» и «флагов нет» живёт ЗДЕСЬ: список
            # флагов в обоих случаях пуст, и без этого поля отчёт врал бы.
            "flags_checked": flags is not None,
        },
        "sections": {
            "taken": taken,
            "defaulted": _defaults(fields, render_result),
            "missing": missing,
        },
        "flags": _flags(flags),
        "counters": _counters(render_result),
        # Вопрос про НАЗВАНИЕ РОЛИ (решение владельца 17.08, пункт 5). Он не
        # привязан к пропущенному полю — роль как раз ОТВЕЧЕНА, — поэтому
        # живёт отдельным ключом, а в списке 3.3 стоит последним: там его и
        # ждёт человек, отправляющий вопросы клиенту.
        "role_wording_question": ROLE_WORDING_QUESTION_UK.format(
            role=_text((fields.get("q16_owner_ref") or {}).get("value")).strip() or "—"),
        # Вопрос про реальность SLA задаётся, только если срок в брифе ЕСТЬ.
        # Нет ответа — про него уже спрашивает общий вопрос раздела 3, и два
        # вопроса об одном поле в одном письме читаются как невнимательность.
        "sla_reality_question": (
            SLA_REALITY_QUESTION_UK.format(sla=sla_value) if sla_value else None),
    }
    return ReportResult(document=document, markdown=render_markdown(document))


# ─────────────────────────────────────────────────────────────────────────────
# REPORT.md
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_counter(value) -> str:
    return "не порахований генератором ⚠️" if value is None else str(value)


def _fmt_anchor(row: dict) -> str:
    """Печатает место как есть.

    Раньше «места нет» решалось ЗДЕСЬ ещё раз, своими словами, — то есть одна
    правда жила в двух местах и могла разойтись: документ говорил `None`,
    markdown говорил «(лише у звіт)», и сверить их было нечем. Теперь решение
    принимается один раз при сборке строки, а формат только показывает.
    """
    target_file = row.get("target_file") or NO_FILE_PLACE
    anchor = row.get("anchor") or ANCHOR_NOT_FOUND
    return f"{target_file}:{anchor}"


def _quote_block(text) -> list[str]:
    """Дословная цитата раздела 3 — блоком, с сохранением переводов строк.

    Раздел 3 — единственное место, где цитата обязана быть ДОСЛОВНОЙ: по ней
    владелец решает, мусор это или ответ. Ужать её в строку значило бы обрезать
    улику ровно там, где она нужна.
    """
    lines = [line for line in _text(text).split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return ["  > (порожньо)"]
    return [f"  > {line}" if line.strip() else "  >" for line in lines]


def render_markdown(report: dict) -> str:
    """Документ → REPORT.md. Чистая функция: тот же документ даёт тот же текст.

    Порядок разделов ровно тот, что в спеке: 1, 2, 3, и ФЛАГИ ПОСЛЕ них. Блок
    флагов три раздела не заменяет — он про то, что пайплайн решить не вправе.
    """
    meta = report.get("meta") or {}
    sections = report.get("sections") or {}
    taken = sections.get("taken") or []
    defaulted = sections.get("defaulted") or []
    missing = sections.get("missing") or []
    flags = report.get("flags") or []
    counters = report.get("counters") or {}

    out: list[str] = []
    slug = meta.get("slug") or "?"
    out.append(f"# Отчёт онбординга: {slug}")
    out.append("")
    out.append(
        f"Бриф: `{meta.get('source') or '?'}` · схема формы v{meta.get('brief_schema_version') or '?'}"
        f" · схема отчёта v{report.get('schema_version') or '?'}"
    )
    files = meta.get("files") or []
    out.append("Сгенерированные файлы: " + (", ".join(f"`{name}`" for name in files) or "⚠️ генератор файлов не передал"))
    out.append("")
    out.append(
        "> Разделы 2 и 3 — не формальность. Непрочитанный отчёт превращает "
        "дефолты и заглушки в «решения», и узнаёт об этом лид."
    )
    out.append("")

    # ── Раздел 1 ────────────────────────────────────────────────────────────
    out.append(f"## 1. ВЗЯТО ИЗ БРИФА — {len(taken)}")
    out.append("")
    out.append("| счётчик | значение |")
    out.append("|---|---|")
    for key in COUNTER_KEYS:
        out.append(f"| {_COUNTER_TITLES_RU[key]} | {_fmt_counter(counters.get(key))} |")
    out.append("")
    if taken:
        for row in taken:
            out.append(f"- `{row.get('field_id')}` → {_fmt_anchor(row)} ← «{row.get('quote')}»")
    else:
        out.append("- из брифа не взято НИ ОДНОГО поля — это находка, а не пустой раздел")
    out.append("")

    # ── Раздел 2 ────────────────────────────────────────────────────────────
    conflicts = [row for row in defaulted if row.get("brief_wanted_other")]
    out.append(f"## 2. ПОДСТАВЛЕНО ДЕФОЛТОМ — {len(defaulted)}")
    out.append("")
    if defaulted:
        for row in defaulted:
            why = row.get("why") or "причина не указана генератором ⚠️"
            out.append(f"- `{row.get('key')}` = `{row.get('value')}` — {why}")
    else:
        out.append(
            "- генератор не передал ни одного дефолта. Это НЕ значит «дефолтов "
            "нет»: `honesty_mode`, `funnel_gate`, `strict_knowledge` и `payments` "
            "жёсткие и подставляются всегда (§2 спеки) — значит, их не донесли до отчёта ⚠️"
        )
    out.append("")
    out.append(f"### ⚠️ бриф просил другое — {len(conflicts)}")
    out.append("")
    if conflicts:
        out.append("Решает владелец. Дефолт уже подставлен, бриф с ним не согласен:")
        out.append("")
        for row in conflicts:
            source = f" ({row['brief_field']})" if row.get("brief_field") else ""
            out.append(
                f"- `{row.get('key')}` = `{row.get('value')}`, а бриф просил"
                f"{source}: «{row.get('brief_quote')}»"
            )
    else:
        out.append("Конфликтов брифа с дефолтами нет.")
    out.append("")

    # ── Раздел 3 ────────────────────────────────────────────────────────────
    stubbed = [row for row in missing if row.get("stub")]
    out.append(f"## 3. В БРИФЕ НЕТ — {len(missing)}")
    out.append("")
    out.append("### 3.1. Поля без ответа, мусор и подозрительное")
    out.append("")
    if missing:
        for row in missing:
            head = row.get("field_id") or "(без поля в форме)"
            out.append(f"- `{head}` — **{row.get('verdict')}**: {row.get('reason') or 'причина не названа ⚠️'}")
            stub = row.get("stub")
            raw = row.get("raw")
            # Цитата ДОСЛОВНАЯ и только там, где есть что цитировать. У факта,
            # которого в форме нет вообще, ячейки не существует, и «> (порожньо)»
            # выглядело бы как пустой ответ клиента — то есть врало бы.
            if _text(raw).strip() and _text(raw) != _text(stub):
                out.extend(_quote_block(raw))
            if stub:
                out.append(f"  - заглушка в knowledge: «{_one_line(stub, 120)}»")
            elif row.get("fact_id"):
                # Громко ТОЛЬКО про обязательные факты (R7): без данных и без
                # заглушки бот их выдумает. Для остальных полей заглушка не
                # предусмотрена вовсе, и предупреждение здесь было бы фоном —
                # сигналом, красным при законной работе.
                out.append("  - заглушки в knowledge НЕТ — обязательный факт не закрыт ничем ⚠️ (C10)")
    else:
        out.append("Пустых и мусорных полей нет — все 57 колонок дали ответ.")
    out.append("")

    out.append(f"### 3.2. Ушло заглушкой в knowledge «{UNKNOWN_SECTION_UK}» — {len(stubbed)}")
    out.append("")
    if stubbed:
        for row in stubbed:
            out.append(f"- «{_one_line(row.get('stub'), 160)}»")
    else:
        out.append("Заглушек нет — генератор не поставил ни одной ⚠️ (проверь C10)")
    out.append("")

    questions: list[str] = []
    for row in missing:
        question = _text(row.get("question_for_client")).strip()
        if question and question not in questions:
            questions.append(question)
    # Вопрос про название роли идёт ПОСЛЕДНИМ и всегда: он не про пропуск в
    # брифе, а про слово, которое лид слышит от бота в каждой второй реплике.
    role_question = _text(report.get("role_wording_question")).strip()
    if role_question and role_question not in questions:
        questions.append(role_question)
    # И последним — вопрос про реальность обещанного срока (решение владельца
    # 17.08 по C12): значение перенесено верно, а верно ли оно ПО ЖИЗНИ, знает
    # только клиент.
    sla_question = _text(report.get("sla_reality_question")).strip()
    if sla_question and sla_question not in questions:
        questions.append(sla_question)
    out.append(f"### 3.3. Готовый текст вопросов клиенту — {len(questions)} (скопировать и отправить)")
    out.append("")
    if questions:
        for number, question in enumerate(questions, 1):
            out.append(f"{number}. {question}")
    else:
        out.append("Вопросов к клиенту нет.")
    out.append("")

    notes = meta.get("notes") or []
    if notes:
        out.append("### 3.4. Требует вычитки смысла")
        out.append("")
        for note in notes:
            out.append(f"- {note}")
        out.append("")

    # ── Флаги: ПОСЛЕ трёх разделов и их не заменяют ─────────────────────────
    out.append(f"## ⚠️ ФЛАГИ — решает владелец — {len(flags)}")
    out.append("")
    out.append(
        "Проверки, которые не могут быть красными: законная формулировка клиента "
        "и дефект выглядят здесь одинаково (C12, C13)."
    )
    out.append("")
    if flags:
        for row in flags:
            place = row.get("file") or "?"
            if row.get("line"):
                place = f"{place}:{row['line']}"
            out.append(f"- ⚠️ **{row.get('check')}** {place} — «{row.get('quote')}»")
            out.append(f"  - {row.get('question') or 'вопрос не сформулирован ⚠️'}")
    else:
        out.append(EMPTY_FLAGS_LINE)
    if not meta.get("flags_checked"):
        out.append("")
        out.append(FLAGS_NOT_RUN_LINE)
    out.append("")

    # ── Счётчики разделов: лечение риска «зелёной ширмы» (§7) ───────────────
    out.append("## Счётчики разделов")
    out.append("")
    out.append(f"- раздел 1 (взято из брифа): {len(taken)}")
    out.append(f"- раздел 2 (дефолты): {len(defaulted)}, из них конфликтуют с брифом: {len(conflicts)}")
    out.append(f"- раздел 3 (в брифе нет): {len(missing)}, заглушек: {len(stubbed)}, вопросов клиенту: {len(questions)}")
    out.append(f"- флаги: {len(flags)}")
    out.append("")
    out.append(
        "Разделы 2 и 3 прочитаны глазами? Пока нет метки вычитки `REVIEWED`, "
        "`--check` зелёным не будет — решение владельца от 17.08."
    )
    out.append("")
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# Запись на диск
# ─────────────────────────────────────────────────────────────────────────────

def write_report(result: ReportResult, out_dir) -> tuple[Path, Path]:
    """REPORT.md + report.json в каталог прогона. Возвращает оба пути.

    UTF-8 задан явно: под Windows дефолтная кодировка — cp1251, и украинский
    отчёт молча превратился бы в исключение на первой же цитате.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "REPORT.md"
    json_path = out_dir / "report.json"
    md_path.write_text(result.markdown, encoding="utf-8")
    json_path.write_text(
        json.dumps(result.document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return md_path, json_path
