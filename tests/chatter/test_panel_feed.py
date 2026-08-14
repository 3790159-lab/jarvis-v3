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


def test_a_log_where_both_encodings_live_keeps_the_NEWEST_lines_readable(tmp_path):
    """Смешанный файл — не гипотеза, а ровно то, что делает Task 6: он ставит
    гардиану `-Encoding utf8`, и с этой минуты в ОДНОМ файле лежит cp1251-прошлое
    и utf-8-будущее.

    Фолбэк на ВЕСЬ блок в такой день ломает самое ценное: одна старая cp1251-байта
    роняет `decode("utf-8")`, и в cp1251 уезжают СВЕЖИЕ строки, а не старые.
    Поэтому декодируем ПОСТРОЧНО. Порядок «utf-8 первым» при этом не меняется —
    cp1251 отображает 255 байт из 256 и молча испортил бы нормальный utf-8."""
    p = tmp_path / "g.log"
    p.write_bytes(
        "2026-08-14 00:40:00 | состав: CHATTER_PERSONAS=yarina, db=по первому слагу\n"
        .encode("cp1251")
        + "2026-08-14 00:45:00 | состав: CHATTER_PERSONAS=volska, db=по первому слагу\n"
        .encode("utf-8"))
    lines, _ = F.read_tail(p)
    assert "состав: CHATTER_PERSONAS=yarina" in lines[0], lines[0]
    assert "состав: CHATTER_PERSONAS=volska" in lines[1], lines[1]


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


# ───────────── какую базу читает лента: лестница «факт → догадка» ────────────
#
# Панель и раннер — СИБЛИНГИ, а не родня: раннера поднимает
# chatter_guardian_detached.ps1, панель живёт в run_backend_detached.py под
# ДРУГИМ гардианом. Общего окружения у них нет вовсе (проверено 14.08 на живых
# PID: у раннера CHATTER_PERSONAS='volska' и CHATTER_DB='.secrets\demo.db', у
# панели обе переменные None, в реестре USER/MACHINE их тоже нет).
#
# Поэтому стенд ниже подсовывает ФАЛЬШИВУЮ таблицу процессов и фальшивый
# `environ`. Спрашивать живые процессы машины тестам нельзя: на ней прямо
# сейчас крутится раннер volska, и сторож зеленел бы или краснел от того, что
# сегодня в проде, а не от кода.

PY = r"C:\jarvis\.venv\Scripts\python.exe"


def _clients_dir(tmp_path, *slugs):
    """Склад клиентов в том же виде, в каком его читает раннер: `clients:` со
    списком слагов, ПЕРВЫЙ — первичный."""
    d = tmp_path / "chatter" / "clients"
    d.mkdir(parents=True)
    (d / "active.yaml").write_text(
        "clients:\n" + "".join(f"  - {s}\n" for s in slugs), encoding="utf-8")
    return d


def _guardian_log(tmp_path, text: str):
    """Лог гардиана В ПОДМЕНЁННОМ ROOT. Живой лог фермы тесты не читают: он
    меняется каждые 30 секунд, и сторож на нём проверял бы прод, а не код."""
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    p = logs / "chatter_guardian.stdout.log"
    p.write_text(text, encoding="utf-8")
    return p


def _runner_row(pid):
    """Строка таблицы процессов ровно в форме `_proc_table` — живой раннер,
    поднятый модулем: scripts/chatter_guardian_detached.ps1:226 запускает его
    как `-u -m chatter.telethon_run --llm real`."""
    return (pid, "python.exe", [PY, "-u", "-m", "chatter.telethon_run",
                                "--llm", "real"], 1.0, 1)


def _environs(monkeypatch, by_pid):
    """Подмена `psutil.Process(pid).environ()`: словарь отдаётся, исключение
    бросается. Проверено на живых PID фермы, что настоящий `environ()` на этой
    машине работает без повышения прав, — но AccessDenied остаётся законным
    ответом Windows на чужой процесс, и он тоже разыгрывается здесь."""
    import psutil

    class _FakeProcess:
        def __init__(self, pid):
            self._pid = pid

        def environ(self):
            answer = by_pid[self._pid]
            if isinstance(answer, Exception):
                raise answer
            return answer

    monkeypatch.setattr(psutil, "Process", _FakeProcess)


def _bare_panel_env(monkeypatch):
    """Окружение панели в проде: ни одной из трёх переменных в нём нет."""
    for var in ("TAMAPI_DB", "CHATTER_DB", "CHATTER_PERSONAS"):
        monkeypatch.delenv(var, raising=False)


# ─────────────────────── шаг 2: ЖИВОЙ раннер — факт ──────────────────────────

def test_the_live_runner_outranks_the_clients_file(tmp_path, monkeypatch):
    """Источник истины — тот процесс, который базу и обслуживает. Файл состава
    говорит, что раннер прочитал БЫ, если бы стартовал сейчас; живой раннер
    стартовал вчера и с тех пор мог быть запущен с чем угодно."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    _environs(monkeypatch, {6864: {"CHATTER_DB": r".secrets\yarina.db"}})
    path, note = F.client_db_path([_runner_row(6864)])
    assert note == ""
    # Путь раннера ОТНОСИТЕЛЬНЫЙ и живёт в его системе координат (cwd C:\jarvis);
    # cwd панели может быть любым, поэтому сверяем абсолютный.
    assert path == str(tmp_path / ".secrets" / "yarina.db"), path


def test_a_live_runner_without_an_explicit_db_is_asked_about_its_personas(tmp_path, monkeypatch):
    """У раннера в проде CHATTER_DB задан не всегда — тогда он выводит базу из
    первичного slug'а сам, и панель обязана вывести её тем же способом."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    _environs(monkeypatch, {6864: {"CHATTER_PERSONAS": "volska"}})
    path, note = F.client_db_path([_runner_row(6864)])
    assert note == ""
    assert path == str(tmp_path / ".secrets" / "volska.db"), path


