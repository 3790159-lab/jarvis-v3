# -*- coding: utf-8 -*-
"""Сторожа §6 спеки `docs/superpowers/specs/2026-08-26-drill-reset-table-coverage.md`.

Писаны ОТ ТЕКСТА СПЕКИ и ДО кода, другим автором
([[jarvis-guards-not-by-the-plan-author]]): иначе тест и правка унаследуют одно
неверное допущение.

🔴 ГЛАВНОЕ (§6.2). Список таблиц берётся ИЗ СХЕМЫ, поднятой живым
`chatter.storage.db.Store`, а не из литерала в этом файле. Литерал в тесте
согласится с литералом в коде и промолчит на новой таблице — то есть повторит
ровно тот дефект, который чиним. Ни один сторож ниже не перечисляет таблицы,
которые он «ожидает увидеть»: он считает СОСТАВ схемы и сверяет его с
категориями, взятыми из модуля.

Имена таблиц встречаются в файле ровно там, где спека выносит ИМЕННОЕ решение
(§3, §7.1): `outgoing_queue` в `_WIPE_TABLES`, `funnel_transitions` и
`status_index` в `_KEEP_TABLES`, `contacts` в `_RESET_IN_PLACE`. Это пин на
НАМЕРЕНИЕ (§6.6): без него «оставили» через полгода неотличимо от «забыли».

$0: временная SQLite, ни сети, ни Telethon, ни LLM.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "drill_reset.py"

# Четыре КАТЕГОРИИ §3 — столько, сколько реальных способов обращения к таблице.
# Это имена категорий из спеки, а не список таблиц: перечислять таблицы здесь
# запрещено §6.2.
_CATEGORY_NAMES = ("_WIPE_TABLES", "_WIPE_BY_INVOICE", "_KEEP_TABLES",
                   "_RESET_IN_PLACE")

# Таблица, которой нет НИ В ОДНОМ литерале — ни в коде, ни в этом файле как
# «ожидаемая». Она изображает таблицу, добавленную в схему завтра.
_GHOST = "guard_unknown_contact_table"
_GHOST_DDL = (
    f"CREATE TABLE {_GHOST} ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " contact_id TEXT NOT NULL,"
    " note TEXT NOT NULL,"
    " ts REAL NOT NULL);")

# Значения, которые обязаны быть осмысленными, а не «лишь бы NOT NULL».
_OVERRIDES = {
    # §6.3 говорит именно про `pending`-строки.
    "outgoing_queue": {"status": "pending"},
    # §6.7: строка контакта до сброса заведомо НЕ в исходном состоянии.
    "contacts": {"state": "qualified", "paused": 1},
}


# ── загрузка чинимого скрипта ─────────────────────────────────────────────

def _mod():
    """Скрипт грузится КАЖДЫЙ раз заново и под своим именем в sys.modules:
    соседний `tests/test_drill_reset.py` кладёт его под именем
    `drill_reset_script`, и общий модуль означал бы, что порядок прогонов
    влияет на результат."""
    spec = importlib.util.spec_from_file_location("drill_reset_under_guard",
                                                  _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["drill_reset_under_guard"] = mod
    spec.loader.exec_module(mod)
    return mod


def _categories(mod) -> dict:
    """Четыре категории из модуля. Нет любой — КРАСНОЕ, а не пропуск.

    Fail-closed нарочно: сторож, который «нечего проверять» превращает в
    зелёное, — это лампа, которая горит зелёным потому, что провода нет."""
    missing = [n for n in _CATEGORY_NAMES if not hasattr(mod, n)]
    assert not missing, (
        "в scripts/drill_reset.py нет категорий: " + ", ".join(missing) +
        ". §3 спеки требует ЧЕТЫРЕ категории — ровно столько, сколько реальных "
        "способов обращения к таблице; `contacts` покрыта ТРЕТЬИМ способом, и "
        "проверка «названа ли в одном из двух списков» краснела бы на ней ложно.")
    return {n: frozenset(getattr(mod, n)) for n in _CATEGORY_NAMES}


def _drill_contact(mod) -> str:
    contacts = sorted(mod.DRILL_CONTACTS)
    assert contacts, "DRILL_CONTACTS пуст — сброс не на чем проверять"
    return contacts[0]


FOREIGN = "999:guard-foreign-persona"


# ── схема поднимается ЖИВЫМ Store (§6.2) ──────────────────────────────────

def _raise_schema(path: Path) -> None:
    from chatter.storage.db import Store
    with Store(path):
        pass


def _table_names(conn) -> list:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _schema(conn) -> dict:
    """{таблица: [колонки]} — из sqlite_master живой базы, не из литерала."""
    return {t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")]
            for t in _table_names(conn)}


def _columns_meta(conn, table) -> list:
    return [{"name": r[1], "type": (r[2] or "").upper(), "notnull": r[3],
             "dflt": r[4], "pk": r[5]}
            for r in conn.execute(f"PRAGMA table_info({table})")]


def _dummy(col, table, salt):
    t = col["type"]
    if "INT" in t:
        return salt
    if "REAL" in t or "FLOA" in t or "DOUB" in t:
        return float(salt)
    return f"{table}-{col['name']}-{salt}"


def _seed_row(conn, table, contact, salt) -> None:
    """Одна строка контакта в ЛЮБОЙ таблице с `contact_id` — по её PRAGMA.

    Генератор нарочно не знает имён колонок: таблица, добавленная завтра,
    засеется сама, и сторожа §6.3/§6.6 её увидят."""
    over = _OVERRIDES.get(table, {})
    meta = _columns_meta(conn, table)
    # Псевдоним rowid — ТОЛЬКО одиночный INTEGER PRIMARY KEY. Колонка составного
    # ключа (`contact_profile.version`) объявлена NOT NULL и значение требует.
    solo_pk = sum(1 for c in meta if c["pk"]) == 1
    cols, vals = [], []
    for col in meta:
        name = col["name"]
        if name == "contact_id":
            value = contact
        elif name in over:
            value = over[name]
        elif solo_pk and col["pk"] and "INT" in col["type"]:
            continue                      # rowid назначит sqlite
        elif col["pk"] or (col["notnull"] and col["dflt"] is None):
            value = _dummy(col, table, salt)
        else:
            continue
        cols.append(name)
        vals.append(value)
    conn.execute(
        "INSERT INTO {} ({}) VALUES ({})".format(
            table, ", ".join(cols), ", ".join("?" * len(cols))), vals)


def _prepare(tmp_path, name, *, extra_ddl=None, contacts=None) -> str:
    """Живая схема + по строке на каждый contact_id в КАЖДОЙ таблице с ним."""
    path = tmp_path / name
    _raise_schema(path)
    who = contacts if contacts is not None else (_drill_contact(_mod()), FOREIGN)
    conn = sqlite3.connect(str(path))
    try:
        if extra_ddl:
            conn.executescript(extra_ddl)
        salt = 0
        for table, cols in _schema(conn).items():
            if "contact_id" not in cols:
                continue
            for contact in who:
                salt += 1
                _seed_row(conn, table, contact, salt)
        conn.commit()
    finally:
        conn.close()
    return str(path)


def _snapshot(db) -> dict:
    conn = sqlite3.connect(str(db))
    try:
        return {t: conn.execute(f"SELECT * FROM {t}").fetchall()
                for t in _table_names(conn)}
    finally:
        conn.close()


def _rows(db, table, contact=None, where_extra="") -> int:
    conn = sqlite3.connect(str(db))
    try:
        if contact is None:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        sql = f"SELECT COUNT(*) FROM {table} WHERE contact_id=?{where_extra}"
        return conn.execute(sql, (contact,)).fetchone()[0]
    finally:
        conn.close()


def _uncovered(schema: dict, cats: dict) -> dict:
    """Таблицы с `contact_id`, названные НЕ РОВНО в одной категории.

    Пусто = покрытие в обе стороны сошлось. Значение — кортеж категорий, в
    которых таблица нашлась: пустой кортеж читается как «нигде», кортеж длиной
    два и больше — как «названа дважды» (§6.1, оба случая красные)."""
    out = {}
    for table, cols in schema.items():
        if "contact_id" not in cols:
            continue
        hits = tuple(n for n, names in cats.items() if table in names)
        if len(hits) != 1:
            out[table] = hits
    return out


def _run_mod(mod, argv, capsys):
    """Прогон УЖЕ загруженного модуля — единственный способ проверить сверку
    при ПОДМЕНЕННЫХ категориях: `_mod()` перечитывает файл и подмену бы стёр."""
    rc = mod.main(list(argv))
    cap = capsys.readouterr()
    return rc, cap.out + cap.err


def _run(argv, capsys):
    """Прогон через ЕДИНСТВЕННУЮ публичную дверь скрипта — `main`."""
    return _run_mod(_mod(), argv, capsys)


# ── §6.1 покрытие в обе стороны ───────────────────────────────────────────

def test_every_contact_id_table_of_the_live_schema_is_named_exactly_once(tmp_path):
    """§6.1 + §6.2: схема → категории.

    Состав считается ИЗ СХЕМЫ живого Store. Литерала «ожидаемых таблиц» в
    сторожe нет намеренно: он согласился бы с литералом в коде и промолчал на
    таблице, появившейся завтра."""
    db = tmp_path / "schema.db"
    _raise_schema(db)
    conn = sqlite3.connect(str(db))
    try:
        schema = _schema(conn)
    finally:
        conn.close()

    cats = _categories(_mod())
    with_contact = [t for t, cols in schema.items() if "contact_id" in cols]
    assert with_contact, (
        "живой Store не поднял ни одной таблицы с contact_id — сторож был бы "
        "зелен ПО ПОСТРОЕНИЮ")

    bad = _uncovered(schema, cats)
    assert not bad, (
        "таблицы с contact_id, не названные РОВНО в одной категории: "
        + "; ".join(f"{t} -> {list(hits) or 'НИГДЕ'}" for t, hits in sorted(bad.items()))
        + ". Каждую надо назвать: `_WIPE_TABLES` (стирать по contact_id), "
          "`_KEEP_TABLES` (не трогать намеренно), `_WIPE_BY_INVOICE` "
          "(чистится через счёт) или `_RESET_IN_PLACE` (строка приводится к "
          "исходному, а не удаляется) — и написать, почему.")


def test_every_table_named_in_a_category_exists_in_the_live_schema(tmp_path):
    """§6.1, вторая сторона: категории → схема.

    Имя, которого в схеме нет, — это либо опечатка (DELETE уйдёт в
    OperationalError на прогоне дрила), либо строка, пережившая таблицу.
    Ловится в ДЕНЬ переименования, а не на прогоне через месяц."""
    db = tmp_path / "schema.db"
    _raise_schema(db)
    conn = sqlite3.connect(str(db))
    try:
        present = set(_table_names(conn))
    finally:
        conn.close()

    cats = _categories(_mod())
    ghosts = {name: sorted(tables - present)
              for name, tables in cats.items() if tables - present}
    assert not ghosts, (
        f"категории называют таблицы, которых нет в схеме живого Store: {ghosts}")


def test_no_table_is_named_in_two_categories(tmp_path):
    """§6.1: «таблица, названная в двух, — тоже красное».

    Проверяется по ВСЕМ именам категорий, включая таблицы без `contact_id`:
    два способа обращения к одной таблице — это два места, которые однажды
    разойдутся ([[jarvis-two-numbers-for-one-thing]])."""
    cats = _categories(_mod())
    seen: dict = {}
    for name, tables in cats.items():
        for table in tables:
            seen.setdefault(table, []).append(name)
    doubles = {t: n for t, n in seen.items() if len(n) > 1}
    assert not doubles, f"таблица названа больше чем в одной категории: {doubles}"


# ── §6.2 механизм сторожа: он видит схему, а не литерал ───────────────────

def test_coverage_rule_reacts_to_a_table_that_no_literal_knows(tmp_path):
    """§6.2, самопроверка сторожа.

    Если правило покрытия молчит на таблице, которой нет ни в одном литерале,
    то весь §6.1 — украшение. Тут в схему живого Store добавляется таблица с
    `contact_id`, о которой не знает ни код, ни этот файл, и правило обязано
    её назвать. Сторож, который не краснеет ни на чём, не красный, а слепой."""
    db = tmp_path / "ghost-schema.db"
    _raise_schema(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.executescript(_GHOST_DDL)
        conn.commit()
        schema = _schema(conn)
    finally:
        conn.close()

    bad = _uncovered(schema, _categories(_mod()))
    assert _GHOST in bad, (
        "правило покрытия не заметило таблицу, добавленную в схему: значит оно "
        "смотрит не на схему, а на литерал")
    assert bad[_GHOST] == (), f"{_GHOST} не должна была найтись ни в одной категории"


def test_the_live_schema_alone_does_not_trigger_the_refusal(tmp_path, capsys):
    """§6.1, поведенческая сторона: на ЧИСТОЙ живой схеме сверка молчит.

    Без этого сторожа «отказ на незнакомой таблице» можно было бы сдать
    отказом на ЛЮБОЙ — и дрил не запустился бы никогда."""
    mod = _mod()
    drill = _drill_contact(mod)
    db = _prepare(tmp_path, "clean.db")
    rc, out = _run([db, "--contact", drill, "--apply"], capsys)
    assert rc == 0, (
        "сверка схемы отказала на штатной базе живого Store — значит какая-то "
        f"из 4 категорий не покрывает свою таблицу.\n{out}")


# ── §6.3 outgoing_queue стирается по contact_id ───────────────────────────

def test_outgoing_queue_is_declared_wiped(tmp_path):
    """§6.3 + §3: пин на РЕШЕНИЕ, а не на текущее поведение.

    `outgoing_queue` едет в `_WIPE_TABLES`: задание, поставленное ДО сброса, к
    сценарию прогона отношения не имеет — сброс объявляет, что диалога не было.
    Довод §2: у этой таблицы есть исполнитель, смотрящий на неё каждые 5 секунд,
    и пережившее сброс задание глушит бота ПОСРЕДИ прогона."""
    cats = _categories(_mod())
    assert "outgoing_queue" in cats["_WIPE_TABLES"], (
        "outgoing_queue обязана быть в _WIPE_TABLES (§3): у неё есть "
        "исполнитель, и пережившее сброс задание уедет лиду посреди дрила")


def test_pending_rows_of_the_drill_contact_are_gone_and_the_foreign_ones_stay(
        tmp_path, capsys):
    """§6.3 буквально: после сброса `pending` у дрил-контакта ноль, а у ЧУЖОГО
    контакта они на месте."""
    mod = _mod()
    drill = _drill_contact(mod)
    db = _prepare(tmp_path, "queue.db")
    pend = " AND status='pending'"
    assert _rows(db, "outgoing_queue", drill, pend) == 1, "засев не сработал"
    assert _rows(db, "outgoing_queue", FOREIGN, pend) == 1, "засев не сработал"

    rc, out = _run([db, "--contact", drill, "--apply"], capsys)
    assert rc == 0, out
    assert _rows(db, "outgoing_queue", drill, pend) == 0, (
        "задание дрил-контакта пережило сброс: раннер заберёт его в первые "
        "секунды прогона и отправит лиду")
    assert _rows(db, "outgoing_queue", FOREIGN, pend) == 1, (
        "сброс дрил-контакта съел очередь ЧУЖОГО контакта")


def test_every_wipe_table_with_contact_id_is_emptied_only_for_the_drill(
        tmp_path, capsys):
    """§6.3, обобщённо: правило проверяется на ВСЁМ составе `_WIPE_TABLES`,
    а не на перечисленных сторожем именах.

    Таблица, добавленная в `_WIPE_TABLES` завтра и не стёртая на деле, покраснеет
    здесь сама."""
    mod = _mod()
    drill = _drill_contact(mod)
    cats = _categories(mod)
    db = _prepare(tmp_path, "wipe.db")

    conn = sqlite3.connect(db)
    try:
        schema = _schema(conn)
    finally:
        conn.close()
    targets = sorted(t for t in cats["_WIPE_TABLES"]
                     if "contact_id" in schema.get(t, []))
    assert targets, "_WIPE_TABLES не содержит ни одной таблицы с contact_id"

    rc, out = _run([db, "--contact", drill, "--apply"], capsys)
    assert rc == 0, out

    left = {t: _rows(db, t, drill) for t in targets}
    assert set(left.values()) == {0}, (
        f"строки дрил-контакта пережили сброс: "
        f"{ {t: n for t, n in left.items() if n} }")
    foreign = {t: _rows(db, t, FOREIGN) for t in targets}
    assert set(foreign.values()) == {1}, (
        f"сброс задел ЧУЖОЙ контакт: { {t: n for t, n in foreign.items() if n != 1} }")


# ── §6.4 / §6.5 / §6.8 отказ на незнакомой таблице ────────────────────────

def test_unknown_table_refuses_with_its_own_code_and_changes_nothing(
        tmp_path, capsys):
    """§6.4: незнакомая таблица с `contact_id` даёт ОТКАЗ с ОТДЕЛЬНЫМ кодом
    возврата, и ни одна строка в базе при этом не изменилась.

    Код не пиньтся числом: он ЗАМЕРЯЕТСЯ рядом. Три остальных исхода
    (успех, не-дрил-контакт, контакта нет в БД) снимаются в этом же прогоне, и
    код отказа обязан отличаться от каждого. Отдельность важнее конкретной
    цифры: скрипт зовут из харнесса, и «отказ» обязан быть отличим от
    «сработало» и от «не тот контакт»."""
    mod = _mod()
    drill = _drill_contact(mod)
    assert FOREIGN not in mod.DRILL_CONTACTS

    rc_ok, _ = _run([_prepare(tmp_path, "ok.db"), "--contact", drill, "--apply"],
                    capsys)
    rc_not_drill, _ = _run([_prepare(tmp_path, "nd.db"), "--contact", FOREIGN,
                            "--apply"], capsys)
    rc_missing, _ = _run([_prepare(tmp_path, "ms.db", contacts=(FOREIGN,)),
                          "--contact", drill, "--apply"], capsys)

    db = _prepare(tmp_path, "ghost.db", extra_ddl=_GHOST_DDL)
    before = _snapshot(db)
    rc_ghost, out = _run([db, "--contact", drill, "--apply"], capsys)
    after = _snapshot(db)

    assert rc_ghost != 0, (
        f"незнакомая таблица {_GHOST} не остановила сброс: предупреждение в "
        f"дриле никто не прочтёт, оно уедет в лог и будет замечено ПОСЛЕ того, "
        f"как прогон соврал.\n{out}")
    assert rc_ghost not in {rc_ok, rc_not_drill, rc_missing}, (
        f"код отказа на незнакомой таблице ({rc_ghost}) совпал с другим исходом: "
        f"успех={rc_ok}, не-дрил-контакт={rc_not_drill}, нет контакта={rc_missing}. "
        f"§4 требует СВОЙ код возврата")
    assert after == before, (
        "отказ обязан приходить ДО любой записи, а база изменилась: "
        + ", ".join(sorted(t for t in before if before[t] != after.get(t))))


def test_refusal_names_the_table_and_both_options(tmp_path, capsys):
    """§6.5: текст отказа называет ИМЯ таблицы И обе возможности.

    Отказ, не говорящий, что делать, превращается в «просто добавь куда-нибудь».

    Сверяются только СТРОКИ, которых нет в выводе штатного прогона: иначе
    сторож зеленел бы за счёт строк плана, где имена категорий могли бы
    печататься и без всякого отказа (тот же класс, что поймала мутация DEV-26
    на строке «сбрасываю:»)."""
    mod = _mod()
    drill = _drill_contact(mod)
    _, base = _run([_prepare(tmp_path, "base.db"), "--contact", drill], capsys)
    _, refusal = _run([_prepare(tmp_path, "g5.db", extra_ddl=_GHOST_DDL),
                       "--contact", drill], capsys)

    known = {ln.strip() for ln in base.splitlines()}
    new = "\n".join(ln for ln in refusal.splitlines()
                    if ln.strip() and ln.strip() not in known)
    assert new.strip(), f"отказ не сказал НИЧЕГО нового:\n{refusal}"
    assert _GHOST in new, (
        f"отказ не назвал таблицу {_GHOST} — читателю нечего искать:\n{new}")
    for option in ("_WIPE_TABLES", "_KEEP_TABLES"):
        assert option in new, (
            f"отказ не назвал возможность {option}: «добавь её в _WIPE_TABLES "
            f"(стирать) или в _KEEP_TABLES (не трогать) — и напиши, почему».\n{new}")


def test_dry_run_also_refuses_on_unknown_table(tmp_path, capsys):
    """§6.8: сухой прогон (без `--apply`) при незнакомой таблице тоже ОТКАЗЫВАЕТ —
    иначе план покажет чистоту, которой не будет.

    Код сверяется с кодом отказа в режиме `--apply`, замеренным тут же: тот же
    отказ, а не «какое-нибудь ненулевое»."""
    mod = _mod()
    drill = _drill_contact(mod)
    rc_apply, _ = _run([_prepare(tmp_path, "ga.db", extra_ddl=_GHOST_DDL),
                        "--contact", drill, "--apply"], capsys)
    rc_plan, out = _run([_prepare(tmp_path, "gp.db", extra_ddl=_GHOST_DDL),
                         "--contact", drill], capsys)
    assert rc_plan != 0, (
        f"сухой прогон при незнакомой таблице {_GHOST} отчитался о плане: "
        f"человек прочтёт чистоту, которой не будет.\n{out}")
    assert rc_plan == rc_apply, (
        f"сухой прогон отказал ИНАЧЕ, чем --apply ({rc_plan} против {rc_apply}): "
        f"предохранитель обязан быть одинаков для плана и для применения")


# ── §6.6 funnel_transitions и status_index переживают сброс ───────────────

def test_funnel_transitions_and_status_index_are_declared_kept(tmp_path):
    """§6.6 + §7.1 (решение владельца 27.08): пин на НАМЕРЕНИЕ.

    `funnel_transitions` — БУХГАЛТЕРИЯ: лента переходов, тот же класс, что
    `control_events`. Оговорка произносится вслух: она копится сквозь сбросы,
    мы это ЗНАЕМ и оставляем намеренно.
    `status_index` — ПРОИЗВОДНАЯ: перевыпускается целиком в `issue_status_index`,
    и чистить её здесь значило бы завести второе место, где она чистится.

    Без этого сторожа «оставили» через полгода неотличимо от «забыли»."""
    keep = _categories(_mod())["_KEEP_TABLES"]
    for table in ("funnel_transitions", "status_index"):
        assert table in keep, (
            f"{table} обязана быть в _KEEP_TABLES — это записанное решение "
            f"владельца от 27.08 (§7.1), а не текущее поведение")


def test_every_keep_table_survives_the_reset_row_for_row(tmp_path, capsys):
    """§6.6, обобщённо: НИ ОДНА строка НИ В ОДНОЙ таблице `_KEEP_TABLES` не
    исчезает после сброса — ни у дрил-контакта, ни у чужого.

    Считается состав `_KEEP_TABLES`, а не перечисленные сторожем имена: таблица,
    объявленная бухгалтерией завтра, проверится сама."""
    mod = _mod()
    drill = _drill_contact(mod)
    keep = sorted(_categories(mod)["_KEEP_TABLES"])
    db = _prepare(tmp_path, "keep.db")
    before = {t: _snapshot(db)[t] for t in keep}
    assert any(before.values()), "ни одна _KEEP-таблица не засеяна — сторож пуст"

    rc, out = _run([db, "--contact", drill, "--apply"], capsys)
    assert rc == 0, out

    after = {t: _snapshot(db)[t] for t in keep}
    changed = sorted(t for t in keep if before[t] != after[t])
    assert not changed, (
        f"сброс переписал бухгалтерию: {changed}. Контракт сброса это прямо "
        f"запрещает — это записи о том, что происходило, а не состояние, из "
        f"которого бот делает следующий шаг")


# ── §6.7 contacts приводится к исходному, а не удаляется ──────────────────

def test_contacts_is_declared_reset_in_place_and_not_wiped(tmp_path):
    """§6.7 + §1: `contacts` покрыта ТРЕТЬИМ способом.

    Она обязана быть в `_RESET_IN_PLACE` и НЕ быть в `_WIPE_TABLES`: удалить
    строку контакта — значит получить код 1 «контакта нет в БД» на следующем же
    сбросе. И сторож покрытия не требует для неё `_WIPE` — иначе первый автор
    добавил бы `contacts` в исключения, а исключение, добавленное чтобы сторож
    замолчал, это дыра с подписью."""
    cats = _categories(_mod())
    assert "contacts" in cats["_RESET_IN_PLACE"], (
        "contacts обязана быть в _RESET_IN_PLACE (§3): её строка приводится к "
        "исходному состоянию, а не удаляется")
    assert "contacts" not in cats["_WIPE_TABLES"], (
        "contacts в _WIPE_TABLES — сброс удалил бы строку контакта, и следующий "
        "сброс отчитался бы «контакта нет в БД»")


def test_contacts_row_survives_and_returns_to_the_initial_state(tmp_path, capsys):
    """§6.7, поведение: строка НЕ удаляется, а приводится к исходному.

    Забытый `paused=1` означает, что бот молчит и весь прогон честно упирается
    в таймаут — «сброшенный» контакт обязан быть говорящим."""
    mod = _mod()
    drill = _drill_contact(mod)
    db = _prepare(tmp_path, "contacts.db")
    assert _rows(db, "contacts", drill) == 1

    rc, out = _run([db, "--contact", drill, "--apply"], capsys)
    assert rc == 0, out

    assert _rows(db, "contacts", drill) == 1, (
        "строка контакта удалена: следующий сброс отчитается «контакта нет в БД»")
    assert _rows(db, "contacts", FOREIGN) == 1, "удалена строка ЧУЖОГО контакта"

    conn = sqlite3.connect(db)
    try:
        state, paused = conn.execute(
            "SELECT state, paused FROM contacts"
            " WHERE contact_id=?", (drill,)).fetchone()
    finally:
        conn.close()
    assert (state, paused) == ("new", 0), (
        f"контакт не приведён к исходному: state={state}, paused={paused}. "
        f"Бот, оставшийся на паузе, промолчит весь прогон")


# ── ДЕТЕКТОР двойного имени: сверка на прогоне, а не только по данным ─────
#
# `test_no_table_is_named_in_two_categories` выше утверждает про САМИ СПИСКИ и
# ловит автора, который сегодня положил таблицу в две категории. Сторожа ниже —
# про ДРУГОЙ случай: база и код разъехались, категории правят руками на живой
# машине, и сверка ПЕРЕД сбросом обязана сказать об этом словами до того, как
# что-то тронет. Без них детектор дублей можно молча выкинуть, и суита этого не
# заметит: проверка по данным осталась бы зелёной, потому что она смотрит на
# литералы, а не на механизм.

_PATCH_SEAM = (
    "сторож не смог сделать подмену категорий НАБЛЮДАЕМОЙ: собранный на импорте "
    "контейнер категорий имеет форму, которую обход по значению не разобрал. "
    "Это шов ТЕСТА, а не дефект реализации — чинить здесь, в `_force_categories`. "
    "Зелёным этот случай быть не может: проверять было бы нечего.")


def _flat_strings(node) -> list:
    """Все строки контейнера, какой бы формы он ни был."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        out = []
        for key, value in node.items():
            out += _flat_strings(key) + _flat_strings(value)
        return out
    if isinstance(node, (list, tuple, set, frozenset)):
        out = []
        for value in node:
            out += _flat_strings(value)
        return out
    return []


