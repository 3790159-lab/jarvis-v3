# -*- coding: utf-8 -*-
"""T2 арки `chatter.onboard`: brief.json → пять файлов клиента (спека §2, R1–R9).

Сторожа написаны ОТ СПЕКИ и ОТ ЭТАЛОНА `chatter/clients/yarina`. Реализация
(`chatter/onboard/render.py`) при написании НЕ читалась ни строкой — иначе тест
и код наследуют одно допущение и оба зеленеют на неверном поведении. Ровно этот
класс лжи уже ловил мутационный гейт этого проекта.

Что защищается и чего стоит ошибка:

  1. **R1 — самое дорогое место арки.** `guardrails._context_numbers` режет
     knowledge по `[\\n.!?;]`. Точка ВНУТРИ ценовой строки рвёт фрагмент, цена
     теряет валюту, число не попадает в обеспеченное множество — и guardrail
     режет СОБСТВЕННЫЙ прайс клиента. Снаружи это выглядит как «бот молчит на
     вопрос о цене», а причина — знак препинания. Поэтому сильнейшие сторожа
     здесь сквозные: прогоняют сгенерированное через НАСТОЯЩИЕ
     `_context_numbers` и `_findings`, то есть проверяют ПОСЛЕДСТВИЕ, а не форму.
  2. **R2.** Число, которого нет в knowledge, произнесённое ботом, — это
     выдуманная цена. Ловить его обязана ГЕНЕРАЦИЯ (`RenderError`), а не
     приёмка: до приёмки доживает не всё.
  3. **R3.** Первая строка `persona.md` уходит лиду дословно на «ты бот?».
     Заголовок `#` = решётка в чате живого клиента.
  4. **R4.** Заголовок не из `escalation._KEYWORD_HEADINGS` = детерминированный
     слой МОЛЧА выключен. Это единственный слой, работающий при мёртвом
     классификаторе, и его отказ ничем не пахнет.
  5. **R5.** Ключи `{client, olga}` — хардкод лоадера; переименование роняет
     конфиг на СТАРТЕ, то есть у живого клиента. Бюджет примеров считается
     против настоящей `brain.EXAMPLES_CHAR_BUDGET`: сверх бюджета пары
     отбрасываются, и голос персоны тихо едет.
  6. **R6.** Ложное срабатывание brand-safety = потерянный ответ лиду. Корень
     «гарант» убил бы легальный раздел «Гарантії та якість роботи».
  7. **R7/R8.** Заглушка без парной записи (или наоборот) — потеря половины
     улики: C10 потом не с чем сверять.
  8. **R9.** Числовой guardrail здесь бессилен ПО КОНСТРУКЦИИ — числа нет.
     Единственная защита — правило генератора.
  9. **Дефолты.** Уступить брифу в `honesty_mode` значит выпустить бота,
     который выдаёт себя за человека.

Настоящий бриф клиента (имя, контакты, внутренние цены живого человека) здесь
НЕ читается: все фикстуры собраны ниже руками и описывают выдуманную студию.
"""
from __future__ import annotations

import re

import pytest
import yaml

from chatter.core.brain import EXAMPLES_CHAR_BUDGET
from chatter.core.brand_safety import forbidden_mention
from chatter.core.escalation import _KEYWORD_HEADINGS, parse_escalation_keywords
from chatter.core.guardrails import _context_numbers, _findings
from chatter.onboard import render, vocabulary

SLUG = "krystal"

NBSP = " "
EN_DASH = "–"   # – тире диапазона, как в эталоне
APO_ASC = "'"   # '
APO_RSQ = "’"   # ’

FIVE_FILES = {"persona.md", "knowledge.md", "playbook.md",
              "examples.yaml", "settings.yaml"}


# ══ ФИКСТУРА БРИФА ═════════════════════════════════════════════════════════
#
# Форма — контракт T1 (`form_schema.yaml`, 57 колонок, v1). Значения выдуманы.
# Прайс намеренно ЧИСТЫЙ: тире `–`, десятичная запятая, без точек — так
# сторожа R1 проверяют, что генератор не ЛОМАЕТ уже правильное; отдельные
# фикстуры ниже подают грязь и проверяют, что он её ЧИНИТ.

#
# ⚠️ ФОРМА ПРАЙСА В СПЕКЕ НЕ ЗАФИКСИРОВАНА. Спека и план описывают, ЧТО
# генератор обязан сделать с ценами (R1/R2), но ни строкой — как клиент их
# написал. Я читал требование как «строка на услугу»; реализация требует
# нумерованный заголовок услуги («1. Назва послуги»). Форма входа — не
# правило спеки, поэтому фикстура подана в том виде, который реализация
# ОБЪЯВЛЯЕТ СВОИМ СООБЩЕНИЕМ ОБ ОШИБКЕ; утверждения сторожей не тронуты.
# Расхождение вынесено интегратору: пока форма не описана в схеме, следующий
# клиент напишет прайс иначе и пайплайн встанет на rc «не состоялось».
PRICE_BLOCK = (
    f"1. Детейлінг-мийка\n"
    f"Ціна 1 200{EN_DASH}2 000 грн, тривалість 1,5{EN_DASH}2,5 години\n"
    f"2. Комплексна хімчистка салону\n"
    f"Ціна 4 500{EN_DASH}7 500 грн, тривалість 6{EN_DASH}10 годин\n"
    f"3. Керамічне покриття кузова\n"
    f"Ціна 8 000{EN_DASH}15 000 грн, тривалість 1 день"
)

# R9: вторая строка — единица времени БЕЗ числа. Лид получил обещание и не
# знает, у кого спросить точное.
DEADLINES = (
    f"Детейлінг-мийка — 1,5{EN_DASH}2,5 години\n"
    f"Комплексна хімчистка — 6{EN_DASH}10 годин\n"
    "Керамічне покриття — 1 день\n"
    "Після кераміки потрібен рекомендований час на первинну полімеризацію"
)

EXAMPLE_REPLIES = (
    "Клієнт: Скільки коштує комплексна хімчистка?\n"
    f"Відповідь: Комплексна хімчистка салону — 4 500{EN_DASH}7 500 грн, "
    f"тривалість 6{EN_DASH}10 годин. Надішліть фото салону, підкажу точніше.\n"
    "\n"
    "Клієнт: Скільки коштує мийка?\n"
    f"Відповідь: Детейлінг-мийка — 1 200{EN_DASH}2 000 грн, тривалість "
    f"1,5{EN_DASH}2,5 години. Підкажіть марку і модель авто."
)

TOP_QUESTIONS = (
    "Клієнт: Що дає керамічне покриття?\n"
    f"Відповідь: Керамічне покриття кузова — 8 000{EN_DASH}15 000 грн, тривалість "
    "1 день. Точний час первинної полімеризації називає старший майстер."
)

# ICP: фрагмент длиннее 40 символов, узнаваемый дословно (§1.3, сторож C9).
ICP_FRAGMENT = "готовий інвестувати в догляд і цінує якість роботи"
ICP = f"Власник преміум-авто, який {ICP_FRAGMENT}"

# Q38: ячейка ДВОЙНОГО назначения. Первое предложение публичное, второе —
# внутренний критерий отказа. Автоматом мы их не разводим (решение владельца).
ANTI_ICP_PUBLIC = "кузовний ремонт, фарбування та тонування скла"
ANTI_ICP_PRIVATE = "не беремо клієнтів, які вимагають гарантувати недосяжний результат"
ANTI_ICP = f"Не беремо в роботу {ANTI_ICP_PUBLIC}. Також {ANTI_ICP_PRIVATE}."

# Списки клиента даны ПОСТРОЧНО. Форма списочного ответа в спеке тоже не
# зафиксирована; замер показал, что построчный список парсер разбирает, а
# перечисление через запятую съедается целиком (5 стоп-слов → 1). Это второй
# случай того же класса, что и форма прайса: контракт входа не описан нигде.
FORBIDDEN_PHRASES = (
    "гарантія результату\n"
    "буде як нова\n"
    "ми №1\n"
    f"обов{APO_ASC}язково потрібно"
)

STOP_WORDS = "менеджер\nвласник\nскарга\nповерніть гроші\nсуд"

GARBAGE_NEEDLE = "asdfqwer"


class Garbage:
    """Ответ, который мусор-детектор T1 понизил до «ответа нет».

    `raw` живёт в brief.json всегда (улика не удаляется), `value` — `None`.
    Смысл для генератора ровно один: такое поле в конфиг не попадает НИ ПРИ
    КАКИХ условиях (§1.2).
    """

    def __init__(self, raw: str, reason: str = "тестовое заполнение"):
        self.raw = raw
        self.reason = reason