def test_the_yarina_demo_scenario_reads_the_database_the_runner_serves(tmp_path, monkeypatch):
    """ТОТ САМЫЙ дефект, ради которого функция переписана. 14.08 раннер
    обслуживал `yarina`, а `active.yaml` называл `demo` — и панель показывала
    ленту demo.db, выдавая её за текущую. Проверено фактом: в `.secrets/demo.db`
    35 control_events, в `.secrets/yarina.db` — ровно ноль."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    _environs(monkeypatch, {6864: {"CHATTER_PERSONAS": "yarina"}})
    path, note = F.client_db_path([_runner_row(6864)])
    assert note == "", note
    assert path == str(tmp_path / ".secrets" / "yarina.db"), path


def test_the_panel_derives_the_file_exactly_as_the_runner_does(tmp_path, monkeypatch):
    """Паритет со схемой имён РАННЕРА, а не с докстрингом о ней. Пока равенство
    держалось только словами, переименование `.secrets/<slug>.db` в
    `chatter/telethon_run.py` молча вернуло бы дефект: панель продолжила бы
    читать файл, которого раннер больше не пишет.

    `secrets_dir` НЕ передаётся — и это правка по факту. С явным аргументом
    `derive_db_path` на свой `SECRETS_DIR` не смотрит вовсе, то есть сторож был
    слеп ровно к половине схемы: смена `.secrets` на `state` в раннере проходила
    незамеченной (проверено мутацией самого раннера). Сверяем ОТНОСИТЕЛЬНУЮ
    часть — каталог и имя разом, — а абсолютной её делает то, что cwd раннера =
    ROOT (`chatter_guardian_detached.ps1:229`, `-WorkingDirectory $Root`).

    Импорт внутри теста: `chatter.telethon_run` тянет telethon и половину
    chatter — платить за это на сборе всего файла ленты незачем."""
    from chatter.telethon_run import derive_db_path

    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    _environs(monkeypatch, {6864: {"CHATTER_PERSONAS": "yarina"}})
    path, _ = F.client_db_path([_runner_row(6864)])
    assert Path(path).relative_to(tmp_path) == Path(derive_db_path("yarina")), path


def test_two_runners_that_disagree_are_named_an_accident(tmp_path, monkeypatch):
    """ДВА PID у раннера — норма фермы (лаунчер + сам сервис), и одинаковый
    ответ обоих ничего не значит. А вот РАЗНЫЙ означает, что живут две сессии
    на разных базах: половина ленты будет не о том клиенте, и молчать об этом
    нельзя."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    _environs(monkeypatch, {6864: {"CHATTER_PERSONAS": "yarina"},
                            9456: {"CHATTER_PERSONAS": "volska"}})
    path, note = F.client_db_path([_runner_row(6864), _runner_row(9456)])
    assert path == str(tmp_path / ".secrets" / "yarina.db"), path
    assert note, "расхождение двух раннеров проглочено"
    assert "yarina.db" in note and "volska.db" in note, note


def test_two_runners_that_agree_are_not_an_accident(tmp_path, monkeypatch):
    """Парный сторож: два PID одного сервиса — обычный день фермы. Красить его
    аварией значит приучить читать пояснение как шум."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    _environs(monkeypatch, {6864: {"CHATTER_PERSONAS": "volska"},
                            9456: {"CHATTER_PERSONAS": "volska"}})
    path, note = F.client_db_path([_runner_row(6864), _runner_row(9456)])
    assert note == "", note
    assert path == str(tmp_path / ".secrets" / "volska.db"), path


def test_a_runner_we_may_not_question_says_so_instead_of_going_quiet(tmp_path, monkeypatch):
    """DEV-18: `environ()` вправе бросить AccessDenied. Ответ при этом даётся —
    из active.yaml, — но он ДОГАДКА, и разница между «спросил раннера» и «не
    смог спросить» обязана быть видна в ленте."""
    import psutil

    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    _environs(monkeypatch, {6864: psutil.AccessDenied(6864)})
    path, note = F.client_db_path([_runner_row(6864)])
    assert path == str(tmp_path / ".secrets" / "demo.db"), path
    assert "AccessDenied" in note, note
    assert "жив" in note, note


def test_the_live_runner_outranks_the_guardian_log(tmp_path, monkeypatch):
    """Сторож ПОРЯДКА ступеней 2 и 3. Ни один тест не подавал оба источника
    разом: живой раннер проверялся при пустом логе, лог — при пустой таблице
    процессов, — и перестановка двух веток местами оставляла всё зелёным.

    Правило: ЖИВОЙ раннер — сегодняшний факт, лог гардиана — вчерашний. Лог
    называет состав ТОЛЬКО на старте гардиана, а раннера с тех пор могли
    перезапустить с чем угодно."""
    _clients_dir(tmp_path, "demo")
    _guardian_log(tmp_path,
                  "2026-08-14 00:44:08 | состав: CHATTER_PERSONAS=volska (флаг), "
                  "db=.secrets\\volska.db\n")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    _environs(monkeypatch, {6864: {"CHATTER_PERSONAS": "yarina"}})
    path, note = F.client_db_path([_runner_row(6864)])
    assert path == str(tmp_path / ".secrets" / "yarina.db"), path
    assert note == "", note


def test_the_runners_own_db_variable_outranks_its_personas(tmp_path, monkeypatch):
    """Приоритет ВНУТРИ окружения раннера: CHATTER_DB > CHATTER_PERSONAS —
    ровно как у него самого (`resolve_runtime_paths`: явный флаг > CHATTER_DB >
    вывод из slug'а). Не гипотеза: живой раннер на этой машине держит ОБЕ разом
    и они противоречат друг другу (CHATTER_DB='.secrets\\demo.db',
    CHATTER_PERSONAS='volska'). Ни один сторож их вместе не выставлял, и
    перестановка веток проходила незамеченной."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    _environs(monkeypatch, {6864: {"CHATTER_DB": r".secrets\demo.db",
                                   "CHATTER_PERSONAS": "volska"}})
    path, note = F.client_db_path([_runner_row(6864)])
    assert note == "", note
    # Полный путь, а не хвост: под `.endswith` пролезло бы и `volska.db`, если
    # бы каталог отличался, — то есть ровно тот дефект, который тут сторожат.
    assert path == str(tmp_path / ".secrets" / "demo.db"), path