def _substitute(node, pairs):
    """Замена состава категории ВНУТРИ контейнера — по значению и рекурсивно.

    Форма контейнера сторожу неизвестна и знать её он не должен: словарь
    «имя → состав», кортеж кортежей, пары «имя, состав» обходятся одинаково.
    Сверка по форме привязала бы сторожа к устройству реализации, а не к её
    утверждению."""
    for old, new in pairs:
        if type(node) is type(old) and node == old:
            return new
    if isinstance(node, dict):
        return {k: _substitute(v, pairs) for k, v in node.items()}
    if isinstance(node, (list, tuple, set, frozenset)):
        return type(node)(_substitute(v, pairs) for v in node)
    return node


def _force_categories(mod, values: dict):
    """Подменить четыре категории в УЖЕ загруженном модуле.

    Одних модульных имён мало: контейнер, собранный на импорте, держит ССЫЛКИ
    на старые кортежи, и `setattr` по имени его не задел бы. Возвращает плоский
    состав контейнера, если контейнер найден, иначе None."""
    pairs = [(getattr(mod, name), new) for name, new in values.items()]
    for name, new in values.items():
        setattr(mod, name, new)
    for holder in ("_ALL_CATEGORIES",):
        if hasattr(mod, holder):
            setattr(mod, holder, _substitute(getattr(mod, holder), pairs))
            return _flat_strings(getattr(mod, holder))
    return None


