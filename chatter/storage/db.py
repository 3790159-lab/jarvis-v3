from __future__ import annotations
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from chatter.core.obligations_slot import Obligation
from chatter.payments.model import PaymentRecord, project_status, validate_dedup_key
from chatter.payments.statuses import assert_transition
from chatter.payments.money import Money
from chatter.core.contact_ref import (
    SEPARATOR,
    TELEGRAM_CHANNEL,
    ContactRefError,
    slug_of,
    upgrade_legacy_callback_contact,
)


class PaymentsMigrationBlocked(RuntimeError):
    """Перестройка `payments` в минорные INTEGER невозможна или не удалась.

    Отдельный громкий тип, а не тихий пропуск: база с двумя разными схемами
    денег хуже, чем не поднявшийся раннер, — второе видно сразу, первое
    обнаружится враньём в отчётности через месяц."""


class ContactIdMigrationBlocked(RuntimeError):
    """Перепись `contact_id` на форму с каналом невозможна или не удалась.

    `RuntimeError`, а НЕ `ValueError` (§13.2), и это не педантизм. Отказ
    РАЗБОРА — плохое значение, его уместно поймать и ответить лиду. Отказ
    МИГРАЦИИ — плохое СОСТОЯНИЕ базы, и ловить его нельзя вовсе: он обязан
    остановить подъём. Один тип на оба означал бы, что первый же
    `except ValueError` вокруг разбора однажды проглотит несостоявшуюся
    перепись живых данных."""


_SCHEMA = """
-- `display_name` — имя лида для интерфейса. Кэш, а не источник: резолв
-- требует Telethon, которого у веба нет; пишет раннер при первом контакте.
-- NULL отличим от «имя стёрли»: пустая строка означала бы, что мы уже
-- спрашивали и получили пустоту.
--
-- Комментарии живут НАД оператором намеренно: внутри CREATE TABLE они
-- ломают ALTER TABLE DROP COLUMN — SQLite пересобирает исходный текст DDL
-- и спотыкается о `--` (поймано тестом миграции).
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
    last_human_out_ts REAL,
    display_name TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id TEXT NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    ts REAL NOT NULL,
    -- Кто написал: 'bot' или 'human'. НЕ роль: роль уходит в поле `role`
    -- вызова Anthropic напрямую (brain.build_messages), и роли 'human' в том
    -- API нет — она сломала бы вызов. Ручное сообщение владельца ложится как
    -- role='assistant' (с точки зрения лида это та же персона, общий аккаунт)
    -- и author='human'.
    author TEXT
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
    cache_creation_input_tokens INTEGER NOT NULL,
    cache_creation_5m INTEGER,
    cache_creation_1h INTEGER
);
CREATE TABLE IF NOT EXISTS runtime_allow (
    peer_id INTEGER PRIMARY KEY,
    ts REAL NOT NULL
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

-- Фундамент дашборда (CLIENT_SCREENS.md §5.2). Обе таблицы пишутся ТОЛЬКО
-- вперёд: восстановить прошлое неоткуда, поэтому каждый день без них —
-- безвозвратно потерянная история.

-- История переходов воронки. `contacts.state` хранит лишь ТЕКУЩЕЕ состояние и
-- перезаписывается, поэтому вопрос «сколько квалифицировалось за неделю» без
-- этой таблицы не имеет ответа: контакт, прошедший new→qualifying→hot→closed,
-- виден только как closed.
CREATE TABLE IF NOT EXISTS funnel_transitions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state   TEXT NOT NULL,
    signal     TEXT,
    ts         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_funnel_transitions_ts ON funnel_transitions(ts);

-- Оплаты (арка payments, Ф0 — перестроена). `amount_minor` NULLABLE намеренно:
-- владелец часто знает «оплатил», но не хочет вводить сумму; запретить — значит
-- потерять и сам факт оплаты.
--
-- Суммы — ЦЕЛЫЕ В МИНОРНЫХ единицах. Прежнее `amount REAL` снято, а не оставлено
-- «для истории»: на 2026-08-10 в боевой БД 0 строк, и это единственное окно,
-- когда замену можно сделать без дуального чтения в отчётности навсегда.
--
-- `dedup_key` — ОДИН ключ идемпотентности с префиксом источника
-- (`tap:` / `panel:` / `evt:`) вместо прежнего card_msg_id с сентинелом `0`.
-- Сентинел был стабилен ровно до второго бескарточного источника: веб-панель
-- уже сегодня схлопывала свои оплаты в одну, а в Ф2 то же случилось бы со
-- всеми webhook'ами провайдера.
--
-- `channel_id` и `confirmed_by` — две РАЗНЫЕ оси вместо прежнего `source`:
-- «через что пришли деньги» и «кто это подтвердил» перестают путаться.
CREATE TABLE IF NOT EXISTS payments (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id        TEXT NOT NULL,
    dedup_key         TEXT NOT NULL,
    invoice_id        TEXT,
    stage_no          INTEGER,
    amount_minor      INTEGER,
    amount_received   INTEGER,
    currency          TEXT NOT NULL DEFAULT 'USD',
    currency_received TEXT,
    channel_id        TEXT,
    confirmed_by      TEXT NOT NULL,
    external_event_id TEXT,
    ts                REAL NOT NULL,
    UNIQUE (contact_id, dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_payments_ts ON payments(ts);
CREATE INDEX IF NOT EXISTS idx_payments_invoice ON payments(invoice_id);

-- Котировка: что назвали лиду. APPEND-ONLY — каждая уступка это новая строка,
-- предыдущая становится `superseded`. Без истории торга бот на повторный заход
-- назовёт другую цену тому же лиду (§2.3).
--
-- `tier_id` — выбранная ПУБЛИЧНАЯ ступень объёма (решение владельца 12.08).
-- NULL — объём не назван, сумма по политике price_upper. Идентификатор, а не
-- текст: по нему счёт узнаёт, за какой объём он выставлен. Комментарий стоит
-- НАД оператором, а не внутри него: SQLite пересобирает исходный текст DDL, и
-- `--` внутри CREATE TABLE ломает перестройку таблицы (та самая, что ниже).
--
-- `origin_msg_id` объявлен TEXT, а не INTEGER: INTEGER-аффинность приведёт
-- '007' к числу 7, и '007' с '7' станут ОДНИМ ключом идемпотентности — два
-- разных сообщения канала схлопнутся, второй счёт будет молча подавлен как
-- дубль (§5.1).
CREATE TABLE IF NOT EXISTS quotes (
    quote_id          TEXT PRIMARY KEY,
    contact_id        TEXT NOT NULL,
    position_id       TEXT NOT NULL,
    step_idx          INTEGER NOT NULL,
    amount_minor      INTEGER NOT NULL,
    currency          TEXT NOT NULL,
    scope_key         TEXT NOT NULL,
    amount_source     TEXT NOT NULL,
    tier_id           TEXT,
    knowledge_version TEXT,
    status            TEXT NOT NULL,
    origin_msg_id     TEXT,
    created_ts        REAL NOT NULL,
    UNIQUE (contact_id, origin_msg_id)
);
CREATE INDEX IF NOT EXISTS idx_quotes_contact ON quotes(contact_id);

-- Счёт. `amount_total` NULL допустим ТОЛЬКО в draft/awaiting_owner: счёт без
-- суммы — нормальный случай гейта сложности, а не край.
-- UNIQUE(contact_id, origin_msg_id) — идемпотентность ВЫСТАВЛЕНИЯ: ретрай
-- пайплайна не имеет права породить второй счёт.
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id           TEXT PRIMARY KEY,
    contact_id           TEXT NOT NULL,
    quote_id             TEXT,
    channel_id           TEXT,
    amount_total         INTEGER,
    currency             TEXT NOT NULL,
    amount_source        TEXT,
    status               TEXT NOT NULL,
    created_ts           REAL NOT NULL,
    created_by           TEXT NOT NULL,
    origin_msg_id        TEXT,
    issued_ts            REAL,
    due_ts               REAL,
    price_source         TEXT,
    requisites_ref       TEXT,
    instruction_snapshot TEXT,
    owner_approved_ts    REAL,
    external_ref         TEXT,
    first_payment_ts     REAL,
    cancelled_reason     TEXT,
    UNIQUE (contact_id, origin_msg_id)
);
CREATE INDEX IF NOT EXISTS idx_invoices_contact ON invoices(contact_id);

-- Ступени. Ф0 пишет РОВНО ОДНУ (stage_no=1): тогда Ф1 — это «строк больше
-- одной», а не бэкфилл и вечная ветка «счета без ступеней».
CREATE TABLE IF NOT EXISTS invoice_stages (
    invoice_id TEXT NOT NULL,
    stage_no   INTEGER NOT NULL,
    amount_due INTEGER NOT NULL,
    due_ts     REAL,
    status     TEXT NOT NULL,
    PRIMARY KEY (invoice_id, stage_no)
);

-- Исходящая очередь: панель КЛАДЁТ задание, раннер ОТПРАВЛЯЕТ (спека
-- 2026-08-25 §2). Панель отправить не может физически — транспорт привязан к
-- конкретному чату и живёт внутри раннера, а сессия Telethon это файл, который
-- держит живой процесс. Поэтому единственный общий язык двух процессов — эта
-- таблица.
--
-- Побочная выгода дороже самой отправки: задание переживает ЛЕЖАЧИЙ раннер, и
-- «владелец нажал, а оно не ушло» перестаёт быть тишиной — у неотправленного
-- есть возраст (`oldest_pending_outgoing_age`), и его видно пробой.
--
-- `token` UNIQUE — идемпотентность: у панели есть кнопка, а у кнопки есть
-- двойное нажатие. Второе нажатие обязано дать ТО ЖЕ задание, а не второе
-- сообщение лиду.
--
-- `sent_msg_id` СТРОКОЙ (спека §5): числовой id есть только у Telegram, у веба
-- и бизнес-API его нет вовсе. Колонка заводится сразу строковой, чтобы второй
-- канал не потребовал миграции живой очереди.
--
-- `attempts` считает НЕУДАЧНЫЕ попытки доставки: успех строку закрывает, и
-- считать в ней нечего. `last_error` — текст СЛОВАМИ, тот же, что увидит
-- владелец: «не доставлено» без причины отправляет его гадать.
--
-- `status='dismissed'` — «владелец увидел отказ и закрыл его». Четвёртое
-- состояние заведено вместе с красной лампой на отказы, и без него лампу
-- заводить было НЕЛЬЗЯ: отказ терминален, а лампа, зажжённая навсегда, за
-- неделю становится фоном — тот самый класс, который в этом доме уже чинили на
-- двух вечно-красных пробах. Строка при снятии не удаляется и не правится
-- иначе: она остаётся уликой (текст, причина, время), но перестаёт кричать.
CREATE TABLE IF NOT EXISTS outgoing_queue (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id   TEXT NOT NULL,
    text         TEXT NOT NULL,
    author       TEXT NOT NULL DEFAULT 'human',
    token        TEXT NOT NULL UNIQUE,
    status       TEXT NOT NULL,
    created_ts   REAL NOT NULL,
    sent_ts      REAL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT,
    sent_msg_id  TEXT
);
-- Раннер спрашивает «что ещё не ушло» каждые 5 секунд весь срок процесса, а
-- панель — на каждый показ лампы. Без индекса это полный скан таблицы, которая
-- растёт вперёд и никогда не чистится.
CREATE INDEX IF NOT EXISTS idx_outgoing_pending
    ON outgoing_queue(status, created_ts);
"""