def test_a_runner_that_names_no_database_says_so_instead_of_guessing_quietly(
        tmp_path, monkeypatch):
    """Ветка «раннер жив, но базу в своём окружении не называет» не была накрыта
    ничем: подмена её на тихую догадку `_db_from_slug("demo"), ""` оставляла все
    сторожа зелёными. А разница именно в пояснении: ответ при этом ДАЁТСЯ, и
    только слова отличают «спросили раннера» от «раннер не ответил»."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    _environs(monkeypatch, {6864: {}})
    path, note = F.client_db_path([_runner_row(6864)])
    assert path == str(tmp_path / ".secrets" / "demo.db"), path
    assert note, "догадка выдана за ответ живого раннера"
    assert "жив" in note and "не называет" in note, note


def test_a_panel_without_psutil_says_so_instead_of_going_quiet(tmp_path, monkeypatch):
    """Импорт psutil здесь ЛЕНИВЫЙ, чтобы панель не умирала без него, — но
    молча съесть ступень 2 значит выдать догадку из active.yaml за опрос живого
    раннера. Стирание этого пояснения в `""` не красило ни один тест."""
    import sys

    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    monkeypatch.setitem(sys.modules, "psutil", None)   # ImportError на `import psutil`
    path, note = F.client_db_path([_runner_row(6864)])
    assert path == str(tmp_path / ".secrets" / "demo.db"), path
    assert "psutil" in note, note


def test_a_runner_that_vanished_mid_question_is_not_called_alive(tmp_path, monkeypatch):
    """Гонка ШТАТНАЯ, а не редкая: между обходом процессов и `environ()`
    гардиан вправе перезапустить раннер (14.08 он сделал это за 61 с). Старый
    разбор сваливал `NoSuchProcess` в ту же кучу, что `AccessDenied`, и панель
    сообщала «раннер жив, но его окружение не прочитать» о процессе, которого
    в этот момент уже не было. Два разных события — два разных слова."""
    import psutil

    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    _environs(monkeypatch, {6864: psutil.NoSuchProcess(6864)})
    path, note = F.client_db_path([_runner_row(6864)])
    assert path == str(tmp_path / ".secrets" / "demo.db"), path
    assert "исчез" in note, note
    assert "жив" not in note, note


def test_an_empty_first_persona_of_the_runner_is_skipped_like_the_runner_skips_it(
        tmp_path, monkeypatch):
    """`chatter.config.active.resolve_personas` пустые куски ОТБРАСЫВАЕТ
    (`if s.strip()`), то есть при `CHATTER_PERSONAS=',volska'` раннер обслуживает
    volska.db. Панель же брала `split(",")[0]` сырым, получала пустой slug и
    говорила «раннер жив, но базу в своём окружении не называет» — пояснение,
    которое ЛОЖНО: раннер её называет, читать не умели мы."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    _environs(monkeypatch, {6864: {"CHATTER_PERSONAS": ",volska"}})
    path, note = F.client_db_path([_runner_row(6864)])
    assert note == "", note
    assert path == str(tmp_path / ".secrets" / "volska.db"), path


def test_a_drive_relative_path_from_the_runner_keeps_its_drive(tmp_path, monkeypatch):
    """`C:x.db` — путь ОТНОСИТЕЛЬНО текущего каталога диска C:, и Windows его
    принимает. `Path("C:x.db").is_absolute()` при этом False, поэтому прежняя
    домысливалка клеила его к ROOT и получала `<ROOT>\\C:x.db` — имя, которого
    не существует. Диск в пути есть — значит домысливать нечего."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    _environs(monkeypatch, {6864: {"CHATTER_DB": "C:x.db"}})
    path, note = F.client_db_path([_runner_row(6864)])
    assert note == "", note
    assert path == "C:x.db", path


def test_a_process_that_only_mentions_the_runner_is_not_asked(tmp_path, monkeypatch):
    """ГРАБЛЯ 1 файла: маркер внутри `-c` — разговор О раннере, а не раннер.
    Спросить окружение такого процесса значит взять базу у диагностического
    однострочника. Поиск идёт тем же `_launches`, что у `processes()`, — второго
    способа искать процессы в файле быть не должно."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    talker = (4242, "python.exe",
              [PY, "-c", "print('chatter.telethon_run is what we grep')"], 1.0, 1)
    _environs(monkeypatch, {4242: {"CHATTER_PERSONAS": "yarina"}})
    path, note = F.client_db_path([talker])
    assert path == str(tmp_path / ".secrets" / "demo.db"), path
    assert "раннер не запущен" in note, note


