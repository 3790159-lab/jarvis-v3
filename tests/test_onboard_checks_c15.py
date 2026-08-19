# -*- coding: utf-8 -*-
"""C15 арки `chatter.onboard`: числа knowledge обязаны совпадать с числами брифа.

Сторожа написаны ОТ СПЕКИ (`docs/superpowers/specs/2026-08-17-c15-numbers-must-
match-the-brief.md`). Реализацию C15 пишет ДРУГОЙ автор, и его код здесь НЕ
читался и не грепался: тест и код, написанные по одному прочтению, зеленеют
вместе на неправильном поведении, и приёмка этого не видит. Читались спека,
`vocabulary.py`, `report.py`, `brief.py`, `__main__.py` и `core/guardrails.py` —
то есть КОНТРАКТ данных и продакшен-функции, которыми проверка обязана
пользоваться.

**Класс ошибки, ради которого написан файл.** C1–C14 проверяют ФОРМУ: есть ли
обещание со сроком, обеспечена ли цена, стоит ли заглушка. Ни одна из них не
спрашивает, ТОТ ли это срок и ТА ли это цена. «Відповідає протягом години» и
«відповідає протягом трьох годин» для всех четырнадцати неразличимы — лид ждёт
час, человек отвечает через три, и виноват бот, который «пообещал». Цена ошибки
не техническая: разъехавшееся число доезжает до живого лида как обещание.

У этой ошибки четыре формы, и все четыре сторожатся ниже поимённо:

  1. **Сравнили не то.** Сверка ТЕКСТА вместо чисел красит законную
     переформулировку и пропускает подмену числа в переписанной фразе. Обратная
     крайность — сверка «число есть где-то в файле»: «3» найдётся в заголовке
     «## 3. Хімчистка», и подменённый SLA пройдёт зелёным. Число обязано
     сходиться В СВОЁМ МЕСТЕ.
  2. **Сверили одно поле из пяти.** Владелец 17.08 решил: все пять полей-
     источников (§2) плюс бесчисловая форма (§3). Проверка, знающая только SLA,
     выглядит работающей и молчит про предоплату, прайс, часы и дату акции.
  3. **«Нечего сверять» посчитано успехом.** Поле забраковано мусор-детектором
     — сверять не с чем, и это `blocked` (rc 2), а не зелёное (§4). Тот же класс,
     что «проверка не нашла, что смотреть, и сочла это успехом» из T4.
  4. **Красное не называет места.** Прайс живой клиентки — сотни строк. «C15 не
     прошла» без `файл:строка` заставляет искать руками, и через неделю такие
     строки начинают пролистывать.

И ровно столько же внимания — ОБРАТНОЙ стороне каждой границы. Сигнал, красный
при законной работе, — это фон, а не сторож: человек имеет право переписать
фразу (§3), пайплайн имеет право не выпустить часть чисел наружу (§3), а
генератор имеет право пронумеровать разделы. Каждая мутация §5 закрыта парой
«подмена ловится» + «законная переформулировка проходит».

**Фикстура доказывается продакшен-кодом, а не моим словом.** Нормализация,
которой я обосновываю зелёные тесты («пробелы сняты»), проверяется здесь же
настоящим `guardrails._numbers` — иначе тест доказывал бы моё представление о
рантайме, а не поведение рантайма.

Настоящий бриф клиента не читается ни одной строкой: студия детейлинга
вымышлена, форма файлов повторяет контракт `report.py`/`brief.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from chatter.core import guardrails
from chatter.onboard import brief as brief_module, checks, report as report_module, vocabulary

SLUG = "detailpro"
OWNER_ID = "Старший майстер"
OWNER_REF = "нашим старшим майстром"
PERSONA_NAME = "Ярина"

S = vocabulary.REQUIRED_SECTIONS_UK

# ─────────────────────────────────────────────────────────────────────────────
# ПЯТЬ ПОЛЕЙ-ИСТОЧНИКОВ (спека §2) — решение владельца: все пять, а не одно SLA.
#
# Ответы клиента — слева, строки собранного файла — справа. Они НАМЕРЕННО
# сформулированы по-разному: человек переписывает фразу законно (§3), и сторож,
# требующий дословного совпадения, покраснел бы на каждом живом клиенте.
# ─────────────────────────────────────────────────────────────────────────────

ANSWER_HOURS = "Пн-Нд, 9:00-20:00"
ANSWER_PRICES = (
    "Детейлінг-мийка — 1 200–2 000 грн, тривалість 1,5–2,5 години\n"
    "Полірування кузова — 5 000–8 000 грн, тривалість 6–10 годин"
)
ANSWER_PROMO = "Знижка 15% на полірування, акція діє до 31.08.2026"
ANSWER_PREPAY = "Передоплата 20% від суми замовлення"
ANSWER_SLA = "Протягом години"

BRIEF_ANSWERS: dict[str, str] = {
    "q12_hours": ANSWER_HOURS,
    "q22_price_list": ANSWER_PRICES,
    "q28_promo": ANSWER_PROMO,
    "q29_prepayment": ANSWER_PREPAY,
    "q35_reply_time": ANSWER_SLA,
}

# Куда поле оседает в knowledge — якорь строки раздела 1 отчёта (`report.py`).
FIELD_ANCHOR: dict[str, str] = {
    "q12_hours": S[0],
    "q22_price_list": S[1],
    "q28_promo": S[1],
    "q29_prepayment": S[3],
    "q35_reply_time": S[7],
}

LINE_HOURS = "Працюємо з 9:00 до 20:00."
LINE_WASH = "- Ціна 1 200–2 000 грн, тривалість 1,5–2,5 години"
LINE_POLISH = "- Ціна 5 000–8 000 грн, тривалість 6–10 годин"
LINE_PROMO = "- Акція: знижка 15% на полірування, діє до 31.08.2026"
LINE_PREPAY = "- Передоплата 20% від суми, решта після приймання роботи"
LINE_SLA = f"- {OWNER_ID} відповідає протягом години"

# Факты, закрытые заглушкой. `owner_reply_time` сюда НЕ входит: его источник
# `q35_reply_time` отвечен, и `build_report` такой факт в раздел 3 не кладёт
# (см. report.py, «Обязательные факты, у которых нет своей строки выше»).
STUB_FACTS = tuple(f for f in vocabulary.REQUIRED_FACTS if f.id != "owner_reply_time")
STUB_LINES = tuple(
    vocabulary.STUB_TEMPLATE_UK.format(title=f.title_uk, owner=OWNER_ID)
    for f in STUB_FACTS
)


# ─────────────────────────────────────────────────────────────────────────────
# Каталог клиента
# ─────────────────────────────────────────────────────────────────────────────

def knowledge_sections() -> dict[str, list[str]]:
    """Разделы knowledge как изменяемая карта.

    Заголовки берутся из `vocabulary.REQUIRED_SECTIONS_UK`, а не переписаны
    руками: переписанный руками заголовок дал бы красное на моей опечатке, то
    есть сторож проверял бы фикстуру вместо кода.
    """
    return {
        S[0]: ["Ми — студія детейлінгу DrivePro у Києві.", LINE_HOURS],
        S[1]: [
            "## 1. Детейлінг-мийка",
            "",
            LINE_WASH,
            "- Входить: двофазна мийка кузова, очищення дисків та шин, сушка",
            "",
            "## 2. Полірування кузова",
            "",
            LINE_POLISH,
            "- Входить: підготовка, абразивна робота, захисний склад",
        ],
        # Акция живёт СВОИМ разделом, а не строкой внутри прайса.
        #
        # Первая редакция фикстуры клала её в «Послуги та ціни» — там её и
        # искала бы C15, сверяя с полем прайса, где ни 15 %, ни даты нет. Это
        # дало 10 красных при сведении половин.
        #
        # Разошлись авторы не на вкусе: `REQUIRED_SECTIONS_UK` акции не
        # содержит (она необязательна), поэтому автору сторожей положить её
        # было НЕКУДА. Замер на настоящем собранном клиенте
        # (`build/onboard/yarina`) показал `# Акція` отдельным разделом
        # верхнего уровня — фикстура приведена к тому, что пайплайн реально
        # пишет, а проверка не тронута.
        vocabulary.PROMO_SECTION_UK: [LINE_PROMO],
        S[2]: ["- Стан лакофарбового покриття та розмір автомобіля"],
        S[3]: [LINE_PREPAY, "- Решту приймаємо на місці після приймання роботи"],
        S[4]: ["- Переробляємо безкоштовно, якщо результат не збігається з узгодженим"],
        S[5]: ["- Глибокі подряпини до металу полірування не прибирає"],
        S[6]: ["- Кузовного ремонту та фарбування ми не робимо, це не наш профіль"],
        S[7]: [
            "- Пишіть просто тут, у Telegram: модель авто та бажану послугу",
            LINE_SLA,
        ],
        S[8]: [f"- {line}" for line in STUB_LINES],
    }


def render_knowledge(sections: dict[str, list[str]]) -> str:
    out: list[str] = []
    for title, lines in sections.items():
        out.append(f"# {title}")
        out.append("")
        out.extend(lines)
        out.append("")
    return "\n".join(out)


def knowledge_with(**replacements: str) -> str:
    """knowledge, где ОДНА строка заменена другой.

    Мутация всегда точечная: меняется ровно та строка, чьё число разъехалось.
    Так адрес красного (§5, C15-7) можно сверить с местом дефекта, а не принять
    на веру любое `line > 0`.
    """
    sections = knowledge_sections()
    for title, lines in list(sections.items()):
        sections[title] = [replacements.get(_key(line), line) for line in lines]
    return render_knowledge(sections)


_KEYS = {
    LINE_HOURS: "hours", LINE_WASH: "wash", LINE_POLISH: "polish",
    LINE_PROMO: "promo", LINE_PREPAY: "prepay", LINE_SLA: "sla",
}


def _key(line: str) -> str:
    return _KEYS.get(line, line)


PERSONA_MD = "\n".join([
    f"Мене звати {PERSONA_NAME}, мені 26. Я адміністраторка студії детейлінгу "
    "DrivePro — перший контакт клієнта зі студією.",
    "",
    f"Я не майстер і не власниця: найскладніші питання вирішує {OWNER_ID}.",
    "",
])

PLAYBOOK_MD = "\n".join([
    "# Плейбук",
    "",
    "## Ідеальний клієнт",
    "",
    "Власник авто преміум-класу, готовий інвестувати в регулярний догляд",
    "",
    "## Кому відмовляємо і чого не робимо",
    "",
    "Кузовного ремонту та фарбування ми не робимо, це не наш профіль",
    "",
    "## Ключові слова ескалації",
    "",
    "- поклич",
    "- покличте",
    "- з менеджером",
    "- з власником",
    "",
])

EXAMPLE_PAIRS = [
    {"client": "Скільки коштує полірування кузова?",
     "olga": "Полірування кузова — 5 000–8 000 грн, залежить від стану лаку. "
             "Який у вас автомобіль?"},
]


def settings_dict() -> dict:
    return {
        "model": "claude-sonnet-5",
        "language": "uk",
        "owner_id": OWNER_ID,
        "owner_ref": OWNER_REF,
        "persona_name": PERSONA_NAME,
        "persona_age": 26,
        "currency": "грн",
        "strict_knowledge": True,
        "honesty_mode": "honest",
        "forbidden_terms": ["гарантуємо результат"],
        "telegram": {"allowlist": [237616472], "funnel_gate": False},
    }


def write_client(tmp_path: Path, *, knowledge: str | None = None,
                 brief: dict | None = None, slug: str = SLUG) -> Path:
    """Каталог `build/onboard/<slug>/` — тот же, что оставляет `--brief`.

    `brief.json` пишется в каталог НАМЕРЕННО: его туда кладёт `cmd_build`
    (`__main__.BRIEF_FILENAME`), и это ВТОРОЙ источник значений поля наряду с
    разделом 1 отчёта. Какой из двух выберет C15 — её дело; фикстура обязана
    держать оба СОГЛАСОВАННЫМИ, иначе тест поймал бы выбор источника вместо
    расхождения чисел. Ровно поэтому мутации правят собранный ФАЙЛ, а не один
    из источников.
    """
    out = tmp_path / "build" / "onboard" / slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "persona.md").write_text(PERSONA_MD, encoding="utf-8")
    (out / "knowledge.md").write_text(
        render_knowledge(knowledge_sections()) if knowledge is None else knowledge,
        encoding="utf-8")
    (out / "playbook.md").write_text(PLAYBOOK_MD, encoding="utf-8")
    (out / "examples.yaml").write_text(
        yaml.safe_dump(EXAMPLE_PAIRS, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    (out / "settings.yaml").write_text(
        yaml.safe_dump(settings_dict(), allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    (out / "brief.json").write_text(
        json.dumps(brief_document() if brief is None else brief,
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# brief.json и report.json — ОДНИ И ТЕ ЖЕ значения в двух формах
#
# Контракт брифа взят из `brief.parse_brief`, контракт отчёта — из
# `report.build_report`. Цитата раздела 1 собирается настоящим `report._one_line`,
# а не переписана руками: у неё есть предел длины, и придуманная руками цитата
# скрыла бы обрезание (см. тест про длинный прайс).
# ─────────────────────────────────────────────────────────────────────────────

def brief_field(value: str, *, target: str = "knowledge", verdict: str = "ok",
                raw: str | None = None, question: str = "питання форми") -> dict:
    return {
        "col": 0,
        "question": question,
        "raw": value if raw is None else raw,
        "value": value if verdict == "ok" else None,
        "verdict": verdict,
        "reason": None if verdict == "ok" else "garbage: відповідь не на питання",
        "target": target,
    }


def brief_document(answers: dict[str, str] | None = None,
                   *, extra: dict[str, dict] | None = None) -> dict:
    fields = {fid: brief_field(value)
              for fid, value in (BRIEF_ANSWERS if answers is None else answers).items()}
    fields.update(extra or {})
    return {"schema_version": 1, "source": "brief_detailpro.xlsx", "fields": fields}


def missing_rows(brief: dict) -> list[dict]:
    """Раздел 3: обязательные факты без данных + забракованные поля.

    Порядок и состав повторяют `build_report`: факт, чей источник отвечен, в
    раздел 3 не попадает, а забракованное поле попадает всегда.
    """
    rows: list[dict] = []
    for fact in STUB_FACTS:
        stub = vocabulary.STUB_TEMPLATE_UK.format(title=fact.title_uk, owner=OWNER_ID)
        rows.append({
            "field_id": f"fact:{fact.id}",
            "raw": None,
            "verdict": "absent",
            "reason": (f"у формі немає питання про це — «{fact.title_uk}»"
                       if not fact.source else
                       f"поле {fact.source} відповіді на цей факт не дало"),
            "stub": stub,
            "question_for_client": fact.question_for_client_uk,
            "fact_id": fact.id,
        })
    for field_id, field in (brief.get("fields") or {}).items():
        if field.get("verdict") == "ok":
            continue
        rows.append({
            "field_id": field_id,
            "raw": field.get("raw"),
            "verdict": field.get("verdict"),
            "reason": field.get("reason"),
            "stub": None,
            "question_for_client": f"Уточніть, будь ласка: {field.get('question')}",
            "fact_id": None,
        })
    return rows


def report_document(brief: dict | None = None) -> dict:
    brief = brief_document() if brief is None else brief
    taken = [
        {
            "field_id": field_id,
            "target_file": "knowledge.md",
            "anchor": FIELD_ANCHOR.get(field_id, S[0]),
            "quote": report_module._one_line(field.get("value")),
        }
        for field_id, field in (brief.get("fields") or {}).items()
        if field.get("verdict") == "ok"
    ]
    return {
        "schema_version": 1,
        "meta": {"slug": SLUG, "source": brief.get("source"),
                 "brief_schema_version": brief.get("schema_version"),
                 "files": ["examples.yaml", "knowledge.md", "persona.md",
                           "playbook.md", "settings.yaml"],
                 "notes": [], "flags_checked": True},
        "sections": {"taken": taken, "defaulted": [], "missing": missing_rows(brief)},
        "flags": [],
        "counters": {"services": 2, "prices": 4, "deadlines": 4, "stop_words": 4,
                     "forbidden": 1, "example_pairs": len(EXAMPLE_PAIRS)},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Опознание результата
# ─────────────────────────────────────────────────────────────────────────────

def run(client_dir: Path, doc: dict | None = None, *, slug: str = SLUG):
    return checks.run_checks(client_dir, report_document() if doc is None else doc,
                             slug=slug)


def c15_of(results):
    for r in results:
        if str(r.id).strip().upper() == "C15":
            return r
    raise AssertionError(
        "C15 нет в результате — исчезнувшая проверка неотличима от пройденной; "
        f"вернулись {[str(r.id) for r in results]}")


def named_line(client_dir: Path, result) -> str:
    """Строка knowledge.md, на которую показывает красное.

    Нумерация 1-based — та же, что печатает `__main__._print_checks`
    («file:line») и что кладёт в якорь `report._find_anchor` («рядок hit+1»).
    """
    lines = (client_dir / "knowledge.md").read_text(encoding="utf-8").split("\n")
    assert isinstance(result.line, int) and 1 <= result.line <= len(lines), (
        f"строка {result.line!r} за пределами knowledge.md ({len(lines)} строк)")
    return lines[result.line - 1]


def assert_points_at(result, client_dir: Path, fragment: str) -> None:
    """Адрес расхождения обязан вести К РАСХОЖДЕНИЮ (§5, C15-7).

    Одного `line > 0` мало: единица тоже больше нуля, и проверка, всегда
    называющая первую строку, прошла бы такой сторож. Поэтому строка читается
    из файла и сверяется с местом мутации.
    """
    assert result.file and "knowledge.md" in str(result.file), (
        f"C15 не назвала файл: file={result.file!r}")
    assert isinstance(result.line, int) and result.line > 0, (
        f"C15 назвала файл, но не строку: file={result.file!r}, line={result.line!r} "
        f"— половина адреса хуже отсутствия адреса, она гонит владельца искать "
        f"по всему прайсу")
    got = named_line(client_dir, result)
    assert fragment.casefold() in got.casefold(), (
        f"адрес ведёт не туда: knowledge.md:{result.line} — это {got!r}, "
        f"а расхождение в строке с {fragment!r}")
    assert "knowledge.md" in str(result.message), (
        f"в сообщении нет файла — красное читают глазами в консоли: "
        f"{result.message!r}")


@pytest.fixture()
def golden(tmp_path):
    """Согласованный клиент: файл, бриф и отчёт говорят одни и те же числа."""
    return write_client(tmp_path), report_document()


# ═════════════════════════════════════════════════════════════════════════════
# 0. ФИКСТУРА И НОРМАЛИЗАЦИЯ ДОКАЗАНЫ ПРОДАКШЕН-КОДОМ
# ═════════════════════════════════════════════════════════════════════════════

def test_runtime_normalization_is_what_the_spec_claims_it_is():
    """Ловит: зелёный тест, опирающийся на МОЁ представление о нормализации.

    Спека §3 велит сверять множества чисел «нормализованные так же, как их видит
    рантайм (`guardrails._numbers`): пробелы сняты, десятичный разделитель
    приведён». Половина этого утверждения — ЗАМЕР, и он делается здесь:
    `_numbers` действительно снимает пробелы. Вторая половина неверна —
    разделитель `_numbers` НЕ приводит, «1,5» и «1.5» остаются разными
    строками. Значит C15 обязана нормализовать его САМА, и тест ниже
    (`..._decimal_separator...`) стоит именно на этом.
    """
    assert guardrails._numbers("1 200 грн") == {"1200"}, (
        "рантайм перестал снимать пробелы — вся арифметика сверки поехала")
    assert guardrails._numbers("1,5") != guardrails._numbers("1.5"), (
        "если `_numbers` начал приводить разделитель, читай §3 буквально и "
        "перепиши обоснование зелёного теста про запятую/точку")


def test_the_golden_fixture_really_carries_all_five_source_fields():
    """Ловит: фикстуру, доказывающую меньше, чем обещает.

    Владелец решил: все ПЯТЬ полей §2, а не одно SLA. Если в фикстуре поля нет,
    «C15 зелёная» ничего про него не значит, а «C15 красная» на его мутации
    невозможна — сторож молча сузился бы до одного поля, ровно как мутация
    C15-3 «сверять только SLA».
    """
    knowledge = render_knowledge(knowledge_sections())
    for field_id, answer in BRIEF_ANSWERS.items():
        assert FIELD_ANCHOR[field_id] in knowledge, (
            f"{field_id}: раздела {FIELD_ANCHOR[field_id]!r} в фикстуре нет")
        if field_id == "q35_reply_time":
            continue
        assert guardrails._numbers(answer), (
            f"{field_id}: в ответе клиента нет чисел — сверять нечего")

    # SLA в золотой фикстуре — БЕСЧИСЛОВОЙ намеренно: «Протягом години» и есть
    # тот отдельный случай §3, который для всех остальных проверок выглядит
    # обычной строкой. Если однажды здесь появится цифра, зелёные тесты про
    # единицу перестанут доказывать то, ради чего написаны, — молча.
    assert guardrails._numbers(ANSWER_SLA) == set(), (
        f"эталонный SLA перестал быть бесчисловым: {ANSWER_SLA!r}")
    assert "годин" in ANSWER_SLA.casefold() and "годин" in LINE_SLA.casefold(), (
        "единица времени пропала из фикстуры — сверять множитель не с чем")


# ═════════════════════════════════════════════════════════════════════════════
# 1. C15 СУЩЕСТВУЕТ И НЕ ИСЧЕЗАЕТ
#
# Спека §6.4: счётчик становится 15 (а с C16 из спеки Хайку — 16), иначе
# `verdict` отдаст RC_NOT_RUN. Проверка,
# молча выпавшая из списка, неотличима от пройденной — глазами видно «красных
# нет».
# ═════════════════════════════════════════════════════════════════════════════

def test_c15_is_the_fifteenth_check(golden):
    """Ловит: проверку, написанную, но не подключённую к списку."""
    assert "C15" in {str(i).strip().upper() for i in checks.CHECK_IDS}, (
        f"C15 нет в CHECK_IDS: {list(checks.CHECK_IDS)}")
    assert len(checks.CHECK_IDS) == 16, (
        f"проверок в списке {len(checks.CHECK_IDS)}, а их должно быть 16: "
        f"пятнадцать по §6.4 плюс C16 (префикс brain, спека Хайку §9)")
    client_dir, doc = golden
    res = run(client_dir, doc)
    assert len(res) == 16, f"вернулось {len(res)} результатов: {[r.id for r in res]}"
    c15_of(res)


def test_c15_survives_a_client_dir_that_does_not_exist(tmp_path):
    """Ловит: «проверка не смогла отработать → её просто нет в списке».

    Каталога нет — C15 не может сказать «ок», но обязана СКАЗАТЬ ЭТО ВСЛУХ, и
    исключение наружу тоже не годится: `__main__` напечатал бы traceback вместо
    приёмки.
    """
    ghost = tmp_path / "build" / "onboard" / SLUG
    res = run(ghost)
    assert len(res) == 16, f"на отсутствующем каталоге вернулось {len(res)}"
    assert not c15_of(res).ok, "каталога нет, а C15 зелёная"


def test_c15_survives_a_broken_report_document(golden):
    """Ловит: битый отчёт, уносящий C15 из списка вместе с собой."""
    client_dir, _ = golden
    res = run(client_dir, {"это": "не отчёт"})
    assert len(res) == 16, f"на битом отчёте вернулось {len(res)}"
    c15_of(res)


def test_c15_is_red_not_a_flag(golden):
    """Спека §4: C15 — КРАСНОЕ, не флаг. Ловит: разметку, тихо выключающую проверку.

    Флаг означает «законно или нет, решает владелец». Здесь решать нечего:
    число либо то, либо не то. Помеченная флагом, C15 перестанет ронять вердикт
    — то есть станет фоновой строкой отчёта, которую пролистывают.
    """
    client_dir, doc = golden
    assert not c15_of(run(client_dir, doc)).is_flag, (
        "C15 помечена флагом — значит перестала ронять вердикт (§4)")


# ═════════════════════════════════════════════════════════════════════════════
# 2. ЗАКОННАЯ ПЕРЕФОРМУЛИРОВКА ПРОХОДИТ
#
# «Не сверяем текст. Человек имеет право переписать фразу; он не имеет права
# изменить число» (§3). Тест, который краснеет всегда, — не сторож: сигнал,
# красный при законной работе, перестают читать через два прогона.
# ═════════════════════════════════════════════════════════════════════════════

def test_c15_green_on_the_golden_client(golden):
    """Ловит: C15, красную при законной работе.

    В фикстуре разошёлся КАЖДЫЙ текст: «Пн-Нд, 9:00-20:00» против «Працюємо з
    9:00 до 20:00», «Протягом години» против «Старший майстер відповідає
    протягом години». Числа при этом сходятся все. Плюс в knowledge законно
    живут числа, которых в брифе НЕТ вовсе, — нумерация разделов «## 1.», «## 2.»
    от генератора. Проверка, требующая совпадения множеств в обе стороны,
    покраснеет здесь, то есть на каждом клиенте сразу.
    """
    client_dir, doc = golden
    c15 = c15_of(run(client_dir, doc))
    assert not c15.blocked, f"C15 не состоялась на полном каталоге: {c15.message!r}"
    assert c15.ok, f"числа сходятся, а C15 красная: {c15.message!r}"


def test_c15_green_when_the_sla_gains_a_digit_the_brief_did_not_have(tmp_path):
    """C15-2 спеки. Мутация: требовать дословного совпадения.

    «Протягом години» в брифе против «відповідає протягом 1 години» в файле —
    расхождения нет, час остался часом. Красное здесь блокировало бы приёмку на
    законной работе редактора: развернуть бесчисловую форму в явную цифру — это
    улучшение текста, а не подмена срока.
    """
    client_dir = write_client(
        tmp_path, knowledge=knowledge_with(
            sla=f"- {OWNER_ID} відповідає протягом 1 години"))
    c15 = c15_of(run(client_dir))
    assert c15.ok, (
        f"«протягом 1 години» признано расхождением с «Протягом години»: "
        f"{c15.message!r}")


def test_c15_green_when_the_brief_spells_the_hour_and_the_file_does_not(tmp_path):
    """C15-5 спеки, обратное направление. Мутация: выбросить нормализацию единиц.

    Бриф с цифрой, файл — без. «Години» без числа = 1 час (§3), значит
    расхождения нет. Без нормализации единиц эта пара выглядит как «1 против
    ничего» и краснеет — а формулировка «протягом години» стоит в живом эталоне
    Ярины (§6.1), то есть красное появилось бы на первом же реальном прогоне.
    """
    brief = brief_document({**BRIEF_ANSWERS, "q35_reply_time": "Протягом 1 години"})
    client_dir = write_client(tmp_path, brief=brief)
    c15 = c15_of(run(client_dir, report_document(brief)))
    assert c15.ok, (
        f"бесчисловая форма в файле не приравнена к 1 единице: {c15.message!r}")


def test_c15_green_when_the_decimal_separator_was_normalized_by_the_pipeline(tmp_path):
    """Ловит: сверку сырых строк вместо чисел на самом дорогом поле.

    R1 переписывает ценовую строку и приводит точку к запятой ОСОЗНАННО: точка
    внутри строки рвёт фрагмент `guardrails._context_numbers`, и guardrail
    режет собственный прайс клиента (это ловит C2). То есть «1.5» в брифе и
    «1,5» в файле — штатная работа пайплайна, и C15 обязана видеть здесь одно
    число. Иначе две проверки требуют противоположного: C2 краснеет на точке,
    C15 — на её исправлении, и клиент не собирается никогда.

    §3 велит нормализовать разделитель; `guardrails._numbers` этого НЕ делает
    (замер в `test_runtime_normalization_is_what_the_spec_claims_it_is`), значит
    нормализация — на C15.
    """
    brief = brief_document({
        **BRIEF_ANSWERS,
        "q22_price_list": ANSWER_PRICES.replace("1,5–2,5", "1.5–2.5"),
    })
    client_dir = write_client(tmp_path, brief=brief)
    c15 = c15_of(run(client_dir, report_document(brief)))
    assert c15.ok, (
        f"«1.5» брифа и «1,5» файла посчитаны разными числами: {c15.message!r}")


def test_c15_green_when_thousands_are_written_without_a_space(tmp_path):
    """Ловит: сверку, чувствительную к пробелу внутри числа.

    «1 200 грн» в брифе и «1200 грн» в файле — одно число (§3, «пробелы
    сняты»), и это ПРОВЕРЕНО настоящим `_numbers` выше. Клиент пишет тысячи
    как попало, генератор нормализует — красное здесь было бы фоном.
    """
    client_dir = write_client(
        tmp_path, knowledge=knowledge_with(
            wash="- Ціна 1200–2000 грн, тривалість 1,5–2,5 години"))
    c15 = c15_of(run(client_dir))
    assert c15.ok, f"пробел внутри числа посчитан расхождением: {c15.message!r}"


def test_c15_green_when_the_wording_of_the_hours_is_rewritten(tmp_path):
    """Ловит: сверку текстов на поле, которое генератор переписывает всегда.

    «Пн-Нд, 9:00-20:00» клиент пишет для человека; в knowledge это едет фразой.
    Ни одна буква не совпадает — совпадают только 9 и 20.
    """
    client_dir = write_client(
        tmp_path, knowledge=knowledge_with(
            hours="Двері студії відчинені щодня від 9:00 і до 20:00, без перерви."))
    c15 = c15_of(run(client_dir))
    assert c15.ok, f"переформулировка часов принята за подмену: {c15.message!r}"


# ═════════════════════════════════════════════════════════════════════════════
# 3. ПОДМЕНА ЧИСЛА ЛОВИТСЯ — ВО ВСЕХ ПЯТИ ПОЛЯХ
#
# «Сверять только SLA» — мутация C15-3. Проверка одного поля из пяти выглядит
# работающей и молчит про предоплату, прайс, часы и дату акции.
# ═════════════════════════════════════════════════════════════════════════════

def test_c15_red_when_the_sla_lost_two_thirds_of_itself(tmp_path):
    """C15-1 спеки — та самая дыра, ради которой написана проверка.

    Бриф говорит «Протягом 3 годин», файл — «протягом години». Для всех
    четырнадцати проверок эти строки неразличимы: форма правильная, обещание со
    сроком на месте, C12 даже даст свой законный флаг. Лид ждёт час, человек
    отвечает через три, и виноват бот. Мутация «сравнивать тексты вместо чисел»
    здесь зеленеет, потому что текст «протягом години» и правда есть в файле.
    """
    brief = brief_document({**BRIEF_ANSWERS, "q35_reply_time": "Протягом 3 годин"})
    client_dir = write_client(tmp_path, brief=brief)
    c15 = c15_of(run(client_dir, report_document(brief)))
    assert not c15.ok, "SLA разъехался втрое, а C15 зелёная"
    assert not c15.blocked, "поле отвечено и файл на месте — сверка состоялась"
    assert_points_at(c15, client_dir, "протягом години")


def test_c15_red_when_the_brief_spells_three_hours_in_words(tmp_path):
    """Спека §3 дословно: «протягом трьох годин» в брифе против «протягом
    години» в файле — это КРАСНОЕ.

    Ловит: обработку бесчисловой формы, срезающую угол. Соблазн прочитать §3
    так: «нет цифр — сравнивать нечего, зелёное». Тогда правило «единица без
    числа = 1» превращается в дыру: любой словесный числитель («трьох»,
    «двох», «півтори») будет проглочен молча, а это ровно та формулировка, в
    которой люди пишут сроки.
    """
    brief = brief_document(
        {**BRIEF_ANSWERS, "q35_reply_time": "Відповідаємо протягом трьох годин"})
    client_dir = write_client(tmp_path, brief=brief)
    c15 = c15_of(run(client_dir, report_document(brief)))
    assert not c15.ok, (
        "«протягом трьох годин» и «протягом години» разошлись втрое, а C15 "
        "зелёная — §3 называет ровно этот случай красным")


def test_c15_red_when_the_prepayment_percent_drifted(tmp_path):
    """C15-3 спеки. Мутация: сверять только SLA.

    20% против 25% — деньги лида. Разница ловится единственной проверкой:
    guardrail молчит (25 стоит в ценовом контексте и «обеспечено» собственным
    файлом), C12 молчит (это не обещание за третье лицо), глаз владельца при
    вычитке сравнивает файл с файлом, а не с брифом.
    """
    client_dir = write_client(
        tmp_path, knowledge=knowledge_with(
            prepay="- Передоплата 25% від суми, решта після приймання роботи"))
    c15 = c15_of(run(client_dir))
    assert not c15.ok, "предоплата 20 → 25, а C15 зелёная"
    assert_points_at(c15, client_dir, "25%")
    assert ("25" in str(c15.message) and "20" in str(c15.message)), (
        f"сообщение не называет ОБА числа — владелец не поймёт, где правда: "
        f"{c15.message!r}")


def test_c15_red_when_a_price_in_the_list_drifted(tmp_path):
    """Ловит: проверку, обходящую самое дорогое поле формы.

    Верхняя граница вилки 2 000 → 2 500. Прайс — единственное поле, чьи числа
    бот называет лиду в каждом втором диалоге, и «обеспеченность» guardrail'а
    здесь бессильна по конструкции: 2 500 обеспечено тем же файлом, где оно
    написано.
    """
    client_dir = write_client(
        tmp_path, knowledge=knowledge_with(
            wash="- Ціна 1 200–2 500 грн, тривалість 1,5–2,5 години"))
    c15 = c15_of(run(client_dir))
    assert not c15.ok, "цена 2 000 → 2 500, а C15 зелёная"
    assert_points_at(c15, client_dir, "2 500")


def test_c15_red_when_the_working_hours_drifted(tmp_path):
    """Ловит: пропущенное поле q12_hours.

    Час закрытия 20:00 → 21:00. Лид приезжает к 20:40 к закрытой двери — и это
    единственное поле, ошибку которого замечает не владелец, а клиент на месте.
    """
    client_dir = write_client(
        tmp_path, knowledge=knowledge_with(hours="Працюємо з 9:00 до 21:00."))
    c15 = c15_of(run(client_dir))
    assert not c15.ok, "часы работы 20:00 → 21:00, а C15 зелёная"
    assert_points_at(c15, client_dir, "21:00")


def test_c15_red_when_the_promo_end_date_drifted(tmp_path):
    """Ловит: пропущенную дату акции (§2, пятая строка таблицы).

    31.08 → 30.09. Бот месяц обещает скидку, которой больше нет; отказ звучит
    уже в диалоге с лидом, которому её пообещали.
    """
    client_dir = write_client(
        tmp_path, knowledge=knowledge_with(
            promo="- Акція: знижка 15% на полірування, діє до 30.09.2026"))
    c15 = c15_of(run(client_dir))
    assert not c15.ok, "дата окончания акции 31.08 → 30.09, а C15 зелёная"
    assert_points_at(c15, client_dir, "30.09.2026")


def test_c15_red_when_the_promo_discount_drifted(tmp_path):
    """Ловит: сверку ТОЛЬКО даты в поле акции.

    У поля акции два числа — процент и дата, — и подменить можно любое. 15% →
    25% дороже даты: скидку бот назовёт вслух и её придётся дать.
    """
    client_dir = write_client(
        tmp_path, knowledge=knowledge_with(
            promo="- Акція: знижка 25% на полірування, діє до 31.08.2026"))
    c15 = c15_of(run(client_dir))
    assert not c15.ok, "скидка 15% → 25%, а C15 зелёная"
    assert_points_at(c15, client_dir, "25%")


def test_c15_does_not_accept_a_number_that_lives_somewhere_else(tmp_path):
    """Ловит: «число есть где-то в файле» вместо «число на своём месте».

    Самая правдоподобная реализация-ширма: собрать множество чисел всего
    knowledge и проверить вхождение. Тогда «3» из заголовка услуги «## 3.
    Хімчистка салону» покроет подменённый SLA, и красное не появится никогда —
    а на живом клиенте с сотней чисел покроется ЛЮБАЯ подмена. Это тот же
    класс, что «зелёное по случайности» на T2: проверка прошла потому, что
    нужная цифра случайно нашлась в прайсе.

    Заодно это ЕДИНСТВЕННЫЙ способ выполнить §5 C15-7: адрес строки нельзя
    назвать, не зная, где поле осело.
    """
    # Третья услуга ЗАКОННА: она есть и в брифе, и в файле — то есть «3» в
    # knowledge стоит по праву. Разошёлся только SLA.
    third_service = "Хімчистка салону — 3 000–4 000 грн, тривалість 3 години"
    sections = knowledge_sections()
    sections[S[1]] = sections[S[1]] + [
        "", "## 3. Хімчистка салону", "", "- Ціна 3 000–4 000 грн, тривалість 3 години"]
    brief = brief_document({
        **BRIEF_ANSWERS,
        "q22_price_list": ANSWER_PRICES + "\n" + third_service,
        "q35_reply_time": "Протягом 3 годин",
    })
    client_dir = write_client(tmp_path, knowledge=render_knowledge(sections), brief=brief)

    knowledge = (client_dir / "knowledge.md").read_text(encoding="utf-8")
    assert "3" in guardrails._numbers(knowledge), (
        "фикстура не воспроизводит ловушку: числа 3 в файле нет")

    c15 = c15_of(run(client_dir, report_document(brief)))
    assert not c15.ok, (
        "«3» нашлось в заголовке услуги и покрыло подменённый SLA — проверка "
        "сверяет не с тем местом")
    assert_points_at(c15, client_dir, "протягом години")


def test_c15_sees_the_whole_price_list_not_the_truncated_quote(tmp_path):
    """Ловит: C15, читающую значение поля из ЦИТАТЫ раздела 1 отчёта.

    `report._one_line` режет цитату на 200 символах (`_QUOTE_LIMIT`) — она
    существует, чтобы прайс из 14 услуг не развалил формат отчёта. Прайс живой
    клиентки этот предел перекрывает вдвое, и всё, что за обрезом, для такой
    C15 не существует: подмена в хвосте прайса пройдёт зелёной, а выглядеть
    проверка будет работающей на всех коротких полях.

    Полное значение лежит в `brief.json` (его пишет `cmd_build`) и в поле
    `value` — там его и надо брать.
    """
    long_prices = "\n".join([
        "Детейлінг-мийка — 1 200–2 000 грн, тривалість 1,5–2,5 години",
        "Полірування кузова — 5 000–8 000 грн, тривалість 6–10 годин",
        "Хімчистка салону — 3 000–4 000 грн, тривалість 4–6 годин",
        "Керамічне покриття — 12 000–18 000 грн, тривалість 2 дні",
        "Захист плівкою — 25 000–40 000 грн, тривалість 3 дні",
    ])
    quote = report_module._one_line(long_prices)
    assert "40 000" not in quote and quote.endswith("…"), (
        "фикстура не воспроизводит обрезание: цитата раздела 1 короче предела, "
        f"и тест ничего не докажет — {quote!r}")

    sections = knowledge_sections()
    sections[S[1]] = [
        "## 1. Детейлінг-мийка", "", LINE_WASH, "",
        "## 2. Полірування кузова", "", LINE_POLISH, "",
        "## 3. Хімчистка салону", "", "- Ціна 3 000–4 000 грн, тривалість 4–6 годин", "",
        "## 4. Керамічне покриття", "", "- Ціна 12 000–18 000 грн, тривалість 2 дні", "",
        "## 5. Захист плівкою", "", "- Ціна 25 000–45 000 грн, тривалість 3 дні", "",
        LINE_PROMO,
    ]
    brief = brief_document({**BRIEF_ANSWERS, "q22_price_list": long_prices})
    client_dir = write_client(tmp_path, knowledge=render_knowledge(sections), brief=brief)

    c15 = c15_of(run(client_dir, report_document(brief)))
    assert not c15.ok, (
        "верхняя цена последней услуги 40 000 → 45 000 не поймана: значение "
        "поля прочитано из обрезанной цитаты отчёта, а не из брифа")
    assert_points_at(c15, client_dir, "45 000")


# ═════════════════════════════════════════════════════════════════════════════
# 4. ПОЛНОТЫ В ОБРАТНУЮ СТОРОНУ НЕ ТРЕБУЕМ
#
# §3: число, оставшееся в брифе и не попавшее в файл, — это раздел 3 отчёта, а
# не красное C15. Проверка, требующая полноты, красная на каждом клиенте, у
# которого пайплайн что-нибудь сознательно не выпустил наружу.
# ═════════════════════════════════════════════════════════════════════════════

def test_c15_green_when_a_number_stayed_in_the_brief_on_purpose(tmp_path):
    """C15-4 спеки. Мутация: требовать полноты.

    Клиент дописал в прайс «Знижка 7% постійним клієнтам» — условие, которое
    пайплайн наружу не выпускает (о нём владелец узнаёт из отчёта, а не от
    бота). Числа 7 в knowledge нет и быть не должно. Требование полноты
    сделало бы C15 красной на этом месте, то есть на каждом живом брифе, где
    клиент написал больше, чем едет в файл.
    """
    brief = brief_document({
        **BRIEF_ANSWERS,
        "q22_price_list": ANSWER_PRICES + "\nЗнижка 7% постійним клієнтам",
    })
    client_dir = write_client(tmp_path, brief=brief)
    knowledge = (client_dir / "knowledge.md").read_text(encoding="utf-8")
    assert "7" not in guardrails._numbers(knowledge), (
        "фикстура не воспроизводит случай: число 7 в файл всё-таки попало")

    c15 = c15_of(run(client_dir, report_document(brief)))
    assert c15.ok, (
        f"число, законно оставшееся в брифе, покрасило C15: {c15.message!r} — "
        f"это раздел 3 отчёта, а не расхождение")


def test_c15_ignores_numbers_of_fields_it_does_not_cover(tmp_path):
    """Ловит: C15, расползшуюся на все 57 колонок формы.

    Спека §2 называет ПЯТЬ полей-источников. Дата запуска (q54) — не одно из
    них: она вообще никуда не едет из брифа, и требовать её числа в knowledge
    значит краснеть на ровном месте. Проверка, красная при законной работе, —
    фон, а не сторож.
    """
    brief = brief_document(extra={
        "q54_launch_date": brief_field("Хочемо стартувати 1 вересня 2026",
                                       target="report_only"),
    })
    client_dir = write_client(tmp_path, brief=brief)
    c15 = c15_of(run(client_dir, report_document(brief)))
    assert c15.ok, (
        f"C15 требует в knowledge числа поля, которое туда не едет: "
        f"{c15.message!r}")


# ═════════════════════════════════════════════════════════════════════════════
# 5. «НЕЧЕГО СВЕРЯТЬ» — ЭТО НЕ УСПЕХ
#
# §4: забракованное мусор-детектором поле — `blocked`, то есть rc «не
# состоялось», а не «зелено». Это тот же класс, что «проверка не нашла, что
# смотреть, и сочла это успехом»: такой прогон не доказал НИЧЕГО.
# ═════════════════════════════════════════════════════════════════════════════

def test_c15_is_blocked_when_a_source_field_was_rejected_as_garbage(tmp_path):
    """C15-6 спеки. Мутация: считать «нечего сверять» успехом.

    Клиент написал в ячейку предоплаты «-» — мусор-детектор забраковал поле,
    значения нет, сверять не с чем. Зелёное здесь означало бы «проценты
    предоплаты сверены», хотя не сверялось ничего: число в knowledge взялось из
    головы генератора и никем не подтверждено.
    """
    brief = brief_document(extra={
        "q29_prepayment": brief_field("-", verdict="garbage", raw="-"),
    })
    client_dir = write_client(tmp_path, brief=brief)
    c15 = c15_of(run(client_dir, report_document(brief)))
    assert c15.blocked, (
        f"поле предоплаты забраковано, а C15 не в состоянии «не состоялось»: "
        f"ok={c15.ok!r}, blocked={c15.blocked!r}, {c15.message!r}")
    assert not c15.ok, "«не состоялось» не может быть одновременно зелёным"
    assert str(c15.message).strip(), "C15 не состоялась молча — причина не названа"
    assert "q29_prepayment" in str(c15.message), (
        f"C15 не назвала, ИЗ-ЗА КАКОГО поля сверка не состоялась: {c15.message!r}")


def test_a_rejected_source_field_makes_the_verdict_two(tmp_path):
    """Та же мутация, но со стороны вердикта: rc «не состоялось», а не «красное».

    Разница дорогая. `1` означает «проверка отработала и нашла дефект» — его
    чинят в файле. `2` означает «прогон ничего не доказал» — чинят бриф и зовут
    клиента. Пара с контролем: на согласованном каталоге тот же вердикт НЕ
    равен 2, значит двойка пришла именно от забракованного поля.
    """
    control_dir = write_client(tmp_path / "control")
    assert checks.verdict(run(control_dir), reviewed=True) != 2, (
        "контроль не годится: на согласованном каталоге вердикт и так «не "
        "состоялось», и тест ниже ничего не докажет")

    brief = brief_document(extra={
        "q29_prepayment": brief_field("-", verdict="garbage", raw="-"),
    })
    client_dir = write_client(tmp_path / "garbage", brief=brief)
    rc = checks.verdict(run(client_dir, report_document(brief)), reviewed=True)
    assert rc == 2, (
        f"поле забраковано — сверка не состоялась, а вердикт {rc}. `0` и `1` "
        f"здесь одинаково врут: первый принимает непроверенное, второй зовёт "
        f"чинить файл вместо брифа")


def test_c15_is_not_blocked_by_garbage_in_a_field_it_does_not_cover(tmp_path):
    """Обратная сторона того же правила. Ловит: `blocked` на КАЖДОМ клиенте.

    В живом брифе мусорных и пустых ячеек всегда несколько — их для того и
    ловит детектор. Если C15 не состоится из-за любой из них, она не станет
    зелёной никогда, а «не состоялось» перестанет что-либо значить: это тот же
    фон, только под другим кодом выхода. Блокировать обязано ТОЛЬКО поле из
    пяти источников §2.
    """
    brief = brief_document(extra={
        "q56_anything_else": brief_field("не знаю що написати 111",
                                         verdict="garbage",
                                         raw="не знаю що написати 111"),
    })
    client_dir = write_client(tmp_path, brief=brief)
    c15 = c15_of(run(client_dir, report_document(brief)))
    assert not c15.blocked, (
        f"мусор в поле, которое C15 не сверяет, остановил всю проверку: "
        f"{c15.message!r}")
    assert c15.ok, f"числа пяти полей сходятся, а C15 не зелёная: {c15.message!r}"


def test_c15_without_any_source_of_brief_values_is_not_green(tmp_path):
    """Ловит: «источника нет → возражений нет» — вторая форма зелёной ширмы.

    ⚠️ Требование зафиксировано КОНСЕРВАТИВНО, и вот граница чтения. §6.1
    обещает C15 зелёной «на эталоне Ярины», а ручной эталон
    `chatter/clients/yarina` собран руками: ни `brief.json`, ни `report.json`
    там нет. Читаю §6.1 как «на КАТАЛОГЕ, СОБРАННОМ из её брифа» — потому что
    противоположное чтение делает C15 вечно зелёной у всякого, кто собрал
    клиента руками, а проверка, которая не может покраснеть, не существует.
    Прецедент арки на моей стороне: `__main__` при отсутствующем отчёте прямо
    называет `blocked` C4 и C10 ожидаемым состоянием на ручном эталоне (§6
    шаг 6), а не поводом их озеленить.

    Утверждается только «не зелёная»: `blocked` или красное — решать автору
    кода, обе формы честны.
    """
    client_dir = write_client(tmp_path)
    (client_dir / "brief.json").unlink()
    empty = {"schema_version": 1, "meta": {"slug": SLUG, "flags_checked": True},
             "sections": {"taken": [], "defaulted": [], "missing": []},
             "flags": [], "counters": {}}
    c15 = c15_of(run(client_dir, empty))
    assert not c15.ok, (
        "ни брифа, ни строк раздела 1 — сверять было НЕ С ЧЕМ, а C15 зелёная: "
        "это «проверка не нашла, что смотреть, и сочла это успехом»")


# ═════════════════════════════════════════════════════════════════════════════
# 5.1. ПУСТО ≠ ЗАБРАКОВАНО (§4, поправка 18.08 — решение владельца)
#
# Ловушка здесь в КОНТРАКТЕ, а не в формулировке спеки: `brief.classify_value`
# отдаёт ОДИН И ТОТ ЖЕ вердикт `garbage` и пустой ячейке, и заполненной мусором
# (см. brief.py: «Пустое поле — не мусор, а ответа нет… понижаем тем же
# механизмом»). Различает их только `raw`. Поэтому реализация, честно читающая
# вердикт, склеит два разных состояния и будет выглядеть правильной.
#
# Цена склейки в обе стороны:
#   * пустое → `blocked`: `q28_promo` не обязательно, и каждый клиент без акции
#     получает rc «не состоялось» — сигнал, всегда красный при законной работе;
#   * забракованное → skip: клиент ДО поля дошёл и ответил мимо, а прогон
#     объявляет «числа сверены», хотя число в knowledge никем не подтверждено.
#
# Сторож — ПАРА в одном тесте. Двумя отдельными тестами склейку не поймать: тот,
# что требует `blocked`, зеленеет на склейке «всё блокирует», а тот, что требует
# skip, — на склейке «ничто не блокирует». Красным обязано становиться РАЗЛИЧИЕ.
# ═════════════════════════════════════════════════════════════════════════════

NO_PROMO_LINE = "- Акцій зараз немає"   # числа в строке нет: клиент без акции


def _cell_as_the_pipeline_sees_it(raw: str) -> dict:
    """Ячейка брифа, разобранная НАСТОЯЩИМ детектором, а не моей рукой.

    Если проставить вердикт вручную, тест докажет моё представление о
    контракте. Здесь важен именно факт «вердикт у обоих случаев ОДИН», и
    держать его обязан продакшен-код.
    """
    verdict, reason = brief_module.classify_value(raw, set())
    field = brief_field(raw, verdict=verdict, raw=raw)
    field["reason"] = reason
    return field


def test_an_empty_cell_and_a_rejected_cell_are_not_the_same_state(tmp_path):
    """C15-8 спеки. Мутация: склеить пусто и забраковано (в любую сторону)."""
    empty_cell = _cell_as_the_pipeline_sees_it("")
    junk_cell = _cell_as_the_pipeline_sees_it("1 1 1")

    # Предусловие теста — то самое, что делает ошибку возможной. Если контракт
    # когда-нибудь заведёт отдельный вердикт для пустого, этот assert покраснеет
    # первым и скажет, что сторож стал сторожить несуществующую ловушку.
    assert empty_cell["verdict"] == junk_cell["verdict"] == "garbage", (
        f"предусловие сломано: вердикты уже разные "
        f"({empty_cell['verdict']!r} против {junk_cell['verdict']!r}) — "
        f"проверь, что тест ещё про то самое")
    assert empty_cell["reason"] != junk_cell["reason"], (
        "детектор перестал называть РАЗНЫЕ причины — тогда различать нечем")

    empty_brief = brief_document(extra={"q28_promo": empty_cell})
    empty_dir = write_client(tmp_path / "empty",
                             knowledge=knowledge_with(promo=NO_PROMO_LINE),
                             brief=empty_brief)
    empty = c15_of(run(empty_dir, report_document(empty_brief)))

    junk_brief = brief_document(extra={"q28_promo": junk_cell})
    junk_dir = write_client(tmp_path / "junk",
                            knowledge=knowledge_with(promo=NO_PROMO_LINE),
                            brief=junk_brief)
    junk = c15_of(run(junk_dir, report_document(junk_brief)))

    assert empty.blocked != junk.blocked, (
        f"пустое поле и забракованное дали ОДНО состояние "
        f"(blocked={empty.blocked!r} у обоих): клиент, не дошедший до поля, и "
        f"клиент, ответивший мимо, — разные сигналы и разные вопросы ему, а "
        f"склейка теряет то, что форму заполняли невнимательно. "
        f"пусто: {empty.message!r} | мусор: {junk.message!r}")

    assert not empty.blocked, (
        f"пустая необязательная ячейка остановила прогон: `q28_promo` в схеме "
        f"не обязательна, и rc «не состоялось» получал бы КАЖДЫЙ клиент без "
        f"акции — это фон, а не сторож: {empty.message!r}")
    assert empty.ok, (
        f"пустое поле не только не блокирует — остальные четыре обязаны быть "
        f"сверены: {empty.message!r}")
    assert junk.blocked, (
        f"мусор в поле-источнике прошёл мимо: зелёное здесь утверждает «числа "
        f"сверены», хотя сверять было не с чем: {junk.message!r}")

    # Пропуск обязан быть НАЗВАН ВСЛУХ и своей причиной — иначе «сверено 4 из 5»
    # неотличимо от «сверено всё», и дыра закрывается тишиной.
    assert "q28_promo" in str(empty.message), (
        f"C15 не назвала поле, которое пропустила: {empty.message!r}")
    assert str(empty_cell["reason"]).split(":")[0] in str(empty.message), (
        f"причина пропуска не названа словами детектора "
        f"({empty_cell['reason']!r}): {empty.message!r}")


# ═════════════════════════════════════════════════════════════════════════════
# 6. АДРЕС РАСХОЖДЕНИЯ
#
# §5, C15-7: адрес содержит и файл, и строку. Мутация — вернуть только имя файла.
# Половина адреса хуже отсутствия адреса: она выглядит названным местом, и
# владелец ищет по всему прайсу.
# ═════════════════════════════════════════════════════════════════════════════

def test_c15_red_names_both_the_file_and_the_line(tmp_path):
    """Ловит: красное без строки. Прайс живой клиентки — сотни строк.

    Проверяется не только наличие `line`, но и то, что строка ВЕДЁТ К
    РАСХОЖДЕНИЮ: `line=1` формально непусто и проходило бы сторож «line > 0».
    """
    client_dir = write_client(
        tmp_path, knowledge=knowledge_with(
            polish="- Ціна 5 000–9 000 грн, тривалість 6–10 годин"))
    c15 = c15_of(run(client_dir))
    assert not c15.ok, "цена полировки 8 000 → 9 000 не поймана"
    assert c15.file, f"C15 назвала только сообщение, без файла: {c15.message!r}"
    assert isinstance(c15.line, int) and c15.line > 0, (
        f"C15 назвала файл, но не строку: file={c15.file!r}, line={c15.line!r}")
    assert "9 000" in named_line(client_dir, c15) or "9000" in named_line(client_dir, c15), (
        f"адрес knowledge.md:{c15.line} ведёт не к расхождению, а к "
        f"{named_line(client_dir, c15)!r}")


def test_c15_message_is_readable_without_opening_the_file(tmp_path):
    """Ловит: сообщение вида «C15 не прошла» — формально красное, практически
    непрочитываемое.

    Красное читают в консоли, между четырнадцатью другими строками. Оно обязано
    нести файл, число из брифа и число из файла — иначе владелец не поймёт,
    какая из двух цифр правда, и пойдёт сверять руками ровно то, ради чего
    писалась проверка.
    """
    brief = brief_document({**BRIEF_ANSWERS, "q35_reply_time": "Протягом 3 годин"})
    client_dir = write_client(tmp_path, brief=brief)
    c15 = c15_of(run(client_dir, report_document(brief)))
    message = str(c15.message or "").strip()
    assert message, "C15 покраснела молча"
    assert message.upper() != "C15", "сообщение повторяет идентификатор"
    assert len(message) >= 12, f"сообщение слишком куцее: {message!r}"
    assert "q35_reply_time" in message or "года" in message.casefold() or (
        "годин" in message.casefold()), (
        f"сообщение не называет ни поля, ни его содержимого: {message!r}")
