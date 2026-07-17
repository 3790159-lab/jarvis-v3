# CHATTER-3A: Перехват и пульт — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Владелец может перехватить любой диалог (просто начав печатать) и заглушить бота глобально; пауза наблюдаема, атрибутирована и переживает рестарт.

**Architecture:** Чистая логика в новых `chatter/core/pause.py` и `chatter/core/console.py` (ноль Telethon, ноль сети). Состояние в SQLite (переживает рестарт). Транспорт/раннер только проводят события. Пять core-файлов (`brain/humanizer/conversation/disclosure/guardrails`) НЕ трогаются — отчёт обязан приложить `git diff --stat` с нулём по ним.

**Tech Stack:** Python 3.14, SQLite (stdlib), Telethon, pytest. Ноль новых зависимостей.

**Спека:** `docs/superpowers/specs/2026-07-17-chatter-arc3a-control-design.md`

> ⚠️ **ЭТОТ ПЛАН — ЧЕРНОВИК. Код в нём НИКОГДА НЕ ЗАПУСКАЛСЯ.** При вычитке в нём уже нашлось три бага (разъехавшееся имя метода, `NameError` на импорте, синхронный вызов корутины) — значит остались ещё.
>
> **Правило приоритета: ТЕСТ > ПРОЗА ПЛАНА.** Красный тест важнее строчки плана. Если код из плана не работает или противоречит тесту — **доложить и починить план**, а НЕ подгонять тест под баг плана. Если тест из плана кажется неверным — тоже доложить, не переписывать молча. План писал тот же человек, что и три бага в нём.

**Запуск тестов (всегда так, cp1251 ломает кириллицу):**
`cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/ -q`

---

## Структура файлов

| Файл | Ответственность |
|---|---|
| `chatter/core/pause.py` | НОВЫЙ. Чистые решения: заглушено ли, атрибутирована ли пауза, пора ли авто-возврат |
| `chatter/core/console.py` | НОВЫЙ. Чистый парсер команд + форматтер `/status` |
| `chatter/storage/db.py` | Схема, идемпотентная миграция с бэкапом, `mute/unmute`, флаги, события, карточки |
| `chatter/config/loader.py` | Опциональный блок `control` |
| `chatter/run.py` | Гейт глушения в `process_batch` (начало + перед каждой `Say`) |
| `chatter/transport/telethon_tg.py` | `SentRegistry`: запоминает id СВОИХ отправок |
| `chatter/telethon_run.py` | Обработчик исходящих + грейс, хендлер Saved Messages, периодическая задача |

---

### Task 1: Store — полная схема + идемпотентная миграция + бэкап

**Files:**
- Modify: `chatter/storage/db.py`
- Test: `tests/chatter/test_store_migration.py` (создать)

- [ ] **Step 1: Написать падающий тест — миграция на КОПИИ ЖИВОЙ базы, дважды**

Создать `tests/chatter/test_store_migration.py`:

```python
"""Миграция схемы 3A. Ключевой тест идёт по КОПИИ ЖИВОЙ базы, а не по свежей:
свежая база проходит миграцию всегда — она пустая, в ней нечему сломаться.
Класс «ломается только на реальных данных» этот проект уже ловил дважды
(cp1251 и cross-thread SQLite)."""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from chatter.storage.db import Store

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LIVE_DB = REPO_ROOT / ".secrets" / "chatter_telethon.db"

NEW_CONTACT_COLUMNS = {
    "paused_at", "pause_source", "pause_msg_id",
    "pause_detail", "pause_until", "last_human_out_ts",
}


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


def test_migrates_a_copy_of_the_live_db_twice_without_losing_data():
    # SKIP громко и с причиной: молчаливый зелёный превратил бы этот тест
    # в пустышку ровно тогда, когда он единственный проверяет реальные данные.
    if not LIVE_DB.exists():
        pytest.skip(f"живой базы нет по пути {LIVE_DB} — тест на реальных данных пропущен")

    tmp = Path(__file__).resolve().parent / "_live_copy.db"
    shutil.copy2(LIVE_DB, tmp)
    try:
        before = sqlite3.connect(str(tmp))
        msgs_before = before.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        contacts_before = before.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        before.close()
        assert msgs_before > 0, "живая база пуста — тест бессмыслен"

        Store(tmp).close()          # первый прогон: миграция
        Store(tmp).close()          # второй прогон: ОБЯЗАН быть no-op, не падать

        assert NEW_CONTACT_COLUMNS <= _columns(tmp, "contacts")
        after = sqlite3.connect(str(tmp))
        assert after.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == msgs_before
        assert after.execute("SELECT COUNT(*) FROM contacts").fetchone()[0] == contacts_before
        after.close()

        backups = list(tmp.parent.glob(f"{tmp.name}.pre-3a-*.bak"))
        assert len(backups) == 1, f"ожидался ровно один бэкап, получено: {backups}"
    finally:
        for p in list(tmp.parent.glob(f"{tmp.name}*")):
            p.unlink(missing_ok=True)


def test_fresh_db_gets_full_schema_and_no_backup(tmp_path):
    db = tmp_path / "fresh.db"
    Store(db).close()
    assert NEW_CONTACT_COLUMNS <= _columns(db, "contacts")
    for table in ("runtime_flags", "console_cards", "control_events"):
        assert _columns(db, table), f"таблица {table} не создана"
    # Бэкапить нечего: базы не было. Мусор в каталоге клиента не плодим.
    assert list(tmp_path.glob("*.bak")) == []
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_store_migration.py -q`
Expected: FAIL — `AssertionError` на отсутствующих колонках (`NEW_CONTACT_COLUMNS <= _columns(...)`).

- [ ] **Step 3: Реализовать схему + миграцию**

В `chatter/storage/db.py` заменить `_SCHEMA` на полную целевую схему и добавить миграцию.
Импорты вверху файла: `import shutil, time` и `from pathlib import Path` (Path уже импортирован).

```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    contact_id TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'new',
    paused INTEGER NOT NULL DEFAULT 0,
    human_took_over INTEGER NOT NULL DEFAULT 0,
    paused_at REAL,
    pause_source TEXT,
    pause_msg_id INTEGER,
    pause_detail TEXT,
    pause_until REAL,
    last_human_out_ts REAL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    contact_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (contact_id, key)
);
CREATE TABLE IF NOT EXISTS runtime_flags (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS console_cards (
    msg_id INTEGER PRIMARY KEY,
    contact_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS control_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    contact_id TEXT,
    detail TEXT,
    ts REAL NOT NULL
);
"""

# Колонки, которых нет в базах арки 1/2. CREATE TABLE IF NOT EXISTS не добавляет
# колонки в СУЩЕСТВУЮЩУЮ таблицу — старая база получит их только через ALTER.
_ADDED_COLUMNS = {
    "contacts": {
        "paused_at": "REAL",
        "pause_source": "TEXT",
        "pause_msg_id": "INTEGER",
        "pause_detail": "TEXT",
        "pause_until": "REAL",
        "last_human_out_ts": "REAL",
    },
}
```

Заменить `Store.__init__` на:

```python
    def __init__(self, path: str | Path):
        path = Path(path)
        pre_existing = path.exists()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            missing = self._missing_columns()
            if missing:
                # Бэкап ТОЛЬКО когда реально мигрируем существующую базу:
                # иначе каждый рестарт раннера сыпал бы .bak-файлы клиенту.
                if pre_existing:
                    self._backup(path)
                self._apply_migration(missing)

    def _missing_columns(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for table, cols in _ADDED_COLUMNS.items():
            have = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            gap = {c: decl for c, decl in cols.items() if c not in have}
            if gap:
                out[table] = gap
        return out

    def _backup(self, path: Path) -> None:
        dest = path.with_name(f"{path.name}.pre-3a-{int(time.time())}.bak")
        shutil.copy2(path, dest)

    def _apply_migration(self, missing: dict[str, dict[str, str]]) -> None:
        # Идемпотентность через ПРОВЕРКУ наличия колонки, а не через ловлю
        # исключения: гардиан перезапускает раннер постоянно, и миграция,
        # падающая на втором прогоне, = краш-петля, которую гардиан будет
        # вечно поддерживать.
        for table, cols in missing.items():
            for col, decl in cols.items():
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        self._conn.commit()
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_store_migration.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Прогнать весь сьют — миграция не должна сломать арки 1/2**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/ -q`
Expected: PASS, ноль падений.

- [ ] **Step 6: Commit**

```bash
cd /c/jarvis
git add chatter/storage/db.py tests/chatter/test_store_migration.py
git commit -m "chatter(3A): full control schema + idempotent migration with backup"
```

---

### Task 2: Store — атрибутированная пауза (`mute`/`unmute`)

