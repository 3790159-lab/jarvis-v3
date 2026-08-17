# -*- coding: utf-8 -*-
"""T3 арки `chatter.onboard`: отчёт онбординга (спека §3, план «Контракт данных»).

Сторожа написаны ОТ СПЕКИ. `chatter/onboard/report.py` при написании НЕ читался
и не грепался — иначе тест и код наследуют одно допущение и оба зеленеют на
неверном поведении. Читались только спека, план, `brief.py` и `vocabulary.py`.

**Что здесь сторожится и почему именно это.** Спека называет отчёт главной
ценностью арки и тут же называет его риск (§7): *отчёт может стать зелёной
ширмой*. Ширма — это не «markdown не собрался»; ширма — это отчёт, который
СОБРАЛСЯ, выглядит полным и при этом молча потерял ровно то, ради чего написан.
Все способы потерять, известные из ручной сборки Ярины:

  1. **Мусор исчез вместо того, чтобы быть процитированным.** Детектор понижает
     поле до «ответа нет» (§1.2), и если отчёт покажет только «ответа нет», то
     «клиент не ответил» станет неотличимо от «мы не разобрали ответ». Разная
     цена: первое — вопрос клиенту, второе — баг детектора, который никто не
     заметит.
  2. **Обязательный факт пропал молча.** R7: либо данные, либо заглушка ПЛЮС
     строка отчёта. Пропущенный факт = бот, который его выдумает лиду.
  3. **Конфликт с брифом растворился среди обычных дефолтов.** У Ярины таких
     было ДВА (Q15 honesty, Q14 имя персоны), и оба были решениями владельца.
     Незамеченный конфликт = наше умолчание, выданное за решение клиента.
  4. **Пустой блок неотличим от несобравшегося.** Отсюда требование печатать
     «флагов нет» словами.
  5. **Счётчик разошёлся с содержимым.** Цифра говорит «всё на месте» — это
     ширма в чистом виде.
  6. **`report.json` зелёный, а `REPORT.md` куцый.** Проверки довольны, человек
     не предупреждён.

Фикстуры собраны здесь же. Настоящий бриф клиента (имя, контакты, внутренние
цены живого человека) не читается ни одной строкой.

`render_result` — заглушка: T2 пишется параллельно, `render` не импортируется.
Фикстура намеренно построена так, что «какие факты не закрыты» одинаково
считается ДВУМЯ способами — по `render_result.stubs` и по самому брифу
(источник факта отсутствует либо забракован). Если реализация выберет любой из
них, ответ один и тот же, и сторож проверяет ПАРНОСТЬ (C10), а не мою догадку
о том, откуда отчёт берёт список.
"""
from __future__ import annotations

import json
import re

import pytest

from chatter.onboard import report, vocabulary

# ─────────────────────────────────────────────────────────────────────────────
# Опознание блоков отчёта
#
# Спека даёт названия разделов текстом (§3), но не даёт разметки. Требование,
# которое фиксирую я: каждый из четырёх блоков — markdown-ЗАГОЛОВОК. Причина
# не косметическая: раздел без заголовка нельзя ни найти глазами в длинном
# файле, ни отличить от абзаца внутри соседнего раздела, а §3 требует, чтобы
# блок ФЛАГИ шёл ПОСЛЕ трёх разделов и их НЕ ЗАМЕНЯЛ — «после» и «не заменяет»
# проверяемы только при наличии границ.
#
# Язык отчёта спека не фиксирует: сама она пишет «флагов нет» по-русски, а
# готовые вопросы клиенту в `vocabulary` — по-украински (отчёт читает владелец,
# вопросы уходят клиенту). Поэтому маркеры принимаются в обоих языках: спор об
# языке заголовка не должен глушить сторожей поведения.
# ─────────────────────────────────────────────────────────────────────────────

SECTION_MARKERS = {
    "taken": ("взято из брифа", "взято з брифу", "взято із брифу", "взято з бріфу"),
    "defaulted": ("подставлено дефолтом", "підставлено дефолтом", "дефолт"),
    "missing": ("в брифе нет", "у брифі немає", "в брифі немає", "немає в брифі"),
    "flags": ("флаг",),
}

CONFLICT_MARKERS = (
    "бриф просил другое", "бриф просив інше", "бриф просил иное",
    "бриф хотел другое", "конфликт", "конфлікт",
)

NO_FLAGS_RE = re.compile(
    r"(флаг\w*\s+(нема|нет|відсутн|отсутств))|((нема\w*|нет)\s+флаг)", re.IGNORECASE)

COUNTER_LABELS = {
    "services": ("услуг", "послуг"),
    "prices": ("цен", "цін"),
    "deadlines": ("срок", "терм", "тривал"),
    "stop_words": ("стоп",),
    "forbidden": ("заборон", "запрещ", "заборонен"),
    "example_pairs": ("прикла", "пример"),
}


def heading_lines(md: str) -> list[tuple[int, str]]:
    return [(i, line) for i, line in enumerate(md.splitlines())
            if line.lstrip().startswith("#")]


def heading_index(md: str, key: str) -> int:
    """Номер строки заголовка блока `key`, или -1."""
    for i, line in heading_lines(md):
        low = line.casefold()
        if any(m in low for m in SECTION_MARKERS[key]):
            return i
    return -1


def line_with(md: str, *parts: str) -> str | None:
    """Первая строка, содержащая ВСЕ куски. `None`, если такой строки нет."""
    for line in md.splitlines():
        if all(p in line for p in parts):
            return line
    return None


def block_after(md: str, markers: tuple[str, ...]) -> str | None:
    """Текст блока, открытого строкой с маркером, до следующего заголовка/линейки."""
    lines = md.splitlines()
    start = None
    for i, line in enumerate(lines):
        low = line.casefold()
        if any(m in low for m in markers):
            start = i
            break
    if start is None:
        return None
    out = [lines[start]]
    for line in lines[start + 1:]:
        s = line.strip()
        if s.startswith("#"):
            break
        if len(s) >= 3 and set(s) <= set("-=_*"):
            break
        out.append(line)
    return "\n".join(out)


def _label_hits(line: str) -> list[tuple[int, str, int]]:
    low = line.casefold()
    hits = []
    for name, tokens in COUNTER_LABELS.items():
        for tok in tokens:
            i = low.find(tok)
            if i >= 0:
                hits.append((i, name, len(tok)))
                break
    return sorted(hits)


def counter_is_shown(md: str, name: str, value: int) -> bool:
    """Значение счётчика стоит ПРИ СВОЁМ ярлыке, а не где-то на странице.

    Ищем в области строки, принадлежащей именно этому ярлыку (от конца
    предыдущего ярлыка до начала следующего): иначе перепутанные местами
    «услуг» и «цен» прошли бы, пока оба числа лежат на одной строке.
    """
    pat = re.compile(rf"(?<!\d){re.escape(str(value))}(?!\d)")
    for line in md.splitlines():
        hits = _label_hits(line)
        for idx, (pos, hit_name, tlen) in enumerate(hits):
            if hit_name != name:
                continue
            start = hits[idx - 1][0] + hits[idx - 1][2] if idx else 0
            end = hits[idx + 1][0] if idx + 1 < len(hits) else len(line)
            if pat.search(line[start:end]):
                return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Фикстуры: brief.json (контракт T1) и заглушка render_result
