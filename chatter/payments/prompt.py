"""Блок счёта в промпте и подстановка ПОСЛЕ модели (§8.3, §8.4, §14 пп.13, 16).

Порядок жёсткий: **модель → guardrails → подстановка**.

  модель  → "... переказ на {REQUISITES} до {DUE}"
  код     → подстановка из конфига

Почему не «попросить модель не менять реквизиты»: модель, увидевшая IBAN, может
его «поправить» — переставить символы, дописать пробел, перевести. Ошибка в одном
символе IBAN означает деньги, ушедшие не туда (риск 10.5, самый дорогой в
списке). Единственная надёжная защита — модель этих символов не видит.

Почему подстановка ПОСЛЕ guardrails, а не до: правило `large_number` вырезает
число ≥ 100, которого нет в knowledge. Подставленная сумма ступени торга там
отсутствует по определению (промежуточные ступени внутренние), и редакция
съела бы её как необеспеченную — а вместе с ней и весь смысл ответа.

Следствие §8.3: ответ, где плейсхолдер остался неподставленным, подавляется
ЦЕЛИКОМ. Полуотрендеренные реквизиты клиенту уходить не должны.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

KYIV_TZ = ZoneInfo("Europe/Kyiv")

# Плейсхолдер — служебная форма: заглавные латинские буквы и подчёркивания.
# Узко намеренно: «{смайл}» в тексте лида не имеет права подавить ответ.
PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z_]*)\}")

# Что Ф0 умеет подставлять. Набор закрытый: новое имя обязано быть объявлено
# здесь вместе со своим источником, а не появиться строкой на месте вызова.
PLACEHOLDERS: tuple[str, ...] = ("AMOUNT", "REQUISITES", "DUE")

# Статусы, при которых счёт в промпте не нужен: закрытые деньгами или решением
# человека. Оплаченный счёт в блоке — это напоминание об уже оплаченном.
_SILENT_STATUSES = frozenset({"paid", "cancelled", "refunded", "refund_requested"})

# Предлог вшит в название дня НАМЕРЕННО: «в вторник» вместо «во вторник» — это
# текст, который читает клиент. Собирать предлог правилом пришлось бы под каждый
# язык отдельно, а список из семи строк не врёт никогда.
_WEEKDAYS = {
    "uk": ("у понеділок", "у вівторок", "у середу", "у четвер",
           "у пʼятницю", "у суботу", "у неділю"),
    "ru": ("в понедельник", "во вторник", "в среду", "в четверг",
           "в пятницу", "в субботу", "в воскресенье"),
    "en": ("on Monday", "on Tuesday", "on Wednesday", "on Thursday",
           "on Friday", "on Saturday", "on Sunday"),
}
_MONTHS = {
    "uk": ("січня", "лютого", "березня", "квітня", "травня", "червня",
           "липня", "серпня", "вересня", "жовтня", "листопада", "грудня"),
    "ru": ("января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"),
    "en": ("January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"),
}
_DUE_PATTERN = {
    "uk": "{time} {weekday}, {day} {month}",
    "ru": "{time} {weekday}, {day} {month}",
    "en": "{time} {weekday}, {month} {day}",
}


class UnsubstitutedPlaceholder(Exception):
    """Плейсхолдер остался без значения — ответ подавляется целиком (§8.3)."""


def find_placeholders(text: str) -> list[str]:
    """Имена плейсхолдеров в порядке появления, без дублей."""
    out: list[str] = []
    for m in PLACEHOLDER_RE.finditer(text or ""):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def missing_values(text: str, values: Mapping[str, str]) -> list[str]:
    """Плейсхолдеры, которым нечего подставить.

    Считается по тексту ДО подстановки — иначе реквизиты с фигурной скобкой
    внутри роняли бы каждый ответ, а значение из конфига могло бы подставить
    себя второй раз. Неизвестное имя (модель придумала `{PHONE}`) тоже здесь:
    «почти отрендеренный» ответ хуже, чем никакой."""
    return [name for name in find_placeholders(text)
            if not str(values.get(name, "")).strip()]


def substitute(text: str, values: Mapping[str, str]) -> str:
    """Один проход. Подставленное значение повторно НЕ сканируется — иначе
    строка из конфига могла бы выступить как шаблон."""
    return PLACEHOLDER_RE.sub(
        lambda m: str(values.get(m.group(1), m.group(0))), text or "")


def finalize(text: str, values: Mapping[str, str]) -> str:
    """Текст для отправки лиду. Зовётся ПОСЛЕ guardrails.

    `UnsubstitutedPlaceholder` — это «не отправлять ничего», а не «отправить
    как есть»: полуотрендеренные реквизиты хуже молчания."""
    missing = missing_values(text, values)
    if missing:
        raise UnsubstitutedPlaceholder(
            "нечем подставить: " + ", ".join(missing))
    return substitute(text, values)


def format_due(due_ts: float | None, *, language: str = "uk",
               tz: ZoneInfo = KYIV_TZ) -> str:
    """Срок С ЧАСОМ (§8.4, требование владельца): «18:00 у четвер, 14 серпня».

    Пустая строка вместо срока дала бы «оплатіть до .» в живом диалоге, поэтому
    отсутствие момента — исключение того же рода, что нехватка плейсхолдера."""
    if due_ts is None:
        raise UnsubstitutedPlaceholder("срок оплаты не задан — подставить нечего")
    lang = language if language in _WEEKDAYS else "uk"
    dt = datetime.fromtimestamp(float(due_ts), tz=timezone.utc).astimezone(tz)
    return _DUE_PATTERN[lang].format(
        time=dt.strftime("%H:%M"),
        weekday=_WEEKDAYS[lang][dt.weekday()],
        day=dt.day,
        month=_MONTHS[lang][dt.month - 1])


def pick_open_invoice(rows: Sequence[Mapping]) -> Mapping | None:
    """Счёт, о котором модель обязана знать: последний незакрытый.

    Ф0 держит не больше одного открытого счёта на контакт, но выбор «последний»
    объявлен здесь, а не подразумевается: в Ф1 их до трёх (`per_contact_invoice_cap`),
    и правило должно быть уже названным, а не сочинённым тогда."""
    open_rows = [r for r in rows or () if r.get("status") not in _SILENT_STATUSES]
    return open_rows[-1] if open_rows else None


def render_invoice_block(invoice: Mapping | None, *, now: float) -> str:
    """Блок счёта для brain — ОТДЕЛЬНЫЙ от слота обязательств и ПОСЛЕ
    cache-breakpoint'а (§14 п.13).

    Отдельный, потому что `render_slot_block` инъектит только `owed_by=bot`, а
    долг оплаты — `owed_by=client`: без этого блока модель о неоплаченном счёте
    не узнаёт вовсе. После breakpoint'а, потому что статус счёта меняется, а
    изменчивое в стабильном префиксе убивает кэш (регрессия 23.07).

    Ни одной цифры: ни суммы, ни реквизитов, ни номера счёта (§8.5 п.3). Модель
    получает ФАКТ долга и служебные плейсхолдеры, а числа подставит код."""
    if invoice is None or invoice.get("status") in _SILENT_STATUSES:
        return ""

    head = "=== РАХУНОК КЛІЄНТА (борг на КЛІЄНТІ, не на тобі) ==="
    if invoice.get("status") == "awaiting_owner" or invoice.get("amount_total") is None:
        # Сумму ещё не подтвердила владелица: обещать срок не с чего, называть
        # сумму нечем. Единственная честная реплика — «уточнюю з керівницею».
        return (f"{head}\n"
                "Суму ще не підтверджено керівницею. Не називай жодних цифр і не "
                "обіцяй строк: чесна відповідь — що ти уточнюєш суму з керівницею.")

    return (f"{head}\n"
            "Рахунок уже виставлено, оплати ще немає. Нагадати можна мʼяко, один раз.\n"
            "Цифри писати ЗАБОРОНЕНО. Замість них службові підстановки, які підставить "
            "код: сума — {AMOUNT}, реквізити — {REQUISITES}, строк оплати — {DUE}. "
            "Пиши їх дослівно, у фігурних дужках, і нічого всередині не міняй.")
