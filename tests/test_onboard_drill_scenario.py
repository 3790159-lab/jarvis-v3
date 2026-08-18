# -*- coding: utf-8 -*-
"""T6 арки `chatter.onboard`: заготовка дрил-сценария (спека §4 C7, план T6).

Сторожа написаны ОТ СПЕКИ. `chatter/onboard/drill_scenario.py` при написании НЕ
открывался и не грепался ни разу. Читались: спека, план, живые сценарии
`docs/chatter/drills/*.yaml` и ПРОДАКШЕН-модули, которыми сгенерированный
сценарий обязан отработать на стенде — `chatter/core/drill.py`,
`chatter/payments/drill_gate.py`, `chatter/core/guardrails.py`,
`chatter/onboard/vocabulary.py`, `chatter/onboard/report.py`.

**Класс ошибки, ради которого этот файл существует, ровно один: сценарий,
ПОХОЖИЙ на сценарий.** Генератор отдаёт YAML, YAML читается глазами как
осмысленный, владелец зовёт человека, платит за живой прогон (демо Ярины —
$0.1392) и получает отчёт, который ничего не значит. Способов у этого класса
четыре, и все четыре сторожатся ниже поимённо:

  1. **Не разбирается стендом.** `parse_scenario` роняет прогон на первом шаге —
     дешёвый исход, потому что громкий. Проверяется настоящим парсером, а не
     «похоже на YAML».
  2. **Разбирается, но не может стать ЗЕЛЁНЫМ.** 🔴 DEV-32: `_normalize_say`
     выбрасывает всё, кроме букв и цифр; у реплики из одних эмодзи («🔥👍» —
     шаг 6 живого `yarina-onboarding-1-6.yaml`) нормализация ПУСТА, `match_step`
     возвращает `None`, и шаг обречён быть красным при идеально работающем боте.
     Сгенерированный шаг, который не может стать зелёным, — это не проверка, а
     ловушка: следующий человек будет чинить бота, который здоров.
  3. **Разбирается, зеленеет, но проверяет НЕ ТО.** Два подвида, и оба уже
     случались на этом стенде: `vacuous_expectations` — проверка, зелёная ещё ДО
     старта (прогон 25.07: три obligations-проверки из четырёх были зелены на
     первом же шаге); `match_step` — проверки шага N, приписанные ходу шага N−1
     (прогон №5 26.07, стенд разъехался на шаг).
  4. **Проверяет то, но на выдуманных данных.** Контрольный вопрос, придуманный
     генератором вместо взятого из раздела 3 отчёта, спрашивает бота про то, что
     у него в базе ЕСТЬ, — и «бот не выдумал» перестаёт что-либо значить. Цена
     ошибки та же: живой прогон за деньги, доказавший ноль.

**Сторожа доказаны продакшен-кодом, а не моим словом.** Там, где сторож мог бы
зеленеть на бедности фикстуры (цены не признаются ценами, эмодзи-шаг «прошёл
бы»), стоит отдельный мета-тест, показывающий, что проверка КУСАЕТСЯ.

Фикстуры собраны здесь же. Настоящий бриф клиента (имя, контакты и внутренние
цены живого человека) не читался ни одной строкой; студия ниже вымышлена.
`slug`/`contact` взяты реальные (`yarina` / `8849893367:yarina`) по одной
причине: `DRILL_CONTACTS` — закрытый список, и пары «выдуманный slug + законный
контакт» в природе не существует.

$0: только чистые функции, tmp_path и yaml. В сеть и в БД тесты не ходят.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from chatter.core import drill, guardrails  # noqa: E402
from chatter.onboard import drill_scenario, vocabulary  # noqa: E402
from chatter.payments.drill_gate import DRILL_CONTACTS  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Канон дрил-контактов
#
# Списков ДВА (`scripts/drill_reset.py` — канон, `chatter/payments/drill_gate.py`
# — копия), и спека §4 C7 требует контакт в ОБОИХ. Копию сверяет свой сторож
# (`tests/test_drill_contacts_sync.py`), но опираться здесь только на неё нельзя:
# если списки однажды разъедутся, сгенерированный контакт пройдёт по копии и
# упрётся в канон на стенде — то есть отказ приедет ПОСЛЕ того, как владельца
# уже позвали к телефону.
# ─────────────────────────────────────────────────────────────────────────────

def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _canon_contacts() -> frozenset[str]:
    module = _load_module(_ROOT / "scripts" / "drill_reset.py",
                          "drill_reset_for_onboard_scenario")
    return frozenset(module.DRILL_CONTACTS)


# ─────────────────────────────────────────────────────────────────────────────
# ВЫМЫШЛЕННЫЙ КЛИЕНТ
#
# Форма файлов повторяет живой каталог клиента: сторожа, которые ищут цены,
# разделы и заглушки, покраснели бы на бедности фикстуры, а не на дефекте.
# Числа подобраны так, чтобы `guardrails._context_numbers` признавал их
# ценовыми, и это ПРОВЕРЕНО настоящим guardrail'ом в
# `test_price_list_is_genuinely_a_price_list`, а не заявлено.
# ─────────────────────────────────────────────────────────────────────────────

SLUG = "yarina"
CONTACT = "8849893367:yarina"

OWNER_ID = "Старший майстер"          # именительный: стоит подлежащим
OWNER_REF = "нашим старшим майстром"  # орудный: только после предлога
PERSONA_NAME = "Ярина"
CURRENCY = "грн"
LANGUAGE = "uk"

S = vocabulary.REQUIRED_SECTIONS_UK  # заголовки из словаря, не с рук

# Услуга = (название, ценовая строка R1, признак услуги в тексте).
# Десятичный разделитель — ЗАПЯТАЯ, точек внутри ценовой строки нет: ровно то,
# чего требует R1, иначе `_context_numbers` порежет прайс на фрагменты и
# guardrail зарежет собственные цены клиента.
DEFAULT_SERVICES: tuple[dict, ...] = (
    {"name": "Детейлінг-мийка",
     "price": "- Ціна 1 200–2 000 грн, тривалість 1,5–2,5 години",
     "includes": "- Входить: двофазна мийка кузова, очищення дисків, сушка"},
    {"name": "Полірування кузова",
     "price": "- Ціна 5 000–8 000 грн, тривалість 6–10 годин",
     "includes": "- Входить: підготовка, абразивна робота, захисний склад"},
    {"name": "Керамічне покриття",
     "price": "- Ціна 12 000–18 000 грн, тривалість 2–3 дні",
     "includes": "- Входить: підготовка кузова, нанесення складу, полімеризація"},
)

STUB_LINES = tuple(
    vocabulary.STUB_TEMPLATE_UK.format(title=fact.title_uk, owner=OWNER_ID)
    for fact in vocabulary.REQUIRED_FACTS
)

# Заглушка, которой нет ни в форме, ни в `REQUIRED_FACTS`. Это НЕ край случая:
# у Ярины руками написаны ровно такие — срок службы керамики, марка материалов,
# наличие на складе, — и контрольным вопросом живого демо 14.08 стала именно
# такая заглушка («А скільки років тримається ця кераміка?»).
LOOSE_STUB_TITLE = "Термін служби керамічного покриття"
LOOSE_STUB = f"{LOOSE_STUB_TITLE} — називає {OWNER_ID}"


def knowledge_text(services=DEFAULT_SERVICES) -> str:
    body: dict[str, list[str]] = {
        S[0]: ["Ми — студія детейлінгу у Києві, беремо на догляд авто "
               "будь-якого класу."],
        S[1]: [],
        S[2]: ["- Стан лакофарбового покриття та розмір автомобіля",
               "- Обраний матеріал та обсяг робіт"],
        S[3]: ["- Розрахунок на місці після приймання роботи",
               "- Часткова оплата наперед для довгих робіт"],
        S[4]: ["- Переробляємо безкоштовно, якщо результат не збігається з узгодженим",
               "- Матеріали беремо лише в офіційних постачальників"],
        S[5]: ["- Глибокі подряпини до металу полірування не прибирає",
               "- Вм'ятини та корозію ми не виправляємо"],
        S[6]: ["- Кузовного ремонту та фарбування ми не робимо",
               "- Шиномонтаж та ремонт двигуна не наша спеціалізація"],
        S[7]: ["- Пишіть просто тут, у Telegram: модель авто та бажану послугу",
               "- Підбираємо вільне вікно та підтверджуємо його"],
        S[8]: [f"- {line}" for line in STUB_LINES] + [f"- {LOOSE_STUB}"],
    }
    for number, service in enumerate(services, 1):
        body[S[1]] += [f"## {number}. {service['name']}", "",
                       service["price"], service["includes"], ""]

    out: list[str] = []
    for title, lines in body.items():
        out += [f"# {title}", ""] + lines + [""]
    return "\n".join(out)


def playbook_text() -> str:
    return "\n".join([
        "# Плейбук", "",
        "## Ідеальний клієнт", "",
        "<!-- Внутрішня розмітка: вголос лідові не цитувати. -->",
        "Власник авто, який готовий інвестувати в регулярний догляд", "",
        "## Ключові слова ескалації", "",
        "<!-- Рядки коментаря НЕ починати з «- »: парсер забирає БУДЬ-ЯКИЙ",
        "     рядок-пункт секції як ключове слово. -->",
        "- поклич", "- з менеджером", "- з власником", "",
    ])


PERSONA_TEXT = (
    f"Мене звати {PERSONA_NAME}, мені 26. Я адміністраторка студії детейлінгу — "
    "перший контакт клієнта зі студією.\n\n"
    f"Я не майстер і не власниця: найскладніші питання вирішує {OWNER_ID}.\n"
)

EXAMPLE_PAIRS = [
    {"client": "Скільки коштує полірування кузова?",
     "olga": "Полірування кузова — 5 000–8 000 грн, залежить від стану лаку. "
             "Який у вас автомобіль?"},
    {"client": "А мийка скільки?",
     "olga": "Детейлінг-мийка — 1 200–2 000 грн, тривалість 1,5–2,5 години. "
             "Підкажіть модель авто?"},
]


def settings_dict(*, language: str = LANGUAGE, currency: str = CURRENCY) -> dict:
    return {
        "model": "claude-sonnet-5",
        "language": language,
        "currency": currency,
        "owner_id": OWNER_ID,
        "owner_ref": OWNER_REF,
        "persona_name": PERSONA_NAME,
        "persona_age": 26,
        "strict_knowledge": True,
        "honesty_mode": "honest",
        "forbidden_terms": ["гарантуємо результат"],
        "telegram": {"allowlist": [8849893367], "funnel_gate": False},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Вход генератора: brief.json, результат генерации, документ отчёта
#
# Все три структуры намеренно НЕСУТ ИЗБЫТОК ключей и синонимов. Спека не
# говорит, по какому ключу T6 берёт услугу, цену и контрольный факт, а сторож не
# имеет права угадывать реализацию: угаданный ключ — это то же самое общее
# неверное допущение, ради разрыва которого сторожей и пишет отдельный автор.
# Отрицательные тесты меняют СМЫСЛ (другие услуги, другой раздел 3), а не
# отдельный ключ, — тогда любая стратегия чтения обязана заметить подмену.
# ─────────────────────────────────────────────────────────────────────────────

class RenderResult:
    """Результат T2 в форме, зафиксированной интегратором: `.files`,
    `.defaults`, `.stubs`, `.counters` (см. `report.py`, «читаем ТОЛЬКО по
    именам атрибутов»). Плюс те же файлы на диске: генератор вправе читать их
    как угодно, но НЕ вправе брать цены с потолка."""

    def __init__(self, *, files: dict, settings: dict, out_dir: Path,
                 services=DEFAULT_SERVICES):
        self.files = dict(files)
        self.paths = {name: str(out_dir / name) for name in files}
        self.out_dir = str(out_dir)
        self.dir = str(out_dir)
        self.settings = dict(settings)
        self.language = settings.get("language")
        self.currency = settings.get("currency")
        self.slug = SLUG
        self.knowledge = files["knowledge.md"]
        self.playbook = files["playbook.md"]
        self.persona = files["persona.md"]
        self.examples = EXAMPLE_PAIRS
        self.services = [service["name"] for service in services]
        self.defaults = [
            {"key": "honesty_mode", "value": "honest",
             "why": "бот не приховує, що він бот", "brief_wanted_other": True,
             "brief_quote": "спілкуватись як людина"},
            {"key": "funnel_gate", "value": False,
             "why": "воронка вмикається лише командою власника"},
            {"key": "payments", "value": "off", "why": "реквізитів у брифі немає"},
        ]
        self.stubs = [
            {"fact_id": fact.id, "title": fact.title_uk, "stub_line": line}
            for fact, line in zip(vocabulary.REQUIRED_FACTS, STUB_LINES)
        ] + [{"fact_id": None, "title": LOOSE_STUB_TITLE, "stub_line": LOOSE_STUB}]
        self.section_stubs = []
        self.counters = {"services": len(services), "prices": 6, "deadlines": 6,
                         "stop_words": 3, "forbidden": 1, "example_pairs": 2}


def brief_document(services=DEFAULT_SERVICES) -> dict:
    price_block = "\n".join(
        f"{service['name']}: {service['price'].lstrip('- ')}" for service in services)
    return {
        "schema_version": 1,
        "source": "brief.xlsx",
        "fields": {
            "q22_price_list": {
                "col": 22, "question": "Для КОЖНОЇ послуги: ціна або вилка",
                "raw": price_block, "value": price_block,
                "verdict": "ok", "reason": None, "target": "knowledge"},
            "q34_stop_words": {
                "col": 34, "question": "Коли підключати живу людину",
                "raw": "поклич, з менеджером, з власником",
                "value": "поклич, з менеджером, з власником",
                "verdict": "ok", "reason": None, "target": "playbook"},
            "q35_reply_time": {
                "col": 35, "question": "За який час ви відповідаєте клієнту",
                "raw": "1 1 1", "value": None,
                "verdict": "garbage", "reason": "числовая заглушка", "target": "settings"},
            "q4_links": {
                "col": 4, "question": "Сайт, соцмережі, портфоліо",
                "raw": "drivepro-detailing.example", "value": None,
                "verdict": "garbage", "reason": "плейсхолдер-URL", "target": "knowledge"},
            "q12_hours": {
                "col": 12, "question": "Графік роботи",
                "raw": "9:00-20:00", "value": "9:00-20:00",
                "verdict": "ok", "reason": None, "target": "knowledge"},
        },
    }


def _missing_row(*, field_id, fact_id, title, stub, question,
                 verdict="absent", reason=None, raw=None) -> dict:
    return {
        "field_id": field_id,
        "fact_id": fact_id,
        "title": title,
        "raw": raw,
        "verdict": verdict,
        "reason": reason or f"у формі немає питання про це — «{title}»",
        "stub": stub,
        "question_for_client": question,
    }


def report_document(*, missing=None) -> dict:
    """Документ отчёта T3 (`ReportResult.document`). Раздел 3 — `sections.missing`."""
    if missing is None:
        missing = [
            _missing_row(field_id=f"fact:{fact.id}", fact_id=fact.id,
                         title=fact.title_uk, stub=stub,
                         question=fact.question_for_client_uk)
            for fact, stub in zip(vocabulary.REQUIRED_FACTS, STUB_LINES)
        ] + [
            _missing_row(field_id=None, fact_id=None, title=LOOSE_STUB_TITLE,
                         stub=LOOSE_STUB,
                         question=f"Уточніть, будь ласка: {LOOSE_STUB_TITLE}.",
                         reason=f"заглушка в knowledge «{vocabulary.UNKNOWN_SECTION_UK}», "
                                f"поля в формі немає")
        ]
    return {
        "schema_version": 1,
        "meta": {"slug": SLUG, "source": "brief.xlsx", "flags_checked": True},
        "sections": {
            "taken": [{"field_id": "q22_price_list", "target_file": "knowledge.md",
                       "anchor": S[1], "quote": DEFAULT_SERVICES[0]["price"]}],
            "defaulted": [{"key": "honesty_mode", "value": "honest",
                           "why": "бот не приховує, що він бот",
                           "brief_wanted_other": True,
                           "brief_quote": "спілкуватись як людина"}],
            "missing": missing,
        },
        "flags": [],
        "counters": {"services": len(DEFAULT_SERVICES), "prices": 6, "deadlines": 6,
                     "stop_words": 3, "forbidden": 1, "example_pairs": 2},
    }


def make_render_result(tmp_path: Path, *, services=DEFAULT_SERVICES,
                       language: str = LANGUAGE, currency: str = CURRENCY) -> RenderResult:
    out = tmp_path / "build" / "onboard" / SLUG
    out.mkdir(parents=True, exist_ok=True)
    settings = settings_dict(language=language, currency=currency)
    files = {
        "persona.md": PERSONA_TEXT,
        "knowledge.md": knowledge_text(services),
        "playbook.md": playbook_text(),
        "examples.yaml": yaml.safe_dump(EXAMPLE_PAIRS, allow_unicode=True,
                                        sort_keys=False),
        "settings.yaml": yaml.safe_dump(settings, allow_unicode=True, sort_keys=False),
    }
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    return RenderResult(files=files, settings=settings, out_dir=out, services=services)


def build(tmp_path: Path, *, services=DEFAULT_SERVICES, missing=None,
          language: str = LANGUAGE, currency: str = CURRENCY,
          contact: str = CONTACT, slug: str = SLUG) -> dict:
    return drill_scenario.build_scenario(
        brief_document(services),
        make_render_result(tmp_path, services=services, language=language,
                           currency=currency),
        report_document(missing=missing),
        slug=slug,
        contact=contact,
    )


def parsed(scenario: dict) -> drill.Scenario:
    """Сценарий глазами СТЕНДА: через `render_yaml` и настоящий `parse_scenario`.

    Проверять словарь напрямую нельзя. На стенде живёт не он, а текст YAML, и
    ровно между ними прячется класс дефектов, который не видит ни один тест
    словаря: реплика, потерявшая перевод строки при сериализации; юникод,
    уехавший в `\\u0414`; ключ, съеденный якорем YAML.
    """
    return drill.parse_scenario(drill_scenario.render_yaml(scenario))


@pytest.fixture()
def scenario(tmp_path) -> drill.Scenario:
    return parsed(build(tmp_path))


# ═════════════════════════════════════════════════════════════════════════════
# 0. ФИКСТУРА ДОКАЗАНА ПРОДАКШЕН-КОДОМ
# ═════════════════════════════════════════════════════════════════════════════

def test_price_list_is_genuinely_a_price_list():
    """Ловит: сторож цен, зелёный потому, что в фикстуре цен нет вовсе.

    «Цены в репликах — только из прайса» проверяется через
    `guardrails._context_numbers`, и если бы мой прайс не признавался ценовым
    (точка вместо запятой внутри строки — R1, отсутствие валюты во фрагменте),
    множество разрешённых чисел оказалось бы пустым, а сторож — «зелёным по
    случайности» ровно так же, как это уже случилось на T2.
    """
    price, deadline = guardrails._context_numbers(knowledge_text())
    assert price, ("прайс фикстуры не признан ценовым настоящим guardrail'ом — "
                   "сторож цен ниже проверял бы фикстуру, а не генератор")
    assert {"1200", "2000", "5000", "8000", "12000", "18000"} <= price
    assert deadline, "сроки фикстуры не признаны срочными — R1 в фикстуре сломан"


def test_emoji_only_step_is_no_longer_a_trap_for_the_judge():
    """ЗАПИСЬ ИЗМЕНИЛАСЬ ВМЕСТЕ С ФАКТОМ (DEV-32 закрыт).

    Прежняя редакция утверждала `match_step(...) is None` — и была права: до
    починки судья не опознавал ни одну реплику без букв, и шаг «🔥👍» из живого
    `yarina-onboarding-1-6.yaml` краснел при идеально работающем боте. Теперь у
    `match_step` есть запасной путь по сырому тексту, и утверждение стало
    ложным. Тест не выброшен, а переписан: он сторожит ОБЕ половины границы.

    Половина первая — судья больше не врёт: эмодзи-шаг опознаётся.
    Половина вторая — нормализация ВСЁ ЕЩЁ пуста, и `test_every_step_survives_
    normalization` ниже по-прежнему кусается: заготовке T6 бесбуквенные шаги
    не положены, потому что человек читает план глазами и «🔥👍» в суфлёре
    неотличимо от опечатки.

    Полный набор сторожей на бесбуквенные реплики — в
    `tests/chatter/test_drill_judge_letterless.py`.
    """
    trap = drill.Step(say="🔥👍", expect={"classifier_errors": 0})
    assert drill._normalize_say(trap.say) == ""
    assert drill.match_step(trap.say, (trap,)) == 0, (
        "DEV-32 откатился: судья снова не опознаёт реплику без букв")


# ═════════════════════════════════════════════════════════════════════════════
# 1. СЦЕНАРИЙ ИСПОЛНИМ, А НЕ ПОХОЖ НА СЦЕНАРИЙ
# ═════════════════════════════════════════════════════════════════════════════

def test_scenario_parses_with_the_production_parser(tmp_path):
    """Ловит: YAML, который читается глазами, но не читается стендом.

    Спека §4 C7. Единственный судья формата — `chatter.core.drill.parse_scenario`;
    любая своя проверка формы здесь означала бы второе мнение о том, что такое
    сценарий, а «два числа на одну вещь» гасят друг друга молча. Цена ошибки:
    человек с телефоном у стенда и прогон, начавшийся с исключения.
    """
    scen = parsed(build(tmp_path))
    assert isinstance(scen, drill.Scenario)
    assert scen.steps, "сценарий без шагов parse_scenario не пропустит"


def test_scenario_has_the_five_moves_of_the_blank(tmp_path, scenario):
    """Ловит: заготовку, которая тише плана.

    План T6 называет ровно пять ходов: цена → выбор услуги → готовность
    записаться → контрольный вопрос из раздела 3 → просьба скидки. Четыре шага
    вместо пяти — это молча выпавшая проверка, и заметить её на живом прогоне
    некому: отчёт по четырём шагам выглядит как полный.

    Зафиксировал РОВНО пять, потому что план называет число прямо, а «не меньше
    пяти» пропустило бы шестой шаг, за который владелец платит, не заказав его.
    """
    assert len(scenario.steps) == 5, (
        "заготовка обязана быть из пяти ходов (план T6); получено: "
        + " | ".join(f"«{s.say}»" for s in scenario.steps))


def test_no_expectation_is_vacuous_before_the_run(tmp_path, scenario):
    """Ловит: проверку, зелёную ещё ДО первой реплики.

    Спека §4 C7 требует `vacuous_expectations` пустым. Дрил идёт по ЖИВОМУ
    контакту, и слот обязательств мог быть закрыт прошлым прогоном: прогон 25.07
    имел три зелёных obligations-проверки из четырёх на первом же шаге и
    подтвердил сам себя за деньги.

    Сторож считает вакуумность не только на чистом слоте, но и на ВРАЖДЕБНОМ:
    заготовка пишется вслепую, состояние живого контакта на момент прогона
    генератору неизвестно, значит ожидание, которое МОЖЕТ оказаться выполненным
    заранее, для заготовки незаконно. Зафиксировал так, потому что спека молчит
    о том, против какого `before` считать C7, а оба живых сценария
    (`yarina-demo.yaml`, `yarina-onboarding-1-6.yaml`) obligations-ожиданий не
    ставят вовсе — то есть строгая трактовка совпадает с практикой владельца.
    """
    assert drill.vacuous_expectations(scenario, {}) == []

    hostile: dict = {}
    for step in scenario.steps:
        for okey, status in (step.expect.get("obligations") or {}).items():
            hostile[okey] = sorted(drill._statuses(status))[0]
    assert drill.vacuous_expectations(scenario, hostile) == [], (
        "ожидание obligations может оказаться выполненным ДО прогона; на живом "
        "контакте такая проверка зеленеет, ничего не доказав")


def test_every_step_carries_at_least_one_check(tmp_path, scenario):
    """Ловит: шаг, который не может стать красным.

    Шаг без `expect` разбирается, исполняется и всегда «состоялся» — то есть
    выглядит как проверка, не будучи ею. Пять таких шагов дают зелёный вердикт
    `run_verdict` при любом поведении бота.

    Зафиксировал «хотя бы одна проверка на шаг», потому что спека прямо этого не
    требует, но оба живых сценария владельца держат минимум
    `classifier_errors: 0` на КАЖДОМ шаге, а спека §4 C7 запрещает
    самоподтверждающийся сценарий по смыслу.
    """
    empty = [i for i, s in enumerate(scenario.steps, 1) if not s.expect]
    assert not empty, (
        f"шаги без единой проверки: {empty} — такой шаг зелен всегда")


def test_expect_keys_come_from_the_closed_dictionary(tmp_path, scenario):
    """Ловит: проверку, выдуманную генератором.

    Словарь `drill._CHECKS` закрыт намеренно. Ключ вне него `parse_scenario`
    отвергает, и сценарий разваливается НА СТЕНДЕ, у телефона, а не здесь.
    Тест избыточен по отношению к парсеру осознанно: он называет виновный ключ
    словами, а сообщение парсера придёт человеку в момент, когда чинить поздно.
    """
    known = set(drill._CHECKS)
    for i, step in enumerate(scenario.steps, 1):
        unknown = sorted(set(step.expect) - known)
        assert not unknown, (
            f"шаг {i} «{step.say}»: проверки {unknown} нет в drill._CHECKS "
            f"({sorted(known)})")


# ═════════════════════════════════════════════════════════════════════════════
# 2. 🔴 DEV-32: НИ ОДИН ШАГ НЕ ТЕРЯЕТ БУКВ ПРИ НОРМАЛИЗАЦИИ
# ═════════════════════════════════════════════════════════════════════════════

def test_every_step_survives_normalization(tmp_path, scenario):
    """Ловит: шаг, обречённый краснеть при исправном боте (DEV-32).

    `_normalize_say` оставляет только буквы, цифры и пробелы. У реплики из одних
    эмодзи, знаков препинания или стрелок нормализация ПУСТА, `match_step`
    сразу возвращает `None`, ход не опознаётся, шаг уходит в `skipped` — а
    `run_verdict` считает пропуск ХУЖЕ красного (rc 2, «прогон не состоялся»).
    Итог: платный прогон, обнуляемый шагом, который бот отработал безупречно, и
    следующий человек чинит здорового бота.
    """
    for i, step in enumerate(scenario.steps, 1):
        assert drill._normalize_say(step.say) != "", (
            f"шаг {i} «{step.say}»: после нормализации не осталось ни буквы — "
            f"match_step вернёт None, шаг красный при любом поведении бота")


def test_every_step_is_matched_to_itself(tmp_path, scenario):
    """Ловит: разъезд стенда на шаг — проверки шага N, приписанные ходу N−1.

    Прогон №5 (26.07) разъехался ровно так, и зелёное с красным в отчёте
    перестали относиться к тому, что в нём написано. Здесь каждая реплика
    прогоняется через настоящий `match_step` по полному списку шагов и обязана
    найти ИМЕННО СЕБЯ: две похожие заготовочные реплики («Скільки коштує X?» и
    «Скільки коштує Y?») дают ratio выше порога 0.72 и указывают на соседа.
    """
    steps = scenario.steps
    for i, step in enumerate(steps):
        got = drill.match_step(step.say, steps)
        assert got == i, (
            f"шаг {i + 1} «{step.say}» опознан как "
            + (f"шаг {got + 1} «{steps[got].say}»" if got is not None else "НИ ОДИН")
            + " — судья припишет проверки чужому ходу")


# ═════════════════════════════════════════════════════════════════════════════
# 3. РЕПЛИКА ОДНОСТРОЧНАЯ И ТОЛЬКО ТЕКСТ
# ═════════════════════════════════════════════════════════════════════════════

def test_replies_are_single_line_plain_text(tmp_path, scenario):
    """Ловит: реплику, которую стенд не отправит вовсе.

    `scripts/drill_lead.py`, предохранитель №2: «только текст: ни файлов, ни
    медиа, ни пересылок — нечем». Многострочная реплика уедет в Telegram либо
    склеенной, либо разорванной на два сообщения — и во втором случае бот
    ответит на половину, а `no_duplicate_reply` покажет то, чего не было.

    Слэш в начале зафиксирован как запрет отдельно: `/allow`, `/funnel_gate` —
    команды ВЛАДЕЛЬЦА, они уходят в другой обработчик, и «реплика лида»,
    начинающаяся со слэша, не доедет до диалога вовсе. Спека об этом молчит,
    цена запрета — ноль.
    """
    for i, step in enumerate(scenario.steps, 1):
        say = step.say
        assert say.strip(), f"шаг {i}: пустая реплика"
        assert "\n" not in say and "\r" not in say, (
            f"шаг {i} «{say}»: многострочную реплику стенд не отправит")
        bad = [ch for ch in say if ord(ch) < 32]
        assert not bad, f"шаг {i}: управляющие символы {bad!r} в реплике"
        assert not say.lstrip().startswith("/"), (
            f"шаг {i} «{say}»: реплика лида не может быть командой")


# ═════════════════════════════════════════════════════════════════════════════
# 4. КОНТАКТ — ТОЛЬКО ДРИЛ-КОНТАКТ, И В ОБОИХ СПИСКАХ
# ═════════════════════════════════════════════════════════════════════════════

def test_contact_is_a_drill_contact_in_both_lists(tmp_path, scenario):
    """Ловит: сценарий, нацеленный на живого лида.

    Спека §4 C7. Списка два — канон `scripts/drill_reset.py` и копия
    `chatter/payments/drill_gate.py`, — и контакт обязан быть в обоих: сброс
    состояния идёт по канону, гейт тестовых активов по копии, и контакт,
    известный только одному из них, даёт отказ ПОСЛЕ того, как владельца позвали
    к телефону. Цена промаха мимо списков — тестовый актив (или стирание
    переписки) на живом клиенте.
    """
    assert scenario.contact in DRILL_CONTACTS, (
        f"{scenario.contact!r} нет в chatter/payments/drill_gate.DRILL_CONTACTS")
    assert scenario.contact in _canon_contacts(), (
        f"{scenario.contact!r} нет в каноне scripts/drill_reset.DRILL_CONTACTS")


def test_contact_outside_the_drill_lists_is_refused(tmp_path):
    """Ловит: генератор, который печатает любой поданный контакт.

    Заготовка — файл, который человек запускает не читая («сгенерировано,
    значит проверено»). Если контакт живого клиента доедет до YAML, стенд
    откажет — но лишь на своём краю, а до тех пор ошибка выглядит как готовый
    сценарий.

    Зафиксировал ГРОМКИЙ отказ (DEV-18), потому что спека требует контакт из
    `DRILL_CONTACTS`, а молчаливая подстановка «чего-нибудь своего» — ровно тот
    исход, против которого написан `drill_gate.guard_test_asset`. Тихо
    вернуть сценарий с чужим контактом нельзя ни при каких условиях.
    """
    alien = "237616472:someliveclient"
    assert alien not in DRILL_CONTACTS
    with pytest.raises(Exception) as exc:
        build(tmp_path, contact=alien)
    assert "someliveclient" in str(exc.value) or "DRILL_CONTACT" in str(exc.value).upper(), (
        f"отказ обязан назвать виновный контакт; сказано: {exc.value}")


def test_scenario_names_its_client(tmp_path, scenario):
    """Ловит: сценарий, не знающий, чей он.

    `Scenario.client` — то, по чему стенд понимает, конфиг какого клиента
    поднят под этим прогоном. Пустое поле не роняет `parse_scenario`, поэтому
    ошибка тихая: прогон идёт по контакту `…:yarina`, а отчёт не говорит, кого
    проверяли, и два прогона разных клиентов становятся неразличимы в архиве.
    """
    assert scenario.client == SLUG, (
        f"client={scenario.client!r}, ждали {SLUG!r}")
    assert scenario.name.strip(), "у сценария нет имени — суфлёр напечатает пустую шапку"


# ═════════════════════════════════════════════════════════════════════════════
# 5. КОНТРОЛЬНЫЙ ШАГ ВЗЯТ ИЗ РАЗДЕЛА 3 ОТЧЁТА
# ═════════════════════════════════════════════════════════════════════════════

def _tokens(text: str) -> set[str]:
    return {word for word in drill._normalize_say(text).split() if len(word) >= 5}


def _section3_tokens(doc: dict) -> set[str]:
    out: set[str] = set()
    for row in doc["sections"]["missing"]:
        for key in ("stub", "title", "question_for_client"):
            out |= _tokens(str(row.get(key) or ""))
    return out


def test_control_step_is_drawn_from_report_section_3(tmp_path, scenario):
    """Ловит: контрольный вопрос, выдуманный генератором.

    В этом весь смысл шага: дрил спрашивает бота ровно про то, чего в его базе
    НЕТ. Вопрос про то, что в knowledge ЕСТЬ, проверяет чтение файла, а не
    границу честности, — и «бот не выдумал» перестаёт что-либо значить.
    DEV-28 делает это критичным: guardrail сверяет ЧИСЛА, а не УТВЕРЖДЕНИЯ, и
    фраза «кераміка тримається кілька років» проходит числовой сторож чисто —
    ловить её нечем, кроме промпта и этого шага.
    """
    said = set()
    for step in scenario.steps:
        said |= _tokens(step.say)
    shared = said & _section3_tokens(report_document())
    assert shared, (
        "ни одна реплика не пересекается с разделом 3 отчёта — контрольный шаг "
        "спрашивает бота про то, что у него в базе есть; реплики: "
        + " | ".join(f"«{s.say}»" for s in scenario.steps))


def test_control_step_follows_section_3_when_it_changes(tmp_path):
    """Ловит: связь «раздел 3 → контрольный шаг», которой нет.

    Самый дорогой вид зелёного в этой задаче: контрольный вопрос ПОХОЖ на
    взятый из отчёта (та же ниша, те же слова), а на деле зашит в генератор.
    Пересечение токенов такое подтвердит, поэтому здесь подменяется САМ раздел 3
    — и заготовка обязана поехать за ним. Если контрольный шаг не изменился,
    следующий клиент получит дрил, проверяющий чужую дыру.
    """
    other_title = "Наявність матеріалів на складі"
    other_stub = f"{other_title} — називає {OWNER_ID}"
    swapped = [
        _missing_row(field_id=None, fact_id=None, title=other_title,
                     stub=other_stub,
                     question=f"Уточніть, будь ласка: {other_title}.")
    ]

    base = parsed(build(tmp_path))
    moved = parsed(build(tmp_path, missing=swapped))

    base_says = [s.say for s in base.steps]
    moved_says = [s.say for s in moved.steps]
    assert base_says != moved_says, (
        "раздел 3 отчёта заменён целиком, а реплики те же — контрольный вопрос "
        "зашит в генератор, а не взят из отчёта")

    moved_tokens: set[str] = set()
    for step in moved.steps:
        moved_tokens |= _tokens(step.say)
    assert moved_tokens & _tokens(other_title), (
        "новый раздел 3 в репликах не отразился: "
        + " | ".join(f"«{s}»" for s in moved_says))
    # Из старого факта берутся только те слова, которые НЕ называют услугу.
    # «Керамічне покриття» — это и старая заглушка, и услуга прайса; требовать,
    # чтобы услуга исчезла из реплик, значило бы краснеть на законном шаге
    # выбора услуги. Отсечение по шестибуквенной основе, потому что склонения
    # («керамічне» / «керамічного») — то же слово, а склонять программно мы не
    # умеем и не будем (спека §5). Остаток («термін», «служби») мог приехать
    # только из прежнего раздела 3 — вот его в новом сценарии быть не должно.
    service_stems = {token[:6] for service in DEFAULT_SERVICES
                     for token in _tokens(service["name"])}
    only_from_old_report = {
        token for token in _tokens(LOOSE_STUB_TITLE) - _tokens(other_title)
        if token[:6] not in service_stems
    }
    assert only_from_old_report, "мутация теста выродилась: сравнивать нечем"
    assert not (moved_tokens & only_from_old_report), (
        "в репликах остался контрольный факт СТАРОГО отчёта — дрил проверяет "
        "дыру предыдущего клиента")


# ═════════════════════════════════════════════════════════════════════════════
# 6. ЦЕНЫ — ТОЛЬКО ИЗ ПРАЙСА КЛИЕНТА
# ═════════════════════════════════════════════════════════════════════════════

def _price_numbers(text: str) -> set[str]:
    """Числа, стоящие в ЦЕНОВОМ контексте, глазами продакшен-guardrail'а."""
    return guardrails._context_numbers(text)[0]