# (col, id, target, значение)
_FIELDS: tuple[tuple[int, str, str, object], ...] = (
    (0, "q0_timestamp", "report_only", "17.08.2026 10:00:00"),
    (1, "q1_company_name", "knowledge", "Кристал Детейлінг"),
    (2, "q2_industry", "knowledge", "Детейлінг та догляд за автомобілями"),
    (3, "q3_offer", "knowledge", "Комплексний догляд за кузовом і салоном авто"),
    (4, "q4_links", "knowledge", "https://krystal-detailing.com.ua"),
    (5, "q5_owner_contact", "report_only", "@krystal_owner"),
    (6, "q6_timezone_city", "knowledge", "Львів"),
    (7, "q7_channels", "settings", "Telegram"),
    (8, "q8_accounts", "settings", "@krystal_detailing"),
    (9, "q9_dialog_volume", "report_only", f"20{EN_DASH}50 діалогів на тиждень"),
    (10, "q10_current_responder", "report_only", Garbage("1 1 1", "числовая заглушка")),
    (11, "q11_night_and_weekend", "settings", "Так, цілодобово"),
    (12, "q12_hours", "knowledge", "з 9:00 до 18:00"),
    (13, "q13_speaks_as", "playbook", "Адміністраторка студії"),
    (14, "q14_persona_name", "settings", "Оксана"),
    (15, "q15_disclose_ai", "settings",
     "Ні, хай спілкується як людина і не каже, що це бот"),
    (16, "q16_owner_ref", "settings", "нашим старшим майстром"),
    (17, "q17_language", "settings", "Українська"),
    (18, "q18_tone", "playbook", "Тепло і по-людськи, звертання на «ви»"),
    (19, "q19_emoji", "playbook", "Помірно, не більше одного на повідомлення"),
    (20, "q20_real_replies", "examples", EXAMPLE_REPLIES),
    (21, "q21_services", "knowledge",
     "Детейлінг-мийка, комплексна хімчистка салону, керамічне покриття кузова"),
    (22, "q22_price_list", "knowledge", PRICE_BLOCK),
    (23, "q23_included_excluded", "knowledge",
     "У мийку входить очищення кузова, дисків та скла, "
     "не входить хімчистка салону та полірування"),
    (24, "q24_price_factors", "knowledge",
     "марка та модель авто, стан кузова, ступінь забруднення салону, обсяг робіт"),
    (25, "q25_deadlines", "knowledge", DEADLINES),
    (26, "q26_can_quote_price", "playbook", "Так, може називати вилку"),
    (27, "q27_discounts", "playbook", "Ні, знижки лише через старшого майстра"),
    (28, "q28_promo", "knowledge", None),
    (29, "q29_prepayment", "knowledge",
     "Передоплата залежить від обсягу робіт, конкретну суму рахує старший майстер"),
    (30, "q30_payment_methods", "knowledge", "Картка, переказ у гривні, готівка"),
    (31, "q31_can_send_payment_details", "playbook",
     "Ні, реквізити надсилаю тільки я"),
    (32, "q32_autonomous_actions", "playbook",
     "Консультувати і збирати дані для запису"),
    (33, "q33_must_escalate", "playbook", "Торг, точний прорахунок, конфлікти"),
    (34, "q34_stop_words", "playbook", STOP_WORDS),
    (35, "q35_reply_time", "knowledge", "Протягом години"),
    (36, "q36_notify_channel", "settings", "Telegram, цей самий чат"),
    (37, "q37_icp", "playbook", ICP),
    (38, "q38_anti_icp", "playbook", ANTI_ICP),
    (39, "q39_required_questions", "playbook", "Марка, модель, рік авто та фото"),
    (40, "q40_info_for_exact_price", "playbook", "Фото кузова при денному світлі"),
    (41, "q41_next_step", "playbook", "Записати на огляд"),
    (42, "q42_objection_expensive", "examples",
     "Порівняймо однаковий обсяг робіт, а не тільки кінцеву цифру"),
    (43, "q43_objection_think", "examples",
     "Коротко зафіксую варіанти і відповім на сумніви"),
    (44, "q44_objection_competitors", "examples",
     "Конкурентів не критикуємо, пояснюємо різницю в обсязі робіт"),
    (45, "q45_guarantees", "knowledge",
     "Перед роботою узгоджуємо реальний результат, "
     "після робіт оглядаємо авто разом із клієнтом"),
    (46, "q46_top_questions", "examples", TOP_QUESTIONS),
    (47, "q47_advantages", "knowledge",
     "Пояснюємо, що реально дасть процедура, і не продаємо дорожче, ніж потрібно"),
    (48, "q48_never_promise", "playbook",
     "Не обіцяти строк служби покриття і точну дату видачі авто"),
    (49, "q49_taboo_topics", "playbook", "Політика, релігія, внутрішні фінанси"),
    (50, "q50_forbidden_phrases", "settings", FORBIDDEN_PHRASES),
    (51, "q51_can_discuss_competitors", "playbook", "Коротко і нейтрально"),
    (52, "q52_access_and_2fa", "report_only", Garbage(f"{GARBAGE_NEEDLE} тест")),
    (53, "q53_other_responders", "report_only", "Ні"),
    (54, "q54_launch_date", "report_only", Garbage("1.0", "числовая заглушка")),
    (55, "q55_success_metrics", "report_only", Garbage("1.0", "числовая заглушка")),
    (56, "q56_anything_else", "playbook", None),
)


def make_brief(**overrides) -> dict:
    """`brief.json` в форме контракта плана (раздел «Контракт данных»).

    Генератор читает ТОЛЬКО этот артефакт — поэтому фикстура собирается по
    контракту, а не по удобству теста.
    """
    fields = {}
    for col, fid, target, value in _FIELDS:
        if fid in overrides:
            value = overrides.pop(fid)
        if isinstance(value, Garbage):
            fields[fid] = {
                "col": col, "question": f"<питання {fid}>", "raw": value.raw,
                "value": None, "verdict": "garbage", "reason": value.reason,
                "target": target,
            }
        else:
            fields[fid] = {
                "col": col, "question": f"<питання {fid}>",
                "raw": value or "", "value": value, "verdict": "ok",
                "reason": None, "target": target,
            }
    assert not overrides, f"неизвестные поля фикстуры: {sorted(overrides)}"
    return {"schema_version": 1, "source": "fixture.xlsx", "fields": fields}


# ══ ХЕЛПЕРЫ ════════════════════════════════════════════════════════════════

def files_of(result) -> dict:
    """`.files` с терпимостью к имени ключа, но без терпимости к составу.

    Точный состав ключей сторожит ОТДЕЛЬНЫЙ тест ниже. Здесь терпимость нужна
    ровно затем, чтобы спор об имени ключа не глушил тридцать сторожей
    ПОВЕДЕНИЯ — иначе одна расходящаяся строка спрячет все настоящие находки.
    """
    files = dict(result.files)
    for name in FIVE_FILES:
        stem = name.rsplit(".", 1)[0]
        if name not in files and stem in files:
            files[name] = files[stem]
    return files


def section_body(md: str, title: str) -> str | None:
    """Тело раздела markdown до следующего заголовка ТОГО ЖЕ или высшего уровня.

    Подразделы (`## 1. Детейлінг-мийка`) остаются ВНУТРИ тела: иначе «раздел
    пуст» сказали бы про раздел, у которого всё содержимое в подпунктах.
    """
    capturing, level, body = False, 0, []
    for line in (md or "").splitlines():
        s = line.strip()
        if s.startswith("#"):
            lv = len(s) - len(s.lstrip("#"))
            head = s.lstrip("#").strip()
            if capturing:
                if lv <= level:
                    break
            elif head.casefold() == title.casefold():
                capturing, level = True, lv
                continue
        if capturing:
            body.append(line)
    return "\n".join(body) if capturing else None


def meaningful_lines(body: str) -> list[str]:
    """Содержательные строки: не пустые, не заголовки, не HTML-комментарий."""
    out = []
    in_comment = False
    for line in (body or "").splitlines():
        s = line.strip()
        if in_comment:
            if "-->" in s:
                in_comment = False
            continue
        if s.startswith("<!--"):
            in_comment = "-->" not in s
            continue
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def price_lines(knowledge: str) -> list[tuple[int, str]]:
    """Пункты списка с валютой и числом — те самые строки, на которых стоит R1.

    Ограничение «пункт списка» снято с ЭТАЛОНА, а не придумано: там 21 такой
    пункт, и все 21 проходят `_findings` начисто. Без ограничения в выборку
    попадает и обычная проза («…у подарунок.»), где точка законна, — сторож
    краснел бы на файлах, собранных руками и отработавших живой дрил.
    """
    return [(i, ln.strip()) for i, ln in enumerate(knowledge.splitlines(), 1)
            if ln.strip().startswith("-") and "грн" in ln and re.search(r"\d", ln)]


