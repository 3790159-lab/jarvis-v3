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
from pathlib import Path

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
    # Строка списана с живого лога БУКВАЛЬНО (`состав` строчными, `(флаг)`):
    # scripts/chatter_guardian_detached.ps1:145 пишет именно так, и фикстура,
    # «примерно похожая» на живую строку, проверяет не то, что приезжает в ленту.
    "2026-08-14 00:44:08 | состав: CHATTER_PERSONAS=volska (флаг), db=.secrets\\demo.db",
    # Ниже — виды, которых не было в окне лога за 14.08, но которые есть в
    # scripts/chatter_guardian_detached.ps1 и без них молча выпадали из ленты.
    "2026-08-13 03:00:00 | another chatter guardian already running (PID 5724) - exiting",  # L133: гонка двух гардианов
    "2026-08-13 03:00:00 | Stop-OldRunner: runner still alive after 10s - NOT starting new (retry next cycle)",  # L210
    "2026-08-13 03:00:00 | Start-Runner: old runner still alive - aborting launch (never start on top of a live session)",  # L219
    "2026-08-13 03:00:00 | Start-Runner: Start-Process FAILED: Access is denied",  # L232: раннер вообще не запустился
    "2026-08-13 03:00:00 | Start-Runner: launch returned no process handle - treating as FAILED",  # L238
    "2026-08-13 03:00:00 | Invoke-WatchCheck FAILED: [Errno 10061] Connection refused",  # L166: алертер сдох молча
    "2026-08-13 03:00:00 | ДЕМО ЯРИНЫ: стоят ОБА флага - поднимаю yarina, Ольга НЕ работает",  # L85/146
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
    line = "2026-08-14 00:44:08 | состав: CHATTER_PERSONAS=volska (флаг), db=.secrets\\demo.db"
    assert F.is_decision(line)


def test_noise_outranks_a_decision_word_in_the_same_line():
    """Сторож ПОРЯДКА проверок. Строка синтетическая: в живом логе гардиана
    ни одна строка не содержит одновременно дебаунс-шум и слово решения, и
    именно поэтому три теста `is_decision` выше (по KEEP, по DROP, по строке
    «Состав») порядок НЕ различают — переставь проверки местами, и они
    останутся зелёными.

    Правило, которое здесь закрепляется: шум ПЕРЕБИВАЕТ решение. Строка,
    сообщающая о дребезге вокруг перезапуска, не является событием, даже
    если в ней стоит слово из маски решений."""
    assert not F.is_decision(
        "2026-08-14 00:44:08 | runner DOWN - debouncing, not relaunching yet")


def test_debounce_noise_survives_even_though_uppercase_failed_is_now_a_decision_word():
    """`FAILED` заглавными стал маркером решения (Start-Process/Invoke-WatchCheck
    FAILED), а дебаунсная строка пишет `failed` строчными — регистр здесь
    несущий. Общий цикл по DROP это гарантирует молча; этот тест — прицельно,
    чтобы правку регистра нельзя было провести незаметно."""
    assert not F.is_decision(
        "2026-08-14 00:44:08 | runner check failed (1/3) - debouncing, not relaunching yet")


def test_is_decision_matches_the_garbage_contract_of_its_neighbour():
    """Парный к `test_garbage_input_never_raises`. `parse_log_ts` и
    `is_decision` разбирают ОДНИ И ТЕ ЖЕ строки одного источника, и разное
    поведение на негодном входе — ловушка для того, кто позовёт их рядом."""
    assert F.is_decision(None) is False
    assert F.parse_log_ts(None) is None


def test_a_utf8_log_is_read_as_utf8(tmp_path):
    """Парный сторож к фолбэку. cp1251 отображает 255 байт из 256, то есть на
    живом логе не споткнётся никогда, — поставь его первым, и нормальный utf-8
    молча станет мусором, причём выглядеть это будет как «так и было в логе»."""
    p = tmp_path / "g.log"
    p.write_text("2026-08-14 00:45:08 | Состав: CHATTER_PERSONAS=volska\n",
                 encoding="utf-8")
    lines, truncated = F.read_tail(p)
    assert truncated is False
    assert "Состав: CHATTER_PERSONAS=volska" in lines[0]


