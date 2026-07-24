from __future__ import annotations
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from chatter.core.obligations_slot import Obligation

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
CREATE TABLE IF NOT EXISTS status_index (
    n INTEGER PRIMARY KEY,
    contact_id TEXT NOT NULL,
    issued_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS contact_profile (
    contact_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    text TEXT NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY (contact_id, version)
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    tag TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_read_input_tokens INTEGER NOT NULL,
    cache_creation_input_tokens INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS contact_obligations (
    contact_id     TEXT NOT NULL,
    okey           TEXT NOT NULL,
    kind           TEXT NOT NULL,
    owed_by        TEXT NOT NULL,
    status         TEXT NOT NULL,
    detail         TEXT NOT NULL,
    created_msg_id INTEGER,
    closed_msg_id  INTEGER,
    created_ts     REAL NOT NULL,
    closed_ts      REAL,
    PRIMARY KEY (contact_id, okey)
);
"""

# Источники паузы уровня КОНТАКТА. Глобальный kill switch живёт в
# runtime_flags и сюда не входит: он не про конкретный диалог.
ROW_MUTE_SOURCES = frozenset({"human_takeover", "command"})

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

class Store:
    """One SQLite file, safe to use across threads.

    The Telethon transport runs `process_batch` in worker threads
    (`asyncio.to_thread`), so the connection is opened with
    `check_same_thread=False` and every operation is serialized under a lock.
    Without this, SQLite raises "objects created in a thread can only be used
    in that same thread" the moment a reply is processed off the event loop.
    (The synchronous fake-console transport and the unit tests all run on one
    thread, which is why this only surfaced under the live async transport.)
    """

    def __init__(self, path: str | Path):
        path = Path(path)
        # ':memory:' не файл — Path(':memory:').exists() корректно даёт False,
        # а str(Path(':memory:')) == ':memory:', так что sqlite3.connect
        # по-прежнему получает in-memory базу, а не создаёт файл на диске.
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

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def get_or_create_contact(self, contact_id: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM contacts WHERE contact_id=?", (contact_id,)).fetchone()
            if row is None:
                self._conn.execute("INSERT INTO contacts(contact_id) VALUES (?)", (contact_id,))
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM contacts WHERE contact_id=?", (contact_id,)).fetchone()
            return dict(row)

    def set_state(self, contact_id: str, state: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE contacts SET state=? WHERE contact_id=?", (state, contact_id))
            self._conn.commit()

    def mute(self, contact_id: str, *, source: str, msg_id: int | None = None,
             detail: str | None = None, until: float | None = None, now: float) -> None:
        """Заглушить диалог. `source` ОБЯЗАТЕЛЕН: пауза без причины — баг-класс
        (спека §4), поэтому её нельзя поставить даже случайно.

        Контакт тоже ОБЯЗАН существовать: `KeyError`, а не тихий no-op.
        `UPDATE ... WHERE contact_id=?` на несуществующей строке обновляет
        0 строк и без проверки rowcount вернул бы None ровно как при успехе —
        вызывающий код думал бы, что пауза встала, а Аня продолжила бы
        отвечать поверх владельца, который уже пишет клиенту руками, и
        /status соврал бы «пауз нет». Это тот же класс ошибки, что и
        отсутствие атрибуции причины, поэтому та же паранойя. `KeyError`,
        а не `ValueError`: это буквально «нет строки с таким ключом»,
        симметрично dict[missing_key] — тогда как `ValueError` в этом файле
        уже занят под «источник паузы не из разрешённого набора»."""
        if source not in ROW_MUTE_SOURCES:
            raise ValueError(f"unknown mute source: {source!r} (need one of {sorted(ROW_MUTE_SOURCES)})")
        with self._lock:
            cur = self._conn.execute(
                "UPDATE contacts SET paused=1, pause_source=?, pause_msg_id=?, "
                "pause_detail=?, pause_until=?, paused_at=? WHERE contact_id=?",
                (source, msg_id, detail, until, now, contact_id))
            if cur.rowcount == 0:
                # Ничего не менялось — коммитить нечего, и raise внутри
                # `with self._lock` всё равно корректно освобождает лок
                # (это гарантия контекст-менеджера, exit вызывается и при
                # исключении).
                raise KeyError(f"mute: unknown contact_id {contact_id!r} - no such contact row")
            self._conn.commit()

    def begin_takeover(self, contact_id: str, *, msg_id: int | None = None,
                        detail: str | None = None, now: float) -> bool:
        """Атомарно попытаться начать НОВЫЙ эпизод перехвата человеком.

        Telethon без `sequential_updates=True` диспетчеризует каждый
        `NewMessage(outgoing=True)` ОТДЕЛЬНОЙ параллельной задачей: владелец
        быстро печатает 3 сообщения лиду -- три параллельных обработчика,
        каждый по отдельности решает "человек вмешался" (после своего
        грейс-окна) и был бы готов заново мутить/слать карточку/писать
        событие `takeover`, хотя это ОДИН эпизод, а не три.

        Один `UPDATE ... WHERE contact_id=? AND paused=0` под `self._lock`
        закрывает это атомарно: rowcount==1 значит строка была свободна и
        теперь заглушена НАМИ -- эпизод наш, единственный, кому положено
        слать карточку и событие `takeover`. rowcount==0 значит контакт УЖЕ
        заглушён (либо параллельный вызов уже выиграл эпизод, либо пауза
        стоит по другой причине) -- эпизод не наш.

        Контакт обязан существовать (`get_or_create_contact` до этого вызова)
        — как и `mute()`, эта операция не создаёт строк сама."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE contacts SET paused=1, pause_source='human_takeover', "
                "pause_msg_id=?, pause_detail=?, pause_until=NULL, paused_at=? "
                "WHERE contact_id=? AND paused=0",
                (msg_id, detail, now, contact_id))
            self._conn.commit()
            return cur.rowcount == 1

    def update_pause_attribution(self, contact_id: str, *, msg_id: int | None,
                                  detail: str | None) -> None:
        """Обновить причину УЖЕ идущего эпизода на более свежее сообщение
        (продолжение перехвата -- см. begin_takeover). Условие
        `pause_msg_id IS NULL OR pause_msg_id<?` — намеренно СРАВНЕНИЕ ID, А
        НЕ "последний вызов побеждает": Telegram message id монотонно
        растёт с каждым новым сообщением, а порядок ЗАВЕРШЕНИЯ параллельных
        asyncio-задач (см. begin_takeover) не гарантирует порядок
        сообщений -- более раннее сообщение может обработаться позже более
        позднего. Без этого условия /status показал бы случайную реплику
        вместо действительно последней."""
        if msg_id is None:
            return
        with self._lock:
            self._conn.execute(
                "UPDATE contacts SET pause_msg_id=?, pause_detail=? "
                "WHERE contact_id=? AND (pause_msg_id IS NULL OR pause_msg_id<?)",
                (msg_id, detail, contact_id, msg_id))
            self._conn.commit()

    def unmute(self, contact_id: str) -> None:
        """В отличие от `mute`, тихий no-op на несуществующем контакте —
        приемлемое поведение: «снять паузу с того, у кого её нет» уже
        достигнуто, это идемпотентность, а не потерянное действие (например
        /resume на диалог, который уже отвечает сам, не должен падать)."""
        with self._lock:
            self._conn.execute(
                "UPDATE contacts SET paused=0, pause_source=NULL, pause_msg_id=NULL, "
                "pause_detail=NULL, pause_until=NULL, paused_at=NULL WHERE contact_id=?",
                (contact_id,))
            self._conn.commit()

    def muted_contacts(self) -> list[dict]:
        # ORDER BY paused_at ASC: легаси-строки арки 1 (paused=1 без
        # paused_at, см. тест на легаси-миграцию) получают NULL, а в SQLite
        # NULL сортируется РАНЬШЕ любого значения при ASC — такая строка
        # уместно оказывается первой в выдаче: её "возраст" неизвестен, и
        # безопаснее показать её владельцу сразу, а не похоронить в хвосте.
        # Она ОБЯЗАНА присутствовать в выдаче вообще: строка, которая глушит
        # Аню и невидима в /status, — это и есть тихая вечная самозаглушка,
        # ровно тот баг-класс, от которого вся эта арка.
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM contacts WHERE paused=1 ORDER BY paused_at").fetchall()
        return [dict(r) for r in rows]

    def note_human_out(self, contact_id: str, *, ts: float) -> None:
        """Отметить ручное сообщение владельца — от него, а НЕ от paused_at,
        отсчитывается авто-возврат."""
        with self._lock:
            self._conn.execute(
                "UPDATE contacts SET last_human_out_ts=? WHERE contact_id=?", (ts, contact_id))
            self._conn.commit()

    def add_message(self, contact_id: str, role: str, text: str, ts: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages(contact_id, role, text, ts) VALUES (?,?,?,?)",
                (contact_id, role, text, ts))
            self._conn.commit()

    def delete_command_messages(self, prefixes: list[str], role: str = "assistant") -> int:
        """Удалить из истории сообщения-команды (Fix 3): владелец набрал /resume
        и т.п. ПРЯМО в диалоге лида, они легли как сообщения Ани и теперь идут в
        модель как контекст. Матч по trim+lower + LIKE '<prefix>%'. Возвращает
        число удалённых. Пустой список префиксов → no-op (0)."""
        if not prefixes:
            return 0
        conds = " OR ".join(["lower(trim(text)) LIKE ?"] * len(prefixes))
        params = [role] + [p.lower() + "%" for p in prefixes]
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM messages WHERE role=? AND ({conds})", params)
            self._conn.commit()
            return cur.rowcount

    def history(self, contact_id: str, limit: int | None = None) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, text, ts FROM messages WHERE contact_id=? ORDER BY id",
                (contact_id,)).fetchall()
        rows = [dict(r) for r in rows]
        if limit is None:
            return rows
        return rows[-limit:] if limit else []

    def set_fact(self, contact_id: str, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO facts(contact_id, key, value) VALUES (?,?,?) "
                "ON CONFLICT(contact_id, key) DO UPDATE SET value=excluded.value",
                (contact_id, key, value))
            self._conn.commit()

    def facts(self, contact_id: str) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM facts WHERE contact_id=?", (contact_id,)).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def count_messages_since(self, contact_id: str, role: str, since_ts: float) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE contact_id=? AND role=? AND ts>=?",
                (contact_id, role, since_ts)).fetchone()[0]

    def count_outbound_between(self, start_ts: float, end_ts: float) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM messages WHERE role='assistant' AND ts>=? AND ts<?",
                (start_ts, end_ts)).fetchone()[0]

    def has_contact(self, contact_id: str) -> bool:
        """Только проверка, БЕЗ побочных эффектов. get_or_create_contact тут
        не годится: он создал бы строку и объявил управляемым любой чат, куда
        владелец написал с этого же аккаунта (не диалог, который ведёт Аня)."""
        with self._lock:
            return self._conn.execute(
                "SELECT 1 FROM contacts WHERE contact_id=?", (contact_id,)).fetchone() is not None

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

    def get_runtime_flag_ts(self, key: str) -> float | None:
        """Момент последней записи флага (unix-время), None если флага нет.
        Дедуп эскалации меряет по нему ВОЗРАСТ активной карточки."""
        with self._lock:
            row = self._conn.execute(
                "SELECT ts FROM runtime_flags WHERE key=?", (key,)).fetchone()
        return float(row["ts"]) if row else None

    def add_event(self, kind: str, *, contact_id: str | None = None,
                  detail: str | None = None, ts: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO control_events(kind, contact_id, detail, ts) VALUES (?,?,?,?)",
                (kind, contact_id, detail, ts))
            self._conn.commit()

    # --- профиль лида (арка «память+стоимость») ------------------------------
    def get_profile(self, contact_id: str) -> str | None:
        """Актуальный профиль = последняя версия. Append-only: прошлые версии
        остаются в таблице для отладки «кто и когда поменял факт»."""
        with self._lock:
            row = self._conn.execute(
                "SELECT text FROM contact_profile WHERE contact_id=? "
                "ORDER BY version DESC LIMIT 1", (contact_id,)).fetchone()
        return row["text"] if row else None

    def set_profile(self, contact_id: str, text: str, *, ts: float) -> None:
        """Новая версия ЗАМЕНЯЕТ профиль целиком (условие 3: конфликт фактов
        решён на уровне текста — актуальное значение с пометкой «раніше X»,
        а не два равнозначных факта в разных записях)."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM contact_profile "
                "WHERE contact_id=?", (contact_id,)).fetchone()[0]
            self._conn.execute(
                "INSERT INTO contact_profile(contact_id, version, text, ts) "
                "VALUES (?,?,?,?)", (contact_id, cur + 1, text, ts))
            self._conn.commit()

    # --- слот открытых обязательств (спека 2026-07-24 §3) --------------------
    def get_obligations(self, contact_id: str) -> list[Obligation]:
        """Все обязательства контакта (включая закрытые — их несёт секция
        «ЗАКРИТО НЕДАВНО» рендера). Порядок по created_ts для стабильности."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT okey, kind, owed_by, status, detail, created_msg_id, "
                "closed_msg_id, created_ts, closed_ts FROM contact_obligations "
                "WHERE contact_id=? ORDER BY created_ts, okey", (contact_id,)).fetchall()
        return [Obligation(
            okey=r["okey"], kind=r["kind"], owed_by=r["owed_by"], status=r["status"],
            detail=r["detail"], created_msg_id=r["created_msg_id"],
            closed_msg_id=r["closed_msg_id"], created_ts=r["created_ts"],
            closed_ts=r["closed_ts"]) for r in rows]

    def save_obligations(self, contact_id: str, obligations) -> None:
        """Заменить весь набор обязательств контакта (delete+insert под одним
        локом, атомарно). Обязательств на контакт единицы — полная замена проще
        апсерта и не даёт дрейфа. РЕШЕНИЕ что хранить принимает merge_obligations
        (obligations_slot) ВЫШЕ; store только персистит результат."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM contact_obligations WHERE contact_id=?", (contact_id,))
            self._conn.executemany(
                "INSERT INTO contact_obligations(contact_id, okey, kind, owed_by, "
                "status, detail, created_msg_id, closed_msg_id, created_ts, closed_ts) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(contact_id, o.okey, o.kind, o.owed_by, o.status, o.detail,
                  o.created_msg_id, o.closed_msg_id, o.created_ts, o.closed_ts)
                 for o in obligations])
            self._conn.commit()

    def max_message_id(self, contact_id: str) -> int | None:
        """MAX(messages.id) контакта — код штампует им created/closed обязательств
        (модель ненадёжно знает id). None, если сообщений нет."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(id) m FROM messages WHERE contact_id=?", (contact_id,)).fetchone()
        return row["m"] if row and row["m"] is not None else None

    # --- llm_usage: prompt-caching / расход токенов (спека 2026-07-23) -------
    def add_llm_usage(self, *, tag: str, model: str, input_tokens: int,
                      output_tokens: int, cache_read_input_tokens: int,
                      cache_creation_input_tokens: int,
                      ts: float | None = None) -> None:
        """Одна строка на каждый LLM-вызов. ts — момент вызова (дефолт: сейчас);
        по нему же считаются интервалы диалога для решения о TTL кэша."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO llm_usage(ts, tag, model, input_tokens, "
                "output_tokens, cache_read_input_tokens, "
                "cache_creation_input_tokens) VALUES (?,?,?,?,?,?,?)",
                (time.time() if ts is None else ts, tag, model, input_tokens,
                 output_tokens, cache_read_input_tokens,
                 cache_creation_input_tokens))
            self._conn.commit()

    def llm_usage_totals(self) -> dict[str, dict[str, int]]:
        """Агрегаты по tag для замера экономии и будущего дайджеста расходов."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT tag, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens, "
                "SUM(output_tokens) AS output_tokens, "
                "SUM(cache_read_input_tokens) AS cache_read_input_tokens, "
                "SUM(cache_creation_input_tokens) AS cache_creation_input_tokens "
                "FROM llm_usage GROUP BY tag").fetchall()
        return {r["tag"]: {k: r[k] for k in
                           ("calls", "input_tokens", "output_tokens",
                            "cache_read_input_tokens",
                            "cache_creation_input_tokens")}
                for r in rows}

    def count_events(self, kind: str, *, since_ts: float) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM control_events WHERE kind=? AND ts>=?",
                (kind, since_ts)).fetchone()[0]

    def add_card(self, *, msg_id: int, contact_id: str, kind: str, ts: float) -> None:
        # На конфликте msg_id перезаписываем ВСЕ поля, не только contact_id:
        # в реальности msg_id в Saved Messages не переиспользуется, так что
        # конфликт — либо повторная запись ТОЙ ЖЕ карточки (все поля и так
        # совпадут), либо программная ошибка/перепривязка. Частичный апдейт
        # (старый вариант обновлял только contact_id) в последнем случае
        # оставил бы kind/ts от предыдущей карточки лгать про новый
        # contact_id — полный оверрайт безопаснее и не добавляет риска.
        with self._lock:
            self._conn.execute(
                "INSERT INTO console_cards(msg_id, contact_id, kind, ts) VALUES (?,?,?,?) "
                "ON CONFLICT(msg_id) DO UPDATE SET "
                "contact_id=excluded.contact_id, kind=excluded.kind, ts=excluded.ts",
                (msg_id, contact_id, kind, ts))
            self._conn.commit()

    def card_contact(self, msg_id: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT contact_id FROM console_cards WHERE msg_id=?", (msg_id,)).fetchone()
        return row["contact_id"] if row else None

    def issue_status_index(self, contact_ids: list[str], *, now: float) -> None:
        """Выдать новый нумерованный список из `/status` — переписывает
        таблицу ЦЕЛИКОМ. Номер привязывается к контакту В МОМЕНТ ВЫДАЧИ
        (спека 3A-UX §3): владелец видит "1. Даниил" глазами и копирует
        "/resume 1" — этот номер обязан навсегда означать Даниила, даже
        если тот успеет авто-вернуться до того, как владелец наберёт
        команду. DELETE+INSERT в ОДНОЙ транзакции под self._lock: половинчатый
        снимок (старые номера удалены, новые ещё не вставлены) — это окно,
        где /resume N увидел бы "нет такого номера" хотя список только что
        был; последовательный DELETE и INSERT без общего лока дал бы такое
        же окно параллельному читателю."""
        with self._lock:
            self._conn.execute("DELETE FROM status_index")
            self._conn.executemany(
                "INSERT INTO status_index(n, contact_id, issued_ts) VALUES (?,?,?)",
                [(i, cid, now) for i, cid in enumerate(contact_ids, start=1)],
            )
            self._conn.commit()

    def status_index_contact(self, n: int) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT contact_id FROM status_index WHERE n=?", (n,)).fetchone()
        return row["contact_id"] if row else None

    def status_index_snapshot(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT contact_id FROM status_index ORDER BY n").fetchall()
        return [r["contact_id"] for r in rows]

    def status_index_is_current(self, muted_contact_ids: list[str]) -> bool:
        """Отвечает на вопрос "тот ли это список, который владелец видел",
        а не "сколько минут прошло" (спека §3: TTL гадает про доверие, набор
        отвечает на настоящий вопрос). Сравнение по МНОЖЕСТВАМ, не по
        последовательности: `muted_contacts()` сортирует по `paused_at`, и
        этот порядок может измениться между /status и /resume без изменения
        сути (например `update_pause_attribution` не трогает paused_at, но
        параллельный второй takeover где-то ещё мог бы) — а КОМУ соответствует
        каждый номер уже зафиксировано в самой таблице status_index при
        выдаче, порядок текущего muted_contacts() на это не влияет. Вопрос
        "тот ли список" — про состав диалогов, не про их взаимный порядок."""
        with self._lock:
            snapshot = {r["contact_id"] for r in self._conn.execute(
                "SELECT contact_id FROM status_index")}
        return snapshot == set(muted_contact_ids)


def usage_sink_for(store: Store):
    """Sink для AnthropicLLM(usage_sink=...): пишет запись вызова в llm_usage.
    Сбой записи ловит сам AnthropicLLM (log.warning, ответ не роняется)."""
    def _sink(rec: dict) -> None:
        store.add_llm_usage(**rec)
    return _sink