def escalation_section(playbook: str) -> tuple[str, str]:
    """(заголовок, тело) секции ключевых слов эскалации."""
    for heading in _KEYWORD_HEADINGS:
        for line in playbook.splitlines():
            s = line.strip()
            if s.startswith("#") and s.lstrip("#").strip().casefold() == heading:
                title = s.lstrip("#").strip()
                return title, section_body(playbook, title) or ""
    return "", ""


def numbers_in(text: str) -> set[str]:
    """Числа как их видит `guardrails._numbers` (пробелы сняты)."""
    return {re.sub(r"\s", "", m.group())
            for m in re.finditer(r"\d[\d\s.,]*\d|\d", text or "")}


def defaults_for(result, needle: str) -> list[dict]:
    return [d for d in result.defaults if needle in str(d.get("key", ""))]


@pytest.fixture(scope="module")
def base():
    """Один валидный прогон на весь модуль: генерация обязана быть чистой
    функцией брифа, и повторять её тридцать раз незачем."""
    return render.render_all(make_brief(), slug=SLUG)


@pytest.fixture(scope="module")
def f(base):
    return files_of(base)


# ══ R1. ЦЕНОВАЯ СТРОКА: ТОЧКА РВЁТ ФРАГМЕНТ ════════════════════════════════

def test_sanitizer_removes_sentence_punctuation_from_price_line():
    """Класс ошибки: `. ! ? ;` внутри ценовой строки.

    `_context_numbers` режет knowledge ровно по `[\\n.!?;]`. Осколок после
    точки теряет валюту, его числа не попадают в ценовое множество, и
    guardrail подавляет ответ бота с СОБСТВЕННОЙ ценой клиента. Внешне это
    «бот молчит на вопрос про цену», и причину знака препинания никто не ищет.
    """
    dirty = "Ціна 5 000 грн. Тривалість 2 години; можливо швидше! Точно?"
    clean = render.sanitize_price_line(dirty)
    for bad in ".!?;":
        assert bad not in clean, f"{bad!r} остался в {clean!r}"
    assert len(re.split(r"[\n.!?;]", clean)) == 1, "строка всё ещё делится на фрагменты"


def test_sanitizer_keeps_the_decimal_separator_a_comma():
    """Класс ошибки: «1.5 години» вместо «1,5 години».

    `_numbers` снимает только ПРОБЕЛЫ, поэтому «1,5» и «1.5» — РАЗНЫЕ токены.
    Точка здесь бьёт дважды: и режет фрагмент, и даёт число, которого нет в
    обеспеченном множестве. Ответ с ним будет подавлен.
    """
    clean = render.sanitize_price_line("Ціна 5 000 грн, тривалість 1.5 години")
    assert "1,5" in clean, clean
    assert "1.5" not in clean, clean


def test_sanitizer_normalizes_nbsp_and_apostrophe():
    """Класс ошибки: из Google Forms NBSP приезжает регулярно.

    NBSP — другой символ, а значит другой токен на всех слоях, которые матчат
    подстрокой (brand-safety, эскалация). Дефект невидим глазом: в файле он
    выглядит как обычный пробел.
    """
    clean = render.sanitize_price_line(f"Ціна 5{NBSP}000 грн, тривалість 2 години")
    assert NBSP not in clean, "NBSP уцелел"
    assert "5000" in numbers_in(clean), f"число потерялось: {clean!r}"


def test_sanitizer_uses_en_dash_for_ranges():
    """Класс ошибки: три разных тире в одном файле.

    Эталон держит `–` (U+2013). Дефис и эм-тире дают другой токен в любых
    сравнениях по подстроке и расходятся с `--diff` приёмки арки (§6).
    """
    clean = render.sanitize_price_line("Ціна 5 000-8 000 грн, тривалість 6—10 годин")
    assert "-" not in clean and "—" not in clean, clean
    assert clean.count(EN_DASH) == 2, clean


def test_sanitizer_never_loses_a_digit_and_is_idempotent():
    """Класс ошибки: санитайзер «починил» строку, съев цену.

    Молча потерянная цифра хуже точки: точка ловится сторожем, а исчезнувшая
    вилка выглядит как решение человека. Повторный прогон обязан быть
    неподвижной точкой — иначе прогон 2 даёт файл, отличный от прогона 1.
    """
    src = f"Ціна 1 200{EN_DASH}2 000 грн, тривалість 1,5 години"
    once = render.sanitize_price_line(src)
    assert re.sub(r"\D", "", once) == re.sub(r"\D", "", src), once
    assert render.sanitize_price_line(once) == once, "санитайзер не идемпотентен"


def test_allowed_numbers_normalizes_exactly_like_guardrails():
    """Класс ошибки: множество допустимых чисел собрано «своей» нормализацией.

    Сверка идёт с `guardrails._numbers`, который снимает ПРОБЕЛЫ и не трогает
    запятую. Любая другая нормализация даёт множество, которое сравнивать не с
    чем: «1 200» никогда не совпадёт с «1200» из рантайма.
    """
    got = render.allowed_numbers(
        f"Мийка — ціна 1 200{EN_DASH}2 000 грн, тривалість 1,5 години")
    assert got == {"1200", "2000", "1,5"}, got


def test_generated_price_lines_carry_no_sentence_punctuation(f):
    """Класс ошибки: правило R1 применено к санитайзеру, но не к выходу.

    Санитайзер, который никто не позвал, — это зелёный юнит-тест и красный
    прод. Проверяем ФАЙЛ, а не функцию.
    """
    lines = price_lines(f["knowledge.md"])
    assert len(lines) >= 3, f"ценовых строк в knowledge {len(lines)} — сторож пуст"
    bad = [(n, ln) for n, ln in lines if re.search(r"[.!?;]", ln)]
    assert not bad, f"ценовые строки с разрывающей пунктуацией: {bad}"


def test_one_decimal_separator_per_file(f):
    """Класс ошибки: «1,5» в одном разделе и «1.5» в другом.

    Два разных десятичных разделителя в одном файле — это «два числа на одну
    вещь»: половина чисел обеспечена, половина нет, и красное придёт не от
    того раздела, где ошибка.

    Даты (`30.09.2026`) маскируются: в эталоне точка живёт именно в дате акции,
    и она не десятичный разделитель. Сторож, красный на ручном эталоне, —
    не сторож, а фон.
    """
    for name in ("knowledge.md", "examples.yaml", "playbook.md"):
        masked = re.sub(r"\b\d{1,2}\.\d{1,2}(?:\.\d{2,4})?\b", " ", f[name])
        seps = {m.group(1) for m in re.finditer(r"\d([.,])\d", masked)}
        assert seps <= {","}, f"{name}: разделители {seps}"


def test_no_nbsp_in_any_generated_file(f):
    """Класс ошибки: невидимый символ доезжает до конфига клиента.

    NBSP не виден при вычитке и ломает матч подстрокой в brand-safety и
    эскалации. Сторож обязан быть на выходе — глазами это не находится.
    """
    for name, text in f.items():
        assert NBSP not in text, f"{name}: NBSP на позиции {text.find(NBSP)}"


def test_every_price_number_is_backed_by_real_context_numbers(base, f):
    """🔴 СКВОЗНОЙ. Класс ошибки: цена клиента не признаётся собственной базой.

    Проверяем ПОСЛЕДСТВИЕ, а не форму: гоняем сгенерированный knowledge через
    НАСТОЯЩИЙ `guardrails._context_numbers` и требуем, чтобы каждое число
    прайса попало в обеспеченное множество. Форматных правил можно придумать
    много; значение имеет ровно одно — увидел ли рантайм эти числа.
    """
    allowed = render.allowed_numbers(PRICE_BLOCK)
    assert len(allowed) >= 8, f"множество прайса подозрительно мало: {allowed}"
    price, deadline = _context_numbers(f["knowledge.md"])
    missing = allowed - (price | deadline)
    assert not missing, (
        f"числа прайса не обеспечены knowledge: {sorted(missing)}; "
        f"ценовых {len(price)}, срочных {len(deadline)}")


def test_each_price_line_passes_real_guardrail(f):
    """🔴 СКВОЗНОЙ (C2 наперёд). Класс ошибки: бот не может процитировать прайс.

    Ценовая строка knowledge — это ровно то, что персона скажет лиду. Если
    `_findings` на ней непуст, guardrail подавит ответ, собранный по нашей же
    базе, и клиент получит молчание вместо цены.
    """
    knowledge = f["knowledge.md"]
    lines = price_lines(knowledge)
    assert len(lines) >= 3, f"ценовых строк {len(lines)} — проверять нечего"
    broken = [(n, ln, [x.number for x in _findings(ln, knowledge)])
              for n, ln in lines if _findings(ln, knowledge)]
    assert not broken, f"необеспеченные ценовые строки: {broken}"