# ─────────────────────────────────────────────────────────────────────────────

def field(col, question, raw, value, *, verdict="ok", reason=None, target="knowledge"):
    """Запись поля в форме контракта `brief.json` (план, «Контракт данных»)."""
    return {"col": col, "question": question, "raw": raw, "value": value,
            "verdict": verdict, "reason": reason, "target": target}


PRICE_VALUE = "Полірування кузова — ціна 5 000–8 000 грн, тривалість 3 години"
ICP_VALUE = "Власник авто преміум-класу, готовий інвестувати в догляд"
ANTI_ICP_VALUE = "Кузовного ремонту не робимо; не беремо тих, хто вимагає гарантій"
REPLY_TIME_VALUE = "Відповідаю протягом години у робочий час"
STOP_WORD_VALUE = "хочу з власником"
FORBIDDEN_VALUE = "гарантія результату"
EXAMPLE_VALUE = "Скільки коштує полірування — 5 000–8 000 грн"

GARBAGE_LINKS = "drivepro-detailing.example"
GARBAGE_STAFF = "1 1 1"
GARBAGE_NOTIFY = "мне)"
GARBAGE_XREF = "заповнив разом"
SUSPECT_NAME = "Джарвис"

REASON_LINKS = "placeholder_url: плейсхолдер вместо реального адреса — .example"
REASON_STAFF = "numeric_stub: числовая заглушка в текстовом поле — «1 1 1»"
REASON_NOTIFY = "stub_fragment: огрызок — «мне)» несёт след обрыва"
REASON_XREF = "cross_reference: ответ ссылается на другое поле — «заповнив разом»"
REASON_NAME = "our_name: в поле клиента стоит наше имя — «джарвис»"
REASON_EMPTY = "empty: поле не заполнено"
REASON_HOURS = "empty: поле не заполнено"


def brief_doc(*, with_q38: bool = True) -> dict:
    """Бриф одного вымышленного клиента: чистые поля, мусор и пустоты вместе.

    Состав подобран так, чтобы каждый требуемый класс поведения имел и
    положительный, и отрицательный пример: без чистых полей раздел 1 нечем
    проверить, без мусора — раздел 3, без закрытого факта нельзя показать, что
    отчёт не спрашивает о том, что уже знает.
    """
    fields = {
        "q0_timestamp": field(0, "Отметка времени", "17.08.2026 09:00:00",
                              "17.08.2026 09:00:00", target="report_only"),
        "q4_links": field(4, "Сайт, соцмережі, портфоліо", GARBAGE_LINKS, None,
                          verdict="garbage", reason=REASON_LINKS),
        "q10_staff": field(10, "Хто відповідає клієнтам зараз?", GARBAGE_STAFF, None,
                           verdict="garbage", reason=REASON_STAFF),
        "q12_hours": field(12, "Графік роботи студії", None, None,
                           verdict="garbage", reason=REASON_HOURS),
        "q14_persona_name": field(14, "Як звати вашого адміністратора у переписці?",
                                  SUSPECT_NAME, None, verdict="suspect",
                                  reason=REASON_NAME, target="settings"),
        "q20_examples": field(20, "Приклади вдалих діалогів",
                              EXAMPLE_VALUE, EXAMPLE_VALUE, target="examples"),
        "q22_price_list": field(22, "Для КОЖНОЇ послуги: ціна або вилка",
                                PRICE_VALUE, PRICE_VALUE),
        "q25_not_provided": field(25, "Які послуги ви НЕ надаєте?", GARBAGE_XREF, None,
                                  verdict="garbage", reason=REASON_XREF),
        "q34_stop_words": field(34, "Коли кликати власника?", STOP_WORD_VALUE,
                                STOP_WORD_VALUE, target="playbook"),
        "q35_reply_time": field(35, "За який час ви відповідаєте?",
                                REPLY_TIME_VALUE, REPLY_TIME_VALUE),
        "q36_notify": field(36, "Кому надсилати сповіщення про заявки?",
                            GARBAGE_NOTIFY, None, verdict="garbage",
                            reason=REASON_NOTIFY, target="settings"),
        "q37_icp": field(37, "Хто ваш ідеальний клієнт?", ICP_VALUE, ICP_VALUE,
                         target="playbook"),
        "q50_forbidden_phrases": field(50, "Яких фраз не вживати?", FORBIDDEN_VALUE,
                                       FORBIDDEN_VALUE, target="settings"),
        "q55_metrics": field(55, "Метрики успіху", None, None,
                             verdict="garbage", reason=REASON_EMPTY,
                             target="report_only"),
    }
    if with_q38:
        fields["q38_anti_icp"] = field(38, "Кому ви відмовляєте і чого не робите?",
                                       ANTI_ICP_VALUE, ANTI_ICP_VALUE,
                                       target="playbook")
    return {"schema_version": 1, "source": "brief.xlsx", "fields": fields}


# Факты, закрытые брифом, и факты без данных — считаются ПО БРИФУ, а не
# переписаны руками: иначе фикстура и заглушка `stubs` разъедутся, и сторож
# начнёт проверять мою же опечатку.
def facts_without_data(doc: dict) -> list:
    out = []
    for fact in vocabulary.REQUIRED_FACTS:
        rec = doc["fields"].get(fact.source) if fact.source else None
        if rec is None or rec["verdict"] != "ok":
            out.append(fact)
    return out


# ДВЕ формы на одну роль, и путать их нельзя — это ровно тот дефект, который
# сторожа T2 нашли в живом тексте («точну дату підтверджує нашим старшим
# майстром»). Именительный стоит подлежащим, орудный — только после предлога.
OWNER_NOMINATIVE = "старший майстер"
OWNER_REF = "нашим старшим майстром"

DEFAULTS = [
    {"key": "honesty_mode", "value": "honest",
     "why": "бот не приховує, що він бот — це наш жорсткий дефолт",
     "brief_wanted_other": True,
     "brief_quote": "спілкуватись як людина, не казати що це AI"},
    {"key": "persona_name", "value": "Ольга",
     "why": "ім'я персони обирає власник, у брифі стоїть наше внутрішнє ім'я",
     "brief_wanted_other": True,
     "brief_quote": SUSPECT_NAME},
    {"key": "funnel_gate", "value": False,
     "why": "воронка вмикається лише командою власника",
     "brief_wanted_other": False, "brief_quote": None},
    {"key": "strict_knowledge", "value": True,
     "why": "бот говорить лише те, що є в knowledge",
     "brief_wanted_other": False, "brief_quote": None},
    {"key": "payments", "value": "off",
     "why": "реквізитів у брифі немає",
     "brief_wanted_other": False, "brief_quote": None},
    {"key": "owner_ref", "value": OWNER_REF,
     "why": "відмінок підставляє людина",
     "brief_wanted_other": False, "brief_quote": None},
]

CONFLICT_KEYS = [d["key"] for d in DEFAULTS if d["brief_wanted_other"]]
PLAIN_KEYS = [d["key"] for d in DEFAULTS if not d["brief_wanted_other"]]

COUNTERS = {"services": 14, "prices": 31, "deadlines": 18,
            "stop_words": 7, "forbidden": 23, "example_pairs": 11}