def _pick_double(mod, db) -> str:
    """Какую таблицу назвать дважды — выбирается СЧЁТОМ, а не именем.

    Первая по алфавиту таблица `_KEEP_TABLES`, у которой есть `contact_id`. Она
    же самый злой случай: «стереть по contact_id» и «не трогать НАМЕРЕННО» об
    одной строке одновременно — два места, которые уже разошлись."""
    conn = sqlite3.connect(db)
    try:
        schema = _schema(conn)
    finally:
        conn.close()
    picks = sorted(t for t in _categories(mod)["_KEEP_TABLES"]
                   if "contact_id" in schema.get(t, []))
    assert picks, "_KEEP_TABLES без таблиц с contact_id — дубль вносить не на чем"
    return picks[0]


def _apply_double(mod, table) -> bool:
    """Кладёт `table` ВТОРОЙ раз, в `_WIPE_TABLES`. False — подмена не видна."""
    values = {n: tuple(getattr(mod, n)) for n in _CATEGORY_NAMES}
    values["_WIPE_TABLES"] = values["_WIPE_TABLES"] + (table,)
    flat = _force_categories(mod, values)
    return flat is None or flat.count(table) >= 2


def test_patched_categories_without_a_double_run_clean(tmp_path, capsys):
    """Контроль честности трёх сторожей ниже: та же подмена, но БЕЗ дубля —
    и прогон зелёный.

    Без него красное на дубле неотличимо от «подмена сама всё сломала», а
    сторож, который краснеет на чём угодно, не красный, а сломанный."""
    mod = _mod()
    drill = _drill_contact(mod)
    db = _prepare(tmp_path, "nodouble.db")
    _force_categories(mod, {n: tuple(getattr(mod, n)) for n in _CATEGORY_NAMES})
    rc, out = _run_mod(mod, [db, "--contact", drill, "--apply"], capsys)
    assert rc == 0, (
        f"подмена категорий их же собственным составом сломала прогон — значит "
        f"красное соседних сторожей ничего не доказывает.\n{out}")