# ══ R2. НИ ОДНОГО ЧИСЛА ВНЕ МНОЖЕСТВА ══════════════════════════════════════

def test_number_outside_the_price_block_never_reaches_the_client():
    """Класс ошибки: выдуманная цена уехала в examples.

    Число, которого нет в прайсе, произнесённое ботом, — это обещание, которое
    клиент не давал. Наружу оно не выходит НИКОГДА.

    ⚠️ ПЕРЕПИСАН ИНТЕГРАТОРОМ 17.08 под уточнённое правило. Прежняя редакция
    требовала `RenderError`, по букве спеки («попытка = ошибка генерации»). На
    живом брифе это роняло сборку клиента целиком: в собственном примере
    клиента (Q42) стоит оборот «переробляти через місяць» — единица времени без
    покрытия. Не собрать клиента из-за оборота в его же примере — цена выше
    любой выгоды.

    Граница проведена ПО АВТОРУ ТЕКСТА, и это решение интегратора:
      · число СОСТАВИЛ генератор (knowledge/playbook/settings) → `RenderError`,
        потому что это наша выдумка, и чинить её должен код;
      · число пришло в тексте КЛИЕНТА (пара примеров) → пара не выпускается,
        и запись уезжает в `dropped` с причиной.

    Оба конца одинаково обязательны. Тихий дроп здесь был бы хуже падения:
    `examples.yaml` мог бы уехать пустым, а голос персоны — молча пропасть.
    """
    brief = make_brief(
        q20_real_replies=(
            "Клієнт: Скільки коштує полірування?\n"
            f"Відповідь: Полірування — 9 999 грн, тривалість 1 день."),
        q46_top_questions=(
            "Клієнт: А точно 9 999?\n"
            "Відповідь: Так, полірування коштує 9 999 грн."),
    )
    result = render.render_all(brief, slug=SLUG)

    for name, text in result.files.items():
        assert "9 999" not in text and "9999" not in text, (
            f"{name}: необеспеченное число дошло до файла клиента")

    dropped = " ".join(
        f"{d.get('value', '')} {d.get('reason', '')}" for d in result.dropped)
    assert "9 999" in dropped or "9999" in dropped, (
        "пара отброшена МОЛЧА: в `dropped` нет ни числа, ни причины — "
        f"владелец не узнает, что голос персоны обеднел. dropped={result.dropped!r}")


def test_unbacked_number_in_playbook_still_fails_generation():
    """Вторая половина того же правила, и без неё первая опасна.

    Если бы дроп применялся ко всему подряд, необеспеченное число могло бы
    уехать в playbook и просто записаться в `dropped` — то есть генератор сам
    себе разрешил бы ошибку.

    ⚠️ ГРАНИЦУ ПРОВОДИТ НАЗНАЧЕНИЕ ФАЙЛА, а не автор текста. Интегратор
    сформулировал правило сперва «по автору» и был поправлен ЗАМЕРОМ: число из
    ответа клиента, приехавшее в `knowledge`, обеспечено ПО КОНСТРУКЦИИ —
    knowledge и есть то, из чего бот говорит, `_context_numbers` берёт
    обеспеченное множество именно оттуда. Проверено живьём: «777 годин» из Q29
    доезжает в knowledge, и `_findings` по этой строке пуст. Значит:

      · `knowledge` — источник правды, числа в нём обеспечены собой;
      · `playbook` обязан не выходить за него → `RenderError`, сборка падает;
      · `examples` — пара не выпускается, запись в `dropped`, сборка живёт.

    Тут проверяется средний случай: скидка из Q27 едет в playbook, процента
    такого в прайсе нет, и это обязано остановить генерацию.
    """
    with pytest.raises(render.RenderError) as exc:
        render.render_all(
            make_brief(q27_discounts="Знижка 55% для постійних клієнтів"), slug=SLUG)
    msg = str(exc.value)
    assert "55" in msg, f"ошибка не назвала число: {msg}"
    assert "playbook" in msg, f"ошибка не назвала файл и строку: {msg}"


def test_derived_price_form_from_the_block_is_allowed():
    """Класс ошибки: сторож R2 слишком груб и режет законную форму.

    «від 8 000 грн» — производная форма числа, которое В ПРАЙСЕ ЕСТЬ (R2 прямо
    называет такие формы законными). Генератор, падающий здесь, заставит
    человека выкинуть правило, а вместе с ним и защиту от выдуманных цен.
    """
    brief = make_brief(
        q46_top_questions=("Клієнт: Від якої суми кераміка?\n"
                           "Відповідь: Керамічне покриття — від 8 000 грн."))
    result = render.render_all(brief, slug=SLUG)
    assert "8 000" in files_of(result)["examples.yaml"]


def test_every_number_the_bot_may_say_is_backed_by_knowledge(f):
    """🔴 СКВОЗНОЙ. Класс ошибки: число в examples, которого нет в базе.

    examples едут в промпт как эталон голоса, и модель их цитирует. Каждый
    ответ персоны прогоняем настоящим `_findings` против сгенерированного
    knowledge — ровно так же, как это сделает рантайм на живом лиде.
    """
    pairs = yaml.safe_load(f["examples.yaml"])
    assert pairs, "пар нет вовсе — сторожу нечего проверять"
    bad = []
    for i, pair in enumerate(pairs, 1):
        found = _findings(pair["olga"], f["knowledge.md"])
        if found:
            bad.append((i, [(x.number, x.rule) for x in found]))
    assert not bad, f"необеспеченные числа в парах примеров: {bad}"


# ⚠️ СНЯТО ПОСЛЕ КАЛИБРОВКИ НА ЭТАЛОНЕ (спека §6, шаг 6).
# Сторож «в playbook нет цены, которой нет в knowledge» краснеет на ручном
# эталоне: `playbook.md:146` держит «від 25 000 грн» — это бюджет ICP из Q37,
# и живёт он под ЗАПРЕТОМ произносить («НІКОЛИ: сказати людині…»). Отличить
# внутреннюю разметку от квотируемой цены механически нельзя — это смысловое
# суждение, а такие §5 оставляет человеку. R2 в playbook остаётся НЕ ПОКРЫТ
# сторожем сознательно; покрыты examples (сквозной `_findings` ниже) и падение
# генерации на числе вне прайса.


def test_legal_non_price_numbers_in_knowledge_do_not_break_generation():
    """Класс ошибки: R2 прочитан как «в knowledge только числа прайса».

    В ЭТАЛОНЕ knowledge держит проценты предоплаты, рабочие часы и дату акции —
    ни одного из этих чисел в прайсе нет, и все они обеспечены СВОИМ фрагментом
    (`_context_numbers` признаёт фрагмент с валютой/процентом или с единицей
    времени). Генератор, падающий на них, не собрал бы даже ручной эталон.
    """
    brief = make_brief(
        q29_prepayment=("Роботи від 8 000 грн — передоплата 20%, "
                        "решта після завершення робіт"),
        q28_promo="Акція діє до 30 вересня 2026 року",
    )
    result = render.render_all(brief, slug=SLUG)
    knowledge = files_of(result)["knowledge.md"]
    assert "20%" in knowledge
    price, deadline = _context_numbers(knowledge)
    assert "20" in price, "процент предоплаты не обеспечен собственным фрагментом"


# ══ R3. ПЕРВАЯ СТРОКА ПЕРСОНЫ УХОДИТ ЛИДУ ══════════════════════════════════

def test_persona_first_line_is_prose_with_the_persona_name(f):
    """Класс ошибки: `# Персона` в первой строке.

    `disclosure`/`honest_prefix` берут ПЕРВУЮ строку persona.md и отдают её
    лиду дословно на вопрос «ты бот?». Заголовок уехал бы решёткой в чат
    живого клиента, а имя персоны — это то, чем она представляется.
    """
    first = f["persona.md"].splitlines()[0].strip()
    assert first, "первая строка персоны пуста — лид получит пустое сообщение"
    assert not first.startswith("#"), f"заголовок уедет в чат: {first!r}"
    assert "Оксана" in first, f"персона не называет себя: {first!r}"


def test_our_product_name_never_becomes_the_persona_name():
    """Класс ошибки: мусор из брифа стал именем персоны.

    В реальном брифе поле имени было заполнено НАШИМ именем продукта — класс
    «наше имя в поле клиента» (§1.2). `garbage` в конфиг не попадает ни при
    каких условиях; тихо подставить его значило бы представить бота клиента
    чужим именем прямо в первом сообщении.
    """
    result = render.render_all(
        make_brief(q14_persona_name=Garbage("Джарвис", "наше имя в поле клиента")),
        slug=SLUG)
    files = files_of(result)
    first = files["persona.md"].splitlines()[0].strip()
    assert first and not first.startswith("#")
    for name, text in files.items():
        assert "Джарвис" not in text, f"{name}: мусорное имя доехало до конфига"