def test_a_cp1251_log_is_still_readable(tmp_path):
    """Сегодня гардиан пишет `Add-Content` БЕЗ `-Encoding`, то есть в
    системную cp1251 (проверено 14.08: живой лог целиком в utf-8 не
    декодируется). `-Encoding utf8` из Task 6 починит только БУДУЩИЕ строки,
    прошлое чинится фолбэком при чтении — иначе строка, называющая активную
    базу, остаётся нечитаемой навсегда."""
    p = tmp_path / "g.log"
    p.write_bytes("2026-08-14 00:40:00 | Состав: CHATTER_PERSONAS=volska, db=.secrets\\demo.db\n"
                  .encode("cp1251"))
    lines, truncated = F.read_tail(p)
    assert "Состав: CHATTER_PERSONAS=volska" in lines[0], lines[0]
    assert "�" not in lines[0], "фолбэк не сработал, строка испорчена"


def test_a_log_longer_than_the_window_is_read_from_the_end(tmp_path):
    """Ротации у гардиана нет — лог растёт вечно, и чтение целиком дорожает
    каждый день. Читаем хвост и ГОВОРИМ, что файл длиннее окна."""
    p = tmp_path / "g.log"
    p.write_text("".join(f"2026-08-14 00:{i % 60:02d}:00 | строка {i}\n"
                         for i in range(4000)), encoding="utf-8")
    lines, truncated = F.read_tail(p, limit=2048)
    assert truncated is True
    # Прочитано НЕ БОЛЬШЕ ОКНА — это и есть предмет задачи. Прежняя проверка
    # `len(lines) < 4000` пропускала слурп целиком: без `seek` возвращается
    # 3999 строк (обрубок первой съедает ровно одну), и порог зеленел, отличая
    # 3999 от 4000 тем же способом, что 49 от 4000.
    assert sum(len(ln) + 1 for ln in lines) <= 2048, f"прочитано {len(lines)} строк"
    assert "строка 3999" in lines[-1]


def test_the_window_stays_a_window_when_the_guardian_writes_mid_read(tmp_path, monkeypatch):
    """Гардиан пишет в этот лог ЖИВОЙ — дописывание между `stat()` и `read()`
    не гипотеза, а обычный ход дел раз в 30 секунд. `f.read()` без аргумента
    читает до текущего EOF, то есть тащит всё дописанное СВЕРХ окна, и окно
    держится только на медленности писателя.

    Дописывание вклинено в сам `stat()` — так момент воспроизводится точно, а
    не «повезло попасть в гонку»."""
    p = tmp_path / "g.log"
    p.write_bytes(b"".join(f"2026-08-14 00:00:00 | строка {i}\n".encode("utf-8")
                           for i in range(200)))
    real_stat = Path.stat

    def stat_then_guardian_writes(self, *a, **kw):
        st = real_stat(self, *a, **kw)
        if self == p:
            with open(p, "ab") as f:
                f.write("2026-08-14 00:00:01 | дописано гардианом\n"
                        .encode("utf-8") * 800)
        return st

    monkeypatch.setattr(Path, "stat", stat_then_guardian_writes)
    lines, truncated = F.read_tail(p, limit=1024)
    assert truncated is True
    assert sum(len(ln) + 1 for ln in lines) <= 1024, f"прочитано {len(lines)} строк сверх окна"


def test_the_first_partial_line_of_the_window_is_dropped(tmp_path):
    """Срез по байтам рассекает строку посередине. Обрубок в ленте выглядит
    как настоящее событие с потерянным началом."""
    p = tmp_path / "g.log"
    p.write_text("A" * 3000 + "\n2026-08-14 00:45:08 | runner DOWN - restarting\n",
                 encoding="utf-8")
    lines, truncated = F.read_tail(p, limit=1024)
    assert truncated is True
    assert not any(set(line) == {"A"} for line in lines), lines


