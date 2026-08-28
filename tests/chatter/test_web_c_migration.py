# -*- coding: utf-8 -*-
"""Сторожа 21-27 спеки «ВЕБ, волна 2 / пара C» — МИГРАЦИЯ ЖИВЫХ БАЗ.

Спека: `docs/superpowers/specs/2026-08-28-web-c-envelope-identity.md`,
§1.1, §4, §9 (сторожа 21-27).

🔴 РИСК НАЗВАН СПЕКОЙ ПРЯМО (§8): это первая правка, которая ПЕРЕПИСЫВАЕТ
живые данные обеих клиенток. Волна 1 меняла код при неизменных данных; здесь
наоборот. Поэтому все сторожа ниже меряют не «функция отработала», а состояние
БАЗЫ до и после — включая то, чего в ней быть НЕ ДОЛЖНО.

ЖИВЫХ БАЗ ЗДЕСЬ НЕТ И БЫТЬ НЕ МОЖЕТ. Стенд собирается в `tmp_path`: свежая
база, потом СЫРЫМ sqlite3 в неё кладётся старая форма (мимо кода, чтобы
стенд не зависел от того, что этот код уже умеет), потом `Store` открывается
заново — и миграция обязана произойти в конструкторе (§4.2).

ПОЧЕМУ МИГРАЦИЯ В `Store.__init__`, А НЕ СКРИПТОМ, — решено спекой §4.2 по
образцу перестройки `payments`, и сторожа опираются на это как на договор:
идемпотентность по ФАКТУ, бэкап только при реальной миграции, проверка ПОСЛЕ.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from chatter.storage.db import Store

# ═══ §4.1: ЛИТЕРАЛЬНЫЙ список из 13 таблиц СО СХЕМЫ ═════════════════════════
# «а не те таблицы, где сегодня есть строки»: данные сегодня в 9 из них, и
# список, выведенный из наличия строк, промолчит в день, когда наполнится
# десятая ([[jarvis-literal-lists-not-introspection]]).
MIGRATED_TABLES = (
    "contacts",
    "messages",
    "facts",
    "console_cards",
    "control_events",
    "status_index",
    "contact_profile",
    "contact_obligations",
    "funnel_transitions",
    "payments",
    "quotes",
    "invoices",
    "outgoing_queue",
)

# ═══ §1.1 / §4.1: ТРИ префикса ключей `runtime_flags` ═══════════════════════
# `contact_id` живёт не только в колонках. Миграция, написанная как
# `UPDATE ... SET contact_id=...`, не тронет НИ ОДНОГО из 17 живых ключей:
# каждая живая карточка эскалации и каждая серия промахов профиля осиротеет
# молча.
FLAG_PREFIXES = ("esc_active:", "profile_miss:", "profile_miss_alerted:")

OLD_A = "237616472:volska"
NEW_A = "telegram:237616472:volska"
OLD_B = "-1001234567890:demo"
NEW_B = "telegram:-1001234567890:demo"


# ═══ оснастка стенда ════════════════════════════════════════════════════════

def _schema_tables_with_contact_id(conn) -> set[str]:
    out = set()
    for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % name)]
        if "contact_id" in cols:
            out.add(name)
    return out


def _dummy(decl: str, col: str, seq: int):
    """Значение-заглушка, УНИКАЛЬНОЕ на строку.

    Уникальность не косметика: у `outgoing_queue.token` стоит UNIQUE, и стенд,
    кладущий одну и ту же заглушку двум контактам, падал бы на IntegrityError
    — то есть краснел бы по вине стенда, а не по предмету спора."""
    d = (decl or "").upper()
    if "INT" in d:
        return seq
    if "REAL" in d or "FLOA" in d or "DOUB" in d:
        return 1000.0 + seq
    return "x-%s-%d" % (col, seq)


def _insert_row(conn, table: str, contact_id: str, *, seq: int) -> None:
    """Одна строка в таблицу с `contact_id`, заполненная по СХЕМЕ.

    По схеме, а не литералом на таблицу: литеральный набор колонок здесь
    сторожил бы схему, а не миграцию, и краснел бы от каждой новой колонки —
    то есть перестал бы читаться."""
    info = list(conn.execute("PRAGMA table_info(%s)" % table))
    n_pk = sum(1 for r in info if r[5])
    cols, vals = [], []
    for _cid, name, decl, notnull, _dflt, pk in info:
        if pk and n_pk == 1 and "INT" in (decl or "").upper():
            # одноколоночный INTEGER PRIMARY KEY = алиас rowid, считает сам.
            # СОСТАВНОЙ ключ так пропускать нельзя: `contact_profile.version`
            # входит в PRIMARY KEY и при этом NOT NULL — стенд, молча его
            # выкинувший, падал бы на IntegrityError, а не на предмете спора.
            continue
        if name == "contact_id":
            cols.append(name)
            vals.append(contact_id)
            continue
        if pk or notnull:
            v = _dummy(decl, name, seq)
            cols.append(name)
            vals.append(v)
    conn.execute(
        "INSERT INTO %s (%s) VALUES (%s)"
        % (table, ", ".join(cols), ", ".join("?" * len(cols))), vals)


def _seed_old(path: Path, contacts=(OLD_A, OLD_B)) -> dict:
    """Свежая база со СТАРОЙ формой во всех 13 таблицах и трёх префиксах
    флагов. Возвращает число строк на таблицу — базлайн сторожа 23."""
    with Store(path):
        pass                                # схема
    conn = sqlite3.connect(str(path))
    try:
        seq = 0
        for cid in contacts:
            for table in MIGRATED_TABLES:
                seq += 1
                _insert_row(conn, table, cid, seq=seq)
            for pref in FLAG_PREFIXES:
                conn.execute(
                    "INSERT OR REPLACE INTO runtime_flags(key, value, ts) VALUES (?,?,?)",
                    (pref + cid, "1", 1000.0))
        conn.commit()
        counts = {t: conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                  for t in MIGRATED_TABLES}
    finally:
        conn.close()
    return counts


def _two_segment_left(path: Path) -> dict[str, list[str]]:
    """Что осталось в СТАРОЙ форме — по всем 13 таблицам и трём префиксам."""
    left: dict[str, list[str]] = {}
    conn = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True)
    try:
        for table in MIGRATED_TABLES:
            rows = [r[0] for r in conn.execute(
                "SELECT DISTINCT contact_id FROM %s" % table) if r[0] is not None]
            bad = [v for v in rows if str(v).count(":") != 2]
            if bad:
                left[table] = bad
        keys = [r[0] for r in conn.execute("SELECT key FROM runtime_flags")]
        bad_keys = [k for k in keys
                    for p in FLAG_PREFIXES
                    if k.startswith(p) and k[len(p):].count(":") != 2]
        if bad_keys:
            left["runtime_flags"] = bad_keys
    finally:
        conn.close()
    return left


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _baks(path: Path) -> list[str]:
    return sorted(p.name for p in path.parent.glob("*.bak"))


# ═══ Сторож 27 (сначала: он судит САМ СПИСОК) ═══════════════════════════════

def test_guard27_spisok_tablits_LITERALEN_i_soglasen_so_shemoy(tmp_path):
    """Сторож 27: списки таблиц и префиксов ключей — ЛИТЕРАЛЬНЫЕ, со встречной
    половиной.

    Прямая половина: каждая названная таблица в схеме ЕСТЬ и держит колонку
    `contact_id` — иначе строка списка сторожит пустоту.
    Встречная: таблица со схемной колонкой `contact_id`, отсутствующая в
    списке, — КРАСНОЕ. Без неё день, когда в схему добавят четырнадцатую
    таблицу с contact_id, наступит молча, и её строки останутся в старой
    форме после миграции — то есть навсегда."""
    with Store(tmp_path / "schema.db") as s:
        actual = _schema_tables_with_contact_id(s._conn)
    named = set(MIGRATED_TABLES)
    assert len(MIGRATED_TABLES) == 13, (
        "в списке §4.1 %d таблиц, спека называет 13." % len(MIGRATED_TABLES))
    missing = sorted(named - actual)
    extra = sorted(actual - named)
    assert not missing, (
        "таблицы %r названы миграцией, но колонки `contact_id` в схеме у них "
        "нет (или самих таблиц нет). Строка списка, которая ничего не "
        "мигрирует, создаёт вид проверенного." % missing)
    assert not extra, (
        "у таблиц %r в схеме ЕСТЬ `contact_id`, а в списке миграции §4.1 их "
        "нет. Их строки останутся в старой форме НАВСЕГДА: миграция идёт "
        "один раз, по факту «двухсегментных не осталось»." % extra)


def test_guard27_prefiksy_klyuchey_LITERALNY_i_ih_ne_stalo_bolshe():
    """Сторож 27, вторая половина: префиксов ключей `runtime_flags`, вшивающих
    `contact_id`, ровно три — и четвёртый обязан краснеть.

    Замер §1.1 нашёл их поимённо: `esc_active:` (`core/escalation.py`),
    `profile_miss:` и `profile_miss_alerted:` (`core/classifier.py`). Четвёртый,
    заведённый завтра, миграция не тронет — а сторож на «UPDATE колонок»
    этого не заметит вовсе."""
    import ast

    repo = Path(__file__).resolve().parents[2]
    found: set[str] = set()
    for rel in ("chatter/core/escalation.py", "chatter/core/classifier.py",
                "chatter/notify/control_bot.py", "chatter/run.py",
                "chatter/telethon_run.py", "chatter/storage/db.py"):
        tree = ast.parse((repo / rel).read_text(encoding="utf-8-sig"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.JoinedStr) or not n.values:
                continue
            head = n.values[0]
            if not (isinstance(head, ast.Constant) and isinstance(head.value, str)):
                continue
            if not head.value.endswith(":"):
                continue
            if any(isinstance(v, ast.FormattedValue)
                   and isinstance(v.value, ast.Name)
                   and v.value.id in ("contact_id", "cid") for v in n.values):
                found.add(head.value)
    assert found == set(FLAG_PREFIXES), (
        "префиксы ключей runtime_flags, вшивающие contact_id, разошлись со "
        "списком §4.1.\n  список: %r\n  найдено в коде: %r\n"
        "Каждый ненайденный списком префикс — это живая карточка эскалации или "
        "серия промахов профиля, которая после миграции осиротеет МОЛЧА."
        % (sorted(FLAG_PREFIXES), sorted(found)))


# ═══ Сторож 21 ══════════════════════════════════════════════════════════════

def test_guard21_posle_migratsii_dvuhsegmentnyh_NOL_vo_vseh_13_tablitsah(tmp_path):
    """Сторож 21: после миграции двухсегментных `contact_id` — НОЛЬ во всех
    тринадцати таблицах списка §4.1.

    Живой объём — 738 значений на двух базах (§1.1); на стенде важен не объём,
    а ПОЛНОТА охвата: миграция, забывшая одну таблицу из тринадцати, выглядит
    как успех ровно до дня, когда в забытую таблицу заглянут."""
    db = tmp_path / "live.db"
    _seed_old(db)
    with Store(db):
        pass
    left = _two_segment_left(db)
    assert not left, (
        "после миграции старая форма осталась: %r. §4.4 п.4: ассерт ПОСЛЕ — "
        "двухсегментных ноль во всех 13 таблицах и ноль ключей по трём "
        "префиксам; «миграция, о результате которой не спросили, — это надежда, "
        "а не миграция»." % left)


# ═══ Сторож 22 ══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("prefix", FLAG_PREFIXES)
def test_guard22_klyuchi_runtime_flags_migrirovany(tmp_path, prefix):
    """Сторож 22: ключи `runtime_flags` мигрированы — по КАЖДОМУ из трёх
    префиксов отдельно.

    Пин ловит миграцию, написанную только по колонкам: `UPDATE ... SET
    contact_id=...` не тронет ни одного из 17 живых ключей (§1.1). Отдельным
    параметром на префикс, потому что «забыли `profile_miss_alerted:`» — это
    свой диагноз: серия промахов профиля перестанет обрываться, и алерт о ней
    придёт снова на уже разобранный случай."""
    db = tmp_path / "flags.db"
    _seed_old(db)
    with Store(db):
        pass
    conn = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
    try:
        keys = [r[0] for r in conn.execute(
            "SELECT key FROM runtime_flags WHERE key LIKE ?", (prefix + "%",))]
    finally:
        conn.close()
    assert keys, (
        "ключей с префиксом %r в базе не осталось вовсе: миграция их не "
        "переписала, а УДАЛИЛА. Живая карточка эскалации без своего ключа — "
        "это карточка, которую нечем закрыть." % prefix)
    old = [k for k in keys if k[len(prefix):].count(":") == 1]
    assert not old, (
        "ключи %r остались в СТАРОЙ форме. Миграция написана по колонкам и "
        "ключей не видит (§1.1): каждая живая карточка эскалации и каждая "
        "серия промахов профиля осиротеет молча." % old)
    assert all(k[len(prefix):].startswith("telegram:") for k in keys), (
        "ключи есть, но голова канала в них не подставлена: %r" % keys)


# ═══ Сторож 23 ══════════════════════════════════════════════════════════════

def test_guard23_chislo_strok_v_kazhdoy_tablitse_DO_i_POSLE_sovpalo(tmp_path):
    """Сторож 23: число строк в каждой таблице ДО и ПОСЛЕ совпало.

    §4.4 п.4 называет причину: «замена, потерявшая строку, иначе выглядит как
    успех». Самый правдоподобный способ потерять строку — `INSERT OR REPLACE`
    или перестройка таблицы, на которой UNIQUE схлопнул две строки в одну:
    двухсегментных не осталось (сторож 21 зелёный), а переписки лида нет."""
    db = tmp_path / "counts.db"
    before = _seed_old(db)
    with Store(db):
        pass
    assert not _two_segment_left(db), (
        "предпосылка сторожа: миграция ДОЛЖНА была произойти. Пока её нет, "
        "«число строк не изменилось» выполняется тривиально — сторож зелен "
        "по построению и не значит ничего.")
    conn = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
    try:
        after = {t: conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                 for t in MIGRATED_TABLES}
    finally:
        conn.close()
    diff = {t: (before[t], after[t]) for t in MIGRATED_TABLES if before[t] != after[t]}
    assert not diff, (
        "после миграции число строк изменилось (таблица: было -> стало): %r. "
        "Строка, потерянная при замене, — это переписка лида, которой больше "
        "нет, и восстановить её можно только из `.bak`." % diff)


# ═══ Сторож 24 ══════════════════════════════════════════════════════════════

def test_guard24_povtornyy_zapusk_nichego_ne_menyaet_i_ne_syplet_bak(tmp_path):
    """Сторож 24: идемпотентность — второй запуск на уже мигрированной базе не
    меняет НИ ОДНОГО значения и не создаёт нового `.bak`.

    §4.2: идемпотентность держится на ФАКТЕ (`contact_id NOT LIKE '%:%:%'`), а
    не на ловле исключения и не на файле-маркере. Причина названа дословно:
    гардиан перезапускает раннер постоянно, и миграция, падающая на втором
    прогоне, — краш-петля, которую он будет вечно поддерживать. Второй повод
    — `.bak`: это ПОЛНАЯ переписка лидов открытым файлом, и сыпать её на
    каждый рестарт значит плодить то, что никто не удаляет (§10 п.2: шесть
    таких копий уже лежат, старейшей месяц)."""
    db = tmp_path / "idem.db"
    _seed_old(db)
    with Store(db):
        pass
    assert not _two_segment_left(db), (
        "предпосылка сторожа: ПЕРВЫЙ запуск обязан был мигрировать базу. Без "
        "этого «второй запуск ничего не изменил» верно и для миграции, которой "
        "нет вовсе — то есть сторож зелен по построению.")
    sha_after_first = _sha(db)
    baks_after_first = _baks(db)

    with Store(db):
        pass
    assert _sha(db) == sha_after_first, (
        "второй запуск изменил базу побайтово: миграция не идемпотентна. "
        "Гардиан поднимает раннер постоянно — значит она будет переписывать "
        "базу снова и снова.")
    assert _baks(db) == baks_after_first, (
        "второй запуск создал новый `.bak` (было %r, стало %r). Бэкап — "
        "только при РЕАЛЬНОЙ миграции существующей базы (§4.2), иначе каждый "
        "рестарт сыплет клиенту полную копию его переписки."
        % (baks_after_first, _baks(db)))


def test_guard24_pri_realnoy_migratsii_bak_vsyo_taki_snimaetsya(tmp_path):
    """Сторож 24, встречная половина: при РЕАЛЬНОЙ миграции `.bak` обязан
    появиться.

    Без неё сторож выше выполняется идеально миграцией, которая бэкапа не
    делает НИКОГДА, — а §4.4 п.3 и §8 п.1 говорят прямо: ошибка необратима без
    `.bak`, и снимок проверяется на читаемость, а не на наличие."""
    db = tmp_path / "backup.db"
    _seed_old(db)
    assert _baks(db) == [], "предпосылка: до миграции `.bak` рядом нет"
    with Store(db):
        pass
    baks = _baks(db)
    assert baks, (
        "миграция переписала живые данные и НЕ сняла `.bak`. §8 п.1: ошибка "
        "необратима без него; это первая правка, которая трогает данные "
        "обеих клиенток.")
    for name in baks:
        copy = db.parent / name
        conn = sqlite3.connect("file:%s?mode=ro" % copy.as_posix(), uri=True)
        try:
            n = conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        finally:
            conn.close()
        assert n, (
            "снимок %s открывается, но `contacts` в нём пуст. §4.4 п.3: "
            "снимок, который не открывается (или пуст), — это не откат." % name)


# ═══ Сторож 25 ══════════════════════════════════════════════════════════════

def test_guard25_nechislovaya_golova_OSTANAVLIVAET_migratsiyu(tmp_path):
    """Сторож 25: контакт с НЕЧИСЛОВОЙ головой в старой базе → миграция
    ОТКАЗЫВАЕТ и базу не трогает.

    §4.1: правило замены одно — `"<peer>:<slug>"` → `"telegram:<peer>:<slug>"`,
    и только если голова числовая (с допуском `-`). Всё остальное — СТОП.

    Почему стоп, а не «пропустить эту строку»: нечисловая голова означает, что
    в базе лежит форма, которой по замеру там быть не может (12 из 12 контактов
    телеграмные, голова числовая у всех). Домигрировать вокруг неё — значит
    угадать, чем она была; а угадывать в необратимой правке живых данных
    нельзя.

    Пин двойной: отказ ГРОМКИЙ и база ПОБАЙТОВО та же."""
    db = tmp_path / "refuse.db"
    _seed_old(db, contacts=("console-user:demo",))
    before = _sha(db)
    with pytest.raises(RuntimeError):
        # РОВНО `RuntimeError`, а не любое исключение (решение владельца
        # 28.08): миграция отказывает по СОСТОЯНИЮ базы, а не по значению
        # аргумента, и ловят эти два разные обработчики. `ValueError` здесь
        # проехал бы мимо того, кто ждёт отказа состояния, — и наоборот.
        with Store(db):
            pass
    assert _sha(db) == before, (
        "после отказа база изменилась побайтово. §4.1: «Всё остальное — СТОП, "
        "база не тронута». Наполовину переписанная база хуже непереписанной: "
        "откатить её можно только из `.bak`, а `.bak` при отказе не снимается.")


# ═══ Сторож 26 ══════════════════════════════════════════════════════════════

def test_guard26_otkaz_na_seredine_ostavlyaet_bazu_v_sostoyanii_DO(tmp_path):
    """Сторож 26: отказ на СЕРЕДИНЕ оставляет базу в состоянии ДО.

    §4.2: одна транзакция на всё — 738 значений в одном `BEGIN...COMMIT`.
    Причина конкретная и она про людей: панель читает ту же базу
    ОДНОВРЕМЕННО (`file:...?mode=ro`), и она обязана увидеть состояние ДО или
    ПОСЛЕ, но никогда МЕЖДУ — иначе владелец получает ленту, где половина
    контактов осиротела.

    Стенд: годный контакт И негодный в одной базе. Годный обязан остаться
    СТАРЫМ: миграция, переписавшая его и споткнувшаяся на втором, — это ровно
    то промежуточное состояние, которого быть не может."""
    db = tmp_path / "half.db"
    _seed_old(db, contacts=(OLD_A, "console-user:demo"))
    before = _sha(db)
    with pytest.raises(RuntimeError):       # отказ СОСТОЯНИЯ, см. сторож 25
        with Store(db):
            pass
    assert _sha(db) == before, (
        "база изменилась после отказа на середине: транзакции нет, и панель "
        "может прочитать состояние МЕЖДУ.")
    conn = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
    try:
        ids = {r[0] for r in conn.execute("SELECT contact_id FROM contacts")}
    finally:
        conn.close()
    assert OLD_A in ids and NEW_A not in ids, (
        "годный контакт уже переписан (%r), хотя миграция отказала: часть "
        "работы закоммичена. Это и есть половина ленты, осиротевшая для "
        "владельца." % sorted(ids))