def test_price_guard_bites():
    """Ловит: мой собственный сторож цен, если он ничего не проверяет.

    Показывает, что `_price_numbers` действительно видит выдуманную цену в
    реплике. Без этого мета-теста «цены чисты» означало бы лишь «мой хелпер
    вернул пустое множество» — ровно тот способ зеленеть, ничего не проверив.
    """
    invented = _price_numbers("А за 7 777 грн зробите?") - _price_numbers(knowledge_text())
    assert invented == {"7777"}


def test_prices_in_replies_come_only_from_the_client_price_list(tmp_path, scenario):
    """Ловит: реплику лида с ценой, которой у клиента нет.

    Реплика «а за 7 000 грн зробите?» при прайсе 5 000–8 000 выглядит невинно, но
    она ЗАДАЁТ боту число, которого клиент не называл, — и контрольный шаг после
    неё теряет смысл: дальше непонятно, бот выдумал цифру или повторил за лидом.
    Сравнение идёт тем же `guardrails._context_numbers`, которым C2 судит
    обеспеченность прайса, — чтобы у сторожа и у приёмки было ОДНО понятие
    «ценовое число», а не два.

    Числа вне ценового контекста (год выпуска авто в «Toyota Camry 2019» —
    живой `yarina-demo.yaml`) законны и здесь не трогаются.
    """
    allowed = _price_numbers(knowledge_text())
    for i, step in enumerate(scenario.steps, 1):
        invented = _price_numbers(step.say) - allowed
        assert not invented, (
            f"шаг {i} «{step.say}»: цен {sorted(invented)} в прайсе клиента нет")


