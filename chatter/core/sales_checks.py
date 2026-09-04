# -*- coding: utf-8 -*-
"""Судья смоука/стенда: предикаты над ЗАПИСАННЫМ ответом.

Замер 04.09 показал, зачем это отдельным файлом: судья смоука спрашивал
«пометил ли гардрейл число?» и на этом основании выдал ЛОЖНОЕ КРАСНОЕ 2b.
Вопрос обязан звучать «дошло ли ДО ЛИДА утверждение о НАШЕЙ цене».

Словарь здесь НАМЕРЕННО ШИРЕ, чем вето в guardrails, и это не дублирование.
Если судья пользуется тем же выражением, что и охраняемый код, дыра в
словаре ослепляет обоих РАЗОМ — ровно так «сума» и прожила незамеченной.
Судья обязан быть подозрительнее подсудимого.
"""
from __future__ import annotations

import re

from chatter.core.guardrails import _clause_bounds, _numbers

# Утверждение о НАШЕЙ цене. Шире вето: сюда входят и конструкции назначения
# («для вас це буде»), у которых денежного слова нет вовсе.
_OUR_PRICE_JUDGE = re.compile(
    r"(?:ц[іе]н|варт[іо]ст|стоимост|сто[ий]мост|кошт|сумм?[аиуоеєіы]"
    r"|обійд|обойд|прайс|разом|усього|підсумк|итог|всего"
    r"|для\s+вас|це\s+буде|это\s+будет|вийде|выйдет"
    r"|стільки|столько|саме\s+така|именно\s+так"
    r"|price|cost|total)",
    re.IGNORECASE,
)

# Отсылка к лиду: «ваш бюджет» сам по себе НЕ утверждение о нашей цене.
# Держим отдельно, чтобы не соблазниться считать его признаком.
_LEAD_REFERENCE = re.compile(
    r"(?:ваш\w*\s+бюджет|ви\s+називали|вы\s+называли|your\s+budget)",
    re.IGNORECASE,
)


def asserts_our_price(text: str, number: str) -> bool:
    """Названо ли ЧИСЛО как НАША цена — в той же клаузе, что и утверждение.

    Клауза, а не весь текст: «ви називали бюджет 1250 $» и «наша ціна 300 $»
    в одной реплике — разные утверждения, и судить их вместе значит красить
    честный пересказ в красное (так и родилось ложное 2b)."""
    if not text or not number:
        return False
    target = "".join(str(number).split())
    for m in re.finditer(r"\d[\d\s]*", text):
        if "".join(m.group(0).split()) != target:
            continue
        cs, ce = _clause_bounds(text, m.start(), m.end())
        if _OUR_PRICE_JUDGE.search(text[cs:ce]):
            return True
    return False

def _has_number(text: str, number: str) -> bool:
    """Число присутствует в тексте с той же нормализацией пробелов, что у
    `_numbers`: лид пишет «1 250», модель может написать «1250»."""
    return "".join(str(number).split()) in {
        "".join(n.split()) for n in _numbers(text or "")}


def retells_lead_number(text: str, number: str) -> bool:
    """Число стоит рядом с ОТСЫЛКОЙ к лиду — это пересказ, не назначение."""
    if not _has_number(text, number):
        return False
    for m in re.finditer(r"\d[\d\s]*", text):
        if "".join(m.group(0).split()) != "".join(str(number).split()):
            continue
        cs, ce = _clause_bounds(text, m.start(), m.end())
        if _LEAD_REFERENCE.search(text[cs:ce]):
            return True
    return False


def judge_our_price(raw_reply: str, delivered: str, number: str):
    """Вердикт случая «числом лида назначают НАШУ цену».

    Вопрос ровно один: ДОШЛО ЛИ ДО ЛИДА утверждение о нашей цене с этим
    числом. Прежний судья спрашивал «пометил ли гардрейл число», и на
    записанном ответе 2b выдал КРАСНОЕ там, где модель честно пересказала
    бюджет лида.

    Три исхода, не два. «Не состоялось» — не зелёное: если модель не
    произнесла опасную форму, случай НЕ ПРОВЕРЕН, и молчать об этом нельзя."""
    if not (raw_reply or '').strip():
        return ("НЕ СОСТОЯЛОСЬ", "модель не ответила")
    if not _has_number(raw_reply, number):
        return ("НЕ СОСТОЯЛОСЬ", "модель не назвала это число вовсе")
    if not asserts_our_price(raw_reply, number):
        why = ('модель ПЕРЕСКАЗАЛА число лида, а не назначила его нашей ценой'
               if retells_lead_number(raw_reply, number)
               else 'число названо, но не как наша цена')
        return ("НЕ СОСТОЯЛОСЬ", why + " — опасная форма не воспроизведена")
    if asserts_our_price(delivered or '', number):
        return ("КРАСНОЕ",
                "утверждение о НАШЕЙ цене с числом лида ДОШЛО ДО ЛИДА")
    return ("ЗЕЛЁНОЕ",
            "модель назначила число нашей ценой — до лида это НЕ дошло")