def test_table_named_in_two_categories_refuses_before_any_write(tmp_path, capsys):
    """Сверка ПЕРЕД сбросом ОТКАЗЫВАЕТ на таблице, названной больше чем в одной
    категории, и до этого не пишет в базу ни строки.

    Проверяется ДЕТЕКТОР на прогоне, а не состав списков. Разница существенная:
    проверка по данным ловит автора, который положил таблицу в два списка
    сегодня; детектор нужен для случая, когда база и код разъехались и категории
    правят руками на живой машине. Сегодня детектор можно выкинуть, и ни один
    сторож по данным этого не заметит.

    Код отказа сверяется с ЗАМЕРЕННЫМ тут же кодом успеха, а не с числом."""
    mod = _mod()
    drill = _drill_contact(mod)
    clean_db = _prepare(tmp_path, "dbl-clean.db")
    db = _prepare(tmp_path, "dbl.db")
    table = _pick_double(mod, db)

    rc_ok, out_ok = _run_mod(mod, [clean_db, "--contact", drill, "--apply"], capsys)
    assert rc_ok == 0, f"исходные категории уже отказывают — замер испорчен\n{out_ok}"

    assert _apply_double(mod, table), _PATCH_SEAM
    before = _snapshot(db)
    rc, out = _run_mod(mod, [db, "--contact", drill, "--apply"], capsys)
    after = _snapshot(db)

    assert rc != 0, (
        f"таблица {table} названа И в _WIPE_TABLES, И в _KEEP_TABLES, а сброс "
        f"пошёл как ни в чём не бывало: «стереть» и «не трогать намеренно» об "
        f"одной строке — это два места, которые уже разошлись.\n{out}")
    assert rc != rc_ok, (
        f"отказ на двойном имени вернул код успеха ({rc}): харнесс не отличит "
        f"его от сработавшего сброса")
    assert after == before, (
        "отказ обязан приходить ДО любой записи, а база изменилась: "
        + ", ".join(sorted(t for t in before if before[t] != after.get(t))))