def test_a_utf8_log_survives_a_window_that_cuts_a_character_in_half(tmp_path):
    """Срез по байтам рассекает не только СТРОКУ, но и многобайтовый СИМВОЛ.
    На таком обрывке `raw.decode("utf-8")` бросает UnicodeDecodeError — и
    фолбэк уводит в cp1251 весь здоровый utf-8 блок: из-за одного разрубленного
    байта в начале окна кракозябрами приезжает ВСЯ лента, включая строку с
    именем активной базы. Фолбэк, задуманный как лечение прошлого, сам
    становится источником порчи настоящего.

    Тот же дефект в проде выглядел бы «плавающим»: попадёт граница окна на
    середину кириллицы — лента испорчена, не попадёт — цела."""
    tail = "2026-08-14 00:45:08 | Состав: CHATTER_PERSONAS=volska\n"
    data = ("я" * 1000).encode("utf-8") + b"\n" + tail.encode("utf-8")
    p = tmp_path / "g.log"
    p.write_bytes(data)
    # 1001 — НЕЧЁТНОЕ смещение внутри ряда двухбайтовых «я»: окно заведомо
    # начинается с половины символа.
    lines, truncated = F.read_tail(p, limit=len(data) - 1001)
    assert truncated is True
    assert "Состав: CHATTER_PERSONAS=volska" in lines[-1], lines[-1]


def test_a_byte_cp1251_cannot_decode_does_not_kill_the_page(tmp_path):
    """«cp1251 никогда не бросает» — почти правда: единственный неопределённый
    в ней байт `0x98`. Без `errors="replace"` одна такая байта в логе роняет
    UnicodeDecodeError наружу из read_tail, то есть всю страницу панели."""
    p = tmp_path / "g.log"
    p.write_bytes(b"2026-08-14 00:00:00 | runner DOWN\n\x98\n2026-08-14 00:00:01 | launched\n")
    lines, _ = F.read_tail(p)
    assert len(lines) == 3, lines


def test_a_bom_never_reaches_the_first_line(tmp_path):
    """Task 6 ставит гардиану `-Encoding utf8`, а PowerShell 5.1 под этим
    именем пишет utf-8 ИМЕННО С BOM. Незамеченный U+FEFF приклеивается к
    первому символу первой строки — `parse_log_ts` на ней вернёт None, и самая
    свежая запись ленты встанет с прочерком вместо времени."""
    p = tmp_path / "g.log"
    p.write_bytes(b"\xef\xbb\xbf2026-08-14 00:45:08 | runner DOWN - restarting\n")
    lines, _ = F.read_tail(p)
    assert not lines[0].startswith("﻿"), repr(lines[0])
    assert F.parse_log_ts(lines[0]) is not None, repr(lines[0])


def test_a_window_that_caught_no_line_break_is_empty_not_garbage(tmp_path):
    """Одна строка длиннее всего окна: целых строк в нём нет ни одной.
    Честный пустой список лучше обрубка, который в ленте неотличим от
    настоящего события с потерянным началом."""
    p = tmp_path / "g.log"
    p.write_bytes(b"A" * 4000)
    assert F.read_tail(p, limit=1024) == ([], True)


def test_a_file_exactly_the_size_of_the_window_keeps_its_first_line(tmp_path):
    """Граница `>` против `>=`. При `size == limit` в окно попала ПОЛНАЯ
    первая строка, и срез обрубка съел бы настоящее событие."""
    p = tmp_path / "g.log"
    body = b"2026-08-14 00:45:08 | runner DOWN - restarting\n"
    p.write_bytes(body + b"x" * (1024 - len(body)))
    lines, truncated = F.read_tail(p, limit=1024)
    assert truncated is False
    assert lines[0] == "2026-08-14 00:45:08 | runner DOWN - restarting"

    p2 = tmp_path / "g2.log"
    p2.write_bytes(body + b"x" * (1025 - len(body)))
    assert F.read_tail(p2, limit=1024)[1] is True


def test_a_missing_log_is_not_an_exception(tmp_path):
    """Лог может отсутствовать на машине без chatter. Это пустая лента, а не
    падение страницы."""
    assert F.read_tail(tmp_path / "нет-такого.log") == ([], False)