FLAGS = [
    {"check": "C12", "file": "knowledge.md", "line": 262,
     "quote": "Старший майстер відповідає протягом години",
     "question": "Це законна обіцянка від імені майстра чи дефект?"},
    {"check": "C13", "file": "knowledge.md", "line": 240,
     "quote": "посилань немає",
     "question": "Сайт є чи його немає — що з двох правда?"},
]

def knowledge_md(stub_lines: list[str]) -> str:
    """Каталог клиента настолько похож на настоящий, насколько это нужно отчёту.

    🔴 Это не украшательство. Первая версия заглушки держала два раздела и
    ничего кроме прайса — и сторожа тут же покраснели на «якорь не найден».
    Причина была в ФИКСТУРЕ: якорь — это МЕСТО, куда значение уехало, и найти
    его в файле, где значения нет, невозможно честным способом. Заглушка,
    непохожая на артефакт, порождает находки, которых в проде не будет, — и
    она же прячет настоящие. Поэтому здесь все девять обязательных разделов
    (R8) и все значения полей с `target: knowledge`.
    """
    body = [
        "# Про студію", "Детейлінг-студія у Києві, працюємо з 2019 року", "",
        "# Послуги та ціни", "- " + PRICE_VALUE, "",
        "# Від чого залежить фінальна ціна", "Стан кузова та обраний матеріал", "",
        "# Оплата та передоплата", "Передоплата 30 відсотків", "",
        "# Гарантії та якість роботи", "Переробимо, якщо є наш недогляд", "",
        "# Межі можливого — що детейлінг НЕ вирішує", "Глибокі вм'ятини не прибираємо", "",
        "# Чого ми не робимо", "Кузовного ремонту не робимо", "",
        "# Як записатися", REPLY_TIME_VALUE, "",
        "# " + vocabulary.UNKNOWN_SECTION_UK,
    ]
    body += ["- " + line for line in stub_lines]
    return "\n".join(body)


PLAYBOOK_MD = "\n".join([
    "# Ідеальний клієнт", ICP_VALUE, "",
    "# Кому відмовляємо і чого не робимо", ANTI_ICP_VALUE, "",
    "# Ключові слова ескалації", "- " + STOP_WORD_VALUE,
])

EXAMPLES_YAML = "\n".join([
    "- client: " + EXAMPLE_VALUE,
    "  olga: Полірування 5 000–8 000 грн, підкажіть модель авто?",
])

SETTINGS_YAML = "\n".join([
    "honesty_mode: honest",
    "funnel_gate: false",
    "strict_knowledge: true",
    "persona_name: Ольга",
    "owner_ref: " + OWNER_REF,
    "forbidden_terms:",
    "  - " + FORBIDDEN_VALUE,
])


class FakeRenderResult:
    """Заглушка выхода T2. Ровно четыре атрибута из контракта задачи.

    `stubs` — {id факта: строка заглушки, дословно ушедшая в knowledge}. Форма
    угадана (контракт её не фиксирует), поэтому ни один сторож не опирается на
    неё как на единственный источник: список незакрытых фактов независимо
    выводится из самого брифа и обязан совпасть.
    """

    def __init__(self, doc, *, defaults=None, counters=None):
        self.defaults = [dict(d) for d in (DEFAULTS if defaults is None else defaults)]
        self.stubs = {
            # Плейсхолдер называется `owner` и требует ИМЕНИТЕЛЬНОГО падежа:
            # позиция подлежащая («називає X»). `owner_ref` — орудный, он
            # годится только после предлога, и подстановка его сюда давала бы
            # лиду «Адреса — називає нашим старшим майстром».
            f.id: vocabulary.STUB_TEMPLATE_UK.format(title=f.title_uk, owner=OWNER_NOMINATIVE)
            for f in facts_without_data(doc)
        }
        self.files = {
            "knowledge.md": knowledge_md(list(self.stubs.values())),
            "playbook.md": PLAYBOOK_MD,
            "persona.md": "Мене звати Ольга, мені 29, я адміністратор студії",
            "examples.yaml": EXAMPLES_YAML,
            "settings.yaml": SETTINGS_YAML,
        }
        self.counters = dict(COUNTERS if counters is None else counters)


@pytest.fixture
def doc():
    return brief_doc()


@pytest.fixture
def rendered(doc):
    return FakeRenderResult(doc)


@pytest.fixture
def built(doc, rendered):
    return report.build_report(doc, rendered, slug="drivepro", flags=list(FLAGS))


@pytest.fixture
def md(built):
    return built.markdown


@pytest.fixture
def document(built):
    return built.document


# ══ 1. ФОРМА ОТЧЁТА: КОНТРАКТ report.json ══════════════════════════════════

def test_document_is_json_serializable(document):
    """Класс ошибки: `report.json` невозможно записать.

    Документ — не «внутренняя структура», это ФАЙЛ, который читает T4 (§4:
    C-проверки стоят на нём). Датакласс, множество или `RequiredFact` внутри
    роняют запись уже после того, как отчёт «собрался», — то есть на приёмке, а
    не в момент ошибки.
    """
    json.dumps(document, ensure_ascii=False)


def test_document_carries_the_four_contract_keys(document):
    """Класс ошибки: T4 пишется от контракта плана и не найдёт своих ключей.

    `schema_version`, `sections`, `flags`, `counters` — согласованный интерфейс
    между двумя параллельными задачами; переименование любого = молчаливо
    пропущенная проверка на приёмке.
    """
    for key in ("schema_version", "sections", "flags", "counters"):
        assert key in document, f"нет ключа контракта `{key}`: {sorted(document)}"
    assert document["schema_version"] == 1


def test_sections_are_exactly_the_three_declared_ones(document):
    """Класс ошибки: разделов оказалось два или четыре.

    Спека даёт РОВНО три раздела плюс блок флагов отдельно. Лишний раздел в
    `sections` увёл бы флаги внутрь трёх — а §3 требует, чтобы блок флаги шёл
    после и разделов НЕ ЗАМЕНЯЛ.
    """
    assert set(document["sections"]) == {"taken", "defaulted", "missing"}
    for name, rows in document["sections"].items():
        assert isinstance(rows, list), f"{name}: ожидали список строк"


def test_all_three_sections_and_the_flag_block_exist_in_markdown(md):
    """Класс ошибки: раздел, исчезающий при пустоте.

    Пропавший раздел читается человеком как «проверка не проводилась», и это
    худший из возможных сигналов: он не отличим от «всё чисто».
    """
    for key in ("taken", "defaulted", "missing", "flags"):
        assert heading_index(md, key) >= 0, (
            f"нет заголовка блока `{key}`; заголовки: {[h for _, h in heading_lines(md)]}")


def test_blocks_keep_the_declared_order_flags_last(md):
    """Класс ошибки: блок ФЛАГИ уехал выше и подменил собой раздел 3.

    §3 прямо: блок флагов идёт ПОСЛЕ трёх разделов и их не заменяет. Флаги —
    это «решает владелец», а раздел 3 — «чего в брифе нет»; человек, дочитавший
    до флагов, считает отчёт прочитанным.
    """
    idx = {k: heading_index(md, k) for k in ("taken", "defaulted", "missing", "flags")}
    assert idx["taken"] < idx["defaulted"] < idx["missing"] < idx["flags"], idx