# Статусы строки очереди. Литеральным множеством, а не «любая строка»: статус,
# которого никто не ждёт, — это задание, которое не отправит и не покажет
# никто, то есть молча потерянное сообщение владельца.
#
# ЧЕТВЁРТЫЙ (`dismissed`) — это не «ещё одно состояние на всякий случай», а
# ПУТЬ СНЯТИЯ красной лампы: без него исход «есть отказ» горел бы вечно, потому
# что отказ терминален. Лампа без пути снятия становится фоном за неделю.
OUTGOING_STATUSES = ("pending", "sent", "refused", "dismissed")

# Источники паузы уровня КОНТАКТА. Глобальный kill switch живёт в
# runtime_flags и сюда не входит: он не про конкретный диалог.
ROW_MUTE_SOURCES = frozenset({"human_takeover", "command"})

# Колонки, которых нет в базах арки 1/2. CREATE TABLE IF NOT EXISTS не добавляет
# колонки в СУЩЕСТВУЮЩУЮ таблицу — старая база получит их только через ALTER.
_ADDED_COLUMNS = {
    # Автор сообщения (TG-1). NULL у исторических строк — осознанно и по той же
    # причине, что у llm_usage ниже: авторство задним числом НЕ восстановимо.
    # Пометить старые строки 'bot' значило бы соврать ровно про те сообщения,
    # ради которых колонка и заводится, — ручные реплики владельца, которые до
    # сих пор были неотличимы от бота. NULL здесь читается «не знаем».
    "messages": {
        "author": "TEXT",
    },
    # Разбивка записи кэша по TTL (арка «кэш классификатора», фаза 0). Ставки
    # записи разные ($3.75/M за 5m, $6/M за 1h), а суммарный
    # cache_creation_input_tokens их не различает. NULL у исторических строк —
    # осознанно: мы НЕ знаем, по какой ставке они оплачены, и не выдумываем 0
    # (дельта-скрипт помечает такие строки как оценку).
    "llm_usage": {
        "cache_creation_5m": "INTEGER",
        "cache_creation_1h": "INTEGER",
    },
    # Ступень объёма у котировки (решение владельца 12.08). NULL у исторических
    # строк честен: ярусов тогда не существовало, и «базовый» им не дописать.
    "quotes": {
        "tier_id": "TEXT",
    },
    "contacts": {
        "display_name": "TEXT",
        "paused_at": "REAL",
        "pause_source": "TEXT",
        "pause_msg_id": "INTEGER",
        "pause_detail": "TEXT",
        "pause_until": "REAL",
        "last_human_out_ts": "REAL",
    },
}

# ЛИТЕРАЛЬНЫЙ список из 13 таблиц СО СХЕМЫ (§4.1), а не «те таблицы, где
# сегодня есть строки»: данные живут в 9 из них, и список, выведенный из
# наличия строк, промолчит в день, когда наполнится десятая
# (jarvis-literal-lists-not-introspection).
_CONTACT_ID_TABLES: tuple[str, ...] = (
    "contacts", "messages", "facts", "console_cards", "control_events",
    "status_index", "contact_profile", "contact_obligations",
    "funnel_transitions", "payments", "quotes", "invoices", "outgoing_queue",
)

# `contact_id` живёт НЕ ТОЛЬКО в колонках: три префикса вшивают его в КЛЮЧ
# `runtime_flags` (`core/escalation.py`, `core/classifier.py`). Перепись,
# написанная как `UPDATE ... SET contact_id=...`, не тронет ни одного из них:
# каждая живая карточка эскалации и каждая серия промахов профиля осиротеет
# МОЛЧА.
_CONTACT_ID_FLAG_PREFIXES: tuple[str, ...] = (
    "esc_active:", "profile_miss:", "profile_miss_alerted:",
)

# Таблицы, у которых ключ идемпотентности выставления переезжает в TEXT (§5).
_ORIGIN_KEY_TABLES: tuple[str, ...] = ("quotes", "invoices")

# SQL переписи живёт КОНСТАНТАМИ МОДУЛЯ намеренно: `contact_id NOT LIKE
# '%:%:%'` — это разбор структуры contact_id средствами SQL, то есть место
# разбора, ВВЕДЁННОЕ переписью и живущее ровно до её конца. Оно названо вслух
# (§13.5), а не спрятано по методам, чтобы перепись мест разбора оставалась
# правдой.
#
# СТАРАЯ ФОРМА — ЭТО РОВНО ОДНО ДВОЕТОЧИЕ, и `LIKE '%:%'` в запросе стоит
# именно поэтому. Строка БЕЗ двоеточия вовсе (`console-user` из
# `chatter/run.py --contact`, формы `demo_switch`) contact_id этого формата
# никогда не была: переписывать в ней нечего, а СТОП на ней означал бы, что
# база, однажды видевшая консольный контакт, больше не открывается НИКОГДА.
# Дом уже принял это решение для `peer_label_of` (односегментный вход
# деградирует, а не роняет карточку) — два ответа на один вопрос здесь были бы
# несогласованностью, а не выбором. СТОП остаётся там, где он и задуман §4.1:
# на строке, которая ВЫГЛЯДИТ старой формой (одно двоеточие), но головы, по
# которой её переписать, у неё нет.
_SQL_LEGACY_CONTACT_IDS = (
    "SELECT DISTINCT contact_id FROM {table} WHERE contact_id LIKE '%:%' "
    "AND contact_id NOT LIKE '%:%:%'")