# ══ R4. ЭСКАЛАЦИЯ: ЕДИНСТВЕННЫЙ СЛОЙ БЕЗ КЛАССИФИКАТОРА ════════════════════

def test_escalation_layer_is_alive_for_the_real_parser(f):
    """Класс ошибки: заголовок секции «почти» правильный.

    `parse_escalation_keywords` знает РОВНО три формулировки. Любая другая —
    слой молча выключен, и узнаем мы об этом на лиде, который просил позвать
    человека и не был передан. Проверяем НАСТОЯЩИМ парсером: сверка заголовка
    глазами уже однажды пропустила это.
    """
    heading, _ = escalation_section(f["playbook.md"])
    assert heading.casefold() in _KEYWORD_HEADINGS, (
        f"заголовок {heading!r} не из _KEYWORD_HEADINGS — слой выключен молча")
    assert parse_escalation_keywords(f["playbook.md"]), "словарь эскалации пуст"


def test_escalation_comment_lines_never_start_with_a_dash(f):
    """Класс ошибки: объяснение уехало в словарь.

    Парсер забирает ЛЮБУЮ строку `- ` внутри секции и не понимает HTML-
    комментариев. Первая версия ручного файла так и сделала: четыре
    предложения пояснения стали «ключевыми словами» и матчили пол-переписки.
    """
    _, body = escalation_section(f["playbook.md"])
    in_comment = False
    offenders = []
    for line in body.splitlines():
        s = line.strip()
        if in_comment and s.startswith("- "):
            offenders.append(s)
        if "<!--" in s:
            in_comment = True
        if "-->" in s:
            in_comment = False
    assert not offenders, f"строки комментария уедут в словарь: {offenders}"
    keywords = parse_escalation_keywords(f["playbook.md"])
    assert not [k for k in keywords if "<!--" in k or "-->" in k], keywords


def test_stop_words_are_full_phrases_not_bare_roots(f):
    """Класс ошибки: корень вместо фразы.

    «власник» голым словом эскалирует лида, который сказал «я власник BMW», —
    то есть каждого второго. Спека требует «з власником»: полная фраза
    однозначна вне контекста, корень — нет.
    """
    keywords = parse_escalation_keywords(f["playbook.md"])
    assert "власник" not in keywords, "голый корень «власник» эскалирует владельца авто"
    assert "менеджер" not in keywords or any(
        k != "менеджер" and "менеджер" in k for k in keywords), keywords
    assert any("власник" in k and k != "власник" for k in keywords), (
        f"фразы с «власник» нет вообще — стоп-слово брифа потеряно: {keywords}")


def test_no_escalation_keyword_matches_an_example_dialogue(f):
    """Класс ошибки: ключевое слово ловит обычный вопрос (C4 наперёд).

    Матч идёт подстрокой по ВХОДЯЩЕМУ сообщению лида. Реплики `client` из
    examples — это и есть образцы входящих: если хоть одна из них попадает в
    словарь, значит слово взято корнем и будет эскалировать нормальный
    разговор («поверн» в «повернути блиск»). Цена — потерянный ответ и
    карточка владельцу на ровном месте.

    КАЛИБРОВКА: knowledge из выборки исключён намеренно. На ручном эталоне 13
    из 35 слов встречаются в knowledge — все в разделе «Коли передаємо
    старшому майстру», который ЗЕРКАЛИТ те же поводы законно. Сторож, красный
    на эталоне, — это фон, а не сигнал.
    """
    haystack = f["examples.yaml"].casefold()
    keywords = parse_escalation_keywords(f["playbook.md"])
    assert keywords, "словарь пуст — сторож нечего проверять"
    hits = [k for k in keywords if k in haystack]
    assert not hits, f"ключевые слова матчат законный диалог: {hits}"


# ══ R5. examples.yaml ══════════════════════════════════════════════════════

def test_example_pairs_use_exactly_client_and_olga(f):
    """Класс ошибки: ключ переименован «по-человечески».

    Лоадер проверяет `set(item) != {"client", "olga"}` хардкодом, и конфиг
    падает НА СТАРТЕ — то есть у живого клиента, а не в тесте. Ключ `olga` —
    имя поля, а не имя персоны, и это ровно та строка, которую хочется
    «поправить».
    """
    pairs = yaml.safe_load(f["examples.yaml"])
    assert isinstance(pairs, list) and pairs, "examples.yaml не список верхнего уровня"
    for i, pair in enumerate(pairs, 1):
        assert set(pair) == {"client", "olga"}, f"пара #{i}: ключи {sorted(pair)}"
        assert pair["client"].strip() and pair["olga"].strip(), f"пара #{i} пуста"


def test_examples_fit_the_real_brain_budget(f):
    """Класс ошибки: пары молча отбрасываются в рантайме.

    `brain._examples_section` набирает пары по порядку, пока влезают в
    `EXAMPLES_CHAR_BUDGET`; хвост отбрасывается, и голос персоны тихо едет.
    Бюджет импортируем НАСТОЯЩИЙ: переписанное здесь число — второе число на
    ту же вещь, и меньшее погасит большее молча.
    """
    pairs = yaml.safe_load(f["examples.yaml"])
    total = sum(len(f"\nКлієнт: {p['client']}\nТи: {p['olga']}\n") for p in pairs)
    assert total <= EXAMPLES_CHAR_BUDGET, (
        f"{total} символов против бюджета {EXAMPLES_CHAR_BUDGET}")


def test_pairs_over_budget_are_dropped_before_the_file_not_after():
    """Класс ошибки: генератор отдал больше, чем рантайм прочитает.

    Если отбор бюджета оставить рантайму, счётчик отчёта покажет одно число,
    а промпт получит другое — «два числа на одну вещь». Отбросить обязан
    генератор, и счётчик обязан считать то, что ДОЕХАЛО до файла.
    """
    # без порядковых НОМЕРОВ в тексте: число вне прайса законно уронило бы
    # генерацию по R2, и тест мерил бы не бюджет, а другое правило
    fat = "\n\n".join(
        f"Клієнт: Питання «{chr(1072 + i)}» про комплексну хімчистку салону?\n"
        f"Відповідь: Комплексна хімчистка салону — 4 500{EN_DASH}7 500 грн, "
        f"тривалість 6{EN_DASH}10 годин. " + "Пояснення для клієнта. " * 20
        for i in range(32))
    result = render.render_all(make_brief(q20_real_replies=fat), slug=SLUG)
    pairs = yaml.safe_load(files_of(result)["examples.yaml"])
    total = sum(len(f"\nКлієнт: {p['client']}\nТи: {p['olga']}\n") for p in pairs)
    assert total <= EXAMPLES_CHAR_BUDGET, f"{total} > {EXAMPLES_CHAR_BUDGET}"
    assert result.counters["example_pairs"] == len(pairs), (
        "счётчик считает пары брифа, а не пары файла")


# ══ R6. forbidden_terms ════════════════════════════════════════════════════

def settings_of(files) -> dict:
    return yaml.safe_load(files["settings.yaml"])


def test_apostrophe_terms_are_given_in_both_variants(f):
    """Класс ошибки: один вариант апострофа молча не срабатывает.

    `forbidden_mention` матчит ПОДСТРОКОЙ без нормализации. Модель напишет
    «обов’язково» (U+2019), а в денилисте будет «обов'язково» (U+0027) — слой
    просто не сработает, и никто не узнает: молчание слоя выглядит как его
    отсутствие срабатываний.
    """
    terms = settings_of(f)["forbidden_terms"]
    for t in list(terms):
        if APO_ASC in t:
            assert t.replace(APO_ASC, APO_RSQ) in terms, f"нет пары U+2019 для {t!r}"
        if APO_RSQ in t:
            assert t.replace(APO_RSQ, APO_ASC) in terms, f"нет пары U+0027 для {t!r}"
    assert any(APO_ASC in t for t in terms), "фраза с апострофом из брифа потеряна"


def test_inherited_foreign_payment_block_is_always_present(f):
    """Класс ошибки: платёжная лексика чужой юрисдикции у украинского клиента.

    Блок унаследован, стоит ноль и ловит класс «модель вспомнила чужую
    страну»: «рублей», «Сбербанк» в ответе — это не стиль, это протечка. R6
    требует его ВСЕГДА, независимо от брифа.
    """
    low = {t.casefold() for t in settings_of(f)["forbidden_terms"]}
    for term in ("рубл", "₽", "сбербанк"):
        assert term in low, f"нет унаследованного термина {term!r}"


