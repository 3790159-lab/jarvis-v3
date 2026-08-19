# -*- coding: utf-8 -*-
"""T4 арки `chatter.onboard`: автоприёмка C1–C15 (спека §4, план T4; C15 —
отдельная спека `2026-08-17-c15-numbers-must-match-the-brief.md`).

Сторожа написаны ОТ СПЕКИ. `chatter/onboard/checks.py` при написании НЕ читался
и не грепался — на T1, T2 и T3 ровно эта дисциплина дала по три-восемь настоящих
дефектов, включая правило, евшее живые ответы клиента, и «зелёное по случайности»,
прошедшее лишь потому, что нужная цифра случайно нашлась в прайсе. Читались
спека, план, `vocabulary.py` и ПРОДАКШЕН-модули, которыми проверки обязаны
пользоваться (`config.loader`, `core.guardrails`, `core.escalation`,
`core.brand_safety`).

**Чем этот файл отличается от остальных сторожей арки.** `checks.py` — это САМ
СТОРОЖ пайплайна, значит здесь пишется сторож над сторожем, и главный класс
ошибки один: **проверка, которая зеленеет, ничего не проверив**. Спека §7 зовёт
это «зелёной ширмой». У проверок ширма выглядит ровно четырьмя способами, и все
четыре сторожатся ниже поимённо:

  1. **Проверка молча исчезла из списка.** Отсутствующий результат неотличим от
     пройденного: глазами видно «красных нет». Отсюда требование «ровно 14
     результатов ВСЕГДА, даже когда проверка не смогла отработать».
  2. **Проверка не нашла, что смотреть, и сочла это успехом.** Нет каталога —
     rc 0; нет дрил-сценария — C7 зелёная; отчёт битый — «претензий нет». Такой
     прогон не доказал НИЧЕГО, и его код обязан быть `2`, а не `0`.
  3. **Проверка сравнивает не с тем.** C10 держит парность «заглушка ⇔ строка
     отчёта»; если она смотрит только на один конец, второй можно удалить
     бесследно. Ровно этот класс («два числа на одну вещь») уже ловили в панели.
  4. **Красное не называет места и потому не будет прочитано.** «Проверка не
     прошла» без `файл:строка` заставляет искать руками — и через неделю такое
     красное начинают пролистывать.

Плюс два прямых решения владельца, которые обязаны быть закреплены тестом:

  · **без файла `REVIEWED` `--check` НЕ ЗЕЛЁНЫЙ** (решение q3 от 17.08): все
    проверки зелёные + отчёт не вычитан = `1`, не `0`;
  · **C12/C13 — ФЛАГИ, не красное**: флаг НЕ роняет вердикт (rc 0) И флаг НЕ
    исчезает. Обе стороны проверяются отдельно: «не роняет» без «не исчезает»
    даёт молчаливый флаг, то есть ширму на месте самого механизма против ширмы.

Фикстуры собраны здесь же. Настоящий бриф клиента (имя, контакты, внутренние
цены живого человека) не читается ни одной строкой; эталон `chatter/clients/yarina`
использован только как образец ФОРМЫ, его текст сюда не копировался.

**Фикстура доказывается продакшен-кодом, а не моим словом.** Отдельный тест
`test_golden_fixture_is_genuinely_clean` прогоняет по «чистому» клиенту настоящие
`load_config`, `guardrails._findings`, `parse_escalation_keywords` и
`forbidden_mention`. Без него «C2 зелёная» означало бы только «моя фикстура
такая», а не «guardrail признал прайс обеспеченным» — это и есть «зелёное по
случайности» с T2.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from chatter.config.loader import ConfigError, load_config
from chatter.core import guardrails
from chatter.core.brand_safety import forbidden_mention
from chatter.core.escalation import parse_escalation_keywords
from chatter.onboard import checks, vocabulary

# ─────────────────────────────────────────────────────────────────────────────
# Опознание результатов
#
# Спека §4 нумерует проверки C1…C14, спека C15 добавляет пятнадцатую, спека
# Хайку §9 — шестнадцатую (C16, префикс brain против эталона).
# Идентификатор сравнивается нормализованным
# (strip + upper): спор о регистре не должен глушить сторожей поведения, а вот
# ПРОПАВШИЙ идентификатор глушить обязан.
# ─────────────────────────────────────────────────────────────────────────────

ALL_IDS = tuple(f"C{i}" for i in range(1, 17))
# C16 — флаг по §9.2 спеки Хайку: рост префикса бывает законным, и красное
# означало бы отказ подключать клиента из-за правки его же плейбука.
FLAG_IDS = ("C12", "C13", "C16")
RED_IDS = tuple(i for i in ALL_IDS if i not in FLAG_IDS)


def ids_of(results) -> list[str]:
    return [str(r.id).strip().upper() for r in results]


def by_id(results, cid: str):
    for r in results:
        if str(r.id).strip().upper() == cid.upper():
            return r
    raise AssertionError(
        f"проверки {cid} нет в результате — исчезнувшая проверка неотличима "
        f"от пройденной; вернулись только {sorted(set(ids_of(results)))}")


def result(cid: str, *, ok: bool = True, is_flag: bool = False,
           message: str = "", file=None, line=None):
    """`CheckResult` руками — для тестов чистой функции `verdict`.

    `verdict` не ходит в файловую систему, поэтому её решения проверяются на
    собранных вручную результатах: иначе тест вердикта зависел бы от того,
    удалось ли фикстуре сделать все 14 проверок зелёными, и «rc 0 недостижим»
    маскировался бы под «вердикт работает».
    """
    return checks.CheckResult(id=cid, ok=ok, is_flag=is_flag, message=message,
                              file=file, line=line)


def all_green() -> list:
    return [result(cid) for cid in ALL_IDS]


# ─────────────────────────────────────────────────────────────────────────────
# ЭТАЛОННЫЙ («золотой») КЛИЕНТ
#
# Вымышленная студия детейлинга. Форма файлов повторяет живой каталог клиента —
# иначе проверки, которые ищут разделы и ценовые строки, покраснели бы на
# бедности фикстуры, а не на дефекте (ровно эту ошибку сторожа T3 уже сделали
# на первой редакции своей заглушки).
#
# Числа подобраны так, чтобы `guardrails._context_numbers` признавал их
# обеспеченными; это ПРОВЕРЕНО настоящим guardrail'ом в
# `test_golden_fixture_is_genuinely_clean`, а не заявлено.
# ─────────────────────────────────────────────────────────────────────────────

SLUG = "detailpro"

OWNER_ID = "Старший майстер"          # именительный: стоит подлежащим
OWNER_REF = "нашим старшим майстром"  # орудный: только после предлога
PERSONA_NAME = "Ярина"

# Полными фразами, не корнями (R6). Термин намеренно НЕ встречается ни в одном
# файле золотого клиента — самоотравление проверяет C5.
FORBIDDEN_TERMS = ("гарантуємо результат", "гарантируем результат")

PRICE_LINE_WASH = "- Ціна 1 200–2 000 грн, тривалість 1,5–2,5 години"
PRICE_LINE_POLISH = "- Ціна 5 000–8 000 грн, тривалість 6–10 годин"

# Тот же прайс, но десятичный разделитель — ТОЧКА. Ровно тот дефект, ради
# которого написан R1: `_context_numbers` режет knowledge по `[\n.!?;]`, точка
# внутри строки рвёт фрагмент, «1.5» и «2.5» остаются без валюты и ценового
# слова — и guardrail режет собственный прайс клиента.
PRICE_LINE_WASH_DIRTY = "- Ціна 1 200–2 000 грн, тривалість 1.5–2.5 години"

ICP_VALUE = "Власник авто преміум-класу, який готовий інвестувати в регулярний догляд"
ANTI_ICP_VALUE = "Кузовного ремонту та фарбування ми не робимо, це не наш профіль"

ESCALATION_HEADING = "Ключові слова ескалації"
ESCALATION_KEYWORDS = ("поклич", "покличте", "з менеджером", "з власником")

STUB_LINES = tuple(
    vocabulary.STUB_TEMPLATE_UK.format(title=f.title_uk, owner=OWNER_ID)
    for f in vocabulary.REQUIRED_FACTS
)

S = vocabulary.REQUIRED_SECTIONS_UK  # порядок и формулировки — из словаря, не с рук


def knowledge_sections() -> dict[str, list[str]]:
    """Разделы knowledge как изменяемая карта: тесты правят ОДИН раздел.

    Заголовки берутся из `vocabulary.REQUIRED_SECTIONS_UK`, а не переписаны
    руками. Переписанный руками заголовок дал бы C11 красное на моей опечатке —
    то есть сторож проверял бы фикстуру вместо кода.
    """
    return {
        S[0]: ["Ми — студія детейлінгу DrivePro у Києві, беремо на догляд "
               "авто будь-якого класу."],
        S[1]: [
            "## 1. Детейлінг-мийка",
            "",
            PRICE_LINE_WASH,
            "- Входить: двофазна мийка кузова, очищення дисків та шин, сушка",
            "",
            "## 2. Полірування кузова",
            "",
            PRICE_LINE_POLISH,
            "- Входить: підготовка, абразивна робота, захисний склад",
        ],
        S[2]: [
            "- Стан лакофарбового покриття та розмір автомобіля",
            "- Обраний матеріал та обсяг робіт",
        ],
        S[3]: [
            "- Розрахунок на місці після приймання роботи",
            "- Часткова оплата наперед для довгих робіт",
        ],
        S[4]: [
            "- Переробляємо безкоштовно, якщо результат не збігається з узгодженим",
            "- Матеріали беремо лише в офіційних постачальників",
        ],
        S[5]: [
            "- Глибокі подряпини до металу полірування не прибирає",
            "- Вм'ятини та корозію ми не виправляємо",
        ],
        S[6]: [
            f"- {ANTI_ICP_VALUE}",
            "- Шиномонтаж та ремонт двигуна не наша спеціалізація",
        ],
        S[7]: [
            "- Пишіть просто тут, у Telegram: модель авто та бажану послугу",
            "- Підбираємо вільне вікно та підтверджуємо його",
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


def render_playbook(*, heading: str = ESCALATION_HEADING,
                    keywords=ESCALATION_KEYWORDS,
                    icp: str = ICP_VALUE,
                    anti_icp: str = ANTI_ICP_VALUE) -> str:
    lines = [
        "# Плейбук",
        "",
        "## Ідеальний клієнт",
        "",
        "<!-- Внутрішня розмітка: вголос лідові не цитувати. -->",
        icp,
        "",
        "## Кому відмовляємо і чого не робимо",
        "",
        anti_icp,
        "",
        f"## {heading}",
        "",
        "<!-- Детермінований шар. Рядки коментаря НЕ починати з «- »:",
        "     парсер забирає БУДЬ-ЯКИЙ рядок-пункт у секції як ключове слово. -->",
    ]
    lines += [f"- {w}" for w in keywords]
    lines.append("")
    return "\n".join(lines)


PERSONA_MD = "\n".join([
    f"Мене звати {PERSONA_NAME}, мені 26. Я адміністраторка студії детейлінгу "
    "DrivePro — перший контакт клієнта зі студією.",
    "",
    f"Я не майстер і не власниця: найскладніші питання вирішує {OWNER_ID}.",
    "",
])

EXAMPLE_PAIRS = [
    {"client": "Скільки коштує полірування кузова?",
     "olga": "Полірування кузова — 5 000–8 000 грн, залежить від стану лаку. "
             "Який у вас автомобіль?"},
    {"client": "А мийка скільки?",
     "olga": "Детейлінг-мийка — 1 200–2 000 грн, тривалість 1,5–2,5 години. "
             "Підкажіть модель авто?"},
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
        "forbidden_terms": list(FORBIDDEN_TERMS),
        "telegram": {"allowlist": [237616472], "funnel_gate": False},
    }


def write_client(tmp_path: Path, *, slug: str = SLUG, knowledge: str | None = None,
                 playbook: str | None = None, persona: str | None = None,
                 examples=None, settings: dict | None = None,
                 settings_text: str | None = None) -> Path:
    """Каталог клиента в форме `build/onboard/<slug>/` (спека, §0).

    Возвращается КАТАЛОГ КЛИЕНТА (тот, где лежат пять файлов, REPORT.md и
    метка вычитки), потому что первый параметр `run_checks` назван `client_dir`.
    C1 при этом обязана звать `load_config(client_dir.parent, slug)` — так же,
    как это делает спека: `load_config(build/onboard, slug)`.
    """
    out = tmp_path / "build" / "onboard" / slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "persona.md").write_text(
        PERSONA_MD if persona is None else persona, encoding="utf-8")
    (out / "knowledge.md").write_text(
        render_knowledge(knowledge_sections()) if knowledge is None else knowledge,
        encoding="utf-8")
    (out / "playbook.md").write_text(
        render_playbook() if playbook is None else playbook, encoding="utf-8")
    (out / "examples.yaml").write_text(
        yaml.safe_dump(EXAMPLE_PAIRS if examples is None else examples,
                       allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    if settings_text is not None:
        (out / "settings.yaml").write_text(settings_text, encoding="utf-8")
    else:
        (out / "settings.yaml").write_text(
            yaml.safe_dump(settings_dict() if settings is None else settings,
                           allow_unicode=True, sort_keys=False),
            encoding="utf-8")
    (out / "brief.json").write_text(
        json.dumps(brief_document(), ensure_ascii=False, indent=2), encoding="utf-8")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# brief.json — второй артефакт прогона, лежащий в том же каталоге
#
# Появился здесь вместе с C15: сборка (`--brief`) кладёт разобранный бриф рядом
# с пятью файлами, и без него сверять числа файлов НЕ С ЧЕМ. Каталог без
# `brief.json` — это каталог, собранный не пайплайном; моделировать им «готовый
# прогон» значит проверять то, чего в жизни не бывает.
#
# Поля согласованы с `report_document()` НАМЕРЕННО: `q12_hours` и
# `q35_reply_time` там стоят строками раздела 3 («поле не заповнене»), значит и
# в брифе они обязаны быть пустыми. Две правды об одном поле в одной фикстуре —
# это ровно тот дефект, который фикстура должна ловить, а не порождать.
# ─────────────────────────────────────────────────────────────────────────────

BRIEF_PRICE_VALUE = "\n".join([
    "1. Детейлінг-мийка",
    "Ціна 1 200–2 000 грн, тривалість 1,5–2,5 години",
    "",
    "2. Полірування кузова",
    "Ціна 5 000–8 000 грн, тривалість 6–10 годин",
])


def brief_field(field_id: str, *, value, target: str, verdict: str = "ok",
                reason: str | None = None, raw=None) -> dict:
    return {
        "col": 0,
        "question": f"питання {field_id}",
        "raw": value if raw is None else raw,
        "value": value,
        "verdict": verdict,
        "reason": reason,
        "target": target,
    }


def brief_document() -> dict:
    return {
        "schema_version": 1,
        "source": "brief.xlsx",
        "fields": {
            "q12_hours": brief_field(
                "q12_hours", value=None, target="knowledge", verdict="garbage",
                reason="empty: поле не заповнене", raw=""),
            "q22_price_list": brief_field(
                "q22_price_list", value=BRIEF_PRICE_VALUE, target="knowledge"),
            "q28_promo": brief_field(
                "q28_promo", value="Акцій зараз немає", target="knowledge"),
            "q29_prepayment": brief_field(
                "q29_prepayment",
                value="Часткова оплата наперед для довгих робіт, "
                      "решта — після приймання роботи",
                target="knowledge"),
            "q35_reply_time": brief_field(
                "q35_reply_time", value=None, target="knowledge", verdict="garbage",
                reason="empty: поле не заповнене", raw=""),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# report.json — вход T4 (контракт плана, раздел «Контракт данных»)
#
# Строки раздела 3 намеренно НЕСУТ ИЗБЫТОК ключей (`fact_id`, `field_id`,
# `title`, `stub`, `question_for_client`). Причина: спека не говорит, ПО КАКОМУ
# ключу C10 сопоставляет факт со строкой отчёта, а мой тест не должен угадывать
# реализацию. Отрицательный тест удаляет строку ЦЕЛИКОМ — тогда любая стратегия
# поиска обязана заметить пропажу, какой бы ключ она ни выбрала.
# ─────────────────────────────────────────────────────────────────────────────

def missing_rows() -> list[dict]:
    rows = []
    for fact, stub in zip(vocabulary.REQUIRED_FACTS, STUB_LINES):
        rows.append({
            "fact_id": fact.id,
            "field_id": fact.source or fact.id,
            "title": fact.title_uk,
            "raw": None,
            "verdict": "garbage" if fact.source else None,
            "reason": "empty: поле не заповнене" if fact.source else "нет поля в форме",
            "stub": stub,
            "question_for_client": fact.question_for_client_uk,
        })
    return rows


def taken_rows() -> list[dict]:
    return [
        {"field_id": "q22_price_list", "target": "knowledge",
         "target_file": "knowledge.md", "anchor": S[1],
         "quote": PRICE_LINE_WASH.lstrip("- ")},
        {"field_id": "q37_icp", "target": "playbook",
         "target_file": "playbook.md", "anchor": "Ідеальний клієнт",
         "quote": ICP_VALUE},
        {"field_id": "q38_anti_icp", "target": "playbook",
         "target_file": "playbook.md", "anchor": "Кому відмовляємо і чого не робимо",
         "quote": ANTI_ICP_VALUE},
        {"field_id": "q34_stop_words", "target": "playbook",
         "target_file": "playbook.md", "anchor": ESCALATION_HEADING,
         "quote": ", ".join(ESCALATION_KEYWORDS)},
        {"field_id": "q20_examples", "target": "examples",
         "target_file": "examples.yaml", "anchor": "пара #1",
         "quote": EXAMPLE_PAIRS[0]["olga"]},
    ]


def report_document() -> dict:
    return {
        "schema_version": 1,
        "sections": {
            "taken": taken_rows(),
            "defaulted": [
                {"key": "honesty_mode", "value": "honest",
                 "why": "бот не приховує, що він бот", "brief_wanted_other": True,
                 "brief_quote": "спілкуватись як людина"},
                {"key": "funnel_gate", "value": False,
                 "why": "воронка вмикається лише командою власника",
                 "brief_wanted_other": False, "brief_quote": None},
                {"key": "payments", "value": "off",
                 "why": "реквізитів у брифі немає",
                 "brief_wanted_other": False, "brief_quote": None},
                {"key": "owner_ref", "value": OWNER_REF,
                 "why": "відмінок підставляє людина",
                 "brief_wanted_other": False, "brief_quote": None},
            ],
            "missing": missing_rows(),
        },
        "flags": [],
        "counters": {"services": 2, "prices": 4, "deadlines": 4,
                     "stop_words": len(ESCALATION_KEYWORDS),
                     "forbidden": len(FORBIDDEN_TERMS),
                     "example_pairs": len(EXAMPLE_PAIRS)},
    }


@pytest.fixture()
def golden(tmp_path):
    """Чистый клиент + согласованный с ним отчёт."""
    return write_client(tmp_path), report_document()


def run(client_dir: Path, doc: dict, *, slug: str = SLUG):
    return checks.run_checks(client_dir, doc, slug=slug)


# ═════════════════════════════════════════════════════════════════════════════
# 0. ФИКСТУРА ДОКАЗАНА ПРОДАКШЕН-КОДОМ
# ═════════════════════════════════════════════════════════════════════════════

def test_golden_fixture_is_genuinely_clean(golden):
    """Ловит: «зелёное по случайности» — сторож T2 уже наступал на это.

    Если бы «C2 зелёная» опиралось только на мою фикстуру, тест доказывал бы
    свойство фикстуры, а не поведение проверки: прайс мог оказаться обеспечен
    потому, что нужное число случайно лежит где-то ещё в файле. Здесь по
    золотому клиенту проходят НАСТОЯЩИЕ продакшен-функции — те же, которыми
    обязана пользоваться автоприёмка.
    """
    client_dir, _ = golden

    cfg = load_config(client_dir.parent, SLUG)          # C1 по-настоящему
    assert cfg.settings.persona_name == PERSONA_NAME

    knowledge = (client_dir / "knowledge.md").read_text(encoding="utf-8")
    for line in knowledge.splitlines():
        if line.strip().startswith("- Ціна"):
            assert guardrails._findings(line, knowledge) == [], (
                f"фикстура нечиста: guardrail не признаёт обеспеченной "
                f"собственную ценовую строку {line!r}")

    playbook = (client_dir / "playbook.md").read_text(encoding="utf-8")
    words = parse_escalation_keywords(playbook)
    assert words, "фикстура нечиста: детерминированный слой эскалации пуст"
    assert set(words) == {w.casefold() for w in ESCALATION_KEYWORDS}, (
        "фикстура нечиста: в словарь эскалации утекли лишние строки "
        f"(получено {words!r}) — так же, как утекали строки HTML-комментария")

    for name in ("persona.md", "knowledge.md", "playbook.md", "examples.yaml"):
        text = (client_dir / name).read_text(encoding="utf-8")
        assert forbidden_mention(text, FORBIDDEN_TERMS) is None, (
            f"фикстура нечиста: самоотравление brand-safety в {name}")


# ═════════════════════════════════════════════════════════════════════════════
# 1. РОВНО 14 РЕЗУЛЬТАТОВ — ВСЕГДА
#
# Проверка, молча исчезнувшая из списка, неотличима от пройденной: глазами
# видно «красных нет». Это первая форма зелёной ширмы, и она опаснее прочих,
# потому что не оставляет следа.
# ═════════════════════════════════════════════════════════════════════════════

def test_run_checks_returns_exactly_the_fourteen_checks(golden):
    """Ловит: тихо выпавшую проверку. Цена — «приёмка зелёная», при которой
    половина правил формата никто не смотрел."""
    client_dir, doc = golden
    res = run(client_dir, doc)
    assert sorted(set(ids_of(res))) == sorted(ALL_IDS), (
        f"ожидались ровно C1…C16, пришло {ids_of(res)}")
    assert len(res) == 16, (
        f"ровно 16 результатов (без дублей), пришло {len(res)}: {ids_of(res)}")


def test_all_fourteen_survive_a_client_dir_that_does_not_exist(tmp_path):
    """Ловит: «проверка не смогла отработать → её просто нет в списке».

    Каталога нет — ни одна проверка не может сказать «ок», но все пятнадцать
    обязаны СКАЗАТЬ ЭТО ВСЛУХ. Исключение наружу тоже не годится: вызывающий
    `__main__` тогда напечатает traceback вместо приёмки.
    """
    ghost = tmp_path / "build" / "onboard" / SLUG
    res = run(ghost, report_document())
    assert len(res) == 16, f"на отсутствующем каталоге вернулось {len(res)}"
    assert sorted(set(ids_of(res))) == sorted(ALL_IDS)


def test_all_fourteen_survive_a_broken_report_document(golden):
    """Ловит: битый отчёт роняет проверки, зависящие от него, и они пропадают.

    C10 читает раздел 3, C4 сверяется со списком отчёта. Если отчёт мусор,
    результат обязан быть «не состоялось», а не «этих проверок сегодня нет».
    """
    client_dir, _ = golden
    res = run(client_dir, {"это": "не отчёт"})
    assert len(res) == 16, f"на битом отчёте вернулось {len(res)}"
    assert sorted(set(ids_of(res))) == sorted(ALL_IDS)


# ═════════════════════════════════════════════════════════════════════════════
# 2. КОДЫ ВЫХОДА — ВЕРДИКТ, А НЕ СЧЁТЧИК
# ═════════════════════════════════════════════════════════════════════════════

def test_missing_client_dir_is_two_not_zero(tmp_path):
    """Ловит: тихий ноль на пустом входе — классика ширмы.

    «Проверять было нечего» и «всё в порядке» — разные события. Первое обязано
    быть `2`: такой прогон не доказал НИЧЕГО, и принимать по нему нельзя.
    """
    ghost = tmp_path / "build" / "onboard" / SLUG
    rc = checks.verdict(run(ghost, report_document()), reviewed=True)
    assert rc == 2, (
        f"каталога клиента нет, а вердикт {rc}. `0` здесь означал бы «приёмка "
        f"прошла», не открыв ни одного файла")


def test_broken_report_document_is_two(golden):
    """Ловит: приёмку по отчёту, которого не разобрали. Тот же класс, что выше:
    проверки не состоялись, значит `2`, а не `0` и не `1`."""
    client_dir, _ = golden
    rc = checks.verdict(run(client_dir, {"это": "не отчёт"}), reviewed=True)
    assert rc == 2, f"отчёт битый, а вердикт {rc}"


def test_green_and_reviewed_is_zero():
    """Единственная дорога к нулю: все зелёные И отчёт вычитан."""
    assert checks.verdict(all_green(), reviewed=True) == 0


def test_green_without_reviewed_is_one():
    """Решение владельца 17.08 (q3): без файла `REVIEWED` `--check` НЕ ЗЕЛЁНЫЙ.

    Ловит: главную ширму спеки §7 — дефолты и вопросы клиенту доезжают до прода
    как «решения» только потому, что разделы 2 и 3 никто не открыл, а rc был 0.
    """
    rc = checks.verdict(all_green(), reviewed=False)
    assert rc == 1, (
        f"все проверки зелёные, отчёт НЕ вычитан → ожидался 1, получено {rc}")


def test_red_stays_red_even_when_reviewed():
    """Ловит: метку вычитки, перебивающую красное. Вычитка добавляет условие,
    а не снимает его."""
    res = all_green()
    res[1] = result("C2", ok=False, message="knowledge.md:31 → 8000 не обеспечено",
                    file="knowledge.md", line=31)
    assert checks.verdict(res, reviewed=True) == 1


def test_flag_does_not_turn_the_verdict_red():
    """Решение спеки §4: C12/C13 — ФЛАГИ, rc остаётся 0.

    Ловит: превращение флага в блокер. Красное здесь означало бы, что пайплайн
    знает намерение клиента лучше владельца: «Старший майстер відповідає
    протягом години» — законная формулировка (Q35), и блокировать её нельзя.
    Сигнал, всегда красный при законной работе, — это фон, а не сторож.
    """
    res = all_green()
    res[11] = result("C12", ok=False, is_flag=True, file="knowledge.md", line=262,
                     message="⚠️ knowledge.md:262 «Старший майстер відповідає "
                             "протягом години» — обіцянка за людину")
    res[12] = result("C13", ok=False, is_flag=True, file="knowledge.md", line=240,
                     message="⚠️ «сайт»: knowledge.md:14 дає посилання, "
                             "knowledge.md:240 каже «посилань немає»")
    rc = checks.verdict(res, reviewed=True)
    assert rc == 0, f"флаги не роняют вердикт, а получено {rc}"


def test_flag_does_not_buy_a_green_without_reviewed():
    """Ловит: флаг, случайно ставший «уважительной причиной» пропустить вычитку.
    Требование вычитки не зависит от того, есть флаги или нет."""
    res = all_green()
    res[11] = result("C12", ok=False, is_flag=True, file="knowledge.md", line=262,
                     message="⚠️ обіцянка за людину")
    assert checks.verdict(res, reviewed=False) == 1


def test_empty_result_list_is_not_green():
    """Ловит: вердикт, считающий красные вместо того, чтобы считать вердикт.

    «Красных нет» на пустом списке — самая чистая форма ширмы: ноль проверок
    прошло, а код выхода говорит «принято».
    """
    rc = checks.verdict([], reviewed=True)
    assert rc != 0, "пустой список результатов не может быть зелёным прогоном"


def test_incomplete_result_list_is_not_green():
    """Ловит: вердикт, которому всё равно, сколько проверок до него доехало.

    ⚠️ Требование зафиксировано КОНСЕРВАТИВНО: спека говорит про 15 проверок,
    но прямо не запрещает `verdict` считать зелёным неполный список. Считаю
    запрет обязательным: пропажа проверки уже стоила арке панели один сторож,
    который «всегда зелёный», и единственный дешёвый способ поймать пропажу —
    отказаться выдавать 0 по неполной разметке.
    """
    res = [r for r in all_green() if str(r.id).upper() != "C7"]
    rc = checks.verdict(res, reviewed=True)
    assert rc != 0, (
        "14 результатов из 15 — это не зелёный прогон, а прогон с пропавшей "
        "проверкой; 0 здесь скрывает пропажу навсегда")


# ═════════════════════════════════════════════════════════════════════════════
# 3. КРАСНОЕ НАЗЫВАЕТ МЕСТО
#
# Спека §4: «Красное обязано назвать файл:строку и виновника, а не „проверка не
# прошла“». Красное без адреса заставляет искать руками и потому не будет
# прочитано — через неделю такие строки начинают пролистывать.
# ═════════════════════════════════════════════════════════════════════════════

def test_c2_red_names_file_line_and_culprit(tmp_path):
    """Ловит: «C2: провалено» без адреса. Прайс у клиента длинный, и без номера
    строки владелец не найдёт, какая именно цена не обеспечена."""
    sections = knowledge_sections()
    sections[S[1]] = [line if line != PRICE_LINE_WASH else PRICE_LINE_WASH_DIRTY
                      for line in sections[S[1]]]
    client_dir = write_client(tmp_path, knowledge=render_knowledge(sections))
    res = run(client_dir, report_document())
    c2 = by_id(res, "C2")

    assert not c2.ok, "точка внутри ценовой строки не признана дефектом"
    assert c2.file and "knowledge.md" in str(c2.file), (
        f"C2 не назвала файл: file={c2.file!r}")
    assert isinstance(c2.line, int) and c2.line > 0, (
        f"C2 не назвала строку: line={c2.line!r}")
    assert "knowledge.md" in c2.message, (
        f"в сообщении нет файла: {c2.message!r}")
    assert ("1.5" in c2.message or "2.5" in c2.message), (
        f"в сообщении нет виновника — числа, которое guardrail не признал "
        f"обеспеченным: {c2.message!r}")


def test_every_red_says_something_beyond_its_own_name(tmp_path):
    """Ловит: сообщение вида «C9 не прошла» — формально красное, практически
    непрочитываемое. Требую, чтобы сообщение несло текст сверх идентификатора."""
    client_dir = write_client(tmp_path, persona="# Ярина\n\nадміністраторка\n")
    res = run(client_dir, report_document())
    for r in res:
        if r.ok:
            continue
        msg = str(r.message or "").strip()
        assert msg, f"{r.id}: красное без сообщения"
        assert msg.upper() != str(r.id).strip().upper(), (
            f"{r.id}: сообщение повторяет идентификатор и ничего не добавляет")
        assert len(msg) >= 12, f"{r.id}: сообщение слишком куцее: {msg!r}"


def test_a_file_without_a_line_is_half_an_address(tmp_path):
    """Ловит: `file` заполнен, `line` — нет.

    Половина адреса хуже отсутствия адреса: она создаёт впечатление, что место
    названо, и владелец ищет по всему файлу.
    """
    sections = knowledge_sections()
    sections[S[1]] = [line if line != PRICE_LINE_WASH else PRICE_LINE_WASH_DIRTY
                      for line in sections[S[1]]]
    client_dir = write_client(tmp_path, knowledge=render_knowledge(sections))
    for r in run(client_dir, report_document()):
        if r.ok or not r.file:
            continue
        assert isinstance(r.line, int) and r.line > 0, (
            f"{r.id}: file={r.file!r}, а line={r.line!r} — адрес назван наполовину")


# ═════════════════════════════════════════════════════════════════════════════
# 4. C1 — КЛИЕНТ РЕАЛЬНО ПОДНИМАЕТСЯ
# ═════════════════════════════════════════════════════════════════════════════

def test_c1_green_when_the_client_really_loads(golden):
    """Ловит: C1, проверяющую наличие файлов вместо подъёма клиента.

    Тот же каталог поднимается настоящим `load_config` прямо здесь — если C1
    красная, а `load_config` довольна, проверка сравнивает не с тем.
    """
    client_dir, doc = golden
    load_config(client_dir.parent, SLUG)      # не должно бросить
    assert by_id(run(client_dir, doc), "C1").ok


def test_c1_red_with_the_config_error_text_when_the_client_does_not_load(tmp_path):
    """Ловит: собственную имитацию загрузчика вместо `load_config`.

    Здесь ломается ровно то, что ловит только настоящий лоадер: `persona_name`
    совпал с `owner_id`. Файлы все на месте, YAML валиден, схему полей никто не
    нарушил — самодельная проверка «пять файлов лежат» была бы зелёной, а живой
    клиент не стартовал бы.
    """
    bad = settings_dict()
    bad["persona_name"] = OWNER_ID
    client_dir = write_client(tmp_path, settings=bad)

    with pytest.raises(ConfigError) as exc:
        load_config(client_dir.parent, SLUG)
    real = str(exc.value)

    c1 = by_id(run(client_dir, report_document()), "C1")
    assert not c1.ok, "клиент не поднимается, а C1 зелёная"
    assert "persona_name" in c1.message, (
        f"текст ConfigError не доехал до сообщения: {c1.message!r} "
        f"(настоящая ошибка: {real!r})")


# ═════════════════════════════════════════════════════════════════════════════
# 5. C2 — ПРОВЕРЯЕТ ПОСЛЕДСТВИЕ, А НЕ ФОРМУ
# ═════════════════════════════════════════════════════════════════════════════

def test_c2_green_on_a_clean_price_list(golden):
    """Ловит: C2, красную на законном прайсе. Сигнал, всегда красный при
    законной работе, — фон, а не сторож."""
    client_dir, doc = golden
    assert by_id(run(client_dir, doc), "C2").ok


def test_c2_red_when_a_dot_inside_the_price_line_splits_the_fragment(tmp_path):
    """Ловит ГЛАВНЫЙ дефект, ради которого написан R1.

    `guardrails._context_numbers` режет knowledge по `[\\n.!?;]`, и число
    попадает в обеспеченное множество только из фрагмента, где есть валюта или
    ценовое слово. Точка внутри строки рвёт фрагмент, «1.5» остаётся без
    валюты — и guardrail режет собственный прайс клиента, то есть бот молчит на
    вопрос о цене. C2 обязана проверять ПОСЛЕДСТВИЕ (guardrail не признал
    цену), а не форму записи: проверка «в строке нет точки» зеленела бы на
    любом другом способе разорвать фрагмент.

    Что дефект настоящий, здесь же подтверждает сам guardrail.
    """
    sections = knowledge_sections()
    sections[S[1]] = [line if line != PRICE_LINE_WASH else PRICE_LINE_WASH_DIRTY
                      for line in sections[S[1]]]
    knowledge = render_knowledge(sections)

    assert guardrails._findings(PRICE_LINE_WASH_DIRTY, knowledge), (
        "фикстура не воспроизводит дефект: guardrail признал строку "
        "обеспеченной, значит краснеть C2 не с чего")

    client_dir = write_client(tmp_path, knowledge=knowledge)
    assert not by_id(run(client_dir, report_document()), "C2").ok


# ═════════════════════════════════════════════════════════════════════════════
# 6. C3 — ПРИМЕРЫ ЧИСТЫ
# ═════════════════════════════════════════════════════════════════════════════

def test_c3_red_when_an_example_names_a_price_absent_from_knowledge(tmp_path):
    """Ловит: примеры, не прогнанные через guardrail.

    Пары examples едут в промпт как эталон ГОЛОСА, и число из них персона
    повторит лиду дословно. Число, которого нет в прайсе, будет вырезано
    редакцией уже в бою — то есть ответ клиенту развалится на живом диалоге.
    """
    pairs = [dict(p) for p in EXAMPLE_PAIRS]
    pairs[0]["olga"] = "Полірування кузова — 3 500 грн, готово за 2 дні."
    client_dir = write_client(tmp_path, examples=pairs)

    knowledge = (client_dir / "knowledge.md").read_text(encoding="utf-8")
    assert guardrails._findings(pairs[0]["olga"], knowledge), (
        "фикстура не воспроизводит дефект: guardrail не возражает")

    assert not by_id(run(client_dir, report_document()), "C3").ok


# ═════════════════════════════════════════════════════════════════════════════
# 7. C4 — СЛОЙ ЭСКАЛАЦИИ ЖИВ
#
# Это ЕДИНСТВЕННЫЙ слой, работающий при мёртвом классификаторе. Пустой список =
# детерминированная защита МОЛЧА выключена, и узнать об этом можно только тогда,
# когда лид попросил владельца, а бот не позвал.
# ═════════════════════════════════════════════════════════════════════════════

def test_c4_green_when_the_layer_is_alive(golden):
    """Ловит: C4, красную на живом слое. Проверено настоящим парсером."""
    client_dir, doc = golden
    playbook = (client_dir / "playbook.md").read_text(encoding="utf-8")
    assert parse_escalation_keywords(playbook)
    assert by_id(run(client_dir, doc), "C4").ok


def test_c4_red_when_the_section_heading_is_not_one_of_the_three(tmp_path):
    """Ловит: проверку «секция про эскалацию есть» вместо «парсер её видит».

    `escalation._KEYWORD_HEADINGS` знает РОВНО три формулировки. Любая другая —
    и слой выключен МОЛЧА: секция на месте, слова написаны, глазами всё хорошо.
    Поэтому C4 обязана звать настоящий `parse_escalation_keywords`, а не искать
    заголовок своим глазом.
    """
    playbook = render_playbook(heading="Слова для ескалації")
    assert parse_escalation_keywords(playbook) == [], (
        "фикстура не воспроизводит дефект: парсер всё-таки нашёл секцию")

    client_dir = write_client(tmp_path, playbook=playbook)
    c4 = by_id(run(client_dir, report_document()), "C4")
    assert not c4.ok, "слой эскалации пуст, а C4 зелёная"
    assert str(c4.message).strip(), "C4 покраснела молча"


def test_c4_red_when_a_keyword_matches_legal_knowledge_text(tmp_path):
    """Ловит: слишком широкий корень в словаре эскалации.

    «поверн» голым корнем матчит легальное «повернути блиск кузова» — и бот
    зовёт владельца на обычный вопрос о полировке. Цена ложного срабатывания —
    владелец, которого дёргают на каждом втором диалоге, и который через неделю
    перестаёт реагировать.
    """
    sections = knowledge_sections()
    sections[S[1]] = sections[S[1]] + [
        "- Полірування дозволяє повернути блиск кузова без фарбування"]
    client_dir = write_client(
        tmp_path,
        knowledge=render_knowledge(sections),
        playbook=render_playbook(keywords=ESCALATION_KEYWORDS + ("поверн",)))
    assert not by_id(run(client_dir, report_document()), "C4").ok


# ═════════════════════════════════════════════════════════════════════════════
# 8. C5/C6 — САМООТРАВЛЕНИЕ И ПЕРВАЯ СТРОКА ПЕРСОНЫ
# ═════════════════════════════════════════════════════════════════════════════

def test_c5_red_when_a_forbidden_term_lives_in_our_own_knowledge(tmp_path):
    """Ловит: brand-safety, стреляющий по собственному конфигу.

    `forbidden_mention` матчит подстрокой без нормализации. Термин, живущий в
    нашем же knowledge, означает, что законный ответ клиенту будет зарублен
    нашей же защитой — потерянный ответ лиду.
    """
    sections = knowledge_sections()
    sections[S[4]] = sections[S[4]] + [
        f"- Ми {FORBIDDEN_TERMS[0]} за умови дотримання рекомендацій"]
    knowledge = render_knowledge(sections)
    assert forbidden_mention(knowledge, FORBIDDEN_TERMS) is not None

    client_dir = write_client(tmp_path, knowledge=knowledge)
    assert not by_id(run(client_dir, report_document()), "C5").ok


def test_c6_red_when_persona_starts_with_a_heading(tmp_path):
    """Ловит: заголовок в первой строке персоны.

    `disclosure`/`honest_prefix` берут ПЕРВУЮ строку persona.md и отдают её
    лиду на вопрос «ты бот?». Решётка уехала бы прямо в чат клиента.
    """
    client_dir = write_client(
        tmp_path,
        persona=f"# {PERSONA_NAME}\n\nМене звати {PERSONA_NAME}, мені 26.\n")
    c6 = by_id(run(client_dir, report_document()), "C6")
    assert not c6.ok, "первая строка персоны — заголовок, а C6 зелёная"
    assert isinstance(c6.line, int) and c6.line > 0, "C6 не назвала строку"


# ═════════════════════════════════════════════════════════════════════════════
# 9. C7 — ОТСУТСТВУЮЩИЙ СЦЕНАРИЙ НЕ УСПЕХ
# ═════════════════════════════════════════════════════════════════════════════

def test_c7_does_not_read_a_missing_drill_scenario_as_a_pass(golden):
    """Ловит: «файла нет → возражений нет» — вторая форма зелёной ширмы.

    ⚠️ Требование зафиксировано КОНСЕРВАТИВНО: спека не говорит прямо, что
    делать C7 при отсутствующем сценарии (генератор заготовки — это T6, волна
    3). Фиксирую «не зелёная», потому что противоположное решение делает C7
    вечно зелёной у всех клиентов сразу и навсегда — а проверка, которая не
    может покраснеть, не существует.
    """
    client_dir, doc = golden
    assert not (client_dir / "drill.yaml").exists()
    assert not by_id(run(client_dir, doc), "C7").ok, (
        "дрил-сценария нет, а C7 зелёная — «не нашёл» посчитано «в порядке»")


# ═════════════════════════════════════════════════════════════════════════════
# 10. C8 — ФОРМАТ-ГИГИЕНА
# ═════════════════════════════════════════════════════════════════════════════

def test_c8_red_on_nbsp_inside_a_price_line(tmp_path):
    """Ловит: NBSP из Google Forms.

    `_numbers` снимает только пробелы `\\s`; NBSP — другой токен, и «1 200» с
    неразрывным пробелом перестаёт совпадать с «1 200» из ответа. Глазами
    отличить невозможно, поэтому это обязана ловить машина.
    """
    sections = knowledge_sections()
    # Экранированный литерал, а не сам символ: NBSP в исходнике невидим глазами
    # и переживёт не всякий редактор — а тогда сторож молча перестанет ловить.
    sections[S[1]] = [line.replace("1 200", "1\u00a0200") for line in sections[S[1]]]
    knowledge = render_knowledge(sections)
    assert "\u00a0" in knowledge, "фикстура не подставила NBSP"
    client_dir = write_client(tmp_path, knowledge=knowledge)
    c8 = by_id(run(client_dir, report_document()), "C8")
    assert not c8.ok, "NBSP в ценовой строке не пойман"
    assert isinstance(c8.line, int) and c8.line > 0, "C8 не назвала строку"


# ═════════════════════════════════════════════════════════════════════════════
# 11. C9 — МАРШРУТ ICP, И ЕГО ЗАКОННОЕ ИСКЛЮЧЕНИЕ
# ═════════════════════════════════════════════════════════════════════════════

def test_c9_stays_green_on_the_dual_purpose_q38(golden):
    """Решение владельца 17.08: Q38 — поле ДВОЙНОГО назначения.

    Ловит: проверку, красную при законной работе. Полный текст Q38 законно
    живёт и в playbook, и в разделе knowledge «Чого ми не робимо» — иначе R8
    остаётся без источника. C9, не знающая про `DUAL_PURPOSE_FIELDS`, красная
    на КАЖДОМ клиенте, а всегда красный сигнал — это фон, который перестают
    читать через два прогона.
    """
    client_dir, doc = golden
    knowledge = (client_dir / "knowledge.md").read_text(encoding="utf-8")
    assert ANTI_ICP_VALUE in knowledge, "фикстура не воспроизводит двойной маршрут"
    assert "q38_anti_icp" in vocabulary.DUAL_PURPOSE_FIELDS

    c9 = by_id(run(client_dir, doc), "C9")
    assert c9.ok, (
        f"C9 покраснела на законном двойном маршруте Q38: {c9.message!r}")


def test_c9_red_when_a_plain_playbook_field_leaks_into_knowledge(tmp_path):
    """Обратная сторона того же исключения.

    Ловит: исключение, съевшее саму проверку. Если C9 замолчала не про Q38, а
    вообще, то строка «наш ідеальний клієнт — той, хто готовий інвестувати»
    однажды будет зачитана лиду вслух: knowledge — то, из чего бот ГОВОРИТ,
    playbook — то, как он себя ВЕДЁТ.
    """
    sections = knowledge_sections()
    sections[S[0]] = sections[S[0]] + [f"- {ICP_VALUE}"]
    client_dir = write_client(tmp_path, knowledge=render_knowledge(sections))

    assert len(ICP_VALUE) >= 40, "фрагмент короче порога сравнения из спеки"
    assert "q37_icp" not in vocabulary.DUAL_PURPOSE_FIELDS

    c9 = by_id(run(client_dir, report_document()), "C9")
    assert not c9.ok, "ICP уехал в knowledge, а C9 зелёная"
    assert "q37_icp" in c9.message or "ідеальний" in c9.message.casefold(), (
        f"C9 не назвала виновника: {c9.message!r}")


# ═════════════════════════════════════════════════════════════════════════════
# 12. C10 — ПАРНОСТЬ «ЗАГЛУШКА ⇔ СТРОКА ОТЧЁТА»
#
# R7: молча пропустить факт нельзя — либо данные, либо заглушка ПЛЮС строка.
# Проверка одного конца из двух — это «два числа на одну вещь»: меньшее гасит
# большее молча.
# ═════════════════════════════════════════════════════════════════════════════

def test_c10_green_when_both_halves_are_in_place(golden):
    client_dir, doc = golden
    c10 = by_id(run(client_dir, doc), "C10")
    assert c10.ok, f"обе половины на месте, а C10 красная: {c10.message!r}"


def test_c10_red_when_the_knowledge_stub_is_gone(tmp_path):
    """Ловит: C10, смотрящую только в отчёт.

    Отчёт обещает владельцу, что бот про адрес молчит и зовёт человека. Без
    заглушки в knowledge бот АДРЕС ВЫДУМАЕТ — и лид поедет не туда. Отчёт при
    этом остаётся идеально зелёным.
    """
    gone = vocabulary.STUB_TEMPLATE_UK.format(
        title=vocabulary.REQUIRED_FACTS[0].title_uk, owner=OWNER_ID)
    sections = knowledge_sections()
    sections[S[8]] = [l for l in sections[S[8]] if gone not in l]
    assert len(sections[S[8]]) == len(STUB_LINES) - 1, "фикстура не убрала заглушку"

    client_dir = write_client(tmp_path, knowledge=render_knowledge(sections))
    c10 = by_id(run(client_dir, report_document()), "C10")
    assert not c10.ok, "заглушки в knowledge нет, а C10 зелёная"
    assert vocabulary.REQUIRED_FACTS[0].id in c10.message or (
        vocabulary.REQUIRED_FACTS[0].title_uk.split(",")[0] in c10.message), (
        f"C10 не назвала, какой факт остался без заглушки: {c10.message!r}")


def test_c10_red_when_the_report_row_is_gone(golden):
    """Ловит: C10, смотрящую только в knowledge.

    Заглушка на месте — бот молчит про телефон и это правильно. Но владелец
    НИКОГДА не узнает, что телефон надо спросить у клиента: раздел 3 — ровно
    то место, откуда он копирует вопросы. Молчание бота без вопроса клиенту =
    факт, который не появится никогда.
    """
    client_dir, doc = golden
    victim = "phone"
    doc["sections"]["missing"] = [
        r for r in doc["sections"]["missing"] if r["fact_id"] != victim]
    assert len(doc["sections"]["missing"]) == len(vocabulary.REQUIRED_FACTS) - 1

    c10 = by_id(run(client_dir, doc), "C10")
    assert not c10.ok, "строки факта в разделе 3 нет, а C10 зелёная"


# ═════════════════════════════════════════════════════════════════════════════
# 13. C11 — ПУСТОЙ ЗАГОЛОВОК СЧИТАЕТСЯ ОТСУТСТВУЮЩИМ
# ═════════════════════════════════════════════════════════════════════════════

def test_c11_green_when_every_section_has_content(golden):
    client_dir, doc = golden
    c11 = by_id(run(client_dir, doc), "C11")
    assert c11.ok, f"все девять разделов на месте, а C11 красная: {c11.message!r}"


def test_c11_red_when_a_section_is_absent(tmp_path):
    """Ловит: R8 в чистом виде. Раздел «Як записатися» собирается из нескольких
    полей плюс решения владельца — при всех заполненных полях его можно просто
    не написать, и бот не сможет записать лида."""
    sections = knowledge_sections()
    del sections[S[7]]
    client_dir = write_client(tmp_path, knowledge=render_knowledge(sections))
    c11 = by_id(run(client_dir, report_document()), "C11")
    assert not c11.ok
    assert S[7] in c11.message, f"C11 не назвала пропавший раздел: {c11.message!r}"


def test_c11_red_when_a_section_is_an_empty_heading(tmp_path):
    """Ловит: «раздел есть» в значении «строка с решёткой напечатана».

    Это разница между проверкой смысла и проверкой разметки. Пустой заголовок —
    ровно тот случай, когда генератор «выполнил» R8, ничего не написав, а C11
    подтвердила выполнение.
    """
    sections = knowledge_sections()
    sections[S[7]] = []
    client_dir = write_client(tmp_path, knowledge=render_knowledge(sections))
    c11 = by_id(run(client_dir, report_document()), "C11")
    assert not c11.ok, (
        f"раздел {S[7]!r} — голый заголовок без содержимого, а C11 зелёная")
    # ДОБАВЛЕНО ИНТЕГРАТОРОМ 17.08: мутация «убрать номер строки из красного
    # C11» прошла ЗЕЛЁНОЙ на всех 43 тестах. Общий сторож половины адреса
    # существует, но его фикстура краснит C2, а не C11, поэтому именно этот
    # адрес не проверял никто. Класс ошибки тот же, что он и ловит: `file` без
    # `line` выглядит названным местом и гонит владельца искать по всему файлу.
    assert isinstance(c11.line, int) and c11.line > 0, (
        f"C11 назвала файл, но не строку: file={c11.file!r}, line={c11.line!r}")


# ═════════════════════════════════════════════════════════════════════════════
# 14. C12/C13 — ФЛАГИ ВИДНЫ
#
# «Флаг не роняет вердикт» без «флаг не исчезает» даёт молчаливый флаг: rc 0,
# блок «ФЛАГИ» пуст, владелец ничего не решал. Это ширма на месте самого
# механизма против ширмы, поэтому проверяются ОБЕ стороны.
# ═════════════════════════════════════════════════════════════════════════════

def test_c12_flags_a_promise_made_on_behalf_of_a_person(tmp_path):
    """Ловит: исчезнувший флаг C12.

    Роль владельца в одном предложении с интервалом времени — это обещание за
    третье лицо. Законно оно или нет, решает владелец (§5), но УВИДЕТЬ он его
    обязан: `is_flag` без `ok=False` невидим, `ok=False` без `is_flag` роняет
    вердикт.
    """
    sections = knowledge_sections()
    sections[S[7]] = sections[S[7]] + [
        f"- {OWNER_ID} передзвонить протягом двох днів після заявки"]
    client_dir = write_client(tmp_path, knowledge=render_knowledge(sections))

    res = run(client_dir, report_document())
    c12 = by_id(res, "C12")
    assert c12.is_flag, "C12 обязана быть флагом, а не обычной проверкой"
    assert not c12.ok, "обещание за третье лицо не отмечено — флаг молчит"
    assert c12.file and isinstance(c12.line, int) and c12.line > 0, (
        f"флаг без адреса: file={c12.file!r}, line={c12.line!r} "
        f"(спека §3: каждый флаг — файл:строка, цитата и вопрос)")

    # Флаг не переводит вердикт в красное: если тот же прогон вышел красным,
    # виновником обязана быть НЕ-флаговая проверка, а не C12.
    if checks.verdict(res, reviewed=True) == 1:
        assert any(not r.ok and not r.is_flag for r in res), (
            "флаг C12 в одиночку сделал вердикт красным")


def test_c12_flags_the_spec_own_example_about_an_hour(tmp_path):
    """Спека §6 обещает этот флаг ЗАРАНЕЕ, на калибровке ручного эталона:
    «C12 даёт флаг на „Старший майстер відповідає протягом години“».

    Ловит: C12, построенную на `guardrails._TIME_UNIT` буквально. Замер:
    украинское «години»/«годин» этим выражением НЕ распознаётся (перечислены
    `год(а|у|е|ом|ы|ах|ам|ов|ами)?` с границей слова, куда «години» не
    попадает). Если C12 просто переиспользует `_TIME_UNIT`, обещанный спекой
    флаг не появится, и §6 сойдётся «зелёным» на пустом месте.

    Тест намеренно оставлен красным до решения: либо C12 расширяет список
    единиц у себя, либо `_TIME_UNIT` чинится в guardrails (это трогает живой
    рантайм двух клиентов и решается владельцем, а не сторожем).
    """
    sections = knowledge_sections()
    sections[S[7]] = sections[S[7]] + [f"- {OWNER_ID} відповідає протягом години"]
    client_dir = write_client(tmp_path, knowledge=render_knowledge(sections))

    c12 = by_id(run(client_dir, report_document()), "C12")
    assert c12.is_flag
    assert not c12.ok, (
        "«відповідає протягом години» не отмечено флагом — ровно та строка, "
        "которую спека §6 обещает как ожидаемый результат калибровки")


def test_c13_flags_an_entity_both_stated_and_declared_unknown(tmp_path):
    """Ловит: исчезнувший флаг C13.

    Сущность и утверждается, и лежит в «Чого ми НЕ знаємо». Одно из двух —
    неправда, и какое именно, решает владелец; но противоречие в knowledge
    означает, что бот скажет лиду то одно, то другое.
    """
    sections = knowledge_sections()
    sections[S[0]] = sections[S[0]] + [
        "- Наш сайт drivepro-detailing працює цілодобово, там же портфоліо"]
    client_dir = write_client(tmp_path, knowledge=render_knowledge(sections))

    c13 = by_id(run(client_dir, report_document()), "C13")
    assert c13.is_flag, "C13 обязана быть флагом"
    assert not c13.ok, (
        "«сайт» одновременно назван и объявлен неизвестным — флаг молчит")


def test_flags_are_marked_as_flags_and_reds_are_not(golden):
    """Ловит: перепутанную разметку `is_flag`.

    Если обычная проверка помечена флагом, она перестаёт ронять вердикт — то
    есть тихо выключается. Если флаг помечен обычной, он блокирует приёмку на
    законной формулировке клиента. Обе ошибки невидимы, пока не смотреть на
    саму разметку.
    """
    client_dir, doc = golden
    res = run(client_dir, doc)
    for cid in FLAG_IDS:
        assert by_id(res, cid).is_flag, f"{cid} обязана быть флагом (спека §4)"
    for cid in RED_IDS:
        assert not by_id(res, cid).is_flag, (
            f"{cid} помечена флагом — значит перестала ронять вердикт")


# ═════════════════════════════════════════════════════════════════════════════
# 15. C14 — ВИСЯЩИЙ СРОК
#
# Числовой guardrail здесь бессилен ПО КОНСТРУКЦИИ: числа нет. Лид получил
# обещание и не знает, у кого спросить точное.
# ═════════════════════════════════════════════════════════════════════════════

def test_c14_red_when_a_vague_deadline_names_nobody(tmp_path):
    """Ловит: срок без числа и без ответственного."""
    sections = knowledge_sections()
    sections[S[1]] = sections[S[1]] + [
        "- Керамічне покриття потребує додаткового часу на полімеризацію"]
    client_dir = write_client(tmp_path, knowledge=render_knowledge(sections))
    c14 = by_id(run(client_dir, report_document()), "C14")
    assert not c14.ok, "висящий срок не пойман"
    assert isinstance(c14.line, int) and c14.line > 0, "C14 не назвала строку"


def test_c14_green_when_the_same_line_names_who_will_tell(tmp_path):
    """Обратное направление: с указанием, кто назовёт точное, — не красное.

    Без этой половины C14 могла бы краснеть на любом упоминании времени, и
    эталон Ярины, уже отработавший живой дрил, покраснел бы целиком.
    """
    sections = knowledge_sections()
    sections[S[1]] = sections[S[1]] + [
        "- Керамічне покриття потребує додаткового часу на полімеризацію, "
        f"точний час називає {OWNER_ID}"]
    client_dir = write_client(tmp_path, knowledge=render_knowledge(sections))
    c14 = by_id(run(client_dir, report_document()), "C14")
    assert c14.ok, f"C14 краснеет там, где ответственный назван: {c14.message!r}"


def test_c14_green_on_the_golden_client(golden):
    """Ловит: C14, красную при законной работе. Все сроки золотого клиента либо
    имеют число, либо называют, кто скажет точное."""
    client_dir, doc = golden
    c14 = by_id(run(client_dir, doc), "C14")
    assert c14.ok, f"C14 красная на чистом клиенте: {c14.message!r}"