def test_sections_survive_when_there_is_nothing_to_put_in_them(doc):
    """Класс ошибки: пустой раздел не печатается вовсе.

    Прогон без дефолтов и без флагов обязан дать ТУ ЖЕ структуру: четыре блока
    на месте, `defaulted` — пустой список, а не отсутствующий ключ.
    """
    rendered = FakeRenderResult(doc, defaults=[])
    built = report.build_report(doc, rendered, slug="drivepro", flags=[])
    assert built.document["sections"]["defaulted"] == []
    assert built.document["flags"] == []
    for key in ("taken", "defaulted", "missing", "flags"):
        assert heading_index(built.markdown, key) >= 0, f"пустой блок `{key}` исчез"


def test_report_says_whose_report_this_is(md):
    """Класс ошибки: отчёты двух клиентов неразличимы.

    Онбординг — повторяющаяся операция (§0); два `REPORT.md` рядом без slug'а
    приводят к вычитке чужого отчёта, и это не заметно вообще ничем.
    """
    assert "drivepro" in md


# ══ 2. РАЗДЕЛ 1 — ВЗЯТО ИЗ БРИФА ═══════════════════════════════════════════

TAKEN_KEYS = {"field_id", "target_file", "anchor", "quote"}


def test_taken_rows_carry_the_contract_keys(document):
    """Класс ошибки: строка раздела 1 без адреса.

    `id → файл:якорь ← цитата` — это маршрут вычитки. Без `target_file`/`anchor`
    владелец ищет строку руками по пяти файлам и перестаёт это делать.
    """
    assert document["sections"]["taken"], "раздел 1 пуст при чистых полях брифа"
    for row in document["sections"]["taken"]:
        assert TAKEN_KEYS <= set(row), f"нет ключей {sorted(TAKEN_KEYS - set(row))}: {row}"
        assert isinstance(row["quote"], str) and row["quote"].strip(), f"пустая цитата: {row}"


def test_no_row_of_section_one_is_left_without_a_place(document):
    """Класс ошибки: строка «взято», которая не говорит КУДА взято.

    `None` в `target_file`/`anchor` печатается человеку как «None» и читается
    как адрес — то есть хуже, чем отсутствие строки. Спека даёт разделу 1 ровно
    один формат: `id → файл:якорь ← цитата`; поле, которому нечего показать в
    двух из четырёх позиций, не «взято из брифа», и его место — раздел 3 или
    вообще нигде. Отдельно про `None` как строку: `str(None)` истинна, и
    наивная проверка на непустоту такой якорь пропускает — эту дыру в самом
    сторожe пришлось закрывать явной проверкой типа.
    """
    for row in document["sections"]["taken"]:
        for key in ("target_file", "anchor"):
            val = row[key]
            assert isinstance(val, str) and val.strip(), (
                f"{row['field_id']}: `{key}` = {val!r} — строка раздела 1 без места")


def test_every_clean_field_reaches_section_one(document, doc):
    """Класс ошибки: чистое поле не доехало ни в один раздел.

    Поле, которого нет ни в разделе 1, ни в разделе 3, для владельца не
    существует: он не знает ни что оно взято, ни что оно потеряно. Это и есть
    зелёная ширма — отчёт полон, а поля нет.
    """
    reported = {row["field_id"] for row in document["sections"]["taken"]}
    for fid, rec in doc["fields"].items():
        if rec["verdict"] == "ok" and rec["target"] != "report_only":
            assert fid in reported, f"{fid}: чистое поле не названо в разделе 1"


# ══ ВОПРОС ПРО НАЗВАНИЕ РОЛИ (решение владельца 17.08, пункт 5) ════════════
#
# Роль уезжает лиду в каждой второй реплике («зв'яжу вас зі старшим майстром»),
# а её происхождение лиду не видно — тестировщик принял законную роль за
# выдумку бота. Чинить в коде нечего: роль верна. Спросить — обязательно.

def _questions_block(md: str) -> list[str]:
    lines = md.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("### 3.3."))
    out = []
    for ln in lines[start + 1:]:
        if ln.startswith("###"):
            break
        if ln.strip() and ln.strip()[0].isdigit():
            out.append(ln.strip())
    return out


def test_the_client_is_always_asked_how_to_call_the_role(md):
    """Ловит: вопрос, который задают только когда поле пустое.

    Роль ОТВЕЧЕНА в брифе, поэтому по обычной логике раздела 3 её здесь быть
    не должно — а спросить надо именно про отвеченное: клиент назвал слово, а
    услышит его КЛИЕНТ КЛИЕНТА, и звучать оно может неуместно.
    """
    questions = " ".join(_questions_block(md))
    assert "менеджер" in questions and "називаємо відповідального" in questions, questions


def test_the_role_question_quotes_the_clients_own_word(doc, rendered):
    """Ловит: вопрос «как называть роль?» без самой роли.

    Без цитаты клиент не поймёт, о чём его спрашивают, и ответит наугад.
    """
    doc["fields"]["q16_owner_ref"] = field(
        16, "Як звертатись до відповідального?", "старший мастер ", "старший мастер")
    built = report.build_report(doc, rendered, slug="drivepro", flags=list(FLAGS))

    questions = " ".join(_questions_block(built.markdown))
    assert "«старший мастер»" in questions, questions


def test_the_role_question_comes_last(md):
    """Ловит: вопрос про слово, вставший впереди вопросов про пропущенные факты.

    Первые пункты списка человек отправляет наверняка, последние — как
    получится. Пропущенный адрес дороже спора о слове, и порядок это говорит.
    """
    questions = _questions_block(md)
    assert "називаємо відповідального" in questions[-1], questions[-3:]


def test_a_rejected_field_never_looks_like_a_taken_one(document, doc):
    """Класс ошибки: мусор попал в раздел «ВЗЯТО ИЗ БРИФА».

    §1.2: `garbage`/`suspect` в конфиг не попадают НИ ПРИ КАКИХ условиях. Строка
    раздела 1 утверждает обратное — что значение уехало в файл клиента, — и
    вычитка пройдёт мимо него как мимо сделанного.
    """
    reported = {row["field_id"] for row in document["sections"]["taken"]}
    for fid, rec in doc["fields"].items():
        if rec["verdict"] != "ok":
            assert fid not in reported, f"{fid} ({rec['verdict']}) выдан за взятый из брифа"


def test_the_quote_is_the_clients_own_text(document, doc):
    """Класс ошибки: в разделе 1 стоит пересказ вместо цитаты.

    Раздел 1 существует, чтобы сверить наш файл с тем, ЧТО НАПИСАЛ КЛИЕНТ.
    Пересказ сверять не с чем — вычитка становится ритуалом.
    """
    for row in document["sections"]["taken"]:
        source = doc["fields"][row["field_id"]]
        core = str(row["quote"]).strip().rstrip("…. ").strip()
        assert core and core in (source["value"] or ""), (
            f"{row['field_id']}: цитата «{row['quote']}» не встречается в значении поля")


