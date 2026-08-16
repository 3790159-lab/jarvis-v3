"""ВХОД пайплайна: xlsx-бриф → нормализованный `brief.json` (спека §1).

Здесь живут ровно три вещи и ни одной больше:

1. **Привязка поля к колонке.** Ведёт ПОЗИЦИЯ колонки, сторожит ОТПЕЧАТОК
   вопроса. Формулировки в Google-форме правят чаще, чем переставляют вопросы,
   поэтому позиция — ключ; но позиция без сторожа однажды соберёт клиенту прайс
   из чужой колонки, поэтому отпечаток обязан подтвердить позицию.
2. **Мусор-детектор.** Шесть классов, все сняты с РЕАЛЬНОГО брифа, ни одного
   выдуманного. Детектор НИЧЕГО НЕ УДАЛЯЕТ — он понижает поле до «ответа нет».
3. **Маршрут поля** (`target`) — объявлен в схеме, а не выведен из текста.

Инварианты выхода (каждый со своей причиной, а не «так вышло»):

- `raw` есть ВСЕГДА, даже у `garbage`: потеря сырого значения = потеря улики,
  а раздел 3 отчёта обязан цитировать клиента ДОСЛОВНО;
- `value is None` при `verdict != "ok"`: ровно это правило не даёт мусору
  доехать до конфига клиента;
- `reason` непуст при `verdict != "ok"`: мусор без причины неотличим от бага
  самого детектора;
- расхождение схемы и брифа = `SchemaMismatch` С КАРТОЙ, а не тихий фолбэк.

Спека писана до кода. Места, где реальный бриф оказался сложнее правила из
спеки, помечены в коде словом ОТСТУПЛЕНИЕ — их разбирает отчёт исполнителя.
"""
from __future__ import annotations

import datetime as _dt
import re
import unicodedata
from pathlib import Path

import yaml
from openpyxl import load_workbook

SCHEMA_VERSION = 1

# Порог совпадения отпечатка вопроса (спека §1.1). Ниже порога — не «похоже»,
# а «сверь руками»: между 0.6 и 0.0 лежит ровно тот случай, когда вопрос
# переписали НАСТОЛЬКО, что он мог стать другим вопросом.
FINGERPRINT_THRESHOLD = 0.6

TARGETS = ("knowledge", "playbook", "settings", "examples", "report_only")
TYPES = ("text", "block", "choice", "datetime")
VERDICTS = ("ok", "suspect", "garbage")

# ─────────────────────────────────────────────────────────────────────────────
# Нормализация текста
# ─────────────────────────────────────────────────────────────────────────────

# В брифе живут РАЗНЫЕ апострофы: U+02BC в «імʼя» и U+2019 в «обов’язково».
# Сравнение по сырому тексту хрупко именно здесь: «імʼя» и «ім’я» — разные
# строки и разные токены, а вопрос один и тот же.
_APOSTROPHES = "ʼʻ’‘′´`‵"

# NBSP из Google Forms приезжает регулярно и даёт другой токен.
_SPACES = "         \t\r\n"

# Стоп-слова — ТОЛЬКО служебные части речи (предлоги, союзы, местоимения,
# частицы). Содержательные слова не выкидываем: отпечаток тем и живёт, что
# различает вопросы, а сокращённый до предлогов отпечаток совпадёт с любым.
_STOP_WORDS = frozenset("""
і й та а але або чи що як де коли куди чому бо тому щоб якщо хоч хоча
в у на з із зі до від для про по за при над під без між через після перед
не ні так це цей ця ці той та те ті так саме ж би б же
ви ваш ваша ваше ваші вас вам я мене мені ми ми нас нам він вона воно вони
його її їх хто кого кому чий яка яке які який
є бути буде будуть має мають можна треба слід
и или а но что как где когда куда почему чтобы если хотя
в на с со из от для про по за при над под без между через после перед
не ни так это этот эта эти тот та то те же бы ли
вы ваш ваша ваше ваши вас вам я меня мне мы нас нам он она оно они его ее их
кто кого кому чей какая какое какие какой
есть быть будет будут имеет имеют можно надо нужно
""".split())