def test_self_poisoning_term_fails_generation_and_names_the_line():
    """Класс ошибки: денилист режет собственный текст клиента.

    Термин, встречающийся в НАШЕМ knowledge, гарантирует подавление законного
    ответа — бот замолчит на теме, которую сам же и описывает. Спека требует
    падение ДО записи и показ строки: «где-то самоотравление» человек чинить
    не сможет.
    """
    brief = make_brief(
        q45_guarantees="Ми не обіцяємо, що гарантія результату можлива завжди",
        q50_forbidden_phrases="гарантія результату")
    with pytest.raises(render.RenderError) as exc:
        render.render_all(brief, slug=SLUG)
    msg = str(exc.value)
    assert "гарантія результату" in msg, msg
    assert re.search(r"\d", msg), f"не названа строка: {msg}"


def test_no_forbidden_term_matches_the_generated_files(f):
    """Класс ошибки: корень вместо фразы (C5 наперёд).

    Корень «гарант» убил бы обязательный раздел «Гарантії та якість роботи»,
    корень «дешев» — легальное «дешевшого рішення». Цена ложного срабатывания
    — потерянный ответ лиду, и заметить его невозможно: подавленный ответ
    выглядит как «бот не понял».
    """
    terms = settings_of(f)["forbidden_terms"]
    assert terms, "денилист пуст — brand-safety выключена целиком"
    for name in ("persona.md", "knowledge.md", "playbook.md", "examples.yaml"):
        hit = forbidden_mention(f[name], terms)
        assert hit is None, f"{name}: собственный текст матчит запрет {hit!r}"


# ══ R7/R8. ФАКТЫ И РАЗДЕЛЫ ═════════════════════════════════════════════════

def test_missing_fact_gives_both_a_stub_and_a_record(base, f):
    """Класс ошибки: половина пары потерялась.

    C10 сверяет ПАРНОСТЬ «заглушка в knowledge ⇔ запись в отчёте». Есть только
    заглушка — вопрос клиенту никто не задаст. Есть только запись — бот
    выдумает адрес. Проверяем множества, а не наличие: расхождение молча
    зеленеет именно на «одно есть, второго нет».
    """
    unknown = section_body(f["knowledge.md"], vocabulary.UNKNOWN_SECTION_UK)
    assert unknown is not None, (
        f"нет раздела «{vocabulary.UNKNOWN_SECTION_UK}» — заглушкам некуда ехать")
    recorded = {s["fact_id"] for s in base.stubs}
    in_file = {fact.id for fact in vocabulary.REQUIRED_FACTS
               if fact.title_uk.casefold() in unknown.casefold()}
    assert recorded == in_file, (
        f"заглушки в knowledge {sorted(in_file)} ≠ записи .stubs {sorted(recorded)}")
    # факты без поля в форме (адрес, канал записи, телефон) не могут быть
    # обеспечены брифом НИКОГДА — они обязаны быть в паре всегда
    assert {"address", "booking_channel", "phone"} <= recorded, sorted(recorded)


def test_stub_record_carries_the_whole_contract(base, f):
    """Класс ошибки: запись без формулы или без строки, ушедшей в файл.

    `.stubs` — вход раздела 3 отчёта и C10. Запись, из которой нельзя достать
    ДОСЛОВНУЮ строку knowledge, делает сверку невозможной: сравнивать будет
    нечего, и проверка станет зелёной по построению.
    """
    assert base.stubs, "не собрано ни одной заглушки — адреса в форме нет вообще"
    for s in base.stubs:
        assert set(s) == {"fact_id", "title", "stub_line"}, sorted(s)
        assert s["stub_line"].strip(), f"{s['fact_id']}: пустая заглушка"
        assert s["stub_line"] in f["knowledge.md"], (
            f"{s['fact_id']}: строка отчёта не найдена в knowledge дословно")
        assert s["title"] in s["stub_line"], (
            f"{s['fact_id']}: заглушка не называет сам факт: {s['stub_line']!r}")
        assert "назива" in s["stub_line"].casefold(), (
            f"{s['fact_id']}: заглушка не говорит, КТО назовёт: {s['stub_line']!r}")


def test_a_fact_covered_by_the_brief_is_not_stubbed(base):
    """Класс ошибки: «есть» и «не знаем» одновременно.

    Сайт в брифе заполнен рабочей ссылкой. Заглушка на него дала бы knowledge,
    который в одном разделе даёт ссылку, а в другом говорит «посилань немає»
    (ровно флаг C13) — и бот выберет, что сказать, сам.
    """
    assert "links" not in {s["fact_id"] for s in base.stubs}


def test_placeholder_domain_downgraded_by_t1_becomes_a_stub():
    """Класс ошибки: мусорная ссылка подана лиду как контакт.

    В реальном брифе ссылки были плейсхолдер-доменами. `garbage` в конфиг не
    попадает — значит факт «сайт» не обеспечен, и он ОБЯЗАН дать пару
    заглушка+запись, а не тихо исчезнуть между «данные есть» и «данных нет».
    """
    result = render.render_all(
        make_brief(q4_links=Garbage("drivepro-detailing.example", "плейсхолдер-URL")),
        slug=SLUG)
    assert "links" in {s["fact_id"] for s in result.stubs}
    assert "example" not in files_of(result)["knowledge.md"]


def test_every_required_section_exists_with_a_meaningful_line(f):
    """Класс ошибки: пустой заголовок считается разделом.

    Раздел собирается из нескольких полей плюс решений владельца, и при всех
    заполненных полях его можно просто не написать. «Раздел есть» обязано
    значить «есть содержимое», иначе проверка C11 меряет напечатанную решётку.
    """
    for title in vocabulary.REQUIRED_SECTIONS_UK:
        body = section_body(f["knowledge.md"], title)
        assert body is not None, f"нет обязательного раздела «{title}»"
        assert meaningful_lines(body), f"раздел «{title}» пуст — только заголовок"


# ══ НАЗВАНИЕ УСЛУГИ: Q21 ПРОТИВ Q22 (решение владельца 17.08) ══════════════
#
# Прайс (Q22) клиент пишет сокращёнными заголовками, перечень услуг (Q21) —
# полными. Решение владельца: брать ДЛИННОЕ из Q21, если оно НАЧИНАЕТСЯ с
# заголовка Q22 и длиннее его. Довод не косметический: «Локальна хімчистка»
# без хвоста путается с «Комплексна хімчистка салону», и лид получает цену НЕ
# ТОЙ работы — то есть неверный счёт, а не некрасивое слово.
#
# Границы правила задаются здесь, потому что решение их и задаёт: не совпал
# префикс — не наше дело; короче — никогда не укорачиваем; два кандидата —
# не угадываем.

def _titles(files) -> list[str]:
    return [ln.strip("# ").strip()
            for ln in files["knowledge.md"].splitlines()
            if ln.startswith("## ")]


def test_a_service_title_is_completed_from_the_services_list():
    """Ловит: цену, названную не для той работы.

    Заголовок прайса — сокращение самого клиента; полное название он дал в
    Q21. У Ярины так вышло 7 раз из 14, и самая дорогая пара — «Локальна
    хімчистка» против «Комплексна хімчистка салону»: без хвоста услуги
    неотличимы, а цены у них разные.
    """
    result = render.render_all(make_brief(
        q21_services="Детейлінг-мийка автомобіля\nКомплексна хімчистка салону",
    ), slug=SLUG)
    titles = " | ".join(_titles(files_of(result)))
    assert "Детейлінг-мийка автомобіля" in titles, titles


def test_a_title_the_services_list_does_not_continue_is_left_alone():
    """Парная: правило не имеет права переименовывать по похожести.

    Q21 «Нанесення керамічного покриття» НЕ начинается с заголовка прайса
    «Керамічне покриття кузова». Разрешить «похоже» — значит дать генератору
    право сочинять названия услуг, а это ровно тот класс, ради которого
    strict_knowledge и существует.
    """
    result = render.render_all(make_brief(
        q21_services="Нанесення керамічного покриття",
    ), slug=SLUG)
    titles = " | ".join(_titles(files_of(result)))
    assert "Керамічне покриття кузова" in titles, titles
    assert "Нанесення" not in titles, titles


def test_a_shorter_entry_never_replaces_the_price_heading():
    """Парная: укорачивать нельзя НИКОГДА — решение владельца про длинные."""
    result = render.render_all(make_brief(
        q21_services="Комплексна хімчистка",
    ), slug=SLUG)
    titles = " | ".join(_titles(files_of(result)))
    assert "Комплексна хімчистка салону" in titles, titles