**Files:**
- Modify: `chatter/storage/db.py`
- Test: `tests/chatter/test_store_control.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/chatter/test_store_control.py`:

```python
"""Пауза в SQLite: атрибуция обязательна, состояние переживает рестарт."""
from __future__ import annotations

import pytest

from chatter.storage.db import Store


def test_mute_requires_a_known_source():
    # Пауза без причины = баг-класс (спека §4): самозаглушка тиха и вечна,
    # поэтому поставить её без атрибуции должно быть ТЕХНИЧЕСКИ невозможно.
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        with pytest.raises(ValueError):
            s.mute("c1", source="потому что", now=100.0)


def test_mute_records_who_and_why():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="human_takeover", msg_id=4821,
               detail="Здравствуйте, я сам перезвоню", now=100.0)
        row = s.get_or_create_contact("c1")
        assert row["paused"] == 1
        assert row["pause_source"] == "human_takeover"
        assert row["pause_msg_id"] == 4821
        assert row["pause_detail"] == "Здравствуйте, я сам перезвоню"
        assert row["paused_at"] == 100.0
        assert row["pause_until"] is None


def test_pause_survives_restart(tmp_path):
    # Гардиан перезапускает раннер штатно — пауза в памяти была бы потеряна
    # ровно тогда, когда владелец на неё рассчитывает.
    db = tmp_path / "s.db"
    with Store(db) as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="command", until=999.0, now=100.0)
    with Store(db) as s2:
        row = s2.get_or_create_contact("c1")
        assert row["paused"] == 1
        assert row["pause_source"] == "command"
        assert row["pause_until"] == 999.0


def test_unmute_clears_every_attribution_field():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="human_takeover", msg_id=7, detail="x", now=100.0)
        s.unmute("c1")
        row = s.get_or_create_contact("c1")
        assert row["paused"] == 0
        # Осколки старой атрибуции соврут в /status при следующей паузе.
        assert row["pause_source"] is None
        assert row["pause_msg_id"] is None
        assert row["pause_detail"] is None
        assert row["pause_until"] is None
        assert row["paused_at"] is None


def test_muted_contacts_returns_only_paused_rows():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.get_or_create_contact("c2")
        s.mute("c1", source="command", now=100.0)
        assert [r["contact_id"] for r in s.muted_contacts()] == ["c1"]


def test_note_human_out_tracks_owner_last_manual_message():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.note_human_out("c1", ts=500.0)
        assert s.get_or_create_contact("c1")["last_human_out_ts"] == 500.0
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_store_control.py -q`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'mute'`.

- [ ] **Step 3: Реализовать**

В `chatter/storage/db.py` добавить рядом с `set_flag`:

```python
# Источники паузы уровня КОНТАКТА. Глобальный kill switch живёт в
# runtime_flags и сюда не входит: он не про конкретный диалог.
ROW_MUTE_SOURCES = frozenset({"human_takeover", "command"})
```

```python
    def mute(self, contact_id: str, *, source: str, msg_id: int | None = None,
             detail: str | None = None, until: float | None = None, now: float) -> None:
        """Заглушить диалог. `source` ОБЯЗАТЕЛЕН: пауза без причины — баг-класс
        (спека §4), поэтому её нельзя поставить даже случайно."""
        if source not in ROW_MUTE_SOURCES:
            raise ValueError(f"unknown mute source: {source!r} (need one of {sorted(ROW_MUTE_SOURCES)})")
        with self._lock:
            self._conn.execute(
                "UPDATE contacts SET paused=1, pause_source=?, pause_msg_id=?, "
                "pause_detail=?, pause_until=?, paused_at=? WHERE contact_id=?",
                (source, msg_id, detail, until, now, contact_id))
            self._conn.commit()

    def unmute(self, contact_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE contacts SET paused=0, pause_source=NULL, pause_msg_id=NULL, "
                "pause_detail=NULL, pause_until=NULL, paused_at=NULL WHERE contact_id=?",
                (contact_id,))
            self._conn.commit()

    def muted_contacts(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM contacts WHERE paused=1 ORDER BY paused_at").fetchall()
        return [dict(r) for r in rows]

    def note_human_out(self, contact_id: str, *, ts: float) -> None:
        """Отметить ручное сообщение владельца — от него, а НЕ от paused_at,
        отсчитывается авто-возврат (спека §8)."""
        with self._lock:
            self._conn.execute(
                "UPDATE contacts SET last_human_out_ts=? WHERE contact_id=?", (ts, contact_id))
            self._conn.commit()
```

Удалить метод `set_flag` целиком: он позволял ставить `paused` без атрибуции — ровно то, что арка запрещает. `human_took_over` ретайрится (спека §6), потребителей у `set_flag` нет (проверить: `grep -rn "set_flag" chatter/ tests/`).

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_store_control.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
cd /c/jarvis
git add chatter/storage/db.py tests/chatter/test_store_control.py
git commit -m "chatter(3A): attributed mute/unmute — a pause without a reason is impossible"
```

---

### Task 3: Store — kill switch, события, карточки

**Files:**
- Modify: `chatter/storage/db.py`
- Test: `tests/chatter/test_store_control.py`

- [ ] **Step 1: Написать падающий тест** (дописать в конец `test_store_control.py`)

```python
def test_runtime_flag_roundtrip_and_default():
    with Store(":memory:") as s:
        assert s.get_runtime_flag("kill_switch") is None
        s.set_runtime_flag("kill_switch", "1", ts=100.0)
        assert s.get_runtime_flag("kill_switch") == "1"
        s.set_runtime_flag("kill_switch", "0", ts=200.0)   # /start перезаписывает
        assert s.get_runtime_flag("kill_switch") == "0"


def test_events_count_within_window_only():
    with Store(":memory:") as s:
        s.add_event("takeover", contact_id="c1", detail="msg 1", ts=100.0)
        s.add_event("takeover", contact_id="c1", detail="msg 2", ts=200.0)
        s.add_event("unknown_outgoing", ts=200.0)
        assert s.count_events("takeover", since_ts=150.0) == 1
        assert s.count_events("takeover", since_ts=0.0) == 2
        assert s.count_events("unattributed_pause", since_ts=0.0) == 0


def test_card_maps_saved_message_to_contact_and_survives_restart(tmp_path):
    # Адресация реплаем обязана пережить рестарт: карточка остаётся лежать в
    # Saved Messages, и владелец ответит на неё через час.
    db = tmp_path / "s.db"
    with Store(db) as s:
        s.add_card(msg_id=555, contact_id="237616472:demo", kind="pause", ts=100.0)
    with Store(db) as s2:
        assert s2.card_contact(555) == "237616472:demo"
        assert s2.card_contact(999) is None
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_store_control.py -q`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'get_runtime_flag'`.

- [ ] **Step 3: Реализовать** (добавить в `Store`)

```python
    def set_runtime_flag(self, key: str, value: str, *, ts: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO runtime_flags(key, value, ts) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
                (key, value, ts))
            self._conn.commit()

    def get_runtime_flag(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM runtime_flags WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def add_event(self, kind: str, *, contact_id: str | None = None,
                  detail: str | None = None, ts: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO control_events(kind, contact_id, detail, ts) VALUES (?,?,?,?)",
                (kind, contact_id, detail, ts))
            self._conn.commit()

    def count_events(self, kind: str, *, since_ts: float) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM control_events WHERE kind=? AND ts>=?",
                (kind, since_ts)).fetchone()[0]

    def add_card(self, *, msg_id: int, contact_id: str, kind: str, ts: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO console_cards(msg_id, contact_id, kind, ts) VALUES (?,?,?,?) "
                "ON CONFLICT(msg_id) DO UPDATE SET contact_id=excluded.contact_id",
                (msg_id, contact_id, kind, ts))
            self._conn.commit()

    def card_contact(self, msg_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT contact_id FROM console_cards WHERE msg_id=?", (msg_id,)).fetchone()
        return row["contact_id"] if row else None
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_store_control.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
cd /c/jarvis
git add chatter/storage/db.py tests/chatter/test_store_control.py
git commit -m "chatter(3A): kill switch flag, control events, saved-message cards"
```

---

### Task 4: `core/pause.py` — `is_muted` + детект неатрибутированной паузы

**Files:**
- Create: `chatter/core/pause.py`
- Test: `tests/chatter/test_pause.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/chatter/test_pause.py`:

```python
"""Чистые решения о глушении. Ноль Telethon, ноль сети, ноль SQLite."""
from __future__ import annotations

from chatter.core.pause import is_attributed, is_muted


def _row(**kw) -> dict:
    base = {"paused": 0, "pause_source": None, "pause_until": None,
            "paused_at": None, "last_human_out_ts": None}
    base.update(kw)
    return base


def test_kill_switch_mutes_everything_including_unknown_contacts():
    assert is_muted(_row(), kill_switch=True, now=100.0) is True
    assert is_muted(None, kill_switch=True, now=100.0) is True


def test_not_paused_is_not_muted():
    assert is_muted(_row(), kill_switch=False, now=100.0) is False
    assert is_muted(None, kill_switch=False, now=100.0) is False


def test_paused_without_deadline_stays_muted():
    row = _row(paused=1, pause_source="human_takeover", paused_at=50.0)
    assert is_muted(row, kill_switch=False, now=1_000_000.0) is True


def test_expired_deadline_is_not_muted_even_before_the_timer_task_runs():
    # Защита в глубину: если периодическая задача авто-возврата умерла,
    # /pause 1h обязан истечь сам, а не залипнуть навсегда.
    row = _row(paused=1, pause_source="command", pause_until=200.0)
    assert is_muted(row, kill_switch=False, now=199.0) is True
    assert is_muted(row, kill_switch=False, now=200.0) is False


def test_pause_without_a_source_is_flagged_as_a_bug_not_silently_trusted():
    assert is_attributed(_row(paused=1, pause_source="human_takeover")) is True
    assert is_attributed(_row(paused=1, pause_source=None)) is False
    # Не заглушено — атрибутировать нечего.
    assert is_attributed(_row(paused=0, pause_source=None)) is True
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_pause.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'chatter.core.pause'`.

- [ ] **Step 3: Реализовать**

Создать `chatter/core/pause.py`:

```python
"""Чистые решения о глушении диалога (арка 3A).

Ноль Telethon, ноль SQLite, ноль сети: раннер приносит сюда строку контакта и
состояние рубильника, получает булев ответ. Поэтому вся логика перехвата
тестируется без аккаунта."""
from __future__ import annotations


def is_muted(contact_row: dict | None, *, kill_switch: bool, now: float) -> bool:
    """Молчит ли Аня в этом диалоге прямо сейчас."""
    if kill_switch:
        return True
    if not contact_row or not contact_row.get("paused"):
        return False
    until = contact_row.get("pause_until")
    # Истёкший дедлайн = не заглушено, даже если периодическая задача ещё не
    # добежала (или умерла). Защита в глубину: /pause 1h не имеет права
    # превратиться в вечную тишину из-за мёртвого таймера.
    if until is not None and now >= float(until):
        return False
    return True


def is_attributed(contact_row: dict) -> bool:
    """False = пауза стоит, а причины нет. Это баг-класс (спека §4), а не
    мелочь: самозаглушка тиха и вечна, и без атрибуции на вопрос «почему Аня
    молчит» нет ответа."""
    if not contact_row.get("paused"):
        return True
    return bool(contact_row.get("pause_source"))
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_pause.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
cd /c/jarvis
git add chatter/core/pause.py tests/chatter/test_pause.py
git commit -m "chatter(3A): pure mute decision + unattributed-pause detection"
```

---

### Task 5: `core/pause.py` — `should_auto_resume`

**Files:**
- Modify: `chatter/core/pause.py`
- Test: `tests/chatter/test_pause.py`

- [ ] **Step 1: Написать падающий тест** (дописать в `test_pause.py`)

```python
from chatter.core.pause import should_auto_resume

HOUR = 3600.0


def test_expired_command_pause_auto_resumes():
    row = _row(paused=1, pause_source="command", pause_until=200.0)
    assert should_auto_resume(row, now=201.0, auto_resume_hours=6) is True
    assert should_auto_resume(row, now=199.0, auto_resume_hours=6) is False


def test_takeover_resumes_after_owner_goes_quiet():
    row = _row(paused=1, pause_source="human_takeover",
               paused_at=0.0, last_human_out_ts=10 * HOUR)
    assert should_auto_resume(row, now=10 * HOUR + 6 * HOUR, auto_resume_hours=6) is True


def test_takeover_does_not_resume_under_the_owners_hands():
    # Владелец переписывается прямо сейчас: разморозить диалог = Аня влезет
    # в живой разговор. Отсчёт от ЕГО последнего сообщения, не от paused_at.
    row = _row(paused=1, pause_source="human_takeover",
               paused_at=0.0, last_human_out_ts=10 * HOUR)
    assert should_auto_resume(row, now=10 * HOUR + 300, auto_resume_hours=6) is False


def test_indefinite_command_pause_never_auto_resumes():
    # Явная команда владельца не отменяется таймером за его спиной.
    row = _row(paused=1, pause_source="command", pause_until=None, paused_at=0.0)
    assert should_auto_resume(row, now=10_000 * HOUR, auto_resume_hours=6) is False


def test_unpaused_row_is_not_resumed():
    assert should_auto_resume(_row(), now=100.0, auto_resume_hours=6) is False
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_pause.py -q`
Expected: FAIL — `ImportError: cannot import name 'should_auto_resume'`.

- [ ] **Step 3: Реализовать** (добавить в `chatter/core/pause.py`)

```python
def should_auto_resume(contact_row: dict, *, now: float, auto_resume_hours: float) -> bool:
    """Пора ли снять паузу самостоятельно (спека §8)."""
    if not contact_row.get("paused"):
        return False
    until = contact_row.get("pause_until")
    if until is not None:
        return now >= float(until)
    if contact_row.get("pause_source") != "human_takeover":
        # /pause без длительности = бессрочно. Явную команду владельца таймер
        # отменять не вправе.
        return False
    # Отсчёт от ПОСЛЕДНЕГО ручного сообщения владельца, а не от начала паузы:
    # иначе диалог, где он активно переписывается второй час, разморозится у
    # него под руками.
    last = contact_row.get("last_human_out_ts") or contact_row.get("paused_at") or 0.0
    return (now - float(last)) >= auto_resume_hours * 3600.0
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_pause.py -q`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
cd /c/jarvis
git add chatter/core/pause.py tests/chatter/test_pause.py
git commit -m "chatter(3A): auto-resume decision — counts from the owner's last message"
```

---

### Task 6: `config/loader.py` — блок `control`

**Files:**
- Modify: `chatter/config/loader.py`
- Test: `tests/chatter/test_loader.py`

- [ ] **Step 1: Написать падающий тест** (дописать в `tests/chatter/test_loader.py`; шаблон клиента взять из соседних тестов файла)

```python
def test_control_block_is_optional_and_has_defaults(tmp_path):
    # Клиент без блока control обязан работать: дефолты живут в коде.
    from chatter.config.loader import load_config
    _write_min_client(tmp_path, "demo")           # helper уже есть в этом файле
    cfg = load_config(tmp_path, "demo")
    assert cfg.settings.control.auto_resume_hours == 6.0
    assert cfg.settings.control.takeover_grace_seconds == 2.0
    assert cfg.settings.control.status_window_hours == 24


def test_control_block_overrides_defaults(tmp_path):
    from chatter.config.loader import load_config
    _write_min_client(tmp_path, "demo", extra={
        "control": {"auto_resume_hours": 2, "takeover_grace_seconds": 0.5,
                    "status_window_hours": 48},
    })
    cfg = load_config(tmp_path, "demo")
    assert cfg.settings.control.auto_resume_hours == 2.0
    assert cfg.settings.control.takeover_grace_seconds == 0.5
    assert cfg.settings.control.status_window_hours == 48
```

Если хелпера `_write_min_client(tmp_path, slug, extra=None)` в файле нет — написать его: создаёт `persona.md`/`knowledge.md`/`playbook.md` с непустым текстом и `settings.yaml` с обязательными ключами (`model`, `language: ru`, `owner_id`, `persona_name`, `work_hours`, `timings` со всеми 12 полями `_TIMING_FIELDS`, `limits` с 3 полями), сливая `extra` в верхний уровень YAML.

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_loader.py -q`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'control'`.

- [ ] **Step 3: Реализовать**

В `chatter/config/loader.py`:
1. Импорт: `from dataclasses import dataclass, field`.
2. Добавить дата-класс перед `Settings`:

```python
@dataclass(frozen=True)
class ControlConfig:
    """Пульт владельца (арка 3A). Блок опционален: дефолты — рабочие."""
    auto_resume_hours: float = 6.0
    takeover_grace_seconds: float = 2.0   # окно на опознание своего исходящего
    status_window_hours: int = 24
```

3. В `Settings` добавить поле последним:

```python
    control: ControlConfig = field(default_factory=ControlConfig)
```

4. В `load_config` перед `return`:

```python
    control = ControlConfig()
    c_raw = raw.get("control")
    if c_raw is not None:
        if not isinstance(c_raw, dict):
            raise ConfigError("settings.yaml: 'control' must be a mapping")
        control = ControlConfig(
            auto_resume_hours=float(c_raw.get("auto_resume_hours", 6.0)),
            takeover_grace_seconds=float(c_raw.get("takeover_grace_seconds", 2.0)),
            status_window_hours=int(c_raw.get("status_window_hours", 24)),
        )
```

5. В конструктор `Settings(...)` внутри `return Config(...)` добавить `control=control`.

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_loader.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /c/jarvis
git add chatter/config/loader.py tests/chatter/test_loader.py
git commit -m "chatter(3A): optional control block in settings.yaml"
```

---

### Task 7: `run.py` — гейт глушения (начало + перед каждой отправкой)

**Files:**
- Modify: `chatter/run.py`
- Test: `tests/chatter/test_run_mute_gate.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/chatter/test_run_mute_gate.py`. Строить `Deps`/фейковый транспорт по образцу `tests/chatter/test_run_core.py` (посмотреть его хелперы и переиспользовать).

```python
"""Гейт глушения в process_batch. Главный тест — отмена НА ЛЕТУ."""
from __future__ import annotations

from chatter.run import process_batch


def test_muted_contact_gets_no_reply_but_the_inbound_is_remembered(muted_deps, fake_transport):
    # Входящее обязано лечь в историю ДО гейта: заглушённая Аня всё равно
    # должна помнить, что ей написали, иначе после /resume контекст рваный.
    deps = muted_deps
    process_batch("c1", ["Привет"], fake_transport, deps)
    assert fake_transport.sent == []
    history = deps.store.history("c1")
    assert [m["text"] for m in history] == ["Привет"]
    assert history[0]["role"] == "user"


def test_mute_arriving_mid_reply_aborts_the_remaining_messages(live_deps, fake_transport):
    # Позорный сценарий: Аня ушла в паузу чтения 4с + печать 10с, владелец за
    # это время ответил руками, а Аня через 5 секунд говорит поверх него.
    # Гейт перед КАЖДОЙ Say, а не один раз в начале.
    deps = live_deps
    deps.store.get_or_create_contact("c1")

    original_sleep = deps.sleep
    def sleep_then_owner_jumps_in(seconds: float) -> None:
        original_sleep(seconds)
        deps.store.mute("c1", source="human_takeover", msg_id=1,
                        detail="я сам отвечу", now=deps.clock())
    deps.sleep = sleep_then_owner_jumps_in

    process_batch("c1", ["Сколько стоит?"], fake_transport, deps)
    assert fake_transport.sent == [], "Аня заговорила поверх владельца"


def test_kill_switch_silences_a_contact_that_has_no_pause_of_its_own(live_deps, fake_transport):
    live_deps.store.set_runtime_flag("kill_switch", "1", ts=live_deps.clock())
    process_batch("c1", ["Привет"], fake_transport, live_deps)
    assert fake_transport.sent == []
```

Фикстуры (в этом же файле): `fake_transport` — объект с `sent: list[str]`, методами `send/send_typing/read_acknowledge/set_online/receive`; `live_deps` — `Deps` с реальным `Store(":memory:")`, `FakeLLM`, детерминированным `rng`, `sleep=lambda _: None`, `clock=lambda: 1000.0`; `muted_deps` — то же, но контакт `c1` создан и заглушён (`mute(source="command")`).

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_run_mute_gate.py -q`
Expected: FAIL — `assert fake_transport.sent == []` не выполняется (Аня отвечает, гейта нет).

- [ ] **Step 3: Реализовать**

В `chatter/run.py` добавить импорт:

```python
from chatter.core.pause import is_attributed, is_muted
```

Добавить хелпер над `process_batch`:

```python
def _muted_now(deps, contact_id: str) -> bool:
    """Читает состояние ЗАНОВО при каждом вызове: владелец мог вмешаться
    секунду назад, пока Аня «печатала»."""
    row = deps.store.get_or_create_contact(contact_id)
    if not is_attributed(row):
        # DEV-18: пауза без причины — баг-класс, а не повод промолчать.
        deps.store.add_event("unattributed_pause", contact_id=contact_id, ts=deps.clock())
        print(f"  [BUG] paused without a source: {contact_id}")
    kill = deps.store.get_runtime_flag("kill_switch") == "1"
    return is_muted(row, kill_switch=kill, now=deps.clock())
```

В `process_batch` сразу после `deps.store.add_message(contact_id, "user", text, ts=deps.clock())`:

```python
    if _muted_now(deps, contact_id):
        print(f"  [muted] {contact_id}: входящее записано, ответа не будет")
        return
```

И в цикле действий заменить ветку `H.Say` на:

```python
        elif isinstance(action, H.Say):
            if _muted_now(deps, contact_id):
                print(f"  [muted mid-reply] {contact_id}: отменяю остаток ответа")
                return
            transport.send(action.text)
            deps.store.add_message(contact_id, "assistant", action.text, ts=deps.clock())
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_run_mute_gate.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Прогнать весь сьют — гейт не должен сломать арки 1/2**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/ -q`
Expected: PASS. Если падает `test_run_core.py` — его фейковый store не умеет `get_runtime_flag`; починить фейк, а не гейт.

- [ ] **Step 6: Commit**

```bash
cd /c/jarvis
git add chatter/run.py tests/chatter/test_run_mute_gate.py
git commit -m "chatter(3A): mute gate in process_batch — checked before every message"
```

---

### Task 8: `core/console.py` — парсер команд

**Files:**
- Create: `chatter/core/console.py`
- Test: `tests/chatter/test_console.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/chatter/test_console.py`:

```python
"""Чистый парсер команд пульта. Ноль Telethon."""
from __future__ import annotations

from chatter.core.console import Command, parse_command


def test_global_commands():
    assert parse_command("/status") == Command(name="status")
    assert parse_command("/stop") == Command(name="stop")
    assert parse_command("/start") == Command(name="start")


def test_case_and_whitespace_tolerated():
    assert parse_command("  /STATUS  ") == Command(name="status")


def test_pause_durations():
    assert parse_command("/pause 1h") == Command(name="pause", duration_seconds=3600.0)
    assert parse_command("/pause 30m") == Command(name="pause", duration_seconds=1800.0)
    assert parse_command("/pause") == Command(name="pause", duration_seconds=None)


def test_pause_with_explicit_target():
    assert parse_command("/pause 1h t.me/ivan") == Command(
        name="pause", duration_seconds=3600.0, target="t.me/ivan")
    assert parse_command("/resume 237616472") == Command(name="resume", target="237616472")


def test_not_a_command():
    assert parse_command("просто текст") is None
    assert parse_command("") is None
    assert parse_command("/unknown") is None


def test_bad_duration_is_reported_not_silently_ignored():
    # Молча проглотить «/pause 1час» = владелец думает, что поставил паузу.
    cmd = parse_command("/pause 1час")
    assert cmd == Command(name="pause", error="не понял длительность: '1час' (примеры: 1h, 30m)")
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_console.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'chatter.core.console'`.

- [ ] **Step 3: Реализовать**

Создать `chatter/core/console.py`:

```python
"""Пульт владельца: чистый разбор команд из Saved Messages (арка 3A).

Ноль Telethon и ноль сети: раннер приносит текст, получает Command."""
from __future__ import annotations

import re
from dataclasses import dataclass

GLOBAL_COMMANDS = frozenset({"status", "stop", "start"})
TARGETED_COMMANDS = frozenset({"pause", "resume"})

_DURATION_RE = re.compile(r"^(\d+)([hm])$", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    name: str
    duration_seconds: float | None = None
    target: str | None = None      # сырая ссылка/id, если владелец указал явно
    error: str | None = None       # человеческая жалоба вместо тихого игнора


def _parse_duration(token: str) -> float | None:
    m = _DURATION_RE.match(token)
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2).lower()
    return value * (3600.0 if unit == "h" else 60.0)


def parse_command(text: str) -> Command | None:
    """None = это не команда (обычный текст в Saved Messages трогать нельзя)."""
    parts = (text or "").strip().split()
    if not parts or not parts[0].startswith("/"):
        return None
    name = parts[0][1:].casefold()
    args = parts[1:]

    if name in GLOBAL_COMMANDS:
        return Command(name=name)
    if name not in TARGETED_COMMANDS:
        return None

    duration: float | None = None
    if name == "pause" and args:
        duration = _parse_duration(args[0])
        if duration is not None:
            args = args[1:]
        elif not args[0].startswith(("t.me", "https://", "@")) and not args[0].isdigit():
            # Похоже на кривую длительность, а не на адресата. Сказать вслух:
            # молчаливый игнор = владелец уверен, что пауза стоит (DEV-18).
            return Command(name=name, error=f"не понял длительность: '{args[0]}' (примеры: 1h, 30m)")

    return Command(name=name, duration_seconds=duration, target=args[0] if args else None)
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_console.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
cd /c/jarvis
git add chatter/core/console.py tests/chatter/test_console.py
git commit -m "chatter(3A): pure console command parser"
```

---

### Task 9: `core/console.py` — форматтер `/status`

**Files:**
- Modify: `chatter/core/console.py`
- Test: `tests/chatter/test_console.py`

- [ ] **Step 1: Написать падающий тест** (дописать в `test_console.py`)

```python
from chatter.core.console import PauseView, format_status

HOUR = 3600.0


def _view(**kw) -> PauseView:
    base = dict(title="Иван Петров", link="t.me/ivan", since_ts=0.0,
                source="human_takeover", detail="Здравствуйте, я сам перезвоню",
                msg_id=4821, resume_eta_ts=3 * HOUR)
    base.update(kw)
    return PauseView(**base)


def test_status_answers_why_is_she_silent_with_a_reason_per_dialog():
    out = format_status(kill_switch=False, pauses=[_view()],
                        counters={"takeover": 2, "unattributed_pause": 0, "unknown_outgoing": 0},
                        autoresume_beat_age=12.0, autoresume_interval=60.0,
                        now=1 * HOUR, window_hours=24)
    assert "Иван Петров" in out
    assert "вы вмешались" in out
    assert "Здравствуйте, я сам перезвоню" in out    # ЧТО именно вызвало паузу
    assert "4821" in out                              # атрибуция по id
    assert "РАБОТАЕТ" in out


def test_status_shows_the_kill_switch_first():
    out = format_status(kill_switch=True, pauses=[], counters={},
                        autoresume_beat_age=5.0, autoresume_interval=60.0,
                        now=0.0, window_hours=24)
    assert "ЗАГЛУШЕНА" in out
    assert "/start" in out          # как расколдовать — прямо в ответе


def test_status_flags_a_dead_autoresume_task_instead_of_staying_quiet():
    # Мёртвый таймер выглядит РОВНО как «пауз к возврату нет»: тихо и
    # правдоподобно. Единственная разница — возраст heartbeat.
    out = format_status(kill_switch=False, pauses=[], counters={},
                        autoresume_beat_age=47 * 60.0, autoresume_interval=60.0,
                        now=0.0, window_hours=24)
    assert "⚠️" in out


def test_status_without_any_pause_says_so_plainly():
    out = format_status(kill_switch=False, pauses=[], counters={},
                        autoresume_beat_age=3.0, autoresume_interval=60.0,
                        now=0.0, window_hours=24)
    assert "Заглушено диалогов: 0" in out
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_console.py -q`
Expected: FAIL — `ImportError: cannot import name 'PauseView'`.

- [ ] **Step 3: Реализовать** (добавить в `chatter/core/console.py`)

```python
SOURCE_LABELS = {
    "human_takeover": "вы вмешались",
    "command": "команда /pause",
}


@dataclass(frozen=True)
class PauseView:
    """Готовая к печати строка о паузе. Раннер разрешает имя/ссылку (это
    Telethon), форматтер остаётся чистым."""
    title: str
    link: str
    since_ts: float
    source: str
    detail: str | None
    msg_id: int | None
    resume_eta_ts: float | None


def _hhmm(ts: float) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(ts).strftime("%H:%M")


def _humanize_gap(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{int(seconds)}с"
    if seconds < 3600:
        return f"{int(seconds // 60)}м"
    return f"{int(seconds // 3600)}ч {int((seconds % 3600) // 60)}м"


def format_status(
    *, kill_switch: bool, pauses: list[PauseView], counters: dict[str, int],
    autoresume_beat_age: float | None, autoresume_interval: float,
    now: float, window_hours: int,
) -> str:
    """Ответ на «почему Аня молчит» за 5 секунд, а не расследованием."""
    lines = ["🤖 Аня — статус"]
    if kill_switch:
        lines.append("Глобально: 🔴 ЗАГЛУШЕНА (/stop). Снять: /start")
    else:
        lines.append("Глобально: РАБОТАЕТ")

    lines.append(f"Заглушено диалогов: {len(pauses)}")
    for p in pauses:
        lines.append(f" • {p.title} — {p.link} — с {_hhmm(p.since_ts)}")
        why = SOURCE_LABELS.get(p.source, p.source)
        if p.detail:
            snippet = p.detail if len(p.detail) <= 40 else p.detail[:40] + "…"
            why += f" («{snippet}»"
            why += f", msg {p.msg_id})" if p.msg_id else ")"
        lines.append(f"   причина: {why}")
        if p.resume_eta_ts is not None:
            lines.append(f"   авто-возврат через {_humanize_gap(p.resume_eta_ts - now)}")
        else:
            lines.append("   авто-возврата нет — снимет только /resume")

    c = counters
    lines.append(
        f"За {window_hours}ч: перехватов {c.get('takeover', 0)} · "
        f"неатрибутированных пауз {c.get('unattributed_pause', 0)} · "
        f"неопознанных исходящих {c.get('unknown_outgoing', 0)}")

    # Кто сторожит сторожа: мёртвая задача авто-возврата неотличима от
    # «пауз к возврату нет», если не показать возраст её heartbeat.
    if autoresume_beat_age is None:
        lines.append("⚠️ Авто-возврат: НИ РАЗУ не отработал")
    elif autoresume_beat_age > 3 * autoresume_interval:
        lines.append(f"⚠️ Авто-возврат: последний прогон {_humanize_gap(autoresume_beat_age)} назад")
    else:
        lines.append(f"Авто-возврат: последний прогон {_humanize_gap(autoresume_beat_age)} назад")
    return "\n".join(lines)
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_console.py -q`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
cd /c/jarvis
git add chatter/core/console.py tests/chatter/test_console.py
git commit -m "chatter(3A): /status formatter — reason per pause + watchdog heartbeat"
```

---

### Task 10: Транспорт — `SentRegistry` (реестр СВОИХ отправок)

**Files:**
- Modify: `chatter/transport/telethon_tg.py`
- Test: `tests/chatter/test_takeover.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/chatter/test_takeover.py`:

```python
"""Различение своё/чужое исходящее. Проигранная гонка = Аня глушит сама
себя и молчит навсегда — худший отказ продукта, поэтому тестов здесь много."""
from __future__ import annotations

from chatter.transport.telethon_tg import SentRegistry


def test_registered_id_is_recognised_as_ours():
    r = SentRegistry()
    r.add(4821)
    assert r.is_ours(4821) is True
    assert r.is_ours(4822) is False


def test_registry_is_bounded_and_forgets_the_oldest():
    # Реестр живёт всю жизнь процесса (недели). Без границы это утечка.
    r = SentRegistry(max_size=3)
    for i in (1, 2, 3, 4):
        r.add(i)
    assert r.is_ours(1) is False      # вытеснен
    assert r.is_ours(4) is True
    assert len(r) == 3
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_takeover.py -q`
Expected: FAIL — `ImportError: cannot import name 'SentRegistry'`.

- [ ] **Step 3: Реализовать**

В `chatter/transport/telethon_tg.py` добавить импорт `from collections import OrderedDict` и класс над `TelethonTransport`:

```python
class SentRegistry:
    """id сообщений, которые отправили МЫ. Единственный надёжный признак
    «своё vs владелец печатает руками»: текст сравнивать нельзя (Аня и
    владелец могут написать одно и то же), а других отличий у сообщений из
    одного аккаунта нет.

    Ограничен по размеру: процесс живёт неделями, а множество id иначе растёт
    вечно."""

    def __init__(self, max_size: int = 500):
        self._ids: "OrderedDict[int, None]" = OrderedDict()
        self._max = max_size

    def add(self, msg_id: int) -> None:
        self._ids[msg_id] = None
        self._ids.move_to_end(msg_id)
        while len(self._ids) > self._max:
            self._ids.popitem(last=False)

    def is_ours(self, msg_id: int) -> bool:
        return msg_id in self._ids

    def __len__(self) -> int:
        return len(self._ids)
```

Подключить к транспорту. В `TelethonTransport.__init__` добавить параметр последним и сохранить:

```python
    def __init__(
        self, client, chat, loop: asyncio.AbstractEventLoop,
        backoff_sleep: Callable[[float], None] = time.sleep,
        sent_registry: "SentRegistry | None" = None,
    ):
        ...
        self._backoff_sleep = backoff_sleep
        self._sent = sent_registry
```

Заменить `send` целиком (`_call_with_floodwait_retry` уже возвращает результат корутины — либо `Message`, либо `None`, если сдались после повторного FloodWait):

```python
    def send(self, text: str) -> None:
        log.info("OUT %s: %s", self._chat, text)
        sent = self._call_with_floodwait_retry(
            lambda: self._client.send_message(self._chat, text),
            desc=f"send_message to {self._chat}",
        )
        # Регистрируем id СВОЕГО сообщения. Гонку это не закрывает и не должно:
        # send() крутится в worker-потоке и маршалит корутину на loop, а
        # обработчик исходящих живёт НА loop — он вполне может увидеть апдейт
        # раньше, чем этот поток сюда доберётся. Гонку закрывает грейс-окно в
        # decide_outgoing (Task 11); здесь важно лишь не потерять id совсем.
        # `sent` = None, когда отправка не состоялась (FloodWait) — регистрировать
        # нечего, и getattr это ловит.
        if self._sent is not None and getattr(sent, "id", None) is not None:
            self._sent.add(sent.id)
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_takeover.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /c/jarvis
git add chatter/transport/telethon_tg.py tests/chatter/test_takeover.py
git commit -m "chatter(3A): SentRegistry — bounded record of our own message ids"
```

---

### Task 11: Раннер — детект перехвата + грейс-окно

**Files:**
- Modify: `chatter/telethon_run.py`
- Test: `tests/chatter/test_takeover.py`

- [ ] **Step 1: Написать падающий тест** (дописать в `test_takeover.py`)

```python
import asyncio

from chatter.telethon_run import decide_outgoing


def test_our_own_message_never_triggers_a_takeover():
    # АНТИРЕГРЕСС на самозаглушку. Если этот тест когда-нибудь покраснеет —
    # Аня замолчит навсегда и молча.
    r = SentRegistry()
    r.add(4821)
    assert asyncio.run(decide_outgoing(4821, registry=r, grace_seconds=0.01)) == "ours"


def test_unknown_id_becomes_a_takeover_only_after_the_grace_window():
    r = SentRegistry()
    assert asyncio.run(decide_outgoing(999, registry=r, grace_seconds=0.01)) == "human"


def test_id_registered_during_the_grace_window_is_ours_not_a_takeover():
    # ГОНКА: обработчик апдейта может обогнать возврат send_message. Голое
    # сравнение id объявило бы наше сообщение чужим и заглушило бы Аню.
    r = SentRegistry()

    async def scenario():
        task = asyncio.create_task(decide_outgoing(4821, registry=r, grace_seconds=0.2))
        await asyncio.sleep(0.05)
        r.add(4821)                 # send_message наконец вернул id
        return await task

    assert asyncio.run(scenario()) == "ours"
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_takeover.py -q`
Expected: FAIL — `ImportError: cannot import name 'decide_outgoing'`.

- [ ] **Step 3: Реализовать**

В `chatter/telethon_run.py` добавить:

```python
async def decide_outgoing(msg_id: int, *, registry, grace_seconds: float) -> str:
    """'ours' | 'human' — кто отправил это исходящее.

    ГРЕЙС-ОКНО — НЕ ПАРАНОЙЯ И НЕ КОСТЫЛЬ. Гонка здесь СТРУКТУРНА, она обязана
    случаться, и вот почему: TelethonTransport.send() крутится в worker-потоке
    (process_batch синхронный) и маршалит send_message на event loop через
    run_coroutine_threadsafe, а ЭТОТ обработчик живёт НА том же loop. Значит
    loop физически может раздать апдейт о нашем сообщении раньше, чем
    worker-поток проснётся и запишет id в реестр. Порядок не зависит от нашего
    кода — он зависит от планировщика.

    Цена проигранной гонки: Аня опознаёт СВОЁ сообщение как чужое → глушит сама
    себя → молчит навсегда, тихо, и это худший отказ продукта.

    Поэтому неопознанный id НЕ решается мгновенно: ждём грейс, перепроверяем
    реестр. 2 секунды невидимы на фоне её ритма в 30-50с.

    ⚠️ НЕ УДАЛЯТЬ как «лишнюю задержку»: без этого окна Аня начнёт глушить себя
    ровно тогда, когда планировщик окажется быстрее, — то есть под нагрузкой и
    не воспроизводимо на тестовой машине. Тесты
    test_id_registered_during_the_grace_window_is_ours_not_a_takeover и
    test_our_own_message_never_triggers_a_takeover стерегут это."""
    if registry.is_ours(msg_id):
        return "ours"
    await asyncio.sleep(grace_seconds)
    return "ours" if registry.is_ours(msg_id) else "human"
```

Подключить обработчик исходящих в `build_runner` рядом с существующим `_handler`:

```python
    async def _outgoing_handler(event) -> None:
        # Saved Messages — это пульт, а не диалог лида: /stop не должен
        # читаться как «владелец перехватил чат с самим собой».
        if event.chat_id == runner.me_id:
            return
        contact_id = runner.contact_id_for_chat(event)
        if contact_id is None:
            return   # диалог, который Аня не ведёт: это просто жизнь аккаунта
        verdict = await decide_outgoing(
            event.message.id, registry=runner.sent_registry,
            grace_seconds=runner.control.takeover_grace_seconds)
        if verdict == "ours":
            return
        await runner.on_human_takeover(event, contact_id)

    client.add_event_handler(_outgoing_handler, events.NewMessage(outgoing=True))
```

Методы `TelethonRunner` (управляемость + запись):

```python
    def primary_store(self):
        """Единственный Store процесса: load_personas получает ОДИН store и
        раздаёт его во все PersonaBundle.deps, так что это не «store первичной
        персоны», а общий."""
        return self.personas[self.primary_slug].deps.store

    @property
    def control(self):
        return self.personas[self.primary_slug].cfg.settings.control

    def contact_id_for_chat(self, event) -> str | None:
        """Управляемый диалог = есть строка в contacts ИЛИ отправитель в
        allowlist. Иначе владелец, написавший с этого аккаунта кому угодно,
        наплодит паузы в чужих диалогах и утопит /status в мусоре."""
        peer_id = event.chat_id
        if peer_id in self.allowlist:
            return f"{peer_id}:{self.persona_for(peer_id)}"
        store = self.primary_store()
        for slug in self.personas:
            cid = f"{peer_id}:{slug}"
            if store.has_contact(cid):
                return cid
        return None

    async def on_human_takeover(self, event, contact_id: str) -> None:
        text = (event.raw_text or "").strip()
        now = time.time()
        log.info("TAKEOVER %s by owner: msg %s %r", contact_id, event.message.id, text[:60])
        store = self.personas[self.persona_for(event.chat_id)].deps.store
        store.get_or_create_contact(contact_id)
        # Ручное сообщение владельца — в историю как assistant: brain.py:32
        # мапит роли НАПРЯМУЮ в Anthropic-сообщения, роли 'human' там нет.
        # Иначе после /resume у Ани амнезия и она противоречит владельцу.
        store.add_message(contact_id, "assistant", text, ts=now)
        store.note_human_out(contact_id, ts=now)
        store.mute(contact_id, source="human_takeover", msg_id=event.message.id,
                   detail=text[:200], now=now)
        store.add_event("takeover", contact_id=contact_id, detail=str(event.message.id), ts=now)
        await self.post_pause_card(event, contact_id, text)
```

`post_pause_card` кладёт в Saved Messages карточку и запоминает её id для адресации реплаем:

```python
    async def post_pause_card(self, event, contact_id: str, text: str) -> None:
        who = getattr(event.chat, "first_name", None) or str(event.chat_id)
        card = await self.client.send_message(
            "me",
            f"⏸ Пауза: {who}\nВы вмешались: «{text[:80]}»\n"
            f"Аня молчит в этом диалоге. Ответьте /resume на это сообщение, чтобы вернуть её.")
        store = self.personas[self.persona_for(event.chat_id)].deps.store
        store.add_card(msg_id=card.id, contact_id=contact_id, kind="pause", ts=time.time())
```

Ещё нужно:

1. В `Store` — метод `has_contact` (для `contact_id_for_chat`; `get_or_create_contact` не подходит: он СОЗДАЛ бы строку и объявил управляемым любой чат):

```python
    def has_contact(self, contact_id: str) -> bool:
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM contacts WHERE contact_id=?", (contact_id,)).fetchone() is not None
```

2. В `TelethonRunner.__init__` — `self.sent_registry = SentRegistry()` и `self.me_id: int | None = None` (импорт `from chatter.transport.telethon_tg import SentRegistry, TelethonTransport`).

3. `me_id` заполнить в `run_client` ПОСЛЕ `client.start()`, там же, где сейчас планируется `catch_up_missed` (нужен живой клиент):

```python
    runner.me_id = (await client.get_me()).id
```

`me_id` обязателен ДО регистрации хендлеров-обработчиков: без него `_outgoing_handler` не отличит Saved Messages от диалога лида и прочтёт `/stop` как перехват.

4. Реестр передать во ВСЕ места конструирования транспорта — их три: `handle_event` (ветка `/switch` и основная) и `_process_missed`. Найти командой `grep -n "TelethonTransport(" chatter/telethon_run.py` и в каждом добавить `sent_registry=self.sent_registry`. Пропущенное место = сообщения Ани оттуда не попадут в реестр и она заглушит себя после первого же ответа.

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_takeover.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
cd /c/jarvis
git add chatter/telethon_run.py chatter/storage/db.py tests/chatter/test_takeover.py
git commit -m "chatter(3A): auto-pause when the owner takes over, with a race-proof grace window"
```

---

### Task 12: Раннер — пульт в Saved Messages

**Files:**
- Modify: `chatter/telethon_run.py`
- Test: `tests/chatter/test_console_wiring.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/chatter/test_console_wiring.py`. Тестируется ЧИСТАЯ функция исполнения команды (без Telethon): раннер отдаёт ей команду + store, получает текст ответа.

```python
"""Исполнение команд пульта над store. Telethon не участвует."""
from __future__ import annotations

from chatter.core.console import Command
from chatter.storage.db import Store
from chatter.telethon_run import execute_command


def test_stop_sets_the_kill_switch_and_start_clears_it():
    with Store(":memory:") as s:
        out = execute_command(Command(name="stop"), store=s, contact_id=None, now=100.0)
        assert s.get_runtime_flag("kill_switch") == "1"
        assert "заглушена" in out.casefold()
        execute_command(Command(name="start"), store=s, contact_id=None, now=200.0)
        assert s.get_runtime_flag("kill_switch") == "0"


def test_resume_by_reply_unmutes_that_dialog():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="human_takeover", msg_id=1, now=100.0)
        execute_command(Command(name="resume"), store=s, contact_id="c1", now=200.0)
        assert s.get_or_create_contact("c1")["paused"] == 0


def test_pause_with_duration_sets_a_deadline():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        execute_command(Command(name="pause", duration_seconds=3600.0),
                        store=s, contact_id="c1", now=100.0)
        assert s.get_or_create_contact("c1")["pause_until"] == 3700.0


def test_targeted_command_without_a_target_explains_itself_instead_of_going_global():
    # «Хотел притормозить один диалог, а заглушил всю воронку» — слишком
    # дорогая опечатка. Глобальное глушение называется /stop.
    with Store(":memory:") as s:
        out = execute_command(Command(name="pause"), store=s, contact_id=None, now=100.0)
        assert "реплаем" in out
        assert s.muted_contacts() == []


def test_parser_error_is_shown_to_the_owner():
    with Store(":memory:") as s:
        out = execute_command(Command(name="pause", error="не понял длительность: 'x'"),
                              store=s, contact_id="c1", now=100.0)
        assert "не понял длительность" in out
        assert s.muted_contacts() == []
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_console_wiring.py -q`
Expected: FAIL — `ImportError: cannot import name 'execute_command'`.

- [ ] **Step 3: Реализовать**

В `chatter/telethon_run.py`:

```python
def execute_command(cmd, *, store, contact_id: str | None, now: float,
                    status_text: str | None = None) -> str:
    """Исполнить команду пульта. Чистая относительно Telethon: только store.
    `contact_id` уже разрешён раннером (из реплая или аргумента)."""
    if cmd.error:
        return f"⚠️ {cmd.error}"

    if cmd.name == "status":
        return status_text or "статус недоступен"
    if cmd.name == "stop":
        store.set_runtime_flag("kill_switch", "1", ts=now)
        store.add_event("kill_on", ts=now)
        return "🔴 Аня ЗАГЛУШЕНА во всех диалогах. Вернуть: /start"
    if cmd.name == "start":
        store.set_runtime_flag("kill_switch", "0", ts=now)
        store.add_event("kill_off", ts=now)
        return "✅ Аня снова работает во всех диалогах."

    if contact_id is None:
        return ("Не понял, какой диалог. Ответьте этой командой реплаем на карточку "
                f"или укажите адресата: /{cmd.name} <ссылка|id>. "
                "Заглушить ВСЕ диалоги — это /stop.")

    if cmd.name == "pause":
        until = now + cmd.duration_seconds if cmd.duration_seconds else None
        store.get_or_create_contact(contact_id)
        store.mute(contact_id, source="command", until=until, now=now)
        when = f" на {int(cmd.duration_seconds // 60)} мин" if cmd.duration_seconds else " бессрочно"
        return f"⏸ Диалог заглушён{when}. Вернуть: /resume реплаем."
    if cmd.name == "resume":
        store.unmute(contact_id)
        store.add_event("resume", contact_id=contact_id, ts=now)
        return "▶️ Аня снова отвечает в этом диалоге."
    return f"неизвестная команда: {cmd.name}"
```

Хендлер Saved Messages в `build_runner`:

```python
    async def _console_handler(event) -> None:
        if event.chat_id != runner.me_id:
            return
        cmd = parse_command(event.raw_text or "")
        if cmd is None:
            return                      # обычная заметка в Saved Messages — не трогаем
        contact_id = await runner.resolve_target(event, cmd)
        status_text = await runner.render_status() if cmd.name == "status" else None
        reply = execute_command(cmd, store=runner.primary_store(), contact_id=contact_id,
                                now=time.time(), status_text=status_text)
        await client.send_message("me", reply)

    client.add_event_handler(_console_handler, events.NewMessage(chats="me"))
```

Здесь же добавить константу (её использует `render_status`, а Task 13 — цикл авто-возврата):

```python
AUTORESUME_INTERVAL_SECONDS = 60.0
```

Методы `TelethonRunner` для адресации и рендера:

```python
    async def resolve_target(self, event, cmd) -> str | None:
        """Какой диалог имел в виду владелец. Приоритет у реплая: карточка уже
        лежит в Saved Messages, и ответить на неё дешевле, чем искать id."""
        if cmd.name not in ("pause", "resume"):
            return None
        reply_to = getattr(event, "reply_to_msg_id", None)
        if reply_to:
            hit = self.primary_store().card_contact(reply_to)
            if hit:
                return hit
        if not cmd.target:
            return None
        # Фоллбек: /resume <id | t.me/user | @user> — для диалогов без свежей карточки.
        raw = cmd.target.strip().rstrip("/").split("/")[-1].lstrip("@")
        try:
            entity = await self.client.get_entity(int(raw) if raw.isdigit() else raw)
        except Exception:
            log.warning("resolve_target: не смог разрешить %r", cmd.target)
            return None
        return f"{entity.id}:{self.persona_for(entity.id)}"

    async def render_status(self) -> str:
        """Собрать PauseView-ы (это единственное место, где нужен Telethon:
        имя и ссылка) и отдать чистому форматтеру."""
        store = self.primary_store()
        now = time.time()
        window = self.control.status_window_hours * 3600.0
        views = []
        for row in store.muted_contacts():
            peer_id = int(row["contact_id"].split(":")[0])
            try:
                entity = await self.client.get_entity(peer_id)
                title = getattr(entity, "first_name", None) or getattr(entity, "title", None) or str(peer_id)
                username = getattr(entity, "username", None)
            except Exception:
                title, username = str(peer_id), None
            eta = None
            if row["pause_until"] is not None:
                eta = float(row["pause_until"])
            elif row["pause_source"] == "human_takeover":
                last = row["last_human_out_ts"] or row["paused_at"] or now
                eta = float(last) + self.control.auto_resume_hours * 3600.0
            views.append(PauseView(
                title=title,
                link=f"t.me/{username}" if username else f"id {peer_id}",
                since_ts=float(row["paused_at"] or now),
                source=row["pause_source"] or "?",
                detail=row["pause_detail"],
                msg_id=row["pause_msg_id"],
                resume_eta_ts=eta,
            ))
        beat_raw = store.get_runtime_flag("autoresume_beat")
        beat_age = (now - float(beat_raw)) if beat_raw else None
        return format_status(
            kill_switch=store.get_runtime_flag("kill_switch") == "1",
            pauses=views,
            counters={k: store.count_events(k, since_ts=now - window)
                      for k in ("takeover", "unattributed_pause", "unknown_outgoing")},
            autoresume_beat_age=beat_age,
            autoresume_interval=AUTORESUME_INTERVAL_SECONDS,
            now=now, window_hours=self.control.status_window_hours,
        )
```

Импорты вверху `telethon_run.py`: `from chatter.core.console import PauseView, format_status, parse_command`.

`render_status` — корутина (нужен `get_entity`), поэтому в `_console_handler` вызывать её так:

```python
        status_text = await runner.render_status() if cmd.name == "status" else None
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_console_wiring.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
cd /c/jarvis
git add chatter/telethon_run.py tests/chatter/test_console_wiring.py
git commit -m "chatter(3A): Saved Messages console — /status /stop /start /pause /resume"
```

---

### Task 13: Раннер — периодический авто-возврат + свой heartbeat

**Files:**
- Modify: `chatter/telethon_run.py`
- Test: `tests/chatter/test_autoresume_task.py` (создать)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/chatter/test_autoresume_task.py`:

```python
"""Периодический авто-возврат. Тестируется ОДИН прогон (sweep), а не цикл."""
from __future__ import annotations

from chatter.storage.db import Store
from chatter.telethon_run import autoresume_sweep

HOUR = 3600.0


def test_sweep_resumes_an_expired_command_pause():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="command", until=200.0, now=100.0)
        autoresume_sweep(s, now=201.0, auto_resume_hours=6)
        assert s.get_or_create_contact("c1")["paused"] == 0


def test_sweep_leaves_a_fresh_takeover_alone():
    with Store(":memory:") as s:
        s.get_or_create_contact("c1")
        s.mute("c1", source="human_takeover", msg_id=1, now=100.0)
        s.note_human_out("c1", ts=100.0)
        autoresume_sweep(s, now=100.0 + 300, auto_resume_hours=6)
        assert s.get_or_create_contact("c1")["paused"] == 1


def test_sweep_writes_its_heartbeat_every_run():
    # Мёртвая задача выглядит РОВНО как «пауз к возврату нет». Единственная
    # разница — этот heartbeat, и /status обязан его показывать.
    with Store(":memory:") as s:
        autoresume_sweep(s, now=1234.0, auto_resume_hours=6)
        assert s.get_runtime_flag("autoresume_beat") == "1234.0"


def test_sweep_never_touches_the_kill_switch():
    # Рубильник снимает только владелец.
    with Store(":memory:") as s:
        s.set_runtime_flag("kill_switch", "1", ts=100.0)
        autoresume_sweep(s, now=10_000 * HOUR, auto_resume_hours=6)
        assert s.get_runtime_flag("kill_switch") == "1"
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_autoresume_task.py -q`
Expected: FAIL — `ImportError: cannot import name 'autoresume_sweep'`.

- [ ] **Step 3: Реализовать**

В `chatter/telethon_run.py`:

Константа `AUTORESUME_INTERVAL_SECONDS = 60.0` уже добавлена в Task 12 (её использует `render_status`) — заново не объявлять.

```python
def autoresume_sweep(store, *, now: float, auto_resume_hours: float) -> int:
    """Один прогон авто-возврата. Возвращает число размороженных диалогов.
    Пишет свой heartbeat ВСЕГДА — по нему /status отличает живую задачу от
    мёртвой (мёртвая выглядит как «пауз к возврату нет»: тихо и правдоподобно)."""
    resumed = 0
    for row in store.muted_contacts():
        if should_auto_resume(row, now=now, auto_resume_hours=auto_resume_hours):
            store.unmute(row["contact_id"])
            store.add_event("auto_resume", contact_id=row["contact_id"], ts=now)
            resumed += 1
    store.set_runtime_flag("autoresume_beat", str(now), ts=now)
    return resumed


async def autoresume_loop(store, *, auto_resume_hours: float,
                          interval: float = AUTORESUME_INTERVAL_SECONDS) -> None:
    while True:
        try:
            autoresume_sweep(store, now=time.time(), auto_resume_hours=auto_resume_hours)
        except Exception:
            # DEV-18: задача, умирающая тихо, = паузы залипли навсегда, а
            # владелец об этом не узнает. Логируем и продолжаем крутиться.
            log.exception("autoresume sweep failed; продолжаю цикл")
        await asyncio.sleep(interval)
```

Запустить задачу там же, где стартует `heartbeat_loop` (рядом с вызовом `catch_up_missed` в `run_client`):

```python
    asyncio.ensure_future(autoresume_loop(
        runner.primary_store(),
        auto_resume_hours=runner.control.auto_resume_hours))
```

Импортировать `should_auto_resume` из `chatter.core.pause` вверху файла.

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/test_autoresume_task.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Прогнать весь сьют**

Run: `cd /c/jarvis && PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/chatter/ tests/test_chatter_watch_check.py -q`
Expected: PASS, ноль падений.

- [ ] **Step 6: Commit**

```bash
cd /c/jarvis
git add chatter/telethon_run.py tests/chatter/test_autoresume_task.py
git commit -m "chatter(3A): periodic auto-resume with its own observable heartbeat"
```

---

### Task 14: Живой дрил + отчёт по арке (СТОП перед мерджем)

**Files:**
- Modify: `docs/superpowers/specs/2026-07-17-chatter-arc3a-control-design.md` (секция результатов)

- [ ] **Step 1: Доказать шов — ноль правок в пяти core-файлах**

```bash
cd /c/jarvis
git diff --stat d35b2be..HEAD -- chatter/core/brain.py chatter/core/humanizer.py \
  chatter/core/conversation.py chatter/core/disclosure.py chatter/core/guardrails.py
```
Expected: пустой вывод. Непустой = шов сломан, ДОЛОЖИТЬ (не чинить молча).

- [ ] **Step 2: 🔴 РУЧНОЙ БЭКАП ЖИВОЙ БАЗЫ — ДО перезапуска, ОТДЕЛЬНО от Store._backup**

Рестарт раннера — момент, когда миграция ВПЕРВЫЕ тронет реальную базу оператора (29 сообщений, единственная живая переписка Ани). Если она сожрёт историю — откатывать будет нечем.

`Store._backup` кладёт `.bak` РЯДОМ с базой, в `.secrets/` — то есть в тот же каталог, который может утащить за собой любая ошибка, чистка или сама неудачная миграция. Это защита от бага в ALTER, а НЕ от потери каталога. Нужна вторая копия, вне `.secrets/` и вне репозитория:

```bash
cd /c/jarvis
mkdir -p "E:/backups/chatter"
cp .secrets/chatter_telethon.db "E:/backups/chatter/chatter_telethon.pre-3a-manual-$(date +%Y%m%d-%H%M%S).db"
ls -la "E:/backups/chatter/"
```

(`E:` — SSD 953.9ГБ, отдельный физический диск от `C:`.)

Проверить, что копия ЧИТАЕТСЯ и данные в ней целы — файл ненулевого размера это не доказательство:

```bash
cd /c/jarvis && PYTHONUTF8=1 python -c "
import sqlite3, glob
p = sorted(glob.glob('E:/backups/chatter/chatter_telethon.pre-3a-manual-*.db'))[-1]
c = sqlite3.connect(p)
print('бэкап:', p)
print('сообщений:', c.execute('select count(*) from messages').fetchone()[0], '(ожидаем 29)')
print('контактов:', c.execute('select count(*) from contacts').fetchone()[0], '(ожидаем 1)')
print('последнее:', c.execute('select text from messages order by id desc limit 1').fetchone()[0][:50])
"
```

**Не переходить к Step 3, пока этот вывод не показал 29 сообщений и читаемую кириллицу.**

- [ ] **Step 3: Перезапустить раннер на новом коде**

Гардиан перезапустит сам после kill (~90с, debounce 3×30с):
```bash
cd /c/jarvis
powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*chatter.telethon_run*' } | ForEach-Object { taskkill.exe /PID \$_.ProcessId /T /F }"
```
Дождаться в `logs/chatter_guardian.stdout.log` строки `runner heartbeat fresh`.
Проверить в `logs/chatter_telethon.log`: миграция прошла, `.secrets/chatter_telethon.db.pre-3a-*.bak` создан.

- [ ] **Step 4: Живой дрил (пункт Е задания) — прогнать с оператором**

1. Оператор (237616472) пишет Ане → Аня отвечает.
2. Оператор пишет с аккаунта TAMAPI **руками** в тот же диалог → **Аня замолкает**; в Saved Messages появляется карточка паузы.
3. `/status` в Saved Messages → показывает диалог, причину «вы вмешались», текст сообщения, msg id, возраст heartbeat авто-возврата.
4. Оператор пишет лиду ещё раз → Аня по-прежнему молчит.
5. `/resume` **реплаем на карточку** → Аня снова отвечает.
6. `/stop` → Аня молчит везде; `/status` показывает 🔴; `/start` → снова работает.
7. **Пауза переживает рестарт:** снова заглушить, убить раннер, дождаться гардиана → `/status` показывает ту же паузу с той же причиной.

- [ ] **Step 5: Записать результат дрила в спеку**

Дописать в спеку секцию «Результат» с фактами: PID раннера, время каждого шага, что показал `/status`, вывод `git diff --stat` по пяти core-файлам. Как в арке 2 — фактами, не «всё работает».

- [ ] **Step 6: Commit**

```bash
cd /c/jarvis
git add docs/superpowers/specs/2026-07-17-chatter-arc3a-control-design.md
git commit -m "docs(chatter): 3A drill results — takeover proven live"
```

- [ ] **Step 7: СТОП. Не мерджить.**

Доложить оператору: что сделано, вывод `git diff --stat` по пяти core-файлам, результат дрила, счётчик тестов. Ждать решения о мердже.