@pytest.mark.parametrize("value", [
    "Київ",              # місто — вопрос-справочник, ответ короче некуда
    "хочу з власником",  # стоп-слово (R4): полная фраза, и она КОРОТКАЯ по природе
    "гарантія",          # запрещённое слово (R6)
])
def test_a_short_value_gets_a_place_just_like_a_long_one(doc, value):
    """Класс ошибки: место находится только для длинных значений.

    Короткое значение — не край случая, а целый класс полей: стоп-слова (R4)
    и запрещённые фразы (R6) короткие ПО НАЗНАЧЕНИЮ, город и телефон тоже.
    Порог длины, унаследованный от сравнения фрагментов (C9 берёт ≥ 40
    символов), в разделе 1 означает другое: строка «взято» без места. И она
    не пустая — она печатает «None», то есть выглядит как адрес.

    Тот же порог однажды уже стоил этому проекту разбора: правило, законное на
    своём слое, беззвучно съедает соседний, если перенести его целиком.
    """
    doc["fields"]["q34_stop_words"]["raw"] = value
    doc["fields"]["q34_stop_words"]["value"] = value
    rendered = FakeRenderResult(doc)
    rendered.files["playbook.md"] = "# Ключові слова ескалації\n- " + value + "\n"
    built = report.build_report(doc, rendered, slug="drivepro", flags=[])
    row = {r["field_id"]: r for r in built.document["sections"]["taken"]}["q34_stop_words"]
    assert isinstance(row["anchor"], str) and row["anchor"].strip(), (
        f"значение «{value}» ({len(value)} симв.) взято из брифа, но места не получило")
    # УЖЕСТОЧЕНО ИНТЕГРАТОРОМ 17.08. Прежняя проверка требовала лишь непустую
    # строку — и её удовлетворяла заглушка «(якір не знайдено)». Мутация
    # «убрать заход для коротких значений» проходила ЗЕЛЁНОЙ: сторож
    # подтверждал наличие места, которого нет. Это ровно та зелёная ширма, от
    # которой сторожа и ставят, только на этаж выше — в самом стороже.
    assert row["anchor"] != report.ANCHOR_NOT_FOUND, (
        f"значение «{value}» ({len(value)} симв.) получило ЗАГЛУШКУ вместо места: "
        f"строка выглядит найденной, а якоря нет")
    assert "ескалації" in row["anchor"], (
        f"якорь «{row['anchor']}» не называет раздел, в котором значение лежит")


def test_a_playbook_field_is_not_reported_as_going_into_knowledge(document, doc):
    """Класс ошибки: отчёт указывает вычитке НЕ ТОТ файл.

    §1.3: ICP, анти-ICP и ЦА едут только в playbook, потому что knowledge — то,
    из чего бот ГОВОРИТ лиду. Отчёт, отправивший вычитку ICP в knowledge.md,
    закрепляет ровно ту ошибку, ради которой заведена C9.

    `report_only` здесь не судится: у него файла нет по определению, и это
    отдельное требование со своим сторожем выше.
    """
    by_id = {row["field_id"]: row for row in document["sections"]["taken"]}
    for fid, rec in doc["fields"].items():
        row = by_id.get(fid)
        if row is None or rec["target"] == "report_only":
            continue
        target_file = str(row["target_file"]).casefold()
        assert rec["target"] in target_file, (
            f"{fid}: target={rec['target']}, а отчёт шлёт в {row['target_file']}")


def test_section_one_rows_are_one_readable_line_each(md, document):
    """Класс ошибки: `report.json` знает адрес, а `REPORT.md` — нет.

    §3: «строка на поле: id → файл:якорь ← цитата». Если id, файл, якорь и
    цитата разъехались по разным строкам или часть не напечатана, машинная
    проверка зелёная, а человек всё равно ищет руками.
    """
    for row in document["sections"]["taken"]:
        core = str(row["quote"]).strip().rstrip("…. ").strip()
        assert line_with(md, row["field_id"], str(row["target_file"]),
                         str(row["anchor"]), core), (
            f"{row['field_id']}: в markdown нет строки с файлом, якорем и цитатой сразу")


# ══ 3. РАЗДЕЛ 3 — МУСОР ЦИТИРУЕТСЯ ДОСЛОВНО ════════════════════════════════

MISSING_KEYS = {"field_id", "raw", "verdict", "reason", "stub", "question_for_client"}


def missing_by_field(document) -> dict:
    return {row.get("field_id"): row for row in document["sections"]["missing"]}


def test_missing_rows_carry_the_contract_keys(document):
    """Класс ошибки: раздел 3 отдаёт T4 половину контракта.

    C10 сверяет парность «заглушка ⇔ строка отчёта» именно по этим ключам;
    отсутствующий ключ делает проверку зелёной не потому, что всё на месте, а
    потому, что ей нечего спросить.
    """
    assert document["sections"]["missing"], "раздел 3 пуст при мусоре и пустых полях в брифе"
    for row in document["sections"]["missing"]:
        assert MISSING_KEYS <= set(row), f"нет ключей {sorted(MISSING_KEYS - set(row))}: {row}"


@pytest.mark.parametrize("fid, raw, reason", [
    ("q4_links", GARBAGE_LINKS, REASON_LINKS),
    ("q10_staff", GARBAGE_STAFF, REASON_STAFF),
    ("q36_notify", GARBAGE_NOTIFY, REASON_NOTIFY),
    ("q25_not_provided", GARBAGE_XREF, REASON_XREF),
])
def test_garbage_is_quoted_verbatim_with_its_reason(document, md, fid, raw, reason):
    """Класс ошибки: детектор понизил поле, а отчёт уничтожил улику.

    §1.2: детектор НИЧЕГО НЕ УДАЛЯЕТ, он понижает поле до «ответа нет», и сырое
    значение цитируется в отчёте. Без цитаты владелец не отличит «клиент не
    ответил» от «мы не разобрали ответ»: первое — вопрос клиенту, второе — баг
    детектора, который так и останется незамеченным. Ровно эти четыре строки
    были в живом брифе.
    """
    row = missing_by_field(document).get(fid)
    assert row is not None, f"{fid}: забракованное поле не попало в раздел 3"
    assert row["raw"] == raw, f"{fid}: улика подменена — {row['raw']!r}"
    assert row["reason"] == reason, f"{fid}: причина потеряна — {row['reason']!r}"
    assert raw in md, f"{fid}: сырая цитата «{raw}» не напечатана человеку"
    assert reason in md, f"{fid}: причина не напечатана человеку"


def test_a_suspect_answer_is_shown_just_like_garbage(document, md):
    """Класс ошибки: `suspect` тише `garbage` и потому не читается.

    Q14 «Джарвис» — наше имя в поле клиента. Оно не мусор, оно РЕШЕНИЕ
    владельца, и одновременно единственный детектор протечки чужого конфига
    (R6). Пропав из отчёта, оно вернётся именем персоны живого клиента.
    """
    row = missing_by_field(document).get("q14_persona_name")
    assert row is not None, "suspect-поле не попало в раздел 3"
    assert row["verdict"] == "suspect", row
    assert row["raw"] == SUSPECT_NAME
    assert SUSPECT_NAME in md


def test_an_unanswered_field_is_distinguishable_from_an_unparsed_one(document):
    """Класс ошибки: «клиент не ответил» и «мы не разобрали ответ» слились.

    Цена у них разная: первое — строка вопроса клиенту, второе — работа над
    детектором. Пустое поле обязано приехать без выдуманной улики, поле с
    мусором — со своей.
    """
    empty = missing_by_field(document)["q55_metrics"]
    dirty = missing_by_field(document)["q10_staff"]
    assert empty["raw"] in (None, ""), f"у пустого поля выдумана улика: {empty['raw']!r}"
    assert dirty["raw"] == GARBAGE_STAFF
    assert empty["reason"], "пустое поле без причины неотличимо от бага"