def test_an_equal_entry_never_rewrites_the_price_heading():
    """Парная к предыдущей, и она же — единственная, где условие длины ВИДНО.

    Замер: мутация «убрать `len(e) > len(title)`» на укорачивании не краснеет
    вовсе — более короткий кандидат не проходит проверку префикса и до длины
    не доходит. Условие длины наблюдаемо ровно в одном случае: кандидат РАВЕН
    заголовку, но написан иначе. Клиент часто перечисляет услуги строчными, и
    замена «Комплексна хімчистка салону» на «комплексна хімчистка салону»
    ничего не уточняет — она лишь переписывает заголовок, который клиент
    оформил сам.
    """
    result = render.render_all(make_brief(
        q21_services="комплексна хімчистка салону",
    ), slug=SLUG)
    joined = " | ".join(_titles(files_of(result)))
    assert "Комплексна хімчистка салону" in joined, joined


def test_two_candidates_leave_the_heading_untouched():
    """Ловит: угаданное название.

    Два продолжения одного заголовка — это вопрос к владельцу, а не выбор
    генератора: «Комплексна хімчистка салону» и «Комплексна хімчистка салону
    та багажника» стоят разных денег. Молчаливый выбор одного из них — то же
    самое, что молчаливый дефолт.
    """
    result = render.render_all(make_brief(
        q21_services=("Детейлінг-мийка автомобіля\n"
                      "Детейлінг-мийка мотоцикла"),
    ), slug=SLUG)
    joined = " | ".join(_titles(files_of(result)))
    assert "Детейлінг-мийка" in joined, joined
    assert "автомобіля" not in joined and "мотоцикла" not in joined, joined


def test_the_number_of_services_never_changes_with_the_titles():
    """Ловит: правило названий, которое склеило или потеряло услугу.

    Инвариант приёмки — 14 услуг у эталона. Заголовок меняет ТЕКСТ, а не
    состав; если состав поехал, красным станет не тут, а на приёмке клиента.
    """
    plain = render.render_all(make_brief(), slug=SLUG)
    long_ = render.render_all(make_brief(
        q21_services="Детейлінг-мийка автомобіля\nКерамічне покриття кузова авто",
    ), slug=SLUG)
    assert len(_titles(files_of(plain))) == len(_titles(files_of(long_)))


# ══ R9. ВИСЯЩИЙ СРОК ═══════════════════════════════════════════════════════

def test_dangling_deadline_names_who_will_tell_the_exact_one(f):
    """Класс ошибки: обещание срока, за которое никто не отвечает.

    «плюс рекомендований час на полімеризацію» — единица времени БЕЗ числа.
    Числовой guardrail здесь бессилен по конструкции: сверять нечего. Лид
    получил обещание и не знает, у кого спросить точное, — а бот, которого
    спросят, выдумает. Единственная защита — правило генератора (C14).
    """
    lines = [ln.strip() for ln in f["knowledge.md"].splitlines()]
    idx = [i for i, ln in enumerate(lines) if "полімеризац" in ln.casefold()]
    assert idx, "фраза про полимеризацию потерялась при генерации"
    tellers = ("назива", "підтверджу", "уточню", "уточнює")
    for i in idx:
        tail = " ".join(lines[i:i + 3]).casefold()
        assert any(t in tail for t in tellers), (
            f"knowledge:{i + 1} «{lines[i]}» — не сказано, кто назовёт точное")


# R9 не говорит, ЧЕМ выражена неопределённость. Первый живой клиент выразил её
# не наречием («орієнтовно»), а МОДАЛЬНОСТЬЮ с условием — и правило промолчало,
# хотя обещание висит ровно так же. Фикстура берёт форму у живого клиента
# (Ярина, «У складних випадках автомобіль може залишатися до наступного дня»),
# утверждение — у спеки: единица времени без числа обязана назвать, кто скажет
# точное. Пара ниже держит границу с другой стороны.
MODAL_DEADLINE = "У складних випадках авто може залишатися до наступного дня"
NUMBERED_MODAL = "Авто може бути готове за 2 години"


def test_a_modal_promise_without_a_number_also_names_who_will_tell_the_exact_one():
    """Класс ошибки: правило узнаёт неопределённость по одному способу её сказать.

    «Може залишатися до наступного дня» — единица времени есть, числа нет,
    адресата нет. Для лида это то же самое обещание, что и «орієнтовно день»,
    и разбирается оно тем же R9. Сторож стоит на СЛЕДСТВИИ («сказано, кто
    назовёт точное»), а не на списке маркеров: список — это реализация, а
    молчание правила на новой форме — дефект независимо от того, как он внутри
    устроен.
    """
    result = render.render_all(
        make_brief(q25_deadlines=DEADLINES + "\n" + MODAL_DEADLINE), slug=SLUG)
    lines = [ln.strip() for ln in files_of(result)["knowledge.md"].splitlines()]
    idx = [i for i, ln in enumerate(lines) if "наступного дня" in ln.casefold()]
    assert idx, "модальное обещание потерялось при генерации — проверять нечего"
    tellers = ("назива", "підтверджу", "уточню", "уточнює")
    for i in idx:
        tail = " ".join(lines[i:i + 3]).casefold()
        assert any(t in tail for t in tellers), (
            f"knowledge:{i + 1} «{lines[i]}» — модальность без числа и без "
            "адресата: лид получил обещание и не знает, у кого спросить точное")


def test_a_modal_promise_WITH_a_number_is_left_alone():
    """Парная: расширять правило до «любая модальность» — испортить его.

    «Може бути готове за 2 години» число называет, guardrail его обеспечивает,
    и дописка «точний час називає …» здесь лишняя строка в конфиге клиента.
    Без этого сторожа мутация «считать висящим ВСЁ» прошла бы зелёной, и
    правило перестало бы отличать обеспеченное обещание от пустого.
    """
    result = render.render_all(
        make_brief(q25_deadlines=DEADLINES + "\n" + NUMBERED_MODAL), slug=SLUG)
    lines = [ln.strip() for ln in files_of(result)["knowledge.md"].splitlines()]
    idx = [i for i, ln in enumerate(lines) if "готове за 2 години" in ln.casefold()]
    assert idx, "обеспеченное обещание потерялось при генерации"
    for i in idx:
        nxt = lines[i + 1].casefold() if i + 1 < len(lines) else ""
        assert "точний час називає" not in nxt, (
            f"knowledge:{i + 2} — дописка к обещанию, у которого число ЕСТЬ")


# ⚠️ СНЯТО ПОСЛЕ КАЛИБРОВКИ НА ЭТАЛОНЕ (спека §6, шаг 6).
# Сплошной обход «все фрагменты knowledge с единицей времени и без числа»
# даёт на ручном эталоне 12 находок и все 12 ложные: `_TIME_UNIT` — это стемы
# для РАНТАЙМА, и `час\w*` матчит «ЧАСтина салону», `ма[йя]\w*` — «МАЙстру»,
# «рік» — «рік авто». Сторож с 12/12 ложных не сторож. Правило R9 остаётся
# покрыто прицельно (тест выше); сплошной обход — это C14 в `checks.py`, и там
# ему нужен фильтр стемов, иначе он утонет в шуме на первом же клиенте.


# ══ ДЕФОЛТЫ ПРОТИВ БРИФА ═══════════════════════════════════════════════════

def test_hard_defaults_survive_a_brief_that_asks_otherwise(f):
    """Класс ошибки: бриф переспорил дефолт.

    Бриф просил «спілкуватись як людина і не казати, що це бот». Уступить —
    значит выпустить бота, выдающего себя за человека, под ответственность
    владельца, который этого решения не принимал. `funnel_gate: true` в файле
    поднялся бы на ребуте БЕЗ команды владельца (гардиан деплоит с рабочего
    дерева) и веером ответил бы незнакомцам на старте.
    """
    s = settings_of(f)
    assert s.get("honesty_mode") == "honest", s.get("honesty_mode")
    assert s.get("telegram", {}).get("funnel_gate", False) is False
    assert s.get("strict_knowledge", True) is True
    assert s.get("payments", {}).get("enabled", False) is False


def test_conflict_with_the_brief_is_recorded_with_a_quote(base):
    """Класс ошибки: дефолт подставлен молча.

    Молчаливый дефолт — это решение владельца, принятое пайплайном. Раздел 2
    отчёта существует ровно затем, чтобы конфликт был ВИДЕН: «бриф просил
    другое» плюс дословная цитата, по которой владелец узнаёт своё поле.
    """
    honesty = defaults_for(base, "honesty_mode")
    assert honesty, f"honesty_mode не попал в .defaults: {base.defaults}"
    rec = honesty[0]
    assert set(rec) == {"key", "value", "why", "brief_wanted_other", "brief_quote"}
    assert rec["value"] == "honest"
    assert rec["brief_wanted_other"] is True, "конфликт с Q15 не отмечен"
    assert rec["brief_quote"] and "людина" in rec["brief_quote"], rec["brief_quote"]
    assert rec["why"], "не сказано, почему подставлено именно это"