def test_only_a_python_process_is_asked_about_the_database(tmp_path, monkeypatch):
    """Фильтр «только python» держит ту же границу, что `py_only` в PROC_SPECS,
    и без сторожа снимался бесследно. Раннер — python-модуль (`-m
    chatter.telethon_run`); powershell-обёртка, у которой та же строка стоит в
    аргументах, ЗАПУСКАЕТ python, а не является им, и её окружение — это
    окружение гардиана, а не сессии клиента."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    wrapper = (5724, "powershell.exe",
               ["powershell.exe", "-NoProfile", PY, "-u", "-m", "chatter.telethon_run"],
               1.0, 1)
    _environs(monkeypatch, {5724: {"CHATTER_PERSONAS": "yarina"}})
    path, note = F.client_db_path([wrapper])
    assert path == str(tmp_path / ".secrets" / "demo.db"), path
    assert "раннер не запущен" in note, note


def test_the_process_list_and_the_question_look_for_the_same_runner():
    """Панель ВИДИТ раннера в `processes()` и СПРАШИВАЕТ его в
    `_db_from_live_runner` — по одному и тому же маркеру, и разъехаться им
    нельзя: с двумя разными литералами панель показала бы «chatter раннер: PID …»
    и рядом «раннер не запущен». Константа `CHATTER_RUNNER` заведена ровно
    против этого, но вернуть в PROC_SPECS литерал можно было бесследно."""
    markers = {key: marker for key, _label, marker, _py_only in F.PROC_SPECS}
    assert markers["chatter"] == F.CHATTER_RUNNER, markers


# ───────────────── шаг 3: раннера нет — что помнит лог гардиана ──────────────

def test_a_dead_runner_leaves_its_database_named_in_the_guardian_log(tmp_path, monkeypatch):
    """Раннер упал — но гардиан при старте записал, кого он поднимает
    (chatter_guardian_detached.ps1:145). Это вчерашний факт, и он вернее
    сегодняшней догадки по файлу состава."""
    _clients_dir(tmp_path, "demo")
    _guardian_log(tmp_path,
                  "2026-08-14 00:40:00 | chatter guardian started (PID 5724)\n"
                  "2026-08-14 00:44:08 | состав: CHATTER_PERSONAS=yarina (флаг), "
                  "db=.secrets\\yarina.db\n")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    path, note = F.client_db_path([])
    assert path == str(tmp_path / ".secrets" / "yarina.db"), path
    assert "лога гардиана" in note, note
    assert "14.08 00:44" in note, note


def test_the_LAST_composition_line_wins_not_the_first(tmp_path, monkeypatch):
    """Каждый рестарт гардиана дописывает свою строку состава, и все прошлые
    остаются в файле. Ни один сторож не подавал ДВЕ такие строки — а `reversed`
    в разборе можно было снять, оставив 40 зелёных. На живом логе 14.08 это
    меняло ответ: первая строка называла yarina (00:28), последняя — demo (00:44).

    Время в пояснении сверяется тем же: разбирать надо ТУ строку, чей ответ
    взят, а не соседнюю."""
    _clients_dir(tmp_path, "demo")
    _guardian_log(tmp_path,
                  "2026-08-14 00:28:00 | состав: CHATTER_PERSONAS=yarina (флаг), "
                  "db=по первому слагу\n"
                  "2026-08-14 00:44:00 | состав: CHATTER_PERSONAS=volska (флаг), "
                  "db=по первому слагу\n")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    path, note = F.client_db_path([])
    assert path == str(tmp_path / ".secrets" / "volska.db"), path
    assert "14.08 00:44" in note, note


def test_the_log_line_names_the_database_even_when_it_only_names_the_roster(tmp_path, monkeypatch):
    """Гардиан пишет `db=по первому слагу`, когда CHATTER_DB не задан вовсе, —
    это самый частый вид строки в живом логе. Базу тогда даёт состав из той же
    строки; принять «по первому слагу» за путь значит показать ленту файла с
    таким именем."""
    _clients_dir(tmp_path, "demo")
    _guardian_log(tmp_path,
                  "2026-08-14 00:44:08 | состав: CHATTER_PERSONAS=yarina (флаг), "
                  "db=по первому слагу\n")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    path, note = F.client_db_path([])
    assert path == str(tmp_path / ".secrets" / "yarina.db"), path
    assert "лога гардиана" in note, note


def test_a_log_that_only_points_back_at_the_clients_file_adds_nothing(tmp_path, monkeypatch):
    """`CHATTER_PERSONAS=active.yaml` в логе означает «состав взят файлом» —
    лог не знает ничего сверх шага 4, и выдавать его за источник нельзя."""
    _clients_dir(tmp_path, "yarina", "demo")
    _guardian_log(tmp_path,
                  "2026-08-14 00:44:08 | состав: CHATTER_PERSONAS=active.yaml, "
                  "db=по первому слагу\n")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    path, note = F.client_db_path([])
    assert path == str(tmp_path / ".secrets" / "yarina.db"), path
    assert "active.yaml" in note, note
    assert "лога гардиана" not in note, note


def test_a_line_that_merely_QUOTES_the_composition_is_not_a_composition_line(
        tmp_path, monkeypatch):
    """ГРАБЛЯ 1 файла (упоминание против записи) в логовом измерении. В тот же
    лог гардиан выливает ЧУЖОЙ текст дословно: `Invoke-WatchCheck FAILED:
    $($_.Exception.Message)` (ps1:166) и `Start-Process FAILED: …` (ps1:232) —
    сообщение исключения приезжает в лог как есть.

    Незаякоренный `.search()` находил «состав: …» ВНУТРИ такой строки, и
    пересказ давал панели путь `C:\\zlo.db` (воспроизведено). Строка состава —
    та, которую написал САМ гардиан: с начала строки, вместе с отметкой
    времени."""
    _clients_dir(tmp_path, "demo")
    _guardian_log(tmp_path,
                  "2026-08-14 00:40:00 | состав: CHATTER_PERSONAS=yarina (флаг), "
                  "db=.secrets\\yarina.db\n"
                  "2026-08-14 00:41:00 | Invoke-WatchCheck FAILED: состав: "
                  "CHATTER_PERSONAS=zlo, db=C:\\zlo.db\n")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    path, note = F.client_db_path([])
    assert path == str(tmp_path / ".secrets" / "yarina.db"), path
    assert "zlo" not in path, path


def test_the_explicit_db_of_a_log_line_outranks_the_roster_of_the_same_line(
        tmp_path, monkeypatch):
    """Все прочие сторожа лога подают строку, где `db=` и состав называют ОДНУ
    базу, — и тогда обе ветки разбора дают один ответ, то есть ни одна из них
    не проверена. Здесь они названы РАЗНЫМИ: приоритет `db=` у гардиана тот же,
    что `CHATTER_DB` у раннера (`resolve_runtime_paths`), и выпадение этой ветки
    обязано краснеть."""
    _clients_dir(tmp_path, "demo")
    _guardian_log(tmp_path,
                  "2026-08-14 00:44:08 | состав: CHATTER_PERSONAS=volska (флаг), "
                  "db=.secrets\\yarina.db\n")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    path, note = F.client_db_path([])
    assert path == str(tmp_path / ".secrets" / "yarina.db"), path
    assert "лога гардиана" in note, note


def test_an_empty_first_persona_in_the_log_line_is_skipped_too(tmp_path, monkeypatch):
    """Тот же разбор состава, что у окружения раннера, — и та же грабля:
    `,volska` в переменной означает у раннера volska, а не пустоту. Два места
    разбирают одну и ту же строку, и разъехаться им нельзя."""
    _clients_dir(tmp_path, "demo")
    _guardian_log(tmp_path,
                  "2026-08-14 00:44:08 | состав: CHATTER_PERSONAS=,volska (флаг), "
                  "db=по первому слагу\n")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    path, note = F.client_db_path([])
    assert path == str(tmp_path / ".secrets" / "volska.db"), path
    assert "лога гардиана" in note, note


def test_a_mixed_encoding_log_names_the_database_of_its_NEWEST_line(tmp_path, monkeypatch):
    """Тот самый воспроизведённый дефект целиком: cp1251-прошлое называет
    `yarina`, utf-8-будущее (после `-Encoding utf8` из Task 6) называет
    `volska`, — и панель выдавала за факт «из лога гардиана» ПРОШЛОЕ, потому
    что блочный фолбэк превращал свежие строки в кракозябры, а старые читал
    словами. Ответ обязан следовать за последней строкой."""
    _clients_dir(tmp_path, "demo")
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "chatter_guardian.stdout.log").write_bytes(
        "2026-08-14 00:40:00 | состав: CHATTER_PERSONAS=yarina (флаг), "
        "db=по первому слагу\n".encode("cp1251")
        + "2026-08-14 00:45:00 | состав: CHATTER_PERSONAS=volska (флаг), "
          "db=по первому слагу\n".encode("utf-8"))
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    path, note = F.client_db_path([])
    assert path == str(tmp_path / ".secrets" / "volska.db"), path
    assert "14.08 00:45" in note, note


def test_a_composition_line_pushed_out_of_the_window_is_named_a_gap(tmp_path, monkeypatch):
    """Гардиан пишет «состав» ТОЛЬКО при СВОЁМ старте, а ротации у лога нет
    вовсе. При долгом аптайме строка уезжает за окно 64 КБ — и ступень 3
    замолкает, ничего не сказав: пояснение выглядит так же, как у машины, где
    гардиан не запускался никогда. `truncated` из `read_tail` для того и
    возвращается, чтобы эту разницу было чем назвать."""
    _clients_dir(tmp_path, "demo")
    _guardian_log(tmp_path,
                  "2026-08-01 00:00:00 | состав: CHATTER_PERSONAS=yarina (флаг), "
                  "db=по первому слагу\n"
                  + "".join(f"2026-08-14 00:{i % 60:02d}:00 | runner alive {i}\n"
                            for i in range(3000)))
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    path, note = F.client_db_path([])
    assert path == str(tmp_path / ".secrets" / "demo.db"), path
    assert "длиннее окна" in note, note


# ─────────────── шаг 4: ни раннера, ни лога — догадка вслух ──────────────────

def test_the_database_follows_the_primary_slug(tmp_path, monkeypatch):
    """Первичный slug МЕНЯЕТСЯ: 14.08 во время демо Ярины он был `yarina`.
    Панель с прибитым литералом в такой момент читает не ту базу и печатает
    «тихо» вместо ленты — тишина, неотличимая от здоровья. Проверено фактом:
    рядом с `.secrets/demo.db` (35 control_events) лежит `.secrets/yarina.db`
    ровно с нулём — на нём лента и была бы пустой.

    Сверяется ПОЛНЫЙ путь, а не хвост `.endswith("yarina.db")`: под хвостовую
    проверку пролезает и заново прибитый литерал `C:/jarvis/.secrets/yarina.db`,
    то есть ровно тот дефект, который здесь сторожат."""
    _clients_dir(tmp_path, "yarina", "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    path, note = F.client_db_path([])
    assert path == str(tmp_path / ".secrets" / "yarina.db"), path
    # Ответ БЕЗ живого раннера — догадка, и лента обязана назвать её догадкой.
    assert "active.yaml" in note, note


def test_a_missing_clients_file_admits_it_took_the_legacy_default(tmp_path, monkeypatch):
    """`chatter/config/active.py` при отсутствии файла ТИХО отдаёт
    LEGACY_PERSONAS (['demo','demo2']) — страховка, осмысленная для раннера
    (прод не падает на первом же рестарте) и ядовитая для панели: она выдала бы
    demo.db за прочитанный состав. Раннера не трогаем, но молчать не имеем
    права."""
    monkeypatch.setattr(F, "ROOT", tmp_path)     # склада клиентов тут нет вовсе
    _bare_panel_env(monkeypatch)
    path, note = F.client_db_path([])
    assert path == str(tmp_path / ".secrets" / "demo.db"), path
    assert "legacy" in note, note
    assert "не найден" in note, note


def test_an_explicit_env_database_still_wins(tmp_path, monkeypatch):
    """На TAMAPI_DB стоит демо-стенд (scripts/panels_demo.py). Отобрать у него
    приоритет значит сломать стенд приёмки.

    Путь возвращается АБСОЛЮТНЫМ, как и у остальных трёх ступеней: два разных
    контракта в одном возврате означали бы, что потребитель разрешает
    относительный путь то от cwd панели (а он у неё какой угодно), то от ROOT."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    monkeypatch.setenv("TAMAPI_DB", "state/panels_demo.db")
    assert F.client_db_path() == (str(tmp_path / "state" / "panels_demo.db"), "")