def _to_text(raw) -> str:
    """Ячейка → строка. openpyxl отдаёт datetime и float как есть."""
    if raw is None:
        return ""
    if isinstance(raw, (_dt.datetime, _dt.date, _dt.time)):
        return raw.isoformat(sep=" ") if isinstance(raw, _dt.datetime) else raw.isoformat()
    if isinstance(raw, bool):
        return "так" if raw else "ні"
    return str(raw)


def _unify(text: str) -> str:
    """Общая часть нормализации: пробелы, апострофы, регистр."""
    out = []
    for ch in text:
        if ch in _SPACES:
            out.append(" ")
        elif ch in _APOSTROPHES:
            out.append("'")
        else:
            out.append(ch)
    return "".join(out).casefold()


def normalize_question(text) -> set[str]:
    """Текст → множество значимых токенов (отпечаток).

    casefold → NBSP в пробел → все апострофы к одному → снять пунктуацию →
    выкинуть стоп-слова. Апостроф намеренно ПЕРЕЖИВАЕТ снятие пунктуации:
    он внутри слова («ім'я»), и если его выкинуть вместе с точками, токен
    поедет ещё раз — уже без нужды.
    """
    unified = _unify(_to_text(text))
    cleaned = []
    for ch in unified:
        if ch == "'":
            cleaned.append(ch)
            continue
        cat = unicodedata.category(ch)
        cleaned.append(" " if cat.startswith(("P", "S", "Z", "C")) else ch)
    tokens = "".join(cleaned).split()
    return {t.strip("'") for t in tokens if t.strip("'") and t.strip("'") not in _STOP_WORDS}


def fingerprint_score(a: set[str], b: set[str]) -> float:
    """Jaccard двух отпечатков, 0..1.

    Пустое пересечение с пустым объединением даёт **0.0, а не 1.0**: у пары,
    о которой не известно ничего, нет права на максимальное сходство. Это
    сознательный отказ от математической конвенции Jaccard в пользу того,
    ради чего отпечаток вообще существует, — сторожить позицию колонки.

    Разбор 17.08 (два автора разошлись здесь, и спор решил замер). Довод за
    1.0 был такой: вопрос из одних стоп-слов нормализуется в пустоту с обеих
    сторон и «на самом деле совпал». В РЕАЛЬНОЙ форме таких колонок нет ни
    одной — самый короткий отпечаток («Знижки?») даёт токен `знижки`. Зато
    существует способ получить пустоту с нашей стороны: незаполненный
    `fingerprint` в схеме. Он ловится ГРОМКО в `load_schema` — там, где это
    дефект схемы, а не результат сравнения.
    """
    a, b = set(a), set(b)
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


# ─────────────────────────────────────────────────────────────────────────────
# Мусор-детектор (спека §1.2) — шесть классов, все с реального брифа
# ─────────────────────────────────────────────────────────────────────────────

# Класс 1: тестовое заполнение. Q5 «тест 01 Артем», Q8 «тест».
_TEST_PREFIXES = ("тест", "test", "asdf", "qwer", "фывa", "ыва")
_DASHES = "-–—_.•"

# Класс 4: ответ-ссылка на другое поле. Q23/Q25 «заполнил разом».
# Фразы, а не корни: «разом» живёт в легальном «оглядається разом із клієнтом»
# (Q45), и корневой матч убил бы содержательный ответ.
_CROSS_REFS = (
    "разом", "вместе", "см выше", "см вище", "смотри выше", "дивись вище",
    "див вище", "дивіться вище", "вище", "выше", "там же", "там само",
    "аналогично", "аналогічно", "як вище", "как выше", "те саме", "то же",
    "як і вище", "заповнив разом", "заполнил разом",
)