def test_every_hard_default_is_declared_not_just_applied(base):
    """Класс ошибки: применено четыре дефолта, показан один.

    Спека называет жёсткими четыре: honesty_mode, funnel_gate,
    strict_knowledge, payments. Тот, которого нет в разделе 2, доедет до прода
    как «решение» — а решения такого никто не принимал.
    """
    for key in ("honesty_mode", "funnel_gate", "strict_knowledge", "payments"):
        assert defaults_for(base, key), f"дефолт {key} не объявлен в .defaults"


def test_payments_stay_off_without_requisites(base, f):
    """Класс ошибки: оплата включена без реквизитов.

    Бриф прямо говорит «реквізити надсилаю тільки я». Включённый блок оплаты
    без реквизитов — это бот, который зовёт платить и не может сказать куда;
    цена ошибки платёжная, а не текстовая.
    """
    assert settings_of(f).get("payments", {}).get("enabled", False) is False
    rec = defaults_for(base, "payments")
    assert rec and rec[0]["why"], "выключение оплаты не объяснено"


# ══ Q38 — ПОЛЕ ДВОЙНОГО НАЗНАЧЕНИЯ (решение владельца 17.08) ═══════════════

def test_q38_full_text_goes_to_playbook(f):
    """Класс ошибки: внутренний критерий отказа зачитан лиду.

    В ячейке смешаны публичный список работ и внутреннее «кого не берём».
    Развести автоматом нельзя — это смысловое суждение. Поэтому ПОЛНЫЙ текст
    едет в playbook, где он безопасен: playbook лиду не зачитывается.
    """
    assert ANTI_ICP_PRIVATE.casefold() in f["playbook.md"].casefold()


def test_q38_also_builds_the_public_knowledge_section(f):
    """Класс ошибки: обязательный раздел knowledge остался пустым.

    R8 требует раздел «Чого ми не робимо», и его единственный источник — та же
    ячейка. Отправить текст только в playbook значит выполнить §1.3 и провалить
    R8; собрать раздел из него — второе следствие того же решения владельца.
    """
    body = section_body(f["knowledge.md"], "Чого ми не робимо")
    assert body is not None and meaningful_lines(body)
    assert "кузовний ремонт" in body.casefold(), (
        f"публичная часть Q38 не доехала в knowledge: {body!r}")


def test_icp_never_reaches_knowledge(f):
    """Класс ошибки: ICP зачитан лиду дословно (C9 наперёд).

    knowledge — то, из чего бот ГОВОРИТ; playbook — то, как он себя ВЕДЁТ.
    «Наш ідеальний клієнт — той, хто готовий інвестувати» в knowledge однажды
    будет процитировано лиду, и это будет стоить сделки. Маршрут объявлен в
    схеме по ИСТОЧНИКУ, и генератор обязан его соблюсти.
    """
    flat = re.sub(r"\s+", " ", f["knowledge.md"]).casefold()
    assert ICP_FRAGMENT.casefold() not in flat, "ICP уехал в knowledge"
    assert ICP_FRAGMENT.casefold() in re.sub(
        r"\s+", " ", f["playbook.md"]).casefold(), "ICP потерян вовсе"


# ══ КОНТРАКТ РЕЗУЛЬТАТА ════════════════════════════════════════════════════

def test_render_all_returns_exactly_the_five_client_files(base):
    """Класс ошибки: файл назван иначе, чем его ищет лоадер.

    Каталог клиента — это ровно пять имён; `load_config` ищет их дословно.
    Шестой файл или другое имя = C1 красная на приёмке, а до приёмки —
    «клиент не поднимается» без объяснения.
    """
    assert set(base.files) == FIVE_FILES, sorted(base.files)
    for name, text in base.files.items():
        assert text.strip(), f"{name} пуст"


def test_settings_carry_every_key_the_loader_demands(f):
    """Класс ошибки: клиент не поднимается (C1 наперёд).

    `config.loader` требует `model`, `language`, `owner_id`, `persona_name`
    и валидирует язык списком. Отсутствие любого — `ConfigError` НА СТАРТЕ
    раннера, то есть уже после того, как каталог отнесли в `chatter/clients`.
    Сгенерированный конфиг, который не поднимается, — это не «почти готово».
    """
    s = settings_of(f)
    for key in ("model", "language", "owner_id", "persona_name", "owner_ref"):
        assert s.get(key), f"settings.yaml: нет ключа {key}"
    assert s["language"] == "uk", s["language"]
    assert s["persona_name"] == "Оксана"


def test_counters_count_the_artifact_not_the_intention(base, f):
    """Класс ошибки: «два числа на одну вещь».

    Счётчики уезжают в раздел 1 отчёта и читаются как приёмка («14 услуг, 31
    ценовое число»). Если счётчик считает намерение (сколько было в брифе), а
    файл содержит другое, меньшее погасит большее МОЛЧА: отчёт зелёный, файл
    неполный. Каждый счётчик сверяем с тем, что реально лежит в файлах.
    """
    c = base.counters
    price, deadline = _context_numbers(f["knowledge.md"])
    pairs = yaml.safe_load(f["examples.yaml"])
    assert c["services"] == 3, f"услуг в прайсе три, счётчик {c['services']}"
    assert c["prices"] == len(price), f"{c['prices']} против {len(price)}"
    assert c["deadlines"] == len(deadline), f"{c['deadlines']} против {len(deadline)}"
    assert c["example_pairs"] == len(pairs)
    assert c["forbidden"] == len(settings_of(f)["forbidden_terms"])
    assert c["stop_words"] == len(parse_escalation_keywords(f["playbook.md"]))


def test_a_knowledge_answer_either_lands_or_is_recorded(f):
    """Класс ошибки: третье состояние — «было в брифе и потерялось».

    Отчёт знает ровно два: «ВЗЯТО ИЗ БРИФА» (раздел 1, `id → файл:якорь`) и
    «В БРИФЕ НЕТ» (раздел 3). Ответ, который клиент написал, детектор пропустил
    как чистый, а генератор не положил никуда, не попадёт НИ В ОДИН раздел:
    владелец прочитает отчёт и решит, что всё на месте. Хуже того, факт при
    этом считается обеспеченным — значит и заглушки не будет, и бот промолчит
    на вопрос, ответ на который клиент дал.

    След ищется мягко (любое слово от пяти букв или число из ответа): текст
    законно переписывается под язык клиента, и требовать дословности нельзя.
    """
    brief = make_brief()
    knowledge = f["knowledge.md"].casefold()
    lost = []
    for fid, rec in brief["fields"].items():
        if rec["target"] != "knowledge" or rec["verdict"] != "ok" or not rec["value"]:
            continue
        words = [w for w in re.findall(r"[\w’'-]{5,}", rec["value"]) if not w.isdigit()]
        nums = re.findall(r"\d+", rec["value"])
        if not (any(w.casefold() in knowledge for w in words)
                or any(n in knowledge for n in nums)):
            lost.append((fid, rec["value"][:60]))
    assert not lost, f"ответы клиента исчезли без следа и без записи: {lost}"


def test_garbage_and_suspect_answers_never_reach_any_file(f):
    """Класс ошибки: мусор доехал до конфига клиента.

    Детектор T1 ничего не удаляет — он понижает поле до «ответа нет», и `raw`
    остаётся уликой в brief.json. Генератор, читающий `raw` вместо `value`,
    аккуратно вернёт «asdf» и «тест» в файлы живого клиента, а verdict
    останется в отчёте как доказательство, что мусор «не прошёл».
    """
    for name, text in f.items():
        assert GARBAGE_NEEDLE not in text, f"{name}: мусорное значение доехало"


def test_generation_is_a_pure_function_of_the_brief():
    """Класс ошибки: два прогона одного брифа дают разные файлы.

    Приёмка арки (§6) — пофайловый `--diff` с ручным эталоном. Недетерминизм
    (порядок множества, дата в шапке) превращает её в шум, а шум перестают
    читать. Он же делает невозможной классификацию расхождений.
    """
    one = render.render_all(make_brief(), slug=SLUG)
    two = render.render_all(make_brief(), slug=SLUG)
    assert one.files == two.files


def test_render_error_is_a_real_exception():
    """Класс ошибки: `RenderError` объявлен, но не поднимается.

    Молча вернуть частичный результат вместо ошибки — это DEV-18 наоборот:
    ошибка проглочена, а каталог клиента выглядит готовым.
    """
    assert issubclass(render.RenderError, Exception)