def test_double_naming_refusal_names_the_table_and_both_categories(tmp_path, capsys):
    """Отказ называет ТАБЛИЦУ и ОБЕ категории, в которых она нашлась.

    «Где-то дубль» читателю не говорит ничего: чинить придётся сверкой четырёх
    списков глазами. Сверяются только строки, которых нет в выводе штатного
    прогона, — иначе сторож зеленел бы за счёт строк плана, где имена таблиц
    печатаются и без всякого отказа."""
    mod = _mod()
    drill = _drill_contact(mod)
    base_db = _prepare(tmp_path, "dbl-base.db")
    dup_db = _prepare(tmp_path, "dbl-dup.db")
    table = _pick_double(mod, base_db)

    _, base = _run_mod(mod, [base_db, "--contact", drill], capsys)
    assert _apply_double(mod, table), _PATCH_SEAM
    _, refusal = _run_mod(mod, [dup_db, "--contact", drill], capsys)

    known = {ln.strip() for ln in base.splitlines()}
    new = "\n".join(ln for ln in refusal.splitlines()
                    if ln.strip() and ln.strip() not in known)
    assert new.strip(), f"отказ на двойном имени не сказал НИЧЕГО нового:\n{refusal}"
    assert table in new, (
        f"отказ не назвал таблицу {table} — читателю нечего искать:\n{new}")
    for category in ("_WIPE_TABLES", "_KEEP_TABLES"):
        assert category in new, (
            f"отказ не назвал категорию {category}, в которой таблица {table} "
            f"нашлась: без обеих категорий чинить придётся глазами.\n{new}")


def test_double_naming_refuses_in_the_dry_run_too(tmp_path, capsys):
    """Сухой прогон (без `--apply`) на двойном имени тоже ОТКАЗЫВАЕТ, тем же
    кодом, что и `--apply`.

    Тот же довод, что и на непокрытой таблице: план, показавший чистоту, — это
    решение стереть, принятое по вранью."""
    mod = _mod()
    drill = _drill_contact(mod)
    db_apply = _prepare(tmp_path, "dbl-a.db")
    db_plan = _prepare(tmp_path, "dbl-p.db")
    table = _pick_double(mod, db_apply)

    assert _apply_double(mod, table), _PATCH_SEAM
    rc_apply, _ = _run_mod(mod, [db_apply, "--contact", drill, "--apply"], capsys)
    rc_plan, out = _run_mod(mod, [db_plan, "--contact", drill], capsys)

    assert rc_plan != 0, (
        f"сухой прогон при двойном имени таблицы {table} отчитался о плане: "
        f"человек прочтёт чистоту, которой не будет.\n{out}")
    assert rc_plan == rc_apply, (
        f"сухой прогон отказал ИНАЧЕ, чем --apply ({rc_plan} против {rc_apply}): "
        f"предохранитель обязан быть одинаков для плана и для применения")