_SQL_SET_CONTACT_ID = "UPDATE {table} SET contact_id=? WHERE contact_id=?"
_SQL_FLAG_KEYS_BY_PREFIX = "SELECT key FROM runtime_flags WHERE key LIKE ?"
_SQL_SET_FLAG_KEY = "UPDATE runtime_flags SET key=? WHERE key=?"


def _schema_create_table(table: str) -> str:
    """`CREATE TABLE` нужной таблицы, вырезанный из `_SCHEMA`.

    Из `_SCHEMA`, а НЕ из `sqlite_master`: перестройка обязана строить НОВУЮ
    схему, а не копировать старую с её же дефектом. Побочная выгода — текст
    DDL наш, без комментариев внутри оператора.
    """
    head = "CREATE TABLE IF NOT EXISTS " + table + " ("
    start = _SCHEMA.index(head)
    end = _SCHEMA.index("\n);", start)
    return _SCHEMA[start:end + 3]


def _origin_key(value):
    """Ключ идемпотентности выставления — на ГРАНИЦЕ `Store`, в одном месте.

    §5.3: `create_invoice` ищет дубль как `WHERE contact_id=? AND
    origin_msg_id=?`, а в SQLite целое `7` и строка `'7'` НЕ РАВНЫ НИКОГДА.
    Значит вызыватель, продолжающий передавать `int`, существующего счёта не
    найдёт и выставит ВТОРОЙ: идемпотентность не сломается заметно, она
    перестанет существовать молча.

    §13.3, решение владельца: `int` приводим — приведение ОБРАТИМО и ключа не
    меняет (законный вызыватель `payments/dialogue.py` шлёт `msg_id: int`).
    `REAL`, `BLOB`, всё прочее — ОТКАЗ: `7.0` привелось бы в `'7.0'`, то есть
    в ДРУГОЙ ключ, и тихо сломало бы ровно ту идемпотентность, ради которой
    колонка существует.

    Отказ — `ValueError`: это плохое ЗНАЧЕНИЕ аргумента, а не состояние базы
    (состояние отказывает `RuntimeError`). И он случается ДО записи: счёт с
    неверным ключом, уже лежащий в таблице, убирать придётся человеку.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        raise ValueError(
            "origin_msg_id=%r: булево не ключ сообщения" % (value,))
    if isinstance(value, int):
        return str(value)
    raise ValueError(
        "origin_msg_id=%r (%s): ключ идемпотентности выставления принимает "
        "строку либо целое. Приведение целого обратимо ('7' и 7 — один ключ), "
        "приведение %s — нет: оно тихо создаёт ВТОРОЙ ключ на то же сообщение"
        % (value, type(value).__name__, type(value).__name__))


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
        # ВСЁ, что ниже, — под try: конструктор, бросивший на полпути, не
        # возвращает объект, и закрыть соединение становится НЕКОМУ. Ссылка
        # остаётся жить в кадре `__init__`, который держит трассировка
        # исключения, и на Windows это запирает сам ФАЙЛ базы: «база побилась»
        # превращается в «побилась И теперь её не удалить и не заменить»
        # (DEV-48, замер 22.08: rmtree даёт WinError 32).
        #
        # Это НЕ только про поломку. `_assert_payments_rebuild_window` бросает
        # НАМЕРЕННО — отказ конвертировать чужие деньги вне окна, — то есть
        # штатный, спроектированный путь запирал файл ровно так же.
        try:
            self._conn.row_factory = sqlite3.Row
            self._lock = threading.Lock()
            with self._lock:
                # Перестройка payments проверяется ДО любого DDL: отказ обязан
                # оставить базу нетронутой, а не «почти мигрированной».
                legacy = self._payments_is_legacy()
                if legacy:
                    self._assert_payments_rebuild_window(path)
                # Перепись личности (§4) и переезд ключа выставления в TEXT
                # (§5) замеряются ЗДЕСЬ ЖЕ, до любого DDL и до любой записи:
                # отказ обязан оставить базу ПОБАЙТОВО той же. Наполовину
                # переписанная база хуже непереписанной — откатить её можно
                # только из `.bak`, а `.bak` при отказе не снимается.
                #
                # Идемпотентность по ФАКТУ (`contact_id NOT LIKE '%:%:%'`), а
                # не по файлу-маркеру: маркер переживает откат базы и после
                # восстановления из `.bak` соврал бы «уже мигрировано». И не
                # по ловле исключения: гардиан перезапускает раннер постоянно,
                # и перепись, падающая на втором прогоне, — краш-петля,
                # которую он будет вечно поддерживать.
                legacy_ids = self._legacy_contact_ids()
                legacy_keys = self._legacy_flag_keys()
                if legacy_ids or legacy_keys:
                    self._assert_contact_ids_migratable(path, legacy_ids,
                                                        legacy_keys)
                rebuild = self._origin_key_legacy_tables()
                if rebuild:
                    self._assert_origin_rebuild_window(path, rebuild)
                if legacy:
                    if pre_existing:
                        self._backup(path, tag="payments")
                    # Снести ДО executescript: индекс по invoice_id не создастся
                    # на старой таблице, где такой колонки нет.
                    self._conn.execute("DROP TABLE payments")
                self._conn.executescript(_SCHEMA)
                self._conn.commit()
                if legacy:
                    self._assert_payments_rebuilt()
                missing = self._missing_columns()
                if missing:
                    # Бэкап ТОЛЬКО когда реально мигрируем существующую базу:
                    # иначе каждый рестарт раннера сыпал бы .bak-файлы клиенту.
                    if pre_existing:
                        self._backup(path)
                    self._apply_migration(missing)
                if legacy_ids or legacy_keys or rebuild:
                    # Бэкап ТОЛЬКО при РЕАЛЬНОЙ переписи существующей базы:
                    # `.bak` — это полная переписка лидов открытым файлом, и
                    # сыпать её на каждый рестарт значит плодить то, что никто
                    # не удаляет.
                    if pre_existing:
                        self._backup(path, tag="web-c")
                    self._apply_identity_migration(legacy_ids, legacy_keys,
                                                   rebuild)
                    self._assert_identity_migrated()
        except BaseException:
            # BaseException, а не Exception: `KeyboardInterrupt` посреди
            # миграции запирает файл точно так же, а прерывают тут руками.
            #
            # Закрываем СЫРОЕ соединение, а не через self.close(): тот берёт
            # self._lock, а до его создания мы могли не дойти. Клинча при этом
            # нет и с ним (замер 28.08: `with lock` отпускает замок ДО того,
            # как исключение уходит наружу), но зависимость лишняя.
            self._conn.close()
            # Голый raise: тип, сообщение и трассировка обязаны дойти до
            # вызывающего неизменными. Лечение владения ресурсом не имеет
            # права превращаться в проглатывание ошибки.
            raise

    def _missing_columns(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for table, cols in _ADDED_COLUMNS.items():
            have = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            gap = {c: decl for c, decl in cols.items() if c not in have}
            if gap:
                out[table] = gap
        return out

    # ── перепись личности: канал в contact_id (пара C, §4) ────────────────

    def _has_table(self, name: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,)).fetchone() is not None

    def _has_column(self, table: str, column: str) -> bool:
        return any(r["name"] == column
                   for r in self._conn.execute(f"PRAGMA table_info({table})"))

    def _legacy_contact_ids(self) -> dict[str, set[str]]:
        """Что осталось в форме БЕЗ головы канала — по 13 таблицам §4.1.

        🔴 УСЛОВИЕ — НАЛИЧИЕ КОЛОНКИ, А НЕ ТАБЛИЦЫ. Литеральный список §4.1
        описывает схему СЕГОДНЯШНЮЮ, а перепись бежит по базе ВЧЕРАШНЕЙ и
        бежит ДО того, как схему подняли: замер обязан оставить базу побайтово
        той же, значит он стоит раньше любого DDL. В окне между этими двумя
        фактами живёт `payments` СТАРОЙ схемы — таблица есть, колонки
        `contact_id` в ней нет вовсе, и `SELECT ... WHERE contact_id LIKE` по
        ней роняет `OperationalError` ещё до первой правки.

        Роняет он при этом не только перепись: исключение уходит из
        КОНСТРУКТОРА, а конструктор, бросивший на полпути, объекта не
        возвращает — закрыть соединение становится некому, и на Windows это
        запирает сам ФАЙЛ базы (DEV-48). То есть цена пропущенной проверки —
        не «миграция не прошла», а «база не открывается и не удаляется».
        Поймали сторожа `test_store_ownership.py`, и поймали правильно.

        Пропуск безопасен ровно потому, что таблица без колонки не может
        хранить старую форму: переписывать в ней нечего. `payments`, дойдя до
        своей перестройки ниже, приедет уже с колонкой и пустая.
        """
        out: dict[str, set[str]] = {}
        for table in _CONTACT_ID_TABLES:
            if not self._has_table(table):
                continue
            if not self._has_column(table, "contact_id"):
                continue
            rows = self._conn.execute(
                _SQL_LEGACY_CONTACT_IDS.format(table=table)).fetchall()
            values = {r[0] for r in rows if r[0] is not None}
            if values:
                out[table] = values
        return out

    def _legacy_flag_keys(self) -> dict[str, str]:
        """Ключи `runtime_flags` старой формы: ключ -> его хвост-contact_id."""
        out: dict[str, str] = {}
        if not self._has_table("runtime_flags"):
            return out
        for prefix in _CONTACT_ID_FLAG_PREFIXES:
            rows = self._conn.execute(
                _SQL_FLAG_KEYS_BY_PREFIX, (prefix + "%",)).fetchall()
            for r in rows:
                key = r[0]
                tail = key[len(prefix):]
                if tail.count(SEPARATOR) == 1:
                    out[key] = tail
        return out

    def _assert_contact_ids_migratable(self, path: Path, legacy_ids, legacy_keys) -> None:
        """Правило замены одно (§4.1): `<peer>:<slug>` -> `telegram:<peer>:<slug>`,
        и только если голова числовая (с допуском `-`). ВСЁ ОСТАЛЬНОЕ — СТОП.

        Почему стоп, а не «пропустить эту строку»: нечисловая голова означает,
        что в базе лежит форма, которой по замеру там быть не может (12 из 12
        контактов телеграмные, голова числовая у всех). Домигрировать вокруг
        неё — значит УГАДАТЬ, чем она была; а угадывать в необратимой правке
        живых данных нельзя.
        """
        bad: list[str] = []
        for table, values in sorted(legacy_ids.items()):
            for value in sorted(values):
                if upgrade_legacy_callback_contact(value) is None:
                    bad.append("%s.contact_id=%r" % (table, value))
        for key, tail in sorted(legacy_keys.items()):
            if upgrade_legacy_callback_contact(tail) is None:
                bad.append("runtime_flags.key=%r" % (key,))
        if bad:
            raise ContactIdMigrationBlocked(
                "%s: перепись contact_id остановлена, база НЕ ТРОНУТА. Формы, "
                "которых правило §4.1 не знает (%d): %s. Правило узкое — ровно "
                "два сегмента и числовая голова; всё прочее пришлось бы "
                "угадывать, а угадывать в необратимой правке живых данных "
                "нельзя" % (path, len(bad), ", ".join(bad[:20])))

    def _origin_key_legacy_tables(self) -> tuple[str, ...]:
        """Таблицы, где `origin_msg_id` ещё не TEXT."""
        out = []
        for table in _ORIGIN_KEY_TABLES:
            if not self._has_table(table):
                continue
            if self._declared_type(table, "origin_msg_id") not in ("TEXT", ""):
                out.append(table)
        return tuple(out)

    def _declared_type(self, table: str, column: str) -> str:
        for r in self._conn.execute(f"PRAGMA table_info({table})"):
            if r["name"] == column:
                return (r["type"] or "").upper()
        return ""

    def _assert_origin_rebuild_window(self, path: Path, tables) -> None:
        """Окно перестройки закрыто НЕПРИВОДИМЫМ значением (образец
        `_assert_payments_rebuild_window`).

        Неприводимое — то, у которого текстового ключа либо нет вовсе (`BLOB`),
        либо он не равен исходному номеру (`REAL`: 7.0 -> '7.0'). Оба хранимы
        в INTEGER-аффинной колонке сегодня, и оба меняют ключ идемпотентности
        МОЛЧА. `int` в этот список не входит: его приведение обратимо.
        """
        for table in tables:
            rows = self._conn.execute(
                "SELECT typeof(origin_msg_id) AS t, COUNT(*) AS n FROM "
                + table + " WHERE origin_msg_id IS NOT NULL GROUP BY 1"
            ).fetchall()
            bad = {r["t"]: r["n"] for r in rows
                   if r["t"] not in ("integer", "text")}
            if bad:
                raise ContactIdMigrationBlocked(
                    f"{path}: в {table}.origin_msg_id лежат значения, которые "
                    f"перестройка не может перенести без потери смысла ({bad}). "
                    f"Перестройка остановлена, база не тронута: значение, "
                    f"меняющее ключ идемпотентности, обязано остановить её "
                    f"громко, а не проехать CAST-ом")

    def _row_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table in _CONTACT_ID_TABLES + ("runtime_flags",):
            if self._has_table(table):
                out[table] = self._conn.execute(
                    "SELECT COUNT(*) FROM " + table).fetchone()[0]
        return out

    def _apply_identity_migration(self, legacy_ids, legacy_keys, rebuild) -> None:
        """ОДНА транзакция на всё: колонки, ключи флагов и перестройка таблиц.

        Причина конкретная и она про людей: панель читает ту же базу
        ОДНОВРЕМЕННО (`file:...?mode=ro`) и обязана увидеть состояние ДО или
        ПОСЛЕ, но никогда МЕЖДУ — иначе владелец получает ленту, где половина
        контактов осиротела.

        Транзакцией управляем САМИ: драйвер по умолчанию открывает её только
        на DML, а DDL (`DROP`/`ALTER` перестройки) исполняет в автокоммите —
        то есть половина работы закоммитилась бы посреди неё.
        """
        self._conn.commit()
        self._conn.isolation_level = None
        self._conn.execute("BEGIN")
        try:
            before = self._row_counts()
            for table, values in sorted(legacy_ids.items()):
                for old in sorted(values):
                    self._conn.execute(
                        _SQL_SET_CONTACT_ID.format(table=table),
                        (upgrade_legacy_callback_contact(old), old))
            for key, tail in sorted(legacy_keys.items()):
                prefix = key[:len(key) - len(tail)]
                self._conn.execute(
                    _SQL_SET_FLAG_KEY,
                    (prefix + upgrade_legacy_callback_contact(tail), key))
            for table in rebuild:
                self._rebuild_origin_key(table)
            after = self._row_counts()
            lost = {t: (before[t], after[t]) for t in before
                    if before.get(t) != after.get(t)}
            if lost:
                raise ContactIdMigrationBlocked(
                    "перепись потеряла строки (таблица: было -> стало): %r. "
                    "Строка, потерянная при замене, — это переписка лида, "
                    "которой больше нет; замена, потерявшая строку, иначе "
                    "выглядит как успех" % (lost,))
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        finally:
            self._conn.isolation_level = ""

    def _rebuild_origin_key(self, table: str) -> None:
        """`ALTER COLUMN` в SQLite нет — таблица перестраивается целиком (§5.2).

        UNIQUE (contact_id, origin_msg_id) не «сохраняется», а ОБЪЯВЛЯЕТСЯ
        ЗАНОВО вместе с таблицей: это единственный способ не потерять его
        молча. Индексы уходят вместе с таблицей и создаются заново отдельным
        шагом — перестройка, забывшая их, ничего не ломает, она просто делает
        ленту панели медленнее с каждым месяцем.
        """
        ddl = _schema_create_table(table).replace(
            "CREATE TABLE IF NOT EXISTS " + table + " (",
            "CREATE TABLE " + table + "__new (")
        self._conn.execute(ddl)
        fresh = [r["name"] for r in
                 self._conn.execute(f"PRAGMA table_info({table}__new)")]
        old = {r["name"] for r in
               self._conn.execute(f"PRAGMA table_info({table})")}
        cols = [c for c in fresh if c in old]
        exprs = ["CAST(origin_msg_id AS TEXT)" if c == "origin_msg_id" else c
                 for c in cols]
        self._conn.execute(
            "INSERT INTO %s__new (%s) SELECT %s FROM %s"
            % (table, ", ".join(cols), ", ".join(exprs), table))
        self._conn.execute("DROP TABLE " + table)
        self._conn.execute(
            "ALTER TABLE %s__new RENAME TO %s" % (table, table))
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_%s_contact ON %s(contact_id)"
            % (table, table))

    def _assert_identity_migrated(self) -> None:
        """Проверка ПОСЛЕ. Миграция, о результате которой не спросили, — это
        надежда, а не миграция (`_assert_payments_rebuilt`, дословно)."""
        left = self._legacy_contact_ids()
        keys = self._legacy_flag_keys()
        if left or keys:
            raise ContactIdMigrationBlocked(
                "после переписи старая форма осталась: колонки=%r, ключи=%r"
                % (left, sorted(keys)))
        for table in _ORIGIN_KEY_TABLES:
            if not self._has_table(table):
                continue
            got = self._declared_type(table, "origin_msg_id")
            if got != "TEXT":
                raise ContactIdMigrationBlocked(
                    "%s.origin_msg_id объявлен %r, а не TEXT: перестройка не "
                    "состоялась" % (table, got))

    def _backup(self, path: Path, *, tag: str = "3a") -> None:
        dest = path.with_name(f"{path.name}.pre-{tag}-{int(time.time())}.bak")
        shutil.copy2(path, dest)

    # ── перестройка payments (арка payments, Ф0) ───────────────────────────

    def _payments_is_legacy(self) -> bool:
        """Старая схема — та, где нет `dedup_key`. Отсутствие таблицы вообще
        (новая база) старой схемой не считается."""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(payments)")}
        return bool(cols) and "dedup_key" not in cols

    def _assert_payments_rebuild_window(self, path: Path) -> None:
        """Окно перестройки открыто, только пока в таблице 0 строк.

        Появилась хоть одна — СТОП. Конвертировать чужие деньги из float в
        центы втихую нельзя: округление невидимо, а ошибка в разряде — нет.
        Падение здесь громкое и его увидит ops-watchdog; молча разъехавшаяся
        схема не видна никому, пока не начнёт врать отчётность."""
        n = self._conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        if n:
            raise PaymentsMigrationBlocked(
                f"{path}: в payments {n} строк — окно перестройки в минорные "
                f"INTEGER закрыто. Миграция остановлена, база не тронута. "
                f"Нужен явный план конвертации сумм и решение владельца")

    def _assert_payments_rebuilt(self) -> None:
        """Проверка ПОСЛЕ перестройки. Миграция, о результате которой не
        спросили, — это надежда, а не миграция.

        Идемпотентность держится на ФАКТЕ схемы (`_payments_is_legacy`), а не на
        ловле исключения: гардиан перезапускает раннер постоянно, и миграция,
        падающая на втором прогоне, — краш-петля, которую он будет вечно
        поддерживать."""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(payments)")}
        n = self._conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        if "dedup_key" not in cols or "amount" in cols or n:
            raise PaymentsMigrationBlocked(
                f"перестройка payments не удалась: колонки={sorted(cols)}, строк={n}")

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

    # ---- фундамент дашборда: история воронки + оплаты (CLIENT_SCREENS §5.2)

    def record_transition(self, contact_id: str, *, from_state: str, to_state: str,
                          signal: str | None, ts: float) -> None:
        """Записать ПЕРЕХОД воронки. Зовётся рядом с `set_state`, потому что
        только там известны оба конца и сигнал. Холостой ход (состояние не
        изменилось) писать нельзя — он раздует метрику «квалифицировано»."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO funnel_transitions (contact_id, from_state, to_state, signal, ts)"
                " VALUES (?,?,?,?,?)",
                (contact_id, from_state, to_state, signal, ts))
            self._conn.commit()

    def transitions_between(self, start_ts: float, end_ts: float,
                            to_state: str | None = None) -> list[dict]:
        """Переходы за период [start, end). `to_state` — фильтр «во что перешли»
        (напр. 'hot' для метрики «квалифицировано»)."""
        sql = ("SELECT * FROM funnel_transitions WHERE ts >= ? AND ts < ?")
        args: list = [start_ts, end_ts]
        if to_state is not None:
            sql += " AND to_state = ?"
            args.append(to_state)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY ts", args).fetchall()
        return [dict(r) for r in rows]

    # ── деньги: запись, счета, котировки (арка payments, Ф0) ──────────────

    def record_payment(self, rec: PaymentRecord) -> None:
        """Записать оплату (самоотчёт владельца или подтверждение провайдера).

        Идемпотентно по (contact_id, dedup_key): повторное событие с тем же
        ключом ПРАВИТ сумму, а не плодит вторую оплату. Это нужно потоку
        «сначала Оплачено, потом уточнил сумму» — и это же не даёт ретраю
        webhook'а удвоить выручку (риск 10.2).

        Личность события приходит ИЗ САМОГО СОБЫТИЯ (`tap:<msg_id>`,
        `panel:<token>`, `evt:<provider>:<id>`) — не из изменяемого состояния,
        которым владеет другая механика. Ровно на этом ломалась прежняя схема:
        ключ брали из runtime-флага, который сам же вызов и затирал (§7.1)."""
        validate_dedup_key(rec.dedup_key)
        amount = rec.amount.minor if rec.amount is not None else None
        received = rec.amount_received.minor if rec.amount_received is not None else None
        ccy = (rec.amount or rec.amount_received)
        ccy = ccy.ccy if ccy is not None else "USD"
        with self._lock:
            self._conn.execute(
                "INSERT INTO payments (contact_id, dedup_key, invoice_id, stage_no,"
                "   amount_minor, amount_received, currency, currency_received,"
                "   channel_id, confirmed_by, external_event_id, ts)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(contact_id, dedup_key) DO UPDATE SET"
                "   amount_minor=excluded.amount_minor,"
                "   amount_received=excluded.amount_received,"
                "   currency=excluded.currency,"
                "   currency_received=excluded.currency_received,"
                # Привязка к счёту НЕ снимается повтором: второй тап по той же
                # карточке приходит уже тогда, когда счёт закрыт и «открытого»
                # для привязки нет. Голое `excluded.invoice_id` обнуляло бы
                # ссылку, и `received_minor` по счёту падал бы в ноль при
                # статусе `paid` — деньги «исчезали» от лишнего тапа.
                # Перепривязка к ДРУГОМУ счёту остаётся возможной (не-NULL).
                "   invoice_id=COALESCE(excluded.invoice_id, payments.invoice_id),"
                "   stage_no=COALESCE(excluded.stage_no, payments.stage_no),"
                "   channel_id=excluded.channel_id,"
                "   confirmed_by=excluded.confirmed_by,"
                "   external_event_id=excluded.external_event_id, ts=excluded.ts",
                (rec.contact_id, rec.dedup_key, rec.invoice_id, rec.stage_no,
                 amount, received, ccy, ccy, rec.channel_id, rec.confirmed_by,
                 rec.external_event_id, rec.ts))
            self._conn.commit()

    def apply_payment(self, rec: PaymentRecord, *, now: float) -> str:
        """Записать оплату и ПЕРЕСЧИТАТЬ статус счёта.

        Единственная дверь, через которую деньги влияют на счёт. Статус — всегда
        проекция от сумм (§14 п.4): будь он тем, что пишет обработчик тапа, в Ф1
        частичная оплата закрыла бы счёт целиком."""
        self.record_payment(rec)
        if rec.invoice_id is None:
            return ""
        return self.recompute_status(rec.invoice_id, now=now)

    def received_minor(self, invoice_id: str) -> int:
        """Сколько ЗАЧИСЛЕНО по счёту. Остаток считается отсюда, а не от
        выставленного: иначе комиссия конверсии оставит вечную недоплату и счёт
        не закроется никогда (риск 10.4)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(amount_received), 0) AS s FROM payments"
                " WHERE invoice_id=?", (invoice_id,)).fetchone()
        return int(row["s"] or 0)

    def remaining_minor(self, invoice_id: str) -> int | None:
        """Остаток. None = сумма счёта ещё не зафиксирована (awaiting_owner)."""
        inv = self.get_invoice(invoice_id)
        if inv is None or inv["amount_total"] is None:
            return None
        return int(inv["amount_total"]) - self.received_minor(invoice_id)

    def set_display_name(self, contact_id: str, name: str) -> None:
        """Запомнить имя лида. Пустое значение НЕ затирает известное: Telethon
        иногда не знает сущность, и «не знаю сейчас» не должно стирать то, что
        мы узнали раньше."""
        clean = (name or "").strip()
        if not clean:
            return
        with self._lock:
            self._conn.execute(
                "UPDATE contacts SET display_name=? WHERE contact_id=?",
                (clean, contact_id))
            self._conn.commit()

    def cancel_invoice(self, invoice_id: str, *, reason: str, actor: str,
                       now: float) -> dict:
        """Снять счёт с названной причиной.

        Причина обязательна, а не опциональна: «отменён» без причины — дыра в
        разборе спора о деньгах через месяц, когда никто уже не помнит хода.

        Право проверяет карта переходов, а не вызыватель: `assert_transition`
        упадёт, если актору этот переход не положен (DEV-18 — молчаливый отказ
        неотличим от «сделано»)."""
        inv = self.get_invoice(invoice_id)
        if inv is None:
            raise KeyError(f"счёт {invoice_id!r} не найден")
        if not (reason or "").strip():
            raise ValueError("отмена счёта без причины запрещена")
        assert_transition(inv["status"], "cancelled", actor)
        with self._lock:
            self._conn.execute(
                "UPDATE invoices SET status='cancelled', cancelled_reason=?"
                " WHERE invoice_id=?", (reason.strip(), invoice_id))
            self._conn.execute(
                "UPDATE invoice_stages SET status='cancelled' WHERE invoice_id=?",
                (invoice_id,))
            self._conn.commit()
        return self.get_invoice(invoice_id)

    def recompute_status(self, invoice_id: str, *, now: float) -> str:
        inv = self.get_invoice(invoice_id)
        if inv is None:
            raise KeyError(f"счёт {invoice_id!r} не найден")
        received = self.received_minor(invoice_id)
        status = project_status(
            amount_total_minor=inv["amount_total"], received_minor=received,
            current=inv["status"], due_ts=inv["due_ts"], now=now)
        with self._lock:
            # first_payment_ts — МИНИМУМ по строкам с зачислением, а не «если
            # пусто, то поставить»: при upsert'ах минимум остаётся верным.
            first = self._conn.execute(
                "SELECT MIN(ts) AS t FROM payments WHERE invoice_id=?"
                "  AND COALESCE(amount_received,0) > 0", (invoice_id,)).fetchone()["t"]
            self._conn.execute(
                "UPDATE invoices SET status=?, first_payment_ts=? WHERE invoice_id=?",
                (status, first, invoice_id))
            if received:
                self._conn.execute(
                    "UPDATE invoice_stages SET status=? WHERE invoice_id=? AND stage_no=1",
                    (status, invoice_id))
            self._conn.commit()
        return status

    def _next_seq(self, table: str, id_col: str, prefix: str) -> str:
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE {id_col} LIKE ?",
            (f"{prefix}%",)).fetchone()
        return f"{prefix}{int(row['n']) + 1:06d}"

    @staticmethod
    def _slug(contact_id: str) -> str:
        """СЛУГ для PRIMARY KEY счёта — через владельца разбора.

        Свой `partition(":")` брал хвост ПОСЛЕ ПЕРВОГО двоеточия и на форме с
        каналом дал бы `Q-8849893367:volska-000001`. Молча и НАВСЕГДА: id
        счёта не переписывают, и увидят дефект в отчёте, а не в логе.
        """
        try:
            return slug_of(contact_id)
        except ContactRefError:
            return "unknown"

    def create_quote(self, *, contact_id: str, position_id: str, step_idx: int,
                     amount: Money, scope_key: str, amount_source: str,
                     knowledge_version: str | None, origin_msg_id: int | None,
                     now: float, tier_id: str | None = None) -> dict:
        """Новая котировка. Прежние по контакту становятся `superseded`:
        активной может быть только одна, иначе «что мы ему называли» перестаёт
        иметь ответ."""
        origin_msg_id = _origin_key(origin_msg_id)
        with self._lock:
            qid = self._next_seq("quotes", "quote_id", f"Q-{self._slug(contact_id)}-")
            self._conn.execute(
                "UPDATE quotes SET status='superseded'"
                " WHERE contact_id=? AND status='active'", (contact_id,))
            self._conn.execute(
                "INSERT INTO quotes (quote_id, contact_id, position_id, step_idx,"
                "   amount_minor, currency, scope_key, amount_source, tier_id,"
                "   knowledge_version, status, origin_msg_id, created_ts)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,'active',?,?)",
                (qid, contact_id, position_id, step_idx, amount.minor, amount.ccy,
                 scope_key, amount_source, tier_id, knowledge_version,
                 origin_msg_id, now))
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM quotes WHERE quote_id=?", (qid,)).fetchone()
        return dict(row)

    def quotes_for(self, contact_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM quotes WHERE contact_id=? ORDER BY created_ts",
                (contact_id,)).fetchall()
        return [dict(r) for r in rows]

    def create_invoice(self, *, contact_id: str, origin_msg_id: int | None,
                       amount: Money | None, channel_id: str | None, due_ts: float | None,
                       created_by: str, amount_source: str | None, status: str,
                       now: float, quote_id: str | None = None,
                       price_source: str | None = None, requisites_ref: str | None = None,
                       instruction_snapshot: str | None = None,
                       currency: str = "USD") -> dict:
        """Выставить счёт. Идемпотентно по (contact_id, origin_msg_id).

        `origin_msg_id` — сообщение лида, породившее счёт. Без него ретрай
        пайплайна выставит второй счёт на то же намерение, и фантомы съедят
        `per_contact_invoice_cap` (§14 п.15). None допустим (счёт завёл
        владелец руками), но тогда идемпотентности нет — и это осознанно.

        Ф0 пишет РОВНО ОДНУ ступень: Ф1 — это «строк больше одной»."""
        # Приведение ключа — в ОДНОМ месте, на границе `Store`, и ДО любой
        # записи: два приведения в двух местах это два числа на одну вещь.
        origin_msg_id = _origin_key(origin_msg_id)
        ccy = amount.ccy if amount is not None else currency
        total = amount.minor if amount is not None else None
        if total is None and status not in ("draft", "awaiting_owner"):
            raise ValueError(
                f"счёт без суммы допустим только в draft/awaiting_owner, не в {status!r}")
        with self._lock:
            if origin_msg_id is not None:
                row = self._conn.execute(
                    "SELECT * FROM invoices WHERE contact_id=? AND origin_msg_id=?",
                    (contact_id, origin_msg_id)).fetchone()
                if row is not None:
                    return dict(row)
            iid = self._next_seq("invoices", "invoice_id", f"INV-{self._slug(contact_id)}-")
            self._conn.execute(
                "INSERT INTO invoices (invoice_id, contact_id, quote_id, channel_id,"
                "   amount_total, currency, amount_source, status, created_ts,"
                "   created_by, origin_msg_id, issued_ts, due_ts, price_source,"
                "   requisites_ref, instruction_snapshot)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (iid, contact_id, quote_id, channel_id, total, ccy, amount_source,
                 status, now, created_by, origin_msg_id,
                 now if status == "issued" else None, due_ts, price_source,
                 requisites_ref, instruction_snapshot))
            self._conn.execute(
                "INSERT INTO invoice_stages (invoice_id, stage_no, amount_due, due_ts, status)"
                " VALUES (?,1,?,?,?)", (iid, total or 0, due_ts, status))
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM invoices WHERE invoice_id=?", (iid,)).fetchone()
        return dict(row)

    def get_invoice(self, invoice_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,)).fetchone()
        return dict(row) if row is not None else None

    def invoices_for(self, *, contact_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM invoices WHERE contact_id=? ORDER BY created_ts",
                (contact_id,)).fetchall()
        return [dict(r) for r in rows]

    def invoice_stages(self, invoice_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM invoice_stages WHERE invoice_id=? ORDER BY stage_no",
                (invoice_id,)).fetchall()
        return [dict(r) for r in rows]

    def payments_between(self, start_ts: float, end_ts: float) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM payments WHERE ts >= ? AND ts < ? ORDER BY ts",
                (start_ts, end_ts)).fetchall()
        return [dict(r) for r in rows]

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
                        detail: str | None = None, now: float,
                        escalate: bool = False) -> bool:
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
        — как и `mute()`, эта операция не создаёт строк сама.

        🔴 `escalate=True` — У ПАУЗЫ ПОЯВЛЯЕТСЯ СИЛА (спека 2026-08-28 §2.4,
        [[jarvis-snooze-eats-the-takeover]]). Источники паузы упорядочены
        ЛИТЕРАЛЬНО и их ровно два: `'command'`-со-сроком (снуз из карточки)
        слабее, `'human_takeover'` сильнее. Без повышения живой путь
        воспроизводит аварию целиком: владелец жмёт «⏸ Ще 1год», через десять
        минут отвечает из панели, `WHERE paused=0` даёт 0 строк, эпизод «не
        наш», источник остаётся `'command'`, срок доживает до истечения — и
        бот заговаривает поверх человека.

        Повышение — ТОТ ЖЕ ОДИН `UPDATE`, только с более широким условием:
        строка захватывается и когда она свободна, и когда её держит ЛЮБАЯ
        причина слабее перехвата. `pause_until=NULL` при этом не «сбрасывает
        таймер», а снимает чужой срок с диалога, который теперь ведёт человек;
        бессрочная команда владельца срока при этом НЕ ПРИОБРЕТАЕТ — у неё
        `pause_until` и так `NULL`, а источник становится сильнее, не слабее.

        Возвращаемое значение по-прежнему различает «эпизод НОВЫЙ» и «эпизод
        уже шёл»: условие не совпадает ровно тогда, когда перехват уже идёт.
        Терять эту разницу нельзя — от неё зависит событие `takeover` в
        журнале, и она защищает от трёх эпизодов на один залп сообщений."""
        where = ("contact_id=? AND paused=0" if not escalate else
                 "contact_id=? AND (paused=0 OR pause_source IS NULL "
                 "OR pause_source<>'human_takeover')")
        with self._lock:
            cur = self._conn.execute(
                "UPDATE contacts SET paused=1, pause_source='human_takeover', "
                "pause_msg_id=?, pause_detail=?, pause_until=NULL, paused_at=? "
                "WHERE " + where,
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

    def add_message(self, contact_id: str, role: str, text: str, ts: float,
                    author: str = "bot") -> None:
        """Записать сообщение в историю.

        `author` — КТО написал ('bot' | 'human'), и это НЕ роль. Роль уходит в
        API как есть, а автор живёт рядом: ручная реплика владельца хранится
        как role='assistant' + author='human'. Умолчание 'bot' держит все
        существующие вызывающие стороны без правок и не притворяется знанием
        про исторические строки — у тех author остаётся NULL (см.
        `_ADDED_COLUMNS`)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages(contact_id, role, text, ts, author) "
                "VALUES (?,?,?,?,?)",
                (contact_id, role, text, ts, author))
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
                "SELECT role, text, ts, author FROM messages "
                "WHERE contact_id=? ORDER BY id",
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

    def runtime_allow_add(self, peer_id: int, *, ts: float) -> None:
        """/allow (контрол-бот): runtime-оверлей allowlist, ОТДЕЛЬНЫЙ от
        settings.yaml.telegram.allowlist -- переживает reload_configs (та
        свопает self.allowlist из свежего YAML и ничего не знает про Store)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO runtime_allow(peer_id, ts) VALUES (?,?) "
                "ON CONFLICT(peer_id) DO UPDATE SET ts=excluded.ts",
                (peer_id, ts))
            self._conn.commit()

    def runtime_allow_remove(self, peer_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM runtime_allow WHERE peer_id=?", (peer_id,))
            self._conn.commit()

    def runtime_allow_ids(self) -> list[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT peer_id FROM runtime_allow ORDER BY peer_id").fetchall()
        return [r["peer_id"] for r in rows]

    def add_event(self, kind: str, *, contact_id: str | None = None,
                  detail: str | None = None, ts: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO control_events(kind, contact_id, detail, ts) VALUES (?,?,?,?)",
                (kind, contact_id, detail, ts))
            self._conn.commit()

    # --- исходящая очередь: панель кладёт, раннер отправляет ------------------
    #
    # Ни один метод ниже НЕ зовёт другой публичный метод Store: `self._lock` —
    # обычный `threading.Lock`, не реентерабельный, и такой вызов повесил бы
    # раннер намертво в первой же строке очереди.

    def enqueue_outgoing(self, contact_id: str, text: str, *, token: str,
                         now: float, author: str = "human") -> tuple[int, bool]:
        """Положить задание на отправку. Возвращает (row_id, created).

        Повтор по тому же `token` возвращает ТОТ ЖЕ id и `created=False` —
        второго сообщения лиду не рождается. Токен приходит от панели
        (`event_token`, личность нажатия, а не личность текста): два разных
        нажатия с одинаковым текстом — это два задания, и оба обязаны уйти.

        Пустой текст и пустой токен — `ValueError` ДО записи, а не строка,
        которая ляжет в очередь и будет вечно отказываться отправляться:
        пустой текст в `send()` — ошибка API, а задание без токена снимает
        единственную защиту от двойного нажатия.
        """
        clean = (text or "").strip()
        if not clean:
            raise ValueError("enqueue_outgoing: пустой текст — отправлять нечего")
        tok = (token or "").strip()
        if not tok:
            raise ValueError(
                "enqueue_outgoing: пустой token — без него двойное нажатие "
                "кнопки породило бы второе сообщение лиду")
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM outgoing_queue WHERE token=?", (tok,)).fetchone()
            if row is not None:
                return int(row["id"]), False
            try:
                cur = self._conn.execute(
                    "INSERT INTO outgoing_queue(contact_id, text, author, token, "
                    "status, created_ts) VALUES (?,?,?,?,'pending',?)",
                    (contact_id, clean, author, tok, now))
            except sqlite3.IntegrityError:
                # Гонка двух запросов панели с одним токеном: UNIQUE поймал
                # второй. Это НЕ ошибка вызывающего — это ровно тот исход, ради
                # которого UNIQUE и стоит, поэтому отвечаем как на повтор.
                # Молча не глотаем чужие IntegrityError: перечитываем строку и,
                # если её нет, отказ уезжает наружу как есть (DEV-18).
                self._conn.rollback()
                row = self._conn.execute(
                    "SELECT id FROM outgoing_queue WHERE token=?", (tok,)).fetchone()
                if row is None:
                    raise
                return int(row["id"]), False
            self._conn.commit()
            return int(cur.lastrowid), True

    def pending_outgoing(self, *, limit: int = 20,
                         channels=None) -> list[dict]:
        """Неотправленные задания, САМЫЕ СТАРЫЕ ПЕРВЫМИ.

        Порядок — не украшение: очередь к одному лиду обязана уйти в том
        порядке, в котором владелец её набирал, иначе он прочитает свой же
        диалог задом наперёд. `id` вторым ключом — на случай одинакового
        `created_ts` (две кнопки в одну миллисекунду).

        `limit` ограничивает ОДИН проход раннера, а не очередь: остаток
        заберёт следующий тик через 5 секунд. Считать по длине этого списка
        нельзя — для чисел есть `outgoing_counts` (иначе лимит занизил бы
        счётчик молча).

        🔴 `channels` — ЧЬЯ ЭТО ОЧЕРЕДЬ (спека 2026-08-28 §2.8). Сливает тот
        процесс, который ВЛАДЕЕТ каналом, и таблица делится по каналу задания.
        Альтернатива «кто первый взял» требует аренды строки и блокировки, то
        есть второго механизма на ту же вещь; фильтр по каналу — то же
        свойство даром, и он уже есть в самом `contact_id`. Без него два
        процесса, читающих `pending` без блокировки, отправят задание ДВАЖДЫ.

        `channels=None` — прежнее поведение «всё, что лежит»: им пользуются
        панель, лампа и стенды, которым делить нечего.

        ⚠️ ЛИМИТ ПРИМЕНЯЕТСЯ ПОСЛЕ ФИЛЬТРА, и это не мелочь: срезав сначала
        двадцать самых старых строк, процесс второго канала не увидел бы своё
        задание, пока не разгребут чужое, — то есть голодал бы тем сильнее,
        чем занятее сосед. Цена названа: при фильтре читается вся `pending`
        (очередь — это набранные руками сообщения, не поток).

        ⚠️ Задание, чей канал НЕ ОПРЕДЕЛЯЕТСЯ вовсе (мусор в `contact_id`),
        видно ЛЮБОМУ фильтру. Оно не принадлежит никакому каналу, и оставить
        его вне всех выборок значило бы завести вечно `pending` строку, о
        которой никто не отказывает словами; забрать его может кто угодно —
        отправить его всё равно нельзя, а отказ обязан прозвучать.
        """
        if channels is None:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT * FROM outgoing_queue WHERE status='pending' "
                    "ORDER BY created_ts, id LIMIT ?", (int(limit),)).fetchall()
            return [dict(r) for r in rows]

        from chatter.core.contact_ref import channel_of
        from chatter.core.contact_ref import ContactRefError

        wanted = frozenset(channels)
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM outgoing_queue WHERE status='pending' "
                "ORDER BY created_ts, id").fetchall()
        out: list[dict] = []
        for r in rows:
            row = dict(r)
            try:
                mine = channel_of(str(row.get("contact_id") or "")) in wanted
            except ContactRefError:
                mine = True
            if mine:
                out.append(row)
                if len(out) >= int(limit):
                    break
        return out

    def mark_outgoing_sent(self, row_id: int, *, msg_id: str | None,
                           now: float) -> None:
        """Задание ушло. `msg_id` строкой (может быть None: у канала без
        числовых id его нет вовсе) — но САМ ФАКТ отправки от этого не зависит,
        поэтому статус ставится в любом случае.

        `last_error` чистится: строка, которая ушла со второй попытки, обязана
        выглядеть отправленной, а не «отправленной с ошибкой» — иначе владелец
        пойдёт чинить то, что уже сработало."""
        with self._lock:
            self._conn.execute(
                "UPDATE outgoing_queue SET status='sent', sent_ts=?, sent_msg_id=?, "
                "last_error=NULL WHERE id=?", (now, msg_id, int(row_id)))
            self._conn.commit()

    def mark_outgoing_failed(self, row_id: int, *, error: str, now: float,
                             terminal: bool) -> None:
        """Попытка не удалась. `terminal=True` — отказ НАВСЕГДА
        (`status='refused'`, повторов не будет); `terminal=False` — строка
        остаётся `pending` и уйдёт на следующем тике.

        Разводить обязательно: «лид заблокировал аккаунт» повтором не лечится
        и повторялось бы каждые 5 секунд до конца времён, а «сеть моргнула»
        лечится ровно повтором. `attempts` растёт в ОБОИХ случаях: попытка
        была, и её видно.

        `error` кладётся ДОСЛОВНО, потому что этот же текст читает владелец.

        ⚠️ `now` в строку НЕ ПИШЕТСЯ, и это осознанно. Момента отказа в схеме
        нет, а положить его в `sent_ts` значило бы назвать неотправленное
        отправленным — колонка с двумя смыслами врёт молча. Заводить колонку
        сверх контракта тоже нельзя. Параметр остаётся в сигнатуре, потому что
        часы обязаны приходить снаружи (их подменяют сторожа), и первая же
        колонка «когда отказало» получит их без смены сигнатуры.
        """
        status = "refused" if terminal else "pending"
        with self._lock:
            self._conn.execute(
                "UPDATE outgoing_queue SET status=?, attempts=attempts+1, "
                "last_error=? WHERE id=?", (status, error, int(row_id)))
            self._conn.commit()

    def dismiss_outgoing(self, row_id: int, *, now: float) -> bool:
        """Владелец увидел отказ и закрыл его. `True` = строка была `'refused'`
        и стала `'dismissed'`. `False` = снимать нечего: такой строки нет либо
        она не в отказе.

        🔴 СНЯТЬ МОЖНО ТОЛЬКО ИЗ `'refused'`, и условие стоит В САМОМ UPDATE
        (`WHERE ... AND status='refused'`), а не проверкой до него: иначе между
        чтением и записью влезает раннер, и погасить можно было бы `pending` —
        то есть ТИХО ОТМЕНИТЬ неотправленное сообщение владельца. Это ровно тот
        отказ, от которого вся очередь: он не виден никому.

        Идемпотентно по построению: второе снятие той же строки меняет 0 строк
        и честно отвечает `False` («сняли не мы, снимать было нечего»), а не
        притворяется, что сделало работу.

        Строка НЕ удаляется и не переписывается: текст, причина и число попыток
        остаются уликой. Снятие гасит только СЧЁТЧИК, по которому горит лампа
        (`outgoing_counts`/`/ops/outgoing` считают `refused`, а `dismissed` в
        него уже не входит).

        ⚠️ `now` в строку НЕ ПИШЕТСЯ — по той же причине и с той же ценой, что
        в `mark_outgoing_failed`: колонки «когда сняли» в схеме нет, а положить
        момент в `sent_ts` значило бы назвать неотправленное отправленным.
        Часы приходят снаружи, потому что решение о времени не имеет права
        принимать хранилище.
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE outgoing_queue SET status='dismissed' "
                "WHERE id=? AND status='refused'", (int(row_id),))
            self._conn.commit()
            return cur.rowcount == 1

    def oldest_pending_outgoing_age(self, *, now: float) -> float | None:
        """Возраст САМОГО СТАРОГО неотправленного задания в секундах.
        `None` — очередь пуста (а не «ноль»: ноль означал бы, что задание
        только что положили, и лампа читалась бы как «всё свежо»).

        Возраст не зажимается снизу: отрицательное значение означает, что часы
        панели и раннера разошлись, и прятать это за `max(0, ...)` значило бы
        чинить симптом молча."""
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(created_ts) AS oldest FROM outgoing_queue "
                "WHERE status='pending'").fetchone()
        oldest = row["oldest"] if row is not None else None
        return None if oldest is None else float(now - float(oldest))

    def outgoing_counts(self) -> dict[str, int]:
        """Сколько заданий в каждом статусе. Ключи — ВСЕ статусы
        (`OUTGOING_STATUSES`, включая `dismissed`), даже с нулями: отсутствующий ключ вызывающий
        обязан был бы подставлять сам, и первое же место, где он этого не
        сделает, покажет «отказов нет» вместо «не знаю».

        Отдельный метод, а не `len(pending_outgoing())`: у той есть `limit`, и
        на 21-м задании счётчик молча занизил бы очередь — ровно ловушка
        `needs_attention(limit=50)`, которую панель уже один раз ловила.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM outgoing_queue "
                "GROUP BY status").fetchall()
        out = {name: 0 for name in OUTGOING_STATUSES}
        for r in rows:
            # Статус вне набора не выбрасывается: он означает, что кто-то завёл
            # ПЯТОЕ состояние мимо `OUTGOING_STATUSES`, и потерять его тихо —
            # худшее из решений (задание, которого не видно ни одной лампе).
            out[str(r["status"])] = int(r["n"])
        return out

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
                      cache_creation_5m: int = 0, cache_creation_1h: int = 0,
                      ts: float | None = None) -> None:
        """Одна строка на каждый LLM-вызов. ts — момент вызова (дефолт: сейчас);
        по нему же считаются интервалы диалога для решения о TTL кэша.

        cache_creation_5m/1h — разбивка записи кэша по ставкам ($3.75/M против
        $6/M). Дефолт 0 сохраняет совместимость с вызывающими, которые её не
        знают (старые тесты, FakeLLM-шов)."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO llm_usage(ts, tag, model, input_tokens, "
                "output_tokens, cache_read_input_tokens, "
                "cache_creation_input_tokens, cache_creation_5m, "
                "cache_creation_1h) VALUES (?,?,?,?,?,?,?,?,?)",
                (time.time() if ts is None else ts, tag, model, input_tokens,
                 output_tokens, cache_read_input_tokens,
                 cache_creation_input_tokens, cache_creation_5m,
                 cache_creation_1h))
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

    def last_llm_usage_ts(self, tags) -> dict[str, float | None]:
        """Момент ПОСЛЕДНЕГО вызова по каждому из `tags`; None — вызовов не было.

        Единственный источник истины о том, когда кэш-запись трогали последний
        раз (спека 2026-08-09 §3: «Дешёвый источник истины уже есть —
        `llm_usage.ts` по тегу»). Держать этот счёт в памяти процесса было бы
        вторым числом на одну вещь: рестарт раннера обнулил бы его, и первый же
        цикл после рестарта отправил бы пинг в живую запись — то есть заплатил
        бы за то, что и так работает.

        Схема НЕ меняется: это чтение существующей таблицы.
        """
        want = tuple(dict.fromkeys(tags))
        out: dict[str, float | None] = {t: None for t in want}
        if not want:
            return out
        qs = ",".join("?" * len(want))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT tag, MAX(ts) AS ts FROM llm_usage WHERE tag IN ({qs}) "
                f"GROUP BY tag", want).fetchall()
        for r in rows:
            out[r["tag"]] = float(r["ts"])
        return out

    def count_dialogs_since(self, since_ts: float) -> int:
        """Сколько РАЗНЫХ контактов писали нам с момента `since_ts`.

        Порог включения keep-alive меряется в «диалогах в месяц» (§10 п.2), и
        единицей взят контакт, а не сообщение: цена арки зависит от того,
        сколько РАЗ в месяц кэш-запись успевает остыть между разговорами, а не
        от длины разговоров. Считаем входящие: исходящие бывают и без лида
        (рассылка карточек, служебные ответы), и они бы завысили объём ровно у
        тихого клиента — того самого, которому режим не окупается.
        """
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(DISTINCT contact_id) FROM messages "
                "WHERE role='user' AND ts >= ?", (since_ts,)).fetchone()[0]

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