def test_the_panels_own_override_outranks_the_runners_one(tmp_path, monkeypatch):
    """TAMAPI_DB > CHATTER_DB, и порядок несущий: обе переменные разом стоят на
    демо-стенде, запущенном в окружении раннера. Ни один сторож их вместе не
    выставлял — приоритет держался порядком слагаемых в одной строке `or`."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    monkeypatch.setenv("TAMAPI_DB", "state/panels_demo.db")
    monkeypatch.setenv("CHATTER_DB", ".secrets/volska.db")
    assert F.client_db_path() == (str(tmp_path / "state" / "panels_demo.db"), "")


def test_an_empty_override_is_not_a_path(tmp_path, monkeypatch):
    """`TAMAPI_DB=''` — это «не выставлена», а не «база в пустом файле».
    Ужесточение `if env_db:` до `is not None` вернуло бы пустой путь, и лента
    молча опустела бы: sqlite открывает пустое имя без единой ошибки на месте
    чтения.

    ДВА случая, потому что за них отвечают ДВЕ разные строки. Пустоту ПЕРВОЙ
    переменной отсеивает `or` — это первая половина. Но когда пусты ОБЕ, `or`
    честно отдаёт пустую строку, и держит ответ уже `if env_db:` — без второй
    половины ужесточение до `is not None` проходило бы незамеченным."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    monkeypatch.setenv("TAMAPI_DB", "")
    monkeypatch.setenv("CHATTER_DB", ".secrets/yarina.db")
    assert F.client_db_path() == (str(tmp_path / ".secrets" / "yarina.db"), "")

    monkeypatch.setenv("CHATTER_DB", "")
    path, note = F.client_db_path([])
    # Обе пусты — ступень 1 промолчала, и ответ пришёл СНИЗУ лестницы, догадкой.
    assert path == str(tmp_path / ".secrets" / "demo.db"), path
    assert note, "пустые переменные выданы за явное указание базы"