def test_every_rejected_field_reaches_section_three(document, doc):
    """Класс ошибки: часть брака молча выпала.

    §3: раздел 3 — это пустые поля ПЛЮС ВСЕ `garbage`/`suspect`. «Все» здесь
    не риторика: у Ярины таких полей было одиннадцать, и любое пропущенное
    означает факт, который бот выдумает.
    """
    reported = set(missing_by_field(document))
    for fid, rec in doc["fields"].items():
        if rec["verdict"] != "ok":
            assert fid in reported, f"{fid} ({rec['verdict']}) потерян: ни в разделе 1, ни в 3"


# ══ 4. РАЗДЕЛ 3 — ОБЯЗАТЕЛЬНЫЕ ФАКТЫ (R7 / C10) ════════════════════════════

def test_the_fixture_agrees_with_itself_about_open_facts(doc, rendered):
    """Сторож на сторожа: список незакрытых фактов считается двумя способами.

    Если бы `render_result.stubs` и вывод из брифа разошлись, все проверки
    ниже доказывали бы мою опечатку в фикстуре, а не поведение отчёта. Это тот
    же класс, что «два числа на одну вещь» — меньшее гасит большее молча.
    """
    assert {f.id for f in facts_without_data(doc)} == set(rendered.stubs)
    assert len(rendered.stubs) >= 3, "мало незакрытых фактов — проверять нечего"
    assert set(rendered.stubs) != {f.id for f in vocabulary.REQUIRED_FACTS}, (
        "все факты открыты — обратная сторона (закрытый факт) не проверяется")


def test_every_open_fact_gets_both_a_stub_and_a_question(document, doc):
    """Класс ошибки: обязательный факт пропал МОЛЧА.

    R7 не оставляет третьего варианта: либо данные, либо заглушка ПЛЮС строка
    отчёта. Факт без строки — это бот, который выйдет к живым лидам и выдумает
    адрес, телефон или канал записи; узнает об этом владелец от лида.
    """
    rows = document["sections"]["missing"]
    for fact in facts_without_data(doc):
        hit = [r for r in rows if r.get("question_for_client") == fact.question_for_client_uk]
        assert hit, f"факт «{fact.id}»: нет строки раздела 3 с готовым вопросом клиенту"
        assert str(hit[0].get("stub") or "").strip(), (
            f"факт «{fact.id}»: строка есть, заглушки нет — C10 сверять нечего")


def test_the_question_for_the_client_is_copy_pasteable(md, doc):
    """Класс ошибки: вопрос клиенту пересказан своими словами.

    §3: раздел 3 существует ради ГОТОВОГО текста, который владелец копирует и
    отправляет. Формулировки живут в `vocabulary` как единственный источник;
    вторая формулировка в отчёте делает C10 слепой и заставляет владельца
    сочинять вопрос заново — то есть не отправлять его вовсе.
    """
    for fact in facts_without_data(doc):
        assert fact.question_for_client_uk in md, (
            f"факт «{fact.id}»: вопроса клиенту нет в REPORT.md дословно")


def test_the_stub_that_went_into_knowledge_is_listed_for_the_human(md, rendered):
    """Класс ошибки: заглушка уехала в knowledge, а в отчёте её нет.

    §3 требует список, ДОСЛОВНО ушедший заглушкой в «Чого ми НЕ знаємо».
    Незаписанная заглушка — это фраза, которую бот скажет лиду, а владелец
    увидит впервые в живом диалоге.
    """
    for fact_id, stub in rendered.stubs.items():
        assert stub in md, f"заглушка факта «{fact_id}» не показана человеку: {stub!r}"


def test_a_fact_the_brief_already_answered_is_not_asked_about_again(document, doc):
    """Класс ошибки: отчёт просит у клиента то, что клиент уже дал.

    Обратная сторона R7 и не менее дорогая: раздел 3, наполовину состоящий из
    лишних вопросов, перестают читать — и вместе с ними перестают читать
    настоящие. Это ровно тот механизм, которым отчёт становится ширмой.
    """
    open_ids = {f.id for f in facts_without_data(doc)}
    closed = [f for f in vocabulary.REQUIRED_FACTS if f.id not in open_ids]
    assert closed, "фикстура не содержит ни одного закрытого факта"
    questions = {r.get("question_for_client") for r in document["sections"]["missing"]}
    for fact in closed:
        assert fact.question_for_client_uk not in questions, (
            f"факт «{fact.id}» закрыт брифом, но отчёт всё равно просит его у клиента")


def test_open_facts_keep_the_declared_order(document, doc):
    """Класс ошибки: порядок фактов плавает от прогона к прогону.

    `vocabulary` объявляет порядок значимым: два прогона одного брифа обязаны
    давать построчно сравнимые отчёты, иначе `diff` двух отчётов бесполезен, а
    приёмка арки (§6) сверяет расхождения именно построчно.
    """
    order = [f.question_for_client_uk for f in facts_without_data(doc)]
    seen = [r.get("question_for_client") for r in document["sections"]["missing"]
            if r.get("question_for_client") in order]
    assert seen == order, f"порядок фактов разъехался: {seen}"


# ══ 5. Q38 — ПОЛЕ ДВОЙНОГО НАЗНАЧЕНИЯ (решение владельца 17.08) ════════════

def test_the_dual_purpose_line_is_in_section_three(document, md):
    """Класс ошибки: решение владельца по Q38 не доехало до вычитки.

    Полный текст Q38 уезжает в playbook, раздел knowledge «Чого ми не робимо»
    собирается из него, и РАЗВЕСТИ их автоматом мы отказались навсегда — это
    смысловое суждение. Единственное, что осталось, — предупредить человека.
    Не предупредили = публичный список работ и внутренний критерий отказа
    клиенту поехали к лиду одной строкой.
    """
    assert vocabulary.DUAL_PURPOSE_REPORT_LINE_UK in md, "нет строки о Q38 в REPORT.md"
    idx = md.index(vocabulary.DUAL_PURPOSE_REPORT_LINE_UK)
    start = sum(len(l) + 1 for l in md.splitlines()[:heading_index(md, "missing")])
    assert idx > start, "строка о Q38 стоит ВЫШЕ раздела 3 — вычитка её не найдёт"
    assert vocabulary.DUAL_PURPOSE_REPORT_LINE_UK in json.dumps(document, ensure_ascii=False)


def test_the_dual_purpose_line_is_absent_when_the_field_is(doc):
    """Класс ошибки: предупреждение печатается всегда, «на всякий случай».

    Строка утверждает факт: «повний текст поїхав у playbook». У клиента без
    Q38 он никуда не поехал, и владелец идёт вычитывать то, чего нет. Сигнал,
    который горит при любом раскладе, — не сторож, а фон.
    """
    without = brief_doc(with_q38=False)
    built = report.build_report(without, FakeRenderResult(without),
                                slug="drivepro", flags=[])
    assert vocabulary.DUAL_PURPOSE_REPORT_LINE_UK not in built.markdown


