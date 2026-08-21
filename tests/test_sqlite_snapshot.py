# -*- coding: utf-8 -*-
"""Сторожа онлайнового снимка клиентской базы — DEV-46, §4.1–§4.3, §7 п. 2 и 6.

Написаны ОТ СПЕКИ, до реализации и не глядя в неё
([[jarvis-guards-not-by-the-plan-author]]): сторож, написанный автором кода,
наследует то же неверное допущение и молчит ровно там, где код забыл.

Дефект, ради которого §4 существует, назван в §4.1 дословно: **sha256 рваного
снимка совпадает с рваным снимком**. Сегодняшняя проверка целостности
(`verify_restored_file`) отвечает на вопрос «доехали ли байты» и зеленеет на
базе, которая не открывается. Поэтому здесь ни один сторож не считает успехом
«файл появился»: успех — это ОТКРЫЛСЯ НАСТОЯЩИМ СЛОЕМ ХРАНЕНИЯ
(`chatter.storage.db.Store`) и вернул связные величины.

Ни одной живой базы и ни одного сетевого вызова: всё строится в `tmp_path`.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app.services.sqlite_snapshot import (
    SnapshotError,
    snapshot_counts,
    snapshot_sqlite,
)
from chatter.storage.db import Store

CID = "lead-1"


# -- общий инструмент -------------------------------------------------------

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fingerprint(path: Path) -> tuple[int, int, str]:
    """Размер, mtime в наносекундах и sha256 — всё, чем файл может измениться."""
    st = path.stat()
    return (st.st_size, st.st_mtime_ns, _sha256(path))


def _listing(directory: Path) -> list[str]:
    """СОСТАВ каталога, а не поимённая проверка на `-journal`/`-wal`/`.bak`:
    поимённый список знает только те хвосты, о которых автор сторожа успел
    подумать, а состав ловит любой новый файл, включая ещё не придуманный."""
    return sorted(p.name for p in directory.iterdir())


def _ro_query(path: Path, sql: str):
    """Чтение обычным read-only соединением — без DDL и без миграций."""
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute(sql).fetchone()
    finally:
        conn.close()


def _seed_base(path: Path, contacts: dict[str, int], *, base_ts: float) -> float:
    """Создать базу НАСТОЯЩЕЙ схемой (`Store`, а не рукописный CREATE TABLE).

    Возвращает ts последнего сообщения. Ни один контакт не остаётся без
    сообщений намеренно: спека зовёт величину «число диалогов», и при пустом
    контакте `COUNT(contacts)` и `COUNT(DISTINCT messages.contact_id)`
    разошлись бы — сторож не должен краснеть на разночтении спеки, он должен
    краснеть на дефекте.
    """
    ts = base_ts
    written = 0
    with Store(path) as store:
        for contact_id, n in contacts.items():
            store.get_or_create_contact(contact_id)
            for _ in range(n):
                written += 1
                ts = base_ts + written  # строго возрастает, максимум = последний
                store.add_message(contact_id, "user", f"{contact_id}-{written}", ts)
    return ts


# -- §7 п. 2: ведущий сторож — снимок СОГЛАСОВАН под параллельной записью ----

BATCH = 200          # сообщений в ОДНОЙ транзакции писателя
ROW_BYTES = 512      # ...такой длины каждое
WRITER_CACHE_PAGES = 8   # крошечный кэш страниц у писателя — см. ниже
SNAPSHOTS = 5        # столько раз снимаем во время цикла записи
TXN_WINDOW_S = 0.03  # столько транзакция держится открытой после вставок
TXN_GAP_S = 0.01     # ...и столько база стоит зафиксированной

# Почему кэш писателя ужат до восьми страниц. При кэше по умолчанию транзакция
# на 200 коротких строк целиком помещается в память, и файл базы до COMMIT не
# меняется НИ РАЗУ — то есть побайтовая копия, снятая в середине транзакции,
# случайно оказывается согласованной, и сторож зеленеет на `shutil.copy2`.
# Это ровно та ложная зелень, ради которой сторожа пишет не автор кода
# (проверено мутантом: копия проходила проверку `% BATCH`). Ужатый кэш
# заставляет SQLite выталкивать НЕЗАФИКСИРОВАННЫЕ страницы в файл до COMMIT —
# то самое состояние, в котором файловая копия врёт.


def test_snapshot_under_concurrent_writes_opens_with_the_real_storage_layer(tmp_path):
    """Без него молча проехал бы снимок с ПОЛОВИНОЙ транзакции внутри: sha сойдётся, `sqlite3` его откроет, а продукт на нём не поднимется.

    Почему проверка через `Store` — не то же самое, что через `sqlite3`.
    `sqlite3.connect` открывает файл лениво: он трогает страницу заголовка и
    ровно те страницы, которых требует запрос, — рваный файл на этом
    проходит. `Store.__init__` — это ПРОДУКТОВЫЙ путь открытия: `PRAGMA
    table_info` по каждой таблице из `_ADDED_COLUMNS`, `executescript(_SCHEMA)`,
    проверка окна перестройки `payments`, при нехватке колонок — миграция. Он
    идёт по всей схеме и по страницам за ней, и падает на снимке, который
    голый `sqlite3` объявил исправным. «Открылся файл» и «вернулся продукт» —
    разные утверждения (§4.3 п. 3), и доказывать надо второе.
    """
    src = tmp_path / "live.db"
    with Store(src) as store:
        store.get_or_create_contact(CID)
    journal = src.with_name(src.name + "-journal")

    stop = threading.Event()
    in_txn = threading.Event()
    committed = [0]
    failure: list[BaseException] = []

    def writer() -> None:
        conn = sqlite3.connect(str(src), isolation_level=None, timeout=30.0)
        conn.execute(f"PRAGMA cache_size={WRITER_CACHE_PAGES}")
        # Жёсткий предел независимо от флага остановки: висящий тест хуже
        # красного, поток не должен пережить свой тест ни при каком исходе.
        hard_deadline = time.monotonic() + 90.0
        try:
            while not stop.is_set() and time.monotonic() < hard_deadline:
                conn.execute("BEGIN IMMEDIATE")
                for i in range(BATCH):
                    conn.execute(
                        "INSERT INTO messages(contact_id, role, text, ts) "
                        "VALUES (?,?,?,?)",
                        (CID, "user", "x" * ROW_BYTES,
                         1000.0 + committed[0] * BATCH + i))
                in_txn.set()
                time.sleep(TXN_WINDOW_S)  # окно, в которое обязан попасть снимок
                conn.execute("COMMIT")
                in_txn.clear()
                committed[0] += 1
                time.sleep(TXN_GAP_S)
        except BaseException as exc:  # noqa: BLE001 - поднимем в основном потоке
            failure.append(exc)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    thread = threading.Thread(target=writer, name="dev46-guard-writer", daemon=True)
    thread.start()
    taken: list[tuple[int, bool]] = []  # (сообщений в снимке, был ли журнал)
    try:
        for k in range(SNAPSHOTS):
            # Каждый снимок обязан прийтись на СВОЮ транзакцию: без этого все
            # пять успевают уложиться в одно окно, и «пять снимков» на деле
            # проверяют один момент времени.
            seen = committed[0]
            deadline = time.monotonic() + 20.0
            while committed[0] <= seen and time.monotonic() < deadline:
                time.sleep(0.001)
            assert committed[0] > seen, (
                f"снимок #{k}: за 20 с писатель не зафиксировал новой "
                f"транзакции (всего {committed[0]}, падение писателя: "
                f"{failure!r}) — снимки пришлись бы на одно и то же окно")
            assert in_txn.wait(timeout=20.0), (
                f"снимок #{k}: писатель не открыл транзакцию за 20 с "
                f"(зафиксировано {committed[0]}, падение писателя: {failure!r}) — "
                f"окно транзакции не ловится, проверять согласованность не на чем")
            out_dir = tmp_path / f"out{k}"
            out_dir.mkdir()
            dst = out_dir / "snapshot.db"
            journal_at_call = journal.exists()
            snapshot_sqlite(src, dst)

            # integrity_check ДО того, как Store что-то запишет в снимок:
            # вердикт обязан описывать снимок таким, каким он приехал.
            verdict = _ro_query(dst, "PRAGMA integrity_check")[0]
            assert verdict == "ok", (
                f"снимок #{k}: PRAGMA integrity_check = {verdict!r}, ожидалось 'ok'")

            with Store(dst) as snap:
                rows = snap.history(CID)
            n = len(rows)
            taken.append((n, journal_at_call))
            assert n % BATCH == 0, (
                f"снимок #{k}: {n} сообщений, а писатель кладёт их транзакциями "
                f"по {BATCH} — {n % BATCH} строк приехали из НЕЗАВЕРШЁННОЙ "
                f"транзакции, снимок не согласован")
    finally:
        stop.set()
        thread.join(timeout=15.0)

    assert not thread.is_alive(), "писатель не завершился за 15 с после флага остановки"
    assert not failure, f"писатель упал: {failure[0]!r}"
    assert committed[0] >= SNAPSHOTS, (
        f"писатель зафиксировал всего {committed[0]} транзакций при "
        f"{SNAPSHOTS} снимках — параллельной записи фактически не было, тест "
        f"ничего не проверил")
    caught = sum(1 for _, seen in taken if seen)
    assert caught >= 1, (
        f"ни один из {len(taken)} снимков не пришёлся на открытую транзакцию "
        f"(журнала рядом с базой не было ни разу) — согласованность под записью "
        f"не проверена, снимки взяты в тишине")
    assert max(n for n, _ in taken) > 0, (
        f"все {len(taken)} снимков пусты при {committed[0]} зафиксированных "
        f"транзакциях — снимок не видит того, что уже лежит в источнике")


# -- §4.1: контрпример к сегодняшней проверке -------------------------------

def test_sha256_of_a_torn_base_matches_itself_while_the_base_refuses_to_open(tmp_path):
    """Без него «sha256 сошёлся» читалось бы как «база вернётся»: рваный файл сверяется сам с собой ЗЕЛЁНО, а продукт на нём падает.

    Тест-документ: он не проверяет новый код, он фиксирует, ПОЧЕМУ новый код
    понадобился. «Байты доехали» и «база поднимется» — разные утверждения, и
    первое не влечёт второго.
    """
    whole = tmp_path / "whole.db"
    _seed_base(whole, {CID: 400}, base_ts=1_700_000_000.0)
    raw = whole.read_bytes()
    assert len(raw) > 64 * 1024, (
        f"база {len(raw)} байт — слишком мала, обрезка может не задеть данных")

    # Рваный снимок: копирование застало базу в середине роста.
    torn = tmp_path / "torn.db"
    torn.write_bytes(raw[: int(len(raw) * 0.6)])
    # То же, что вернул бы бакет: побайтовая копия того, что уехало.
    restored = tmp_path / "restored.db"
    shutil.copy2(torn, restored)

    # Сегодняшняя проверка (verify_restored_file, state_backup.py:347) — sha256.
    assert _sha256(torn) == _sha256(restored), (
        "побайтовая копия обязана совпасть по sha256 — иначе контрпример не о том")
    assert restored.stat().st_size == torn.stat().st_size > 0, "байты доехали"

    # ...и вот на этом ЗЕЛЁНОМ файле продукт не поднимается.
    with pytest.raises(sqlite3.DatabaseError) as err:
        with Store(restored) as store:
            store.history(CID)
    assert "malformed" in str(err.value) or "not a database" in str(err.value), (
        f"ожидалось, что рваная база не откроется; получено: {err.value!r}")


# -- §4.2: источник не тронут -----------------------------------------------

def test_snapshot_leaves_the_source_untouched(tmp_path):
    """Без него снимок мог бы оставить рядом с БОЕВОЙ базой журнал, `-wal` или `.bak`, и обнаружилось бы это чужим сбоем, а не здесь."""
    src_dir = tmp_path / "secrets"
    src_dir.mkdir()
    src = src_dir / "demo.db"
    _seed_base(src, {CID: 40, "lead-2": 12}, base_ts=1_700_000_000.0)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    before_listing = _listing(src_dir)
    before_fp = _fingerprint(src)
    time.sleep(1.1)  # чтобы сдвиг mtime был различим, а не съеден гранулярностью

    snapshot_sqlite(src, out_dir / "snapshot.db")

    after_listing = _listing(src_dir)
    assert after_listing == before_listing, (
        f"рядом с источником изменился состав каталога: было {before_listing}, "
        f"стало {after_listing}")
    after_fp = _fingerprint(src)
    assert after_fp == before_fp, (
        f"источник изменился: (size, mtime_ns, sha256) {before_fp} -> {after_fp}")


# -- §4.2: никакой файловой копии -------------------------------------------

def test_snapshot_refuses_a_base_with_a_hot_journal(tmp_path):
    """Без него бэкап уносил бы файловую копию с НЕЗАФИКСИРОВАННЫМИ строками чужой транзакции: integrity_check на ней зелёный, а данные — те, которых не было."""
    src_dir = tmp_path / "secrets"
    src_dir.mkdir()
    src = src_dir / "demo.db"
    _seed_base(src, {CID: 200}, base_ts=1_700_000_000.0)

    # Чужая незакрытая транзакция: процесс открыл базу, выбрал крошечный кэш
    # (чтобы грязные страницы ушли В ФАЙЛ, а не остались в памяти) и умер, не
    # зафиксировав. На диске остаётся журнал отката, а в самой базе — строки,
    # которых по данным журнала быть не должно.
    child = tmp_path / "crash_writer.py"
    child.write_text(
        "import sqlite3, os, sys\n"
        "c = sqlite3.connect(sys.argv[1], isolation_level=None)\n"
        "c.execute('PRAGMA cache_size=8')\n"
        "c.execute('BEGIN IMMEDIATE')\n"
        "for i in range(20000):\n"
        "    c.execute(\"INSERT INTO messages(contact_id, role, text, ts)\"\n"
        "              \" VALUES ('ghost-lead','user','GHOST',9999.0)\")\n"
        "os._exit(0)\n",
        encoding="utf-8")
    proc = subprocess.run([sys.executable, str(child), str(src)],
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, (
        f"писатель-самоубийца вышел {proc.returncode}: {proc.stderr[:400]}")

    journal = src.with_name(src.name + "-journal")
    assert journal.exists(), (
        f"после обрыва транзакции журнала отката рядом нет (состав каталога: "
        f"{_listing(src_dir)}) — стенд не воспроизвёл дефект")

    # Доказательство, что «скопировали и ладно» здесь ВРЁТ, а не просто
    # рискует: побайтовая копия открывается, даёт integrity_check == 'ok' и
    # содержит строки незафиксированной транзакции.
    naive_dir = tmp_path / "naive"
    naive_dir.mkdir()
    naive = naive_dir / "copied.db"
    shutil.copy2(src, naive)
    ghosts = _ro_query(naive, "SELECT COUNT(*) FROM messages WHERE text='GHOST'")[0]
    verdict = _ro_query(naive, "PRAGMA integrity_check")[0]
    assert ghosts > 0 and verdict == "ok", (
        f"стенд не воспроизвёл дефект: в файловой копии {ghosts} "
        f"незафиксированных строк, integrity_check = {verdict!r}; ожидались "
        f"строки > 0 при зелёном вердикте")

    before_listing = _listing(src_dir)
    before_fp = _fingerprint(src)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    dst = out_dir / "snapshot.db"

    with pytest.raises(SnapshotError):
        snapshot_sqlite(src, dst)

    assert not dst.exists(), (
        f"после отказа остался огрызок снимка {dst.stat().st_size} байт — "
        f"он поедет в бэкап как исправный")
    assert _listing(src_dir) == before_listing, (
        f"попытка снимка изменила состав каталога источника: было "
        f"{before_listing}, стало {_listing(src_dir)}")
    assert _fingerprint(src) == before_fp, (
        f"попытка снимка изменила источник: {before_fp} -> {_fingerprint(src)} "
        f"(откат чужой транзакции — это ЗАПИСЬ в боевую базу)")


# -- отказы на входе --------------------------------------------------------

def test_existing_destination_is_refused_and_kept(tmp_path):
    """Без него сегодняшний снимок молча затирал бы вчерашний, и потеря обнаружилась бы в момент восстановления — то есть поздно."""
    src = tmp_path / "demo.db"
    _seed_base(src, {CID: 5}, base_ts=1_700_000_000.0)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    dst = out_dir / "snapshot.db"
    dst.write_bytes(b"snapshot of yesterday")
    before_fp = _fingerprint(dst)
    time.sleep(1.1)

    with pytest.raises(SnapshotError):
        snapshot_sqlite(src, dst)

    assert _fingerprint(dst) == before_fp, (
        f"существующий снимок изменён: {before_fp} -> {_fingerprint(dst)}")
    assert _listing(out_dir) == ["snapshot.db"], (
        f"в каталоге назначения появилось лишнее: {_listing(out_dir)}")


def test_a_non_database_source_is_refused(tmp_path):
    """Без него на вход проехал бы не тот файл (лог, обрезок, полускачанный объект), а на выходе лежал бы «снимок» на ноль строк."""
    src = tmp_path / "requisites.yaml"
    src.write_text("card: 0000 0000 0000 0000\nname: not a database\n",
                   encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    dst = out_dir / "snapshot.db"

    with pytest.raises(SnapshotError):
        snapshot_sqlite(src, dst)

    assert _listing(out_dir) == [], (
        f"после отказа в каталоге назначения лежит {_listing(out_dir)} — "
        f"пустой файл в бэкапе неотличим от пустой базы")


# -- §7 п. 6: величины для манифеста ----------------------------------------

def test_snapshot_counts_reports_the_numbers_the_manifest_will_carry(tmp_path):
    """Без него в манифест уехали бы числа, посчитанные не по той базе, и дрил §4.3 сверял бы восстановленное с самим собой."""
    src = tmp_path / "demo.db"
    plan = {"lead-1": 3, "lead-2": 5, "lead-3": 2, "lead-4": 1}
    last_ts = _seed_base(src, plan, base_ts=1_700_000_000.0)

    got = snapshot_counts(src)

    assert got["dialogs"] == len(plan), (
        f"диалогов: получено {got['dialogs']}, в базе {len(plan)} контактов")
    assert got["messages"] == sum(plan.values()), (
        f"сообщений: получено {got['messages']}, в базе {sum(plan.values())}")
    assert got["last_message_at"] == pytest.approx(last_ts), (
        f"время последнего сообщения: получено {got['last_message_at']!r}, "
        f"в базе {last_ts!r}")


def test_snapshot_counts_on_an_empty_base_is_zeros_and_none(tmp_path):
    """Без него пустая база отвечала бы `last_message_at` каким-нибудь `0.0`, и «сообщений не было никогда» стало бы неотличимо от «последнее было в 1970-м»."""
    src = tmp_path / "fresh.db"
    with Store(src):
        pass

    got = snapshot_counts(src)

    assert got["dialogs"] == 0, f"диалогов на пустой базе: {got['dialogs']}, ожидалось 0"
    assert got["messages"] == 0, f"сообщений на пустой базе: {got['messages']}, ожидалось 0"
    assert got["last_message_at"] is None, (
        f"время последнего сообщения на пустой базе: {got['last_message_at']!r}, "
        f"ожидалось None")


def test_snapshot_counts_does_not_modify_the_base(tmp_path):
    """Без него сама сверка величин писала бы в боевую базу — «только чтение» переставало бы быть правдой ровно в тот момент, когда её читают."""
    src_dir = tmp_path / "secrets"
    src_dir.mkdir()
    src = src_dir / "demo.db"
    _seed_base(src, {CID: 9}, base_ts=1_700_000_000.0)

    before_listing = _listing(src_dir)
    before_fp = _fingerprint(src)
    time.sleep(1.1)

    snapshot_counts(src)

    assert _listing(src_dir) == before_listing, (
        f"подсчёт величин изменил состав каталога: было {before_listing}, "
        f"стало {_listing(src_dir)}")
    assert _fingerprint(src) == before_fp, (
        f"подсчёт величин изменил базу: (size, mtime_ns, sha256) {before_fp} -> "
        f"{_fingerprint(src)}")


def test_snapshot_counts_does_not_migrate_a_legacy_base(tmp_path):
    """Без него сверка величин МИГРИРОВАЛА бы боевую базу и сыпала `.bak` рядом с ней: читающий шаг бэкапа менял бы то, что бэкапит.

    На базе актуальной схемы `Store` файла не трогает, поэтому предыдущий
    сторож на неё слеп по построению. Отставшая схема — то состояние, в
    котором разница видна: `Store.__init__` дописывает колонки и, раз база
    существовала, делает копию `*.pre-3a-*.bak` РЯДОМ С ИСТОЧНИКОМ.
    """
    src_dir = tmp_path / "secrets"
    src_dir.mkdir()
    src = src_dir / "legacy.db"
    conn = sqlite3.connect(str(src))
    try:
        # contacts БЕЗ display_name и прочих колонок из _ADDED_COLUMNS
        conn.execute("CREATE TABLE contacts (contact_id TEXT PRIMARY KEY, "
                     "state TEXT NOT NULL DEFAULT 'new')")
        conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                     "contact_id TEXT NOT NULL, role TEXT NOT NULL, "
                     "text TEXT NOT NULL, ts REAL NOT NULL)")
        for cid in ("lead-1", "lead-2"):
            conn.execute("INSERT INTO contacts(contact_id) VALUES (?)", (cid,))
            for i in range(3):
                conn.execute("INSERT INTO messages(contact_id, role, text, ts) "
                             "VALUES (?,?,?,?)", (cid, "user", "hi", 1000.0 + i))
        conn.commit()
    finally:
        conn.close()

    before_listing = _listing(src_dir)
    before_fp = _fingerprint(src)
    time.sleep(1.1)

    got = snapshot_counts(src)

    assert got["dialogs"] == 2 and got["messages"] == 6, (
        f"на отставшей схеме получено {got['dialogs']} диалогов и "
        f"{got['messages']} сообщений, в базе 2 и 6")
    assert _listing(src_dir) == before_listing, (
        f"подсчёт величин изменил состав каталога источника: было "
        f"{before_listing}, стало {_listing(src_dir)} — рядом с боевой базой "
        f"появился файл")
    assert _fingerprint(src) == before_fp, (
        f"подсчёт величин изменил отставшую базу: {before_fp} -> "
        f"{_fingerprint(src)}")

    # Предпосылка: через `Store` та же база мигрирует и обзаводится соседом.
    twin_dir = tmp_path / "twin"
    twin_dir.mkdir()
    twin = twin_dir / "legacy.db"
    shutil.copy2(src, twin)
    with Store(twin):
        pass
    assert len(_listing(twin_dir)) > 1, (
        f"предпосылка сторожа не воспроизвелась: после открытия через Store в "
        f"каталоге {_listing(twin_dir)} — ожидался ещё и .bak рядом")


def test_missing_table_is_an_error_not_a_zero(tmp_path):
    """Без него база БЕЗ таблицы `messages` отчиталась бы «0 сообщений», и пустой бэкап уехал бы в манифест как исправный."""
    src = tmp_path / "broken.db"
    conn = sqlite3.connect(str(src))
    try:
        conn.execute("CREATE TABLE contacts (contact_id TEXT PRIMARY KEY, "
                     "state TEXT NOT NULL DEFAULT 'new')")
        conn.execute("INSERT INTO contacts(contact_id) VALUES ('lead-1')")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(SnapshotError):
        snapshot_counts(src)

    # Почему величины нельзя читать через `Store` (контракт называет это
    # отдельно): `Store` исполняет DDL при открытии. На той же базе он не
    # краснеет — он СОЗДАЁТ пропавшую таблицу, отвечает «0 сообщений» и
    # оставляет файл другим. Это и есть тихий ноль, от которого сторож стоит.
    twin = tmp_path / "broken_twin.db"
    shutil.copy2(src, twin)
    before_fp = _fingerprint(twin)
    with Store(twin) as store:
        via_store = len(store.history("lead-1"))
    after_fp = _fingerprint(twin)
    assert via_store == 0 and after_fp != before_fp, (
        f"предпосылка сторожа не воспроизвелась: через Store получено "
        f"{via_store} сообщений (ожидался тихий 0), файл "
        f"{'не изменился' if after_fp == before_fp else 'изменился'} "
        f"(ожидалось: изменился, потому что Store исполнил DDL)")