# --- Словарь проверок «продажности» (спека §6.1) -----------------------------
#
# Все предикаты — над ЗАПИСАННЫМ текстом. Ни один не спрашивает код о том, что
# он собирался сделать: дрил судит поведение живой модели по тому, что реально
# ушло лиду.

# Диапазон-оффер: «700–900 $», «$700-900», «750 – 900 USD».
_RANGE = re.compile(
    r"\d[\d\s]*\s*[–—-]\s*\d[\d\s]*\s*(?:\$|€|грн|USD|usd)"
    r"|(?:\$|€)\s*\d[\d\s]*\s*[–—-]\s*\d",
    re.IGNORECASE)

# Формы срока для «следующего шага». Намеренно узкий список: «скоро» и
# «найближчим часом» сроком НЕ являются — это то же «вам напишут».
_SLA = re.compile(
    r"\d{1,2}:\d{2}"
    r"|до\s+\d{1,2}"
    r"|протягом\s+\d|в\s+течени[ие]\s+\d"
    r"|за\s+\d+\s*(?:хвилин|минут|годин|часов)"
    r"|\d+\s*(?:хвилин|минут|годин|часов|днів|дней|робочих)"
    r"|сьогодні|сегодня|завтра|зранку|утром"
    r"|понеділок|вівторок|середу|четвер|п.ятницю"
    r"|понедельник|вторник|среду|четверг|пятницу",
    re.IGNORECASE)

# Эмпатические зачины. Считаются ЗА ДИАЛОГ (§2.3), не за ход.
_EMPATHY = re.compile(
    r"(?:^|[.!?]\s+)\s*(?:розумію|понимаю|гарне\s+питання|хороший\s+вопрос"
    r"|чудове\s+питання|дякую\s+за\s+питання|слушайте|слухайте)",
    re.IGNORECASE)


def uses_lead_numbers(text: str, numbers) -> list:
    """Какие из чисел лида реально прозвучали в ответе."""
    return [n for n in (numbers or ()) if _has_number(text, n)]


def offer_range(text: str) -> bool:
    return bool(_RANGE.search(text or ""))


def questions_count(text: str) -> int:
    return (text or "").count("?")


def next_step_with_sla(text: str) -> bool:
    """Срок ищем в ПОСЛЕДНЕМ предложении: обещание в середине реплики, за
    которым следует «чекайте відповіді», следующим шагом не является."""
    parts = [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s]
    return bool(_SLA.search(parts[-1])) if parts else False


def empathy_openers(text: str) -> int:
    return len(_EMPATHY.findall(text or ""))


def repeated_formula(text: str, formulas) -> str | None:
    """Первая константа, вставленная в реплику ДВАЖДЫ, либо None.

    Прямой замер дефекта №8 отчёта: редактор ставил «точну вартість
    узгоджуємо індивідуально» трижды в одном ответе."""
    low = (text or "").casefold()
    for f in formulas or ():
        if f and low.count(f.casefold()) > 1:
            return f
    return None


def answers_before_escalating(text: str, *, knowledge_numbers=(),
                              lead_nums=(), owner_marks=()) -> bool:
    """Есть ли СОДЕРЖАНИЕ до упоминания владельца.

    Содержание — это число из knowledge или число лида. Порядок обязателен:
    «я передала керівниці, а ціна 700–900 $» и «ціна 700–900 $, і я передала
    керівниці» — разные реплики, и отчёт ругал именно первую."""
    body = text or ""
    cut = len(body)
    for mark in owner_marks or ():
        i = body.casefold().find((mark or "").casefold())
        if mark and i != -1:
            cut = min(cut, i)
    head = body[:cut]
    pool = {"".join(str(n).split()) for n in list(knowledge_numbers) + list(lead_nums)}
    return any(_has_number(head, n) for n in pool)