# ═════════════════════════════════════════════════════════════════════════════
# 7. ЯЗЫК И ДАННЫЕ КЛИЕНТА, А НЕ ХАРДКОД
# ═════════════════════════════════════════════════════════════════════════════

_RU_ONLY_LETTERS = set("ыэъё")
_UK_ONLY_LETTERS = set("іїєґ")


def test_replies_are_in_the_client_language(tmp_path, scenario):
    """Ловит: заготовку, написанную на языке разработчика.

    Панель Джарвиса по-русски, клиентская сторона по-украински — и лид,
    пишущий боту по-русски там, где вся база украинская, проверяет реакцию на
    смену языка вместо того, ради чего шаг стоит в сценарии. Мультиязычности у
    нас нет; язык объявлен в `settings.yaml` клиента.

    Проверка держится за буквы, которых нет во втором языке: `ы/э/ъ/ё` —
    русские, `і/ї/є/ґ` — украинские. Это грубо, зато не зависит от словаря и не
    краснеет на общих словах. Зафиксировал так, потому что спека требует «язык
    клиента» без указания, чем это измерять.
    """
    joined = " ".join(s.say for s in scenario.steps).lower()
    ru = _RU_ONLY_LETTERS & set(joined)
    assert not ru, (
        f"в репликах русские буквы {sorted(ru)} при language=uk: "
        + " | ".join(f"«{s.say}»" for s in scenario.steps))
    assert _UK_ONLY_LETTERS & set(joined), (
        "ни одной украинской буквы в пяти репликах — язык клиента взят не из "
        "settings.yaml")