# ══ 6. РАЗДЕЛ 2 — ДЕФОЛТЫ И КОНФЛИКТ С БРИФОМ ══════════════════════════════

DEFAULT_KEYS = {"key", "value", "why", "brief_wanted_other", "brief_quote"}


def defaults_by_key(document) -> dict:
    return {row.get("key"): row for row in document["sections"]["defaulted"]}


def test_every_default_reaches_the_document_with_its_reason(document):
    """Класс ошибки: дефолт подставлен молча.

    §2: `owner_ref`, имя персоны, модель, `funnel_gate`, `payments` генератор не
    решает — он подставляет дефолт и обязан сказать об этом явной строкой.
    Дефолт без «почему» через месяц читается как решение клиента.
    """
    rows = defaults_by_key(document)
    for src in DEFAULTS:
        row = rows.get(src["key"])
        assert row is not None, f"дефолт `{src['key']}` пропал из раздела 2"
        assert DEFAULT_KEYS <= set(row), f"нет ключей {sorted(DEFAULT_KEYS - set(row))}"
        assert row["value"] == src["value"], f"{src['key']}: значение подменено"
        assert str(row["why"]).strip(), f"{src['key']}: дефолт без причины"
        assert row["brief_wanted_other"] == src["brief_wanted_other"]
        assert row["brief_quote"] == src["brief_quote"]


def test_every_default_is_visible_to_a_human(md):
    """Класс ошибки: `report.json` знает про дефолт, `REPORT.md` молчит.

    Проверки зелёные, владелец не предупреждён — и `funnel_gate: false` доедет
    до прода как «решение», которого никто не принимал (§7).
    """
    for src in DEFAULTS:
        assert src["key"] in md, f"дефолт `{src['key']}` не показан человеку"
        if isinstance(src["value"], str):
            assert line_with(md, src["key"], src["value"]), (
                f"дефолт `{src['key']}`: значение не стоит рядом с ключом")


def is_marked_as_conflict(md: str, key: str) -> bool:
    """Конфликт помечен: либо своим блоком, либо маркером на своей строке.

    Обе разметки читаются человеком одинаково; чего быть НЕ должно — это
    конфликт, неотличимый от обычного дефолта.
    """
    block = block_after(md, CONFLICT_MARKERS)
    if block is not None and key in block:
        return True
    for line in md.splitlines():
        if key in line and (any(m in line.casefold() for m in CONFLICT_MARKERS)
                            or "⚠" in line):
            return True
    return False


@pytest.mark.parametrize("key", CONFLICT_KEYS)
def test_a_default_the_brief_argued_with_is_marked_as_a_conflict(md, key):
    """Класс ошибки: конфликт растворился среди обычных дефолтов.

    У Ярины таких было ДВА — Q15 «спілкуватись як людина» против
    `honesty_mode: honest` и Q14 «Джарвис» против имени персоны, — и оба были
    решениями ВЛАДЕЛЬЦА (§5, п.2). Растворившись в общем списке, решение
    превращается в незамеченное умолчание: мы сделали по-своему и не сказали.
    """
    assert is_marked_as_conflict(md, key), (
        f"дефолт `{key}` спорит с брифом, но напечатан как обычный")


@pytest.mark.parametrize("key", CONFLICT_KEYS)
def test_the_conflict_carries_the_brief_quote(md, document, key):
    """Класс ошибки: «бриф просил другое» без того, чего именно бриф просил.

    Решать владельцу, а решать не по чему: он видит, что клиент чего-то хотел,
    но не видит чего. Отдельно — цитата обязана дожить и до `report.json`.
    """
    quote = defaults_by_key(document)[key]["brief_quote"]
    assert quote, f"{key}: конфликт без цитаты брифа в документе"
    assert quote in md, f"{key}: цитата брифа «{quote}» не напечатана"


def test_both_conflicts_are_shown_not_just_the_first(md):
    """Класс ошибки: показан первый конфликт, остальные съедены.

    Вчера их было два, и оба стоили решения. Отчёт, показывающий один, читается
    как «конфликт был один» — и второе умолчание уходит в прод.
    """
    assert len(CONFLICT_KEYS) >= 2, "фикстура обязана содержать два конфликта"
    marked = [k for k in CONFLICT_KEYS if is_marked_as_conflict(md, k)]
    assert marked == CONFLICT_KEYS, f"помечен не каждый конфликт: {marked}"


@pytest.mark.parametrize("key", PLAIN_KEYS)
def test_an_ordinary_default_is_not_dressed_up_as_a_conflict(md, key):
    """Класс ошибки: конфликтом помечено всё подряд.

    Обратная сторона того же требования: если ⚠️ стоит у каждого дефолта, метка
    перестаёт что-либо значить, и два настоящих решения тонут ровно так же, как
    если бы их не пометили вовсе.
    """
    assert not is_marked_as_conflict(md, key), (
        f"дефолт `{key}` брифу не противоречил, но помечен как конфликт")


# ══ 7. БЛОК ФЛАГОВ (§3, C12/C13) ═══════════════════════════════════════════

FLAG_KEYS = {"check", "file", "line", "quote", "question"}


def test_flags_pass_through_to_the_document(document):
    """Класс ошибки: флаг потерян по дороге в `report.json`.

    C12/C13 не могут быть красными: законная формулировка клиента и дефект
    выглядят одинаково. Флаг — единственная форма, в которой они вообще
    доезжают до человека; потеряв его, мы получаем rc 0 и молчание.
    """
    assert len(document["flags"]) == len(FLAGS), document["flags"]
    for src, row in zip(FLAGS, document["flags"]):
        assert FLAG_KEYS <= set(row), f"нет ключей {sorted(FLAG_KEYS - set(row))}: {row}"
        assert row["check"] == src["check"]
        assert row["file"] == src["file"] and row["line"] == src["line"]
        assert row["quote"] == src["quote"] and row["question"] == src["question"]


@pytest.mark.parametrize("flag", FLAGS, ids=[f["check"] for f in FLAGS])
def test_every_flag_names_the_place_the_quote_and_the_question(md, flag):
    """Класс ошибки: флаг без адреса.

    §3 и §4: красное (и флаг тоже) обязано назвать ФАЙЛ:СТРОКУ и виновника, а не
    «проверка не прошла». Флаг, заставляющий искать место руками, не будет
    прочитан — а значит, его нет. Три части нужны все: место говорит куда
    смотреть, цитата — что там, вопрос — что именно решает владелец.
    """
    where = f"{flag['file']}:{flag['line']}"
    assert where in md, f"нет адреса «{where}» в блоке флагов"
    assert flag["quote"] in md, f"флаг {flag['check']} без цитаты"
    assert flag["question"] in md, f"флаг {flag['check']} без вопроса владельцу"


def test_an_empty_flag_block_says_so_in_words(doc, rendered):
    """Класс ошибки: молчание неотличимо от несобравшегося блока.

    Инвариант плана дословно: пустой блок печатается как «флагов нет». Иначе
    однажды не отличим «проверки прошли чисто» от «блок не собрался», и второе
    будет прочитано как первое — это и есть зелёная ширма.
    """
    built = report.build_report(doc, rendered, slug="drivepro", flags=[])
    assert built.document["flags"] == []
    assert heading_index(built.markdown, "flags") >= 0, "блок флагов исчез вместе с флагами"
    assert NO_FLAGS_RE.search(built.markdown), (
        "пустой блок флагов не сказал словами, что флагов нет")