def test_the_runners_own_env_override_moves_the_panel_too(tmp_path, monkeypatch):
    """CHATTER_DB — переопределение САМОГО раннера (telethon_run.py,
    `resolve_runtime_paths`: явный флаг > CHATTER_DB > вывод из slug'а). В
    окружении панели он стоит только при ручном запуске из той же консоли — и
    тогда спрашивать кого-то ещё незачем."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    monkeypatch.delenv("TAMAPI_DB", raising=False)
    monkeypatch.setenv("CHATTER_DB", ".secrets/yarina.db")
    assert F.client_db_path() == (str(tmp_path / ".secrets" / "yarina.db"), "")


def test_the_composition_env_moves_the_panel_like_it_moves_the_runner(tmp_path, monkeypatch):
    """CHATTER_PERSONAS перебивает active.yaml у раннера
    (`chatter.config.active.resolve_personas`), и первичным становится первый
    slug из переменной. Панель обязана ехать туда же.

    Пояснение здесь НЕПУСТОЕ, и это правка по факту: переменная в окружении
    ПАНЕЛИ говорит о панели, а не о живом раннере, — то есть ответ остаётся
    догадкой, и лента обязана назвать её источник."""
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    monkeypatch.delenv("TAMAPI_DB", raising=False)
    monkeypatch.delenv("CHATTER_DB", raising=False)
    monkeypatch.setenv("CHATTER_PERSONAS", "yarina,demo")
    path, note = F.client_db_path([])
    assert path == str(tmp_path / ".secrets" / "yarina.db"), path
    assert "CHATTER_PERSONAS" in note, note


def test_a_broken_composition_says_so_instead_of_falling_back_silently(tmp_path, monkeypatch):
    """Тихий откат на demo.db И ЕСТЬ починяемый дефект: панель показала бы
    ленту чужой базы и назвала бы её текущей."""
    d = tmp_path / "chatter" / "clients"
    d.mkdir(parents=True)
    (d / "active.yaml").write_text("clients: [](((битый", encoding="utf-8")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    path, note = F.client_db_path([])
    assert path is None
    assert "не прочитан" in note, note
    assert "demo.db" not in note


def test_an_empty_composition_is_a_note_not_an_IndexError(tmp_path, monkeypatch):
    """Сегодня `resolve_personas` на пустом списке кричит сама (проверено:
    ActiveClientsError), но панель берёт `slugs[0]` — и в тот день, когда
    источник начнёт возвращать пустой список молча, страница фермы упадёт
    IndexError'ом целиком. Пустой состав — такое же «неизвестно, какую базу
    читать», как и битый файл."""
    import chatter.config.active as A
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    monkeypatch.setattr(A, "resolve_personas", lambda **kw: [])
    _bare_panel_env(monkeypatch)
    path, note = F.client_db_path([])
    assert path is None
    assert "не прочитан" in note, note


# ───────────────── лента целиком: четыре функции, сложенные вместе ────────────
#
# `events(table=[])` во ВСЕХ сторожах ниже — не украшение стенда. Пустая таблица
# процессов означает «раннера не видно», и лестница Task 4 уходит на ступени 3–4,
# то есть в подменённый ROOT. Без неё лента спросила бы ЖИВОЙ раннер этой машины
# (на ней прямо сейчас крутится volska) и читала бы прод-базу — сторож зеленел
# бы от состава фермы, а не от кода.


def _client_db(tmp_path, slug: str, rows: list[tuple]):
    """База клиента там, где её ищет лента: `<ROOT>/.secrets/<slug>.db`.

    Схема берётся у САМОГО раннера (`chatter.storage.db._SCHEMA`), а не
    переписывается рядом: своя копия DDL разъехалась бы с прод-схемой молча, и
    сторож ленты продолжил бы зеленеть на таблице, которой в проде уже нет.

    `rows` — кортежи `(kind, contact_id, detail, ts)`."""
    import sqlite3
    from chatter.storage.db import _SCHEMA

    secrets = tmp_path / ".secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    p = secrets / f"{slug}.db"
    conn = sqlite3.connect(p)
    try:
        conn.executescript(_SCHEMA)
        conn.executemany(
            "INSERT INTO control_events (kind, contact_id, detail, ts) VALUES (?,?,?,?)",
            rows)
        conn.commit()
    finally:
        conn.close()
    return p


def test_the_feed_sorts_by_time_and_carries_it(tmp_path, monkeypatch):
    """До правки строки гардиана шли с ts=None и вставали в конец кучей.
    Со временем лента наконец читается как лента."""
    _guardian_log(tmp_path, "2026-08-14 00:45:08 | runner DOWN - restarting\n"
                            "2026-08-14 00:45:09 | launched chatter runner (PID 6864) -> x.log\n")
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)

    rows = F.events(table=[])
    guard = [r for r in rows if r["src"] == "гардиан"]
    assert len(guard) == 2, rows
    assert all(r["ts"] for r in guard), "лента снова без времени"
    assert guard[0]["ts"] >= guard[1]["ts"], "лента не отсортирована по времени"


def test_the_timestamp_prefix_is_stripped_from_the_detail(tmp_path, monkeypatch):
    """Время теперь отдельная колонка (`_events_table` рисует его под
    «Когда»). Дублировать его в тексте значит занимать узкую колонку тем, что
    уже нарисовано рядом."""
    _guardian_log(tmp_path, "2026-08-14 00:45:08 | runner DOWN - restarting\n")
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    row = [r for r in F.events(table=[]) if r["src"] == "гардиан"][0]
    assert row["detail"] == "runner DOWN - restarting", row["detail"]


def test_a_truncated_log_states_where_visibility_begins(tmp_path, monkeypatch):
    """Граница видимости, названная вслух, — не то же самое, что молча
    обрезанное окно. Это и есть починяемый дефект.

    Строк гардиана здесь ТРИ ТЫСЯЧИ, и это часть сторожа: строка о границе
    обязана пережить и окно чтения, и бюджет ленты. Попади она в общую очередь
    на равных — её вытеснили бы те самые события, границу которых она
    объявляет."""
    body = "".join(f"2026-08-14 00:{i % 60:02d}:00 | runner DOWN - restarting\n"
                   for i in range(3000))
    _guardian_log(tmp_path, body)
    _clients_dir(tmp_path, "demo")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    monkeypatch.setattr(F, "GUARDIAN_LOG_WINDOW", 2048)
    _bare_panel_env(monkeypatch)
    rows = F.events(table=[])
    assert any("видно с" in r["detail"] for r in rows), rows


def test_an_unreadable_composition_reaches_the_feed_as_a_row(tmp_path, monkeypatch):
    """DEV-18: провал не глотается. Пустая лента вместо объяснения — это
    тишина ровно там, где произошла авария конфигурации."""
    d = tmp_path / "chatter" / "clients"
    d.mkdir(parents=True)
    (d / "active.yaml").write_text("clients: [](((битый", encoding="utf-8")
    _guardian_log(tmp_path, "")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    rows = F.events(table=[])
    assert any(r["src"] == "панель" and "не прочитан" in r["detail"] for r in rows), rows


def test_an_unreadable_database_reaches_the_feed_as_a_row(tmp_path, monkeypatch):
    """Второй конец того же правила (DEV-18): путь к базе есть, а открыть её не
    вышло. Прежняя `events()` глотала это голым `except Exception: pass`, и
    отсутствующий файл базы выглядел на экране ровно как «клиент сегодня
    молчал» — два разных положения дел, требующих разных действий."""
    _clients_dir(tmp_path, "demo")           # .secrets/demo.db не создан вовсе
    _guardian_log(tmp_path, "")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    rows = F.events(table=[])
    assert any(r["src"] == "панель" and "база" in r["kind"] for r in rows), rows


def test_a_guessed_database_is_still_READ_not_merely_explained(tmp_path, monkeypatch):
    """`if db_note`, а НЕ `elif`. По лестнице Task 4 непустое пояснение приходит
    ВМЕСТЕ с рабочим путём: ступени 3–4 отвечают догадкой и честно её называют.
    При `elif` панель показала бы одну строку-пояснение и пустую ленту — тишину
    ровно там, где данные есть. Пояснение и чтение базы независимы."""
    _clients_dir(tmp_path, "demo")
    _client_db(tmp_path, "demo", [("пауза", "c1", "клиент попросил паузу", 2e9)])
    _guardian_log(tmp_path, "")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    rows = F.events(table=[])
    assert any(r["src"] == "панель" and "active.yaml" in r["detail"] for r in rows), rows
    assert any(r["src"] == "chatter" and r["detail"] == "клиент попросил паузу"
               for r in rows), rows


def test_a_ready_process_table_is_not_rebuilt(tmp_path, monkeypatch):
    """Параметр `table` обязан доезжать до `client_db_path`, а не быть
    декоративным. Замер 14.08 на живой машине: `client_db_path(table)` с готовой
    таблицей — 0.9 мс, без неё — 10.7 мс тёплым и 591 мс ХОЛОДНЫМ. Уронить
    аргумент по дороге можно бесследно, поэтому `_proc_table` здесь подменён на
    заглушку, которая кричит."""
    _clients_dir(tmp_path, "demo")
    _guardian_log(tmp_path, "")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)
    _environs(monkeypatch, {6864: {"CHATTER_PERSONAS": "demo"}})

    def never():
        raise AssertionError("готовая таблица процессов не доехала — собрана заново")

    monkeypatch.setattr(F, "_proc_table", never)
    rows = F.events(table=[_runner_row(6864)])
    assert rows is not None


# ───────────────────────── бюджет окна между источниками ─────────────────────
#
# Замер ДО правки на ЖИВЫХ источниках: в ленте 40 строк, из них 35 —
# control_events базы и только 5 — гардиан (2 из этих 5 дебаунсные). Дефект не в
# фильтре: обе пачки складывались в ОДИН список и резались общим `limit`.
# Сортировка по времени его не чинит — клиентских событий и больше, и они
# свежее, так что после сортировки гардиан уезжает за срез ещё вернее.
#
# Пара сторожей ниже держит РЕШЕНИЕ: каждому источнику гарантированная доля
# окна, недобранное достаётся соседу. Односторонняя проверка тут негодна —
# «гардиан доехал» чинится перекосом в другую сторону, и лента снова показывает
# один источник, только другой.


def test_a_flood_of_client_events_does_not_starve_the_guardian(tmp_path, monkeypatch):
    """Двести свежих клиентских событий против шести старых строк гардиана —
    ровно та пропорция, что дала на живых источниках 35 против 5."""
    _clients_dir(tmp_path, "demo")
    _client_db(tmp_path, "demo",
               [("пауза", f"c{i}", f"клиентское событие {i}", 2e9 + i) for i in range(200)])
    _guardian_log(tmp_path, "".join(
        f"2026-08-14 00:{i:02d}:00 | runner DOWN - restarting {i}\n" for i in range(6)))
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)

    rows = F.events(table=[])
    guard = [r for r in rows if r["src"] == "гардиан"]
    assert len(guard) == 6, f"решения гардиана вытеснены: доехало {len(guard)} из 6"


def test_a_flood_of_guardian_lines_does_not_starve_the_client(tmp_path, monkeypatch):
    """Парный. Доля гардиана — доля, а не окно целиком: перекос в эту сторону
    выглядел бы «починенным» под первым сторожем и прятал бы от владельца
    работу самого клиента."""
    _clients_dir(tmp_path, "demo")
    _client_db(tmp_path, "demo",
               [("пауза", f"c{i}", f"клиентское событие {i}", 1e9 + i) for i in range(6)])
    _guardian_log(tmp_path, "".join(
        f"2026-08-14 00:{i % 60:02d}:00 | runner DOWN - restarting {i}\n"
        for i in range(200)))
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)

    rows = F.events(table=[])
    client = [r for r in rows if r["src"] == "chatter"]
    assert len(client) == 6, f"события клиента вытеснены: доехало {len(client)} из 6"


def test_a_failed_source_is_never_crowded_out_by_the_flood(tmp_path, monkeypatch):
    """Строки о самой ленте (провал источника, граница видимости) стоят ВНЕ
    бюджета. Они говорят про СЕЙЧАС, а не про прошлое: попади они в общую
    очередь на равных, двести свежих клиентских событий вытеснили бы аварию
    конфигурации, и она снова выглядела бы тишиной."""
    _clients_dir(tmp_path, "demo")
    _client_db(tmp_path, "demo",
               [("пауза", f"c{i}", f"клиентское событие {i}", 2e9 + i) for i in range(200)])
    _guardian_log(tmp_path, "".join(
        f"2026-08-14 00:{i % 60:02d}:00 | runner DOWN - restarting {i}\n"
        for i in range(200)))
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)

    rows = F.events(table=[])
    assert rows[0]["ts"] is None, rows[0]
    assert any(r["src"] == "панель" and "active.yaml" in r["detail"] for r in rows), \
        "пояснение о догадке вытеснено потоком событий"


def test_the_feed_never_grows_past_its_limit(tmp_path, monkeypatch):
    """Бюджет по источникам не имеет права раздуть ленту: `limit` — это размер
    окна, а не размер доли."""
    _clients_dir(tmp_path, "demo")
    _client_db(tmp_path, "demo",
               [("пауза", f"c{i}", f"клиентское событие {i}", 2e9 + i) for i in range(200)])
    _guardian_log(tmp_path, "".join(
        f"2026-08-14 00:{i % 60:02d}:00 | runner DOWN - restarting {i}\n"
        for i in range(200)))
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)

    for limit in (1, 2, 10, 40):
        rows = F.events(limit=limit, table=[])
        assert len(rows) <= limit, f"limit={limit}, строк {len(rows)}"


def test_a_silent_source_wastes_none_of_the_window(tmp_path, monkeypatch):
    """Доля молчащего источника не пропадает, а достаётся говорящему: иначе
    гарантия превратилась бы в дыру посреди ленты в самый обычный день, когда
    гардиан просто ничего не решал."""
    _clients_dir(tmp_path, "demo")
    _client_db(tmp_path, "demo",
               [("пауза", f"c{i}", f"клиентское событие {i}", 2e9 + i) for i in range(200)])
    _guardian_log(tmp_path, "")
    monkeypatch.setattr(F, "ROOT", tmp_path)
    _bare_panel_env(monkeypatch)

    rows = F.events(limit=40, table=[])
    notes = [r for r in rows if r["src"] == "панель"]
    client = [r for r in rows if r["src"] == "chatter"]
    assert len(client) == 40 - len(notes), f"окно недобрано: {len(client)} + {len(notes)}"