def test_replies_follow_the_client_services(tmp_path):
    """Ловит: заготовку, одинаковую для всех клиентов.

    Шаги «цена» и «выбор услуги» обязаны называть услугу ЭТОГО клиента: дрил
    про полірування у стоматологии проверит только то, что бот умеет отказывать.
    Здесь прайс подменяется целиком (другая ниша, другие услуги, другие числа), и
    реплики обязаны поехать за ним. Совпадение реплик = услуги зашиты в код.
    """
    other = (
        {"name": "Професійна чистка зубів",
         "price": "- Ціна 1 400–2 600 грн, тривалість 1–2 години",
         "includes": "- Входить: ультразвук, Air Flow, поліровка"},
        {"name": "Лікування карієсу",
         "price": "- Ціна 2 800–4 900 грн, тривалість 1–2 години",
         "includes": "- Входить: анестезія, обробка каналу, пломба"},
    )
    base = [s.say for s in parsed(build(tmp_path)).steps]
    moved = parsed(build(tmp_path, services=other))

    assert [s.say for s in moved.steps] != base, (
        "прайс и услуги подменены целиком, а реплики те же — заготовка не "
        "читает данные клиента")

    said = set()
    for step in moved.steps:
        said |= _tokens(step.say)
    assert said & (_tokens(other[0]["name"]) | _tokens(other[1]["name"])), (
        "ни одна услуга нового клиента в репликах не названа: "
        + " | ".join(f"«{s.say}»" for s in moved.steps))

    allowed = _price_numbers(knowledge_text(other))
    for step in moved.steps:
        assert not (_price_numbers(step.say) - allowed), (
            f"«{step.say}»: цена от ПРЕЖНЕГО клиента доехала до нового сценария")