def test_flags_omitted_is_the_same_as_no_flags(doc, rendered):
    """Класс ошибки: `flags=None` роняет отчёт или тихо теряет блок.

    Аргумент необязателен по контракту; «не передали» и «пусто» обязаны
    печататься одинаково, иначе смысл блока зависит от того, как его вызвали.
    """
    built = report.build_report(doc, rendered, slug="drivepro")
    assert built.document["flags"] == []
    assert NO_FLAGS_RE.search(built.markdown)


def test_a_report_with_flags_never_claims_there_are_none(md):
    """Класс ошибки: шаблон печатает «флагов нет» рядом с двумя флагами.

    Читается как «проверки чисты», и владелец пролистывает блок. Заготовка,
    напечатанная безусловно, — самый дешёвый способ получить ширму.
    """
    assert not NO_FLAGS_RE.search(md), "отчёт с флагами утверждает, что флагов нет"


# ══ 8. СЧЁТЧИКИ (§3) ═══════════════════════════════════════════════════════

def test_all_six_counters_reach_the_document(document):
    """Класс ошибки: счётчик, который вчера считали руками, снова не считается.

    §3 называет шесть: услуги, цены, сроки, стоп-слова, запрещённые фразы, пары
    примеров. Отсутствующий счётчик — это ручной пересчёт, к которому мы больше
    не вернёмся; приёмка арки (§6) сверяет по ним числовые инварианты.
    """
    counters = document["counters"]
    for name, value in COUNTERS.items():
        assert name in counters, f"нет счётчика `{name}`: {sorted(counters)}"
        assert counters[name] == value, f"{name}: {counters[name]} вместо {value}"


@pytest.mark.parametrize("name, value", sorted(COUNTERS.items()))
def test_every_counter_is_printed_next_to_its_own_label(md, name, value):
    """Класс ошибки: счётчик стоит не при своём ярлыке.

    Перепутанные местами числа — зелёная ширма в чистом виде: «14 цен, 31
    услуга» выглядит как заполненный отчёт и сходится по сумме, а приёмка §6
    сверяет их поимённо с ручной сборкой (14 услуг, 31 цена, 18 сроков, 11 пар).
    Значения фикстуры намеренно различны — совпадающие числа скрыли бы обмен.
    """
    assert counter_is_shown(md, name, value), (
        f"счётчик `{name}` = {value} не напечатан при своём ярлыке")


def test_counters_in_the_document_and_in_the_markdown_are_the_same(document, md):
    """Класс ошибки: две дороги к одному числу разошлись.

    `report.json` считает по документу, `REPORT.md` — по своему проходу, и
    расхождение обнаружится на живом клиенте. Одна вещь — одно число.
    """
    for name, value in document["counters"].items():
        assert counter_is_shown(md, name, value), (
            f"счётчик `{name}` в документе = {value}, а в markdown такого нет")


# ══ 9. `document` И `markdown` НЕ РАСХОДЯТСЯ ═══════════════════════════════

def test_markdown_is_exactly_the_rendering_of_the_document(built):
    """Класс ошибки: у отчёта ДВА источника правды.

    `report.json` зелёный при куцем `REPORT.md` — ровно тот случай, когда
    проверки довольны, а человек не предупреждён. Единственная защита — markdown
    собран ИЗ документа, а не рядом с ним: тогда потерянное в одном месте видно
    в обоих.
    """
    assert report.render_markdown(built.document) == built.markdown


def test_the_document_survives_a_json_round_trip(built):
    """Класс ошибки: отчёт, записанный на диск, читается уже другим.

    T4 читает не объект в памяти, а ФАЙЛ. Кортеж, ставший списком, или ключ,
    ставший строкой, меняют отчёт молча — и «прочитано глазами» будет означать
    не тот текст, который лежит рядом с конфигом.
    """
    reloaded = json.loads(json.dumps(built.document, ensure_ascii=False))
    assert reloaded == built.document
    assert report.render_markdown(reloaded) == built.markdown


def test_nothing_machine_readable_is_invisible_to_the_human(document, md):
    """Класс ошибки (сводный): любая строка документа, не доехавшая до текста.

    Отдельные сторожа выше проверяют по разделам; этот проходит по ВСЕМУ
    документу разом и ловит то, что появится позже: новое поле контракта, новый
    флаг, новый дефолт. Требование одно и оно из §7: всё, что есть в
    машиночитаемом виде, обязано быть видно человеку — иначе отчёт станет
    ширмой не сегодня, так на следующей правке.
    """
    invisible = []
    for row in document["sections"]["taken"]:
        for key in ("field_id", "target_file", "anchor"):
            if str(row[key]) not in md:
                invisible.append(f"taken.{row['field_id']}.{key}={row[key]!r}")
    for row in document["sections"]["defaulted"]:
        if str(row["key"]) not in md:
            invisible.append(f"defaulted.{row['key']}")
        if row["brief_quote"] and str(row["brief_quote"]) not in md:
            invisible.append(f"defaulted.{row['key']}.brief_quote")
    for row in document["sections"]["missing"]:
        for key in ("raw", "stub", "question_for_client", "reason"):
            val = row.get(key)
            if isinstance(val, str) and val.strip() and val not in md:
                invisible.append(f"missing.{row.get('field_id')}.{key}={val!r}")
    for row in document["flags"]:
        if f"{row['file']}:{row['line']}" not in md:
            invisible.append(f"flags.{row['check']}.place")
        for key in ("quote", "question"):
            if str(row[key]) not in md:
                invisible.append(f"flags.{row['check']}.{key}")
    assert not invisible, "в report.json есть, в REPORT.md нет:\n  " + "\n  ".join(invisible)


def test_the_report_is_stable_across_two_identical_runs(doc, rendered):
    """Класс ошибки: два прогона одного брифа дают разные отчёты.

    Приёмка арки (§6) классифицирует КАЖДОЕ расхождение письменно. Отчёт с
    плавающим порядком или временной меткой внутри документа делает это
    невозможным: `diff` шумит, и настоящее расхождение тонет в шуме.
    """
    first = report.build_report(doc, rendered, slug="drivepro", flags=list(FLAGS))
    second = report.build_report(doc, rendered, slug="drivepro", flags=list(FLAGS))
    assert first.document == second.document
    assert first.markdown == second.markdown


def test_build_report_does_not_mutate_its_inputs(doc, rendered):
    """Класс ошибки: отчёт правит бриф под себя.

    `brief.json` — артефакт для человека и вход для генератора (§1.2). Отчёт,
    дописавший в него поле или занёсший значение вместо `None`, ломает
    следующий шаг пайплайна и делает `brief.json` неправдой о клиенте.
    """
    before = json.dumps(doc, ensure_ascii=False, sort_keys=True)
    defaults_before = json.dumps(rendered.defaults, ensure_ascii=False, sort_keys=True)
    report.build_report(doc, rendered, slug="drivepro", flags=list(FLAGS))
    assert json.dumps(doc, ensure_ascii=False, sort_keys=True) == before
    assert json.dumps(rendered.defaults, ensure_ascii=False,
                      sort_keys=True) == defaults_before
