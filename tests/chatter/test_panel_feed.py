"""Лента панели Джарвиса: время, фильтр решений, окно чтения, выбор базы.

Четыре дефекта, найденные на ЖИВЫХ данных 14.08 (спека
docs/superpowers/specs/2026-08-14-jarvis-panel-event-journal.md, §5):
  1. база бралась литералом `.secrets/demo.db`, а раннер выводит её из
     первичного slug'а в active.yaml — при смене состава панель читала не ту
     базу и печатала «тихо»;
  2. время строк гардиана не разбиралось вовсе (`ts: None`);
  3. `read_text()` слурпал весь растущий лог ради последних 40 строк;
  4. 163 из 421 строки лога — дебаунс-шум сторожа, он же съедал окно.

Стенд намеренно кормит функции УРОДЛИВЫМИ данными: смешанные кодировки,
строки без префикса, лог длиннее окна. Опрятный стенд зеленел бы, ничего не
проверив, — весь дефект именно в уродливом.
"""
from __future__ import annotations

import time

from app.services import jarvis_farm as F


def test_the_guardian_timestamp_is_read_as_local_time():
    """Время в логе ЛОКАЛЬНОЕ и без зоны. Принять его за UTC значит сдвинуть
    события на три часа и получить «события из будущего» — поэтому сверяем
    круговым разбором через localtime, а не сравнением с константой."""
    ts = F.parse_log_ts("2026-08-14 00:45:08 | runner DOWN - restarting")
    assert ts is not None
    assert time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) == "2026-08-14 00:45:08"


def test_a_line_without_a_timestamp_gets_none_not_a_guess():
    """Прочерк честнее выдуманного времени: строка без префикса встанет в
    ленте с «—», а не притворится свежей."""
    assert F.parse_log_ts("runner DOWN - restarting") is None
    assert F.parse_log_ts("") is None
    assert F.parse_log_ts("2026-13-45 99:99:99 | битая дата") is None


def test_garbage_input_never_raises():
    """Год вне диапазона эпохи валит `time.mktime` `OverflowError`'ом — эта
    ветка не была накрыта. `None` на входе (сегодня недостижим, но контракт
    функции — «на любом мусоре None, не исключение») туда же."""
    assert F.parse_log_ts("9999-12-31 23:59:59 | x") is None
    assert F.parse_log_ts(None) is None


# Реальные виды строк из logs/chatter_guardian.stdout.log с их частотой на
# 14.08. Тест держит РЕШЕНИЕ по каждому виду, а не абстрактный «фильтр».
KEEP = [
    "2026-08-14 00:45:08 | runner DOWN - restarting",
    "2026-08-14 00:45:09 | launched chatter runner (PID 6864) -> C:\\jarvis\\logs\\chatter_volska.log",
    "2026-08-13 21:04:11 | chatter guardian started (PID 5724), heartbeat<=180s every 30s, debounce=3",
    "2026-08-12 09:10:00 | runner heartbeat NOT fresh after 45s - will retry next cycle",
    "2026-08-14 00:40:00 | Состав: CHATTER_PERSONAS=volska (файл), db=.secrets\\demo.db",
]
DROP = [
    "2026-08-14 00:44:08 | runner check failed (1/3) - debouncing, not relaunching yet",
    "2026-08-14 00:44:38 | runner check failed (2/3) - debouncing, not relaunching yet",
    "2026-08-14 00:45:10 | runner heartbeat fresh after ~0s",
    "2026-08-13 22:00:00 | runner alive",
]


def test_decisions_of_the_guardian_reach_the_feed():
    for line in KEEP:
        assert F.is_decision(line), f"решение выброшено из ленты: {line}"


def test_the_guardians_own_debounce_noise_never_reaches_the_feed():
    """163 из 421 строки лога — дебаунс. Он и съедал окно: настоящие
    DOWN/launched вытеснялись собственным шумом сторожа."""
    for line in DROP:
        assert not F.is_decision(line), f"шум попал в ленту: {line}"


def test_the_composition_line_survives_because_it_names_the_database():
    """Парный к дефекту выбора базы: строка «Состав: … db=…» — единственное
    место, где лог прямо называет активную базу. Выбросить её значит оставить
    слепое пятно ровно там, где мы его чиним."""
    line = "2026-08-14 00:40:00 | Состав: CHATTER_PERSONAS=volska (файл), db=.secrets\\demo.db"
    assert F.is_decision(line)


def test_noise_outranks_a_decision_word_in_the_same_line():
    """Сторож ПОРЯДКА проверок. Строка синтетическая: в живом логе гардиана
    ни одна строка не содержит одновременно дебаунс-шум и слово решения, и
    именно поэтому шесть тестов выше порядок НЕ различают — переставь
    проверки местами, и они останутся зелёными.

    Правило, которое здесь закрепляется: шум ПЕРЕБИВАЕТ решение. Строка,
    сообщающая о дребезге вокруг перезапуска, не является событием, даже
    если в ней стоит слово из маски решений."""
    assert not F.is_decision(
        "2026-08-14 00:44:08 | runner DOWN - debouncing, not relaunching yet")