# ═════════════════════════════════════════════════════════════════════════════
# 8. YAML, КОТОРЫЙ ЧИТАЮТ ГЛАЗАМИ И ДВАЖДЫ
# ═════════════════════════════════════════════════════════════════════════════

def test_render_yaml_is_readable_by_the_owner(tmp_path):
    """Ловит: сценарий, который человек не прочитает перед прогоном.

    Заготовку правит человек (решение q1: «генерируем заготовку, человек
    правит»), а весь сценарий печатается суфлёром ДО старта — прогон 25.07 висел
    в фоне, и реплики пришлось диктовать по одной. Кириллица, уехавшая в
    `\\u0414\\u043e\\u0431...`, формально валидна и полностью нечитаема: править
    и сверять такое владелец не станет.
    """
    text = drill_scenario.render_yaml(build(tmp_path))
    assert isinstance(text, str) and text.strip()
    assert "\\u04" not in text, "юникод сериализован escape-последовательностями"
    first = parsed(build(tmp_path)).steps[0].say
    assert first in text, (
        "первой реплики нет в YAML дословно — владелец не найдёт, что править")


def test_scenario_is_reproducible(tmp_path):
    """Ловит: заготовку, меняющуюся от прогона к прогону.

    Сценарий-файл существует ровно затем, чтобы два прогона были сравнимы
    (`drill.py`: «два прогона несравнимы, регресс-прогон невозможен»). Случайный
    выбор услуги или контрольного факта убивает регресс молча: сравнить вчерашний
    отчёт с сегодняшним больше нечем.
    """
    first = drill_scenario.render_yaml(build(tmp_path))
    second = drill_scenario.render_yaml(build(tmp_path))
    assert first == second, "два вызова на одних данных дали разные сценарии"


def test_yaml_round_trips_without_losing_a_step(tmp_path, scenario):
    """Ловит: шаг, потерянный на сериализации.

    Словарь и YAML — две формы одного сценария, и расходятся они тихо: ключ,
    съеденный якорем, дубль `say`, свёрнутый YAML в один узел. На стенде живёт
    YAML, поэтому сверяется он, а не словарь.
    """
    text = drill_scenario.render_yaml(build(tmp_path))
    raw = yaml.safe_load(text)
    assert isinstance(raw, dict) and isinstance(raw.get("steps"), list)
    assert len(raw["steps"]) == len(scenario.steps)
    assert [str(s["say"]).strip() for s in raw["steps"]] == [s.say for s in scenario.steps]