# Класс 5: плейсхолдер-URL. Q4 `drivepro-detailing.example`.
_PLACEHOLDER_HOST_PREFIXES = ("example.", "test.", "localhost.", "sample.")
# ОТСТУПЛЕНИЕ от буквы спеки: спека называет «домены example.*, test.*», а
# реальный бриф даёт `drivepro-detailing.example` — плейсхолдер сидит в
# ЗОНЕ, а не в начале хоста. Буквальное чтение спеки не поймало бы тот самый
# случай, ради которого класс и заведён. Зоны — резервные по RFC 2606.
_PLACEHOLDER_HOST_SUFFIXES = (".example", ".test", ".invalid", ".localhost")
_HOST_RE = re.compile(r"(?<![\w.-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})(?![\w-])")

# Класс 6: наше имя в поле клиента. Q14 «Джарвис».
# Имена продукта и персон ДРУГИХ клиентов. Слово «керівниця» сюда НЕ входит,
# хотя и стоит в forbidden_terms эталона: в брифе оно живёт как осознанный
# ответ клиента (Q50 «не кажи "керівниця вирішить"»), и детектор, съевший
# этот ответ, убил бы легальную настройку brand-safety.
_OUR_NAMES = frozenset({
    "джарвис", "джарвіс", "jarvis", "ярина", "yarina", "ольга", "olga",
    "волська", "volska", "chatter", "чаттер",
})

# Класс 3 (огрызок) и класс 2 (числовая заглушка) осмысленны только там, где
# клиент ПЕЧАТАЛ ответ. У поля-выбора значение приезжает из НАШЕГО списка
# опций — это правило по ИСТОЧНИКУ (§1.3), тот же принцип, что и у `target`.
_TYPED_TYPES = ("text", "block")

_SHORT_VALUE = 40   # выше этой длины ответ уже содержателен, словарные классы молчат
_FRAGMENT_LEN = 12  # спека §1.2: огрызок — «длина < 12»
_NUMERIC_STUB_LEN = 8
_NUMERIC_STUB_RATIO = 0.8


def normalize_question_sequence(text) -> list[str]:
    """То же, что `normalize_question`, но с сохранением ПОРЯДКА и повторов.

    Нужно фразам-ссылкам: «там же» — это два токена подряд, а не два токена
    где-то в множестве.
    """
    unified = _unify(_to_text(text))
    cleaned = []
    for ch in unified:
        if ch == "'":
            cleaned.append(ch)
            continue
        cat = unicodedata.category(ch)
        cleaned.append(" " if cat.startswith(("P", "S", "Z", "C")) else ch)
    return [t.strip("'") for t in "".join(cleaned).split() if t.strip("'")]


def _has_digit(text: str) -> bool:
    return any(ch.isdigit() for ch in text)


def _is_test_filler(text: str, tokens: list[str]) -> bool:
    stripped = text.strip()
    if stripped and all(ch in _DASHES or ch.isspace() for ch in stripped):
        return True
    if len(stripped) > _SHORT_VALUE:
        return False
    return any(t.startswith(_TEST_PREFIXES) for t in tokens)


def _is_our_name(tokens: list[str]) -> bool:
    return any(t in _OUR_NAMES for t in tokens)


def _is_cross_reference(text: str, tokens: list[str]) -> bool:
    if len(text.strip()) > _SHORT_VALUE:
        return False
    line = " " + " ".join(tokens) + " "
    return any(f" {ref} " in line for ref in _CROSS_REFS)


def _placeholder_hosts(text: str) -> list[str]:
    lowered = _unify(text)
    hosts = [m.group(1) for m in _HOST_RE.finditer(lowered)]
    if re.search(r"(?<![\w.-])localhost(?![\w-])", lowered):
        hosts.append("localhost")
    bad = []
    for host in hosts:
        if host == "localhost" or host.startswith(_PLACEHOLDER_HOST_PREFIXES) \
                or host.endswith(_PLACEHOLDER_HOST_SUFFIXES):
            bad.append(host)
    return bad


def _is_numeric_stub(raw, text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    # Ячейка, приехавшая ЧИСЛОМ в текстовое поле, — заглушка независимо от
    # длины: Google Forms отдаёт «1» как число, только если человек не писал
    # ответа. Пример с брифа: Q52/Q54/Q55 = 1.0.
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return True
    if len(stripped) > _NUMERIC_STUB_LEN:
        return False
    # ОТСТУПЛЕНИЕ: спека считает «долю цифр и пробелов», но эталонный случай
    # «1.0» даёт 2/3 = 0.67 и правило по букве промахнулось бы мимо своего же
    # примера. Десятичный разделитель — часть числа, а не текста, поэтому
    # считается вместе с цифрами. Контроль: «9-18» (легальные часы работы)
    # даёт 0.75 и остаётся `ok`.
    numeric = sum(1 for ch in stripped if ch.isdigit() or ch.isspace() or ch in ".,")
    return numeric / len(stripped) > _NUMERIC_STUB_RATIO


def _is_fragment(text: str, tokens: list[str], question_tokens: set[str]) -> bool:
    """Огрызок — это ОБОРВАННЫЙ текст, а не просто короткий.

    🔴 ОТСТУПЛЕНИЕ ОТ СПЕКИ, и оно принципиальное. §1.2 даёт правило «длина
    < 12 и ни одного токена из отпечатка вопроса». По букве оно съедает
    легальные ответы, и это не догадка — обе независимые проверки 17.08
    принесли свои списки жертв:

      «Київ» (місто), «Українська» (мова), «9-18» (години), «До 100»
      (обсяг діалогів), «Telegram, 1» (канали).

    Причина общая: у вопроса-СПРАВОЧНИКА («у якому місті», «яка мова»)
    правильный ответ и НЕ МОЖЕТ пересекаться с текстом вопроса — города в
    вопросе про город не бывает. То есть условие непересечения ловит там не
    брак, а саму природу такого поля. Цена ошибки несимметрична: пропущенный
    мусор виден в отчёте и стоит одну строку, а съеденный ответ уходит в
    заглушку «називає старший майстер» при ЖИВЫХ данных, и владелец узнает об
    этом от лида.

    Настоящий признак у единственного реального огрызка («мне)») — не длина,
    а СЛЕД ОБРЫВА: непарная скобка. Его и берём. Длина осталась вторым
    условием: в длинном тексте непарная скобка — обычная опечатка.
    """
    stripped = text.strip()
    if not stripped or len(stripped) >= _FRAGMENT_LEN:
        return False
    # Ни буквы, ни цифры — «…», «-», «??». Содержания нет вовсе, и длина тут
    # ни при чём: это отсутствие ответа, оформленное символом.
    if not any(ch.isalnum() for ch in stripped):
        return True
    for opening, closing in (("(", ")"), ("[", "]"), ("{", "}"), ("«", "»")):
        if stripped.count(opening) != stripped.count(closing):
            return True
    # Висящий разделитель в конце — тоже след обрыва: «Telegram,» это начало
    # перечисления, которое не дописали. Точка и «!»/«?» сюда НЕ входят: ими
    # заканчивают нормально.
    return stripped[-1] in ",;:-–—…"


def classify_value(raw, question_tokens: set[str], *, field_type: str = "text") -> tuple[str, str | None]:
    """Значение ячейки → (verdict, reason).

    `verdict` ∈ {"ok", "suspect", "garbage"}; `reason is None` ТОЛЬКО у "ok".

    Разделение вердиктов: `garbage` — значение не является ответом вообще
    (заглушка, огрызок, ссылка на другое поле, фейковый домен); `suspect` —
    ответ настоящий, но противоречит нашему конфигу и требует решения
    владельца (Q14 «Джарвис»). Оба одинаково не доезжают до конфига, разница
    только в том, какой вопрос отчёт задаёт владельцу.

    `field_type` — необязательный ключевой аргумент; по умолчанию "text",
    то есть все шесть классов активны. Схема передаёт настоящий тип поля,
    чтобы правила, осмысленные только для НАПЕЧАТАННОГО ответа, не судили
    значение, приехавшее из нашего же списка опций.
    """
    text = _to_text(raw)
    tokens = normalize_question_sequence(text)
    typed = field_type in _TYPED_TYPES

    if not text.strip():
        # Пустое поле — не «мусор», а «ответа нет». Отдельного вердикта спека
        # не заводит, а «ok» с пустым значением сделал бы дыру: поле выглядело
        # бы заполненным. Понижаем тем же механизмом и называем причину.
        return "garbage", "empty: поле не заполнено"

    if _is_test_filler(text, tokens):
        return "garbage", f"test_filler: тестовое заполнение — «{text.strip()}»"

    if _is_our_name(tokens):
        hit = next(t for t in tokens if t in _OUR_NAMES)
        return "suspect", (
            f"our_name: в поле клиента стоит наше имя или имя персоны другого "
            f"клиента — «{hit}»"
        )

    if _is_cross_reference(text, tokens):
        return "garbage", f"cross_reference: ответ ссылается на другое поле — «{text.strip()}»"

    bad_hosts = _placeholder_hosts(text)
    if bad_hosts:
        return "garbage", (
            "placeholder_url: плейсхолдер вместо реального адреса — "
            + ", ".join(sorted(set(bad_hosts)))
        )

    if typed and _is_numeric_stub(raw, text):
        return "garbage", f"numeric_stub: числовая заглушка в текстовом поле — «{text.strip()}»"

    if typed and _is_fragment(text, tokens, question_tokens):
        return "garbage", (
            f"stub_fragment: огрызок — «{text.strip()}» несёт след обрыва "
            f"(непарная скобка, висящий разделитель или ни одной буквы)"
        )

    return "ok", None


# ─────────────────────────────────────────────────────────────────────────────
# Схема формы
# ─────────────────────────────────────────────────────────────────────────────

class SchemaMismatch(Exception):
    """Схема и бриф разошлись — прогон НЕ СОСТОЯЛСЯ (rc 2), а не «почти сошёлся».

    Молча угадать колонку значит однажды собрать клиенту прайс из чужого
    столбца, и узнаем мы об этом от лида. Поэтому расхождение — исключение
    с картой, а не фолбэк (DEV-18).

    `.problems` — список человекочитаемых строк расхождения, тот же, что
    возвращает `match_schema`.
    """

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = list(problems or [])


_REQUIRED_FIELD_KEYS = ("id", "col", "fingerprint", "type", "required", "target")


def load_schema(path) -> dict:
    """`form_schema.yaml` → {"version": int, "fields": {id: {...}}}.

    Схема, которую нельзя применить, — это тот же несостоявшийся прогон:
    битый файл поднимает `SchemaMismatch`, а не тихо теряет половину полей.
    """
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SchemaMismatch(f"[onboard] схема не найдена: {path}") from exc
    except yaml.YAMLError as exc:
        raise SchemaMismatch(f"[onboard] схема {path} не разбирается: {exc}") from exc

    if not isinstance(raw, dict):
        raise SchemaMismatch(f"[onboard] схема {path}: ожидали словарь верхнего уровня")

    # Имя ключа — `schema_version`, ОДНО на весь пайплайн: тем же ключом
    # версия едет в brief.json и в report.json. Два имени на одну вещь уже
    # стоили этому проекту отдельного разбора; здесь они дополнительно
    # развели бы сторожа и реализацию (17.08: сторож писал `schema_version`,
    # реализация читала `version`, и одна буква глушила 23 проверки разом).
    version = raw.get("schema_version")
    if not isinstance(version, int):
        raise SchemaMismatch(
            f"[onboard] схема {path}: нет целочисленного `schema_version`")

    fields_raw = raw.get("fields")
    if not isinstance(fields_raw, list) or not fields_raw:
        raise SchemaMismatch(f"[onboard] схема {path}: `fields` пуст или не список")

    problems: list[str] = []
    fields: dict[str, dict] = {}
    by_col: dict[int, str] = {}
    for entry in fields_raw:
        if not isinstance(entry, dict):
            problems.append(f"поле {entry!r}: ожидали словарь")
            continue
        missing = [k for k in _REQUIRED_FIELD_KEYS if k not in entry]
        if missing:
            problems.append(f"поле {entry.get('id', entry)!r}: нет ключей {missing}")
            continue
        # Пустой отпечаток = схему не дозаполнили. Ловим ЗДЕСЬ, а не при
        # сравнении: там он неотличим от честного совпадения, и колонка
        # осталась бы без сторожа молча — при том что позицию она сторожит
        # одна. В настоящей форме пустых отпечатков нет ни одного (замер
        # 17.08 по всем 57 колонкам), так что это ловушка на будущую правку.
        if not entry.get("fingerprint"):
            problems.append(
                f"поле {entry.get('id', entry)!r}: пустой `fingerprint` — "
                f"колонку сторожить нечем, дозаполни схему")
            continue
        fid, col = entry["id"], entry["col"]
        if not isinstance(col, int):
            problems.append(f"поле {fid}: `col` не целое ({col!r})")
            continue
        if fid in fields:
            problems.append(f"поле {fid}: id встречается дважды")
            continue
        if col in by_col:
            problems.append(f"колонка {col}: занята сразу двумя полями — {by_col[col]} и {fid}")
            continue
        if entry["target"] not in TARGETS:
            problems.append(f"поле {fid}: неизвестный target {entry['target']!r}, ждали {TARGETS}")
            continue
        if entry["type"] not in TYPES:
            problems.append(f"поле {fid}: неизвестный type {entry['type']!r}, ждали {TYPES}")
            continue
        if not isinstance(entry["fingerprint"], list) or not entry["fingerprint"]:
            problems.append(f"поле {fid}: пустой fingerprint — сторожить позицию нечем")
            continue
        by_col[col] = fid
        fields[fid] = {
            "id": fid,
            "col": col,
            "fingerprint": [str(t) for t in entry["fingerprint"]],
            "type": entry["type"],
            "required": bool(entry["required"]),
            "target": entry["target"],
        }

    if problems:
        raise SchemaMismatch(
            f"[onboard] схема {path.name} v{version} не пригодна к применению:\n  "
            + "\n  ".join(problems),
            problems,
        )
    return {"version": version, "fields": fields}


def match_schema(headers: list[str], schema: dict) -> list[str]:
    """Сверка схемы с шапкой брифа. ПУСТОЙ список = совпало.

    Возвращаем список, а не булево: владельцу нужна карта расхождений
    (какая колонка, что ждали, что нашли, насколько совпало), иначе на
    правку схемы уходит вечер сличения глазами.
    """
    fields = schema.get("fields", {})
    problems: list[str] = []
    covered: set[int] = set()

    for fid, field in sorted(fields.items(), key=lambda kv: kv[1]["col"]):
        col = field["col"]
        expected = set(field["fingerprint"])
        if col >= len(headers):
            # Чего ждали — называем и здесь. Карта существует, чтобы человек
            # сверил форму со схемой, а «в брифе нет» без предмета заставляет
            # открывать схему за каждой строкой.
            problems.append(
                f"колонка {col}: в схеме есть ({fid}), ожидали "
                f"~«{' '.join(sorted(expected))}», в брифе нет")
            continue
        covered.add(col)
        found_text = _to_text(headers[col]).strip()
        score = fingerprint_score(expected, normalize_question(found_text))
        if score < FINGERPRINT_THRESHOLD:
            problems.append(
                f"колонка {col}: ожидали ~«{' '.join(sorted(expected))}», "
                f"нашли «{found_text}» (совпадение {score:.2f}, порог {FINGERPRINT_THRESHOLD})"
            )

    for col, header in enumerate(headers):
        if col not in covered and _to_text(header).strip():
            problems.append(f"колонка {col}: в брифе есть («{_to_text(header).strip()}»), в схеме нет")

    return problems


# ─────────────────────────────────────────────────────────────────────────────
# Разбор брифа
# ─────────────────────────────────────────────────────────────────────────────

def _read_rows(xlsx_path) -> list[tuple]:
    wb = load_workbook(Path(xlsx_path), read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        return [row for row in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _jsonable(raw):
    """Сырое значение в форму, переживающую json.dump, БЕЗ потери содержания.

    Числа и строки едут как есть — «как есть» в контракте означает именно это.
    Дата приводится к ISO-строке: другого способа положить её в JSON нет, а
    терять `raw` нельзя ни у одного поля.
    """
    if isinstance(raw, (_dt.datetime, _dt.date, _dt.time)):
        return _to_text(raw)
    if raw is None or isinstance(raw, (str, int, float, bool)):
        return raw
    return str(raw)


def _normalize_value(text: str) -> str:
    """Нормализация ЗНАЧЕНИЯ (не отпечатка): только то, что нельзя не сделать.

    Снимаем NBSP и хвостовые пробелы, приводим переводы строк к `\\n`. Ни
    апострофы, ни десятичный разделитель здесь НЕ трогаем: это правила
    ФОРМАТА выходных файлов (R1/R6), они принадлежат генератору, и сделанные
    здесь спрятали бы от отчёта то, что клиент написал на самом деле.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(" " if ch in _SPACES and ch != "\n" else ch for ch in text)
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip()


def parse_brief(xlsx_path, schema, *, row: int = 1) -> dict:
    """Бриф → структура `brief.json` (контракт плана, раздел «Контракт данных»).

    Строка 0 — тексты вопросов, строка `row` — ответы одной заявки.
    Расхождение схемы и брифа = `SchemaMismatch` с картой: прогон, собравший
    конфиг из непроверенных колонок, не доказал бы ничего.
    """
    xlsx_path = Path(xlsx_path)
    rows = _read_rows(xlsx_path)
    if not rows:
        raise SchemaMismatch(f"[onboard] бриф {xlsx_path.name} пуст — разбирать нечего")

    headers = [_to_text(h) for h in rows[0]]
    problems = match_schema(headers, schema)
    if problems:
        raise SchemaMismatch(
            f"[onboard] схема form_schema.yaml v{schema.get('version')} "
            f"не совпала с брифом {xlsx_path.name}:\n  "
            + "\n  ".join(problems)
            + "\n  → сверь и обнови form_schema.yaml (версия схемы += 1)",
            problems,
        )

    if row >= len(rows):
        raise SchemaMismatch(
            f"[onboard] в брифе {xlsx_path.name} нет строки {row}: "
            f"строк с ответами {max(len(rows) - 1, 0)}",
            [f"строка {row}: в брифе нет"],
        )
    answers = rows[row]

    fields: dict[str, dict] = {}
    for fid, field in sorted(schema["fields"].items(), key=lambda kv: kv[1]["col"]):
        col = field["col"]
        raw = answers[col] if col < len(answers) else None
        question = headers[col]
        question_tokens = normalize_question(question)
        verdict, reason = classify_value(raw, question_tokens, field_type=field["type"])
        fields[fid] = {
            "col": col,
            "question": question,
            "raw": _jsonable(raw),
            "value": _normalize_value(_to_text(raw)) if verdict == "ok" else None,
            "verdict": verdict,
            "reason": reason,
            "target": field["target"],
        }

    return {
        "schema_version": schema.get("version", SCHEMA_VERSION),
        "source": xlsx_path.name,
        "fields": fields,
    }
