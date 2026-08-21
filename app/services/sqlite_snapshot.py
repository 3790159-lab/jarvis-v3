# -*- coding: utf-8 -*-
"""Согласованный снимок живой SQLite-базы (DEV-46, §4.2 спеки).

Зачем отдельный модуль, а не `shutil.copy` внутри бэкапа. Раннер пишет в базу
клиента при каждом сообщении, `journal_mode` в `chatter/storage/db.py` не задан
— значит действует умолчание (rollback journal). Файловая копия может застать
базу в середине транзакции: рядом с оригиналом останется журнал отката, а в
копии — состояние, которое без этого журнала не открывается. И самое неприятное
здесь не сам рваный снимок, а то, что **sha256 рваного снимка совпадает с
рваным снимком**: сегодняшняя проверка целостности (`verify_restored_file`) на
таком снимке ЗЕЛЁНАЯ (§4.1).

Поэтому снимок берётся средствами самой SQLite — онлайновым backup API
(`sqlite3.Connection.backup`). Он даёт согласованное состояние, НЕ останавливая
писателя (§6 п. 4: простой живого бота ради копии запрещён). `VACUUM INTO`
такой же снимок даёт, но требует записи в источник — а этот модуль обязан быть
для боевой базы строго читателем.

Что модуль НЕ делает и не будет делать:

* не копирует файл (`shutil.copy`/`copy2`) — ни основным путём, ни запасным;
* не открывает источник на запись НИКОГДА, даже чтобы «починить» (см.
  `_connect_ro`: горячий журнал отката — громкий отказ, фейл-клоуз);
* не возвращает молчаливое «ну как-то скопировали»: любой сбой — `SnapshotError`.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["SnapshotError", "snapshot_sqlite", "snapshot_counts"]


class SnapshotError(Exception):
    """Снимок не состоялся. Молчаливого возврата «ну как-то скопировали» нет."""


def _ro_uri(path: Path) -> str:
    """URI источника СТРОГО на чтение: `file:<путь>?mode=ro`.

    `Path.as_uri()` берёт на себя раскладку Windows (`C:\\x` -> `file:///C:/x`)
    и процентное экранирование: `?`, `#` или пробел в имени каталога иначе
    разобрались бы парсером URI как разделители параметров.
    """
    return path.resolve().as_uri() + "?mode=ro"


def _hot_journal(src: Path) -> Path | None:
    """Незакрытый журнал отката рядом с источником, если он есть.

    Пустой файл журнала не в счёт: SQLite в режиме PERSIST обнуляет заголовок
    вместо удаления файла, и такой журнал горячим не является.
    """
    journal = src.with_name(src.name + "-journal")
    try:
        if journal.is_file() and journal.stat().st_size > 0:
            return journal
    except OSError as exc:
        # Не глотаем (DEV-18): осмотр журнала — уточнение диагноза, и его
        # провал обязан быть слышен, но подменять собой исходную ошибку он
        # не должен.
        logger.warning("не удалось осмотреть журнал %s: %s", journal, exc)
    return None


def _connect_ro(src: Path, timeout: float) -> sqlite3.Connection:
    """Открыть источник на чтение и УБЕДИТЬСЯ, что это база SQLite.

    `sqlite3.connect` ленив: и «файл не база», и горячий журнал вылезают не на
    открытии, а на первом обращении к схеме. Поэтому обращение делается здесь,
    сразу, чтобы дальше по коду не оставалось соединения-обманки.
    """
    try:
        conn = sqlite3.connect(_ro_uri(src), uri=True, timeout=timeout)
    except sqlite3.Error as exc:
        raise SnapshotError(f"источник {src} не открывается на чтение: {exc}") from exc
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error as exc:
        conn.close()
        journal = _hot_journal(src)
        if journal is not None:
            # ФЕЙЛ-КЛОУЗ, и это решение, а не недоделка. Чтобы откатить
            # незакрытый журнал, SQLite нужен доступ на ЗАПИСЬ в боевую базу.
            # Процесс бэкапа такого доступа не получает ни при каких условиях:
            # снимок, ради которого чинят оригинал, — это уже не снимок.
            raise SnapshotError(
                f"источник {src} не открывается на чтение: рядом лежит "
                f"незакрытый журнал отката {journal.name}. Базу оставил "
                "недописанной упавший писатель; откатить журнал можно только "
                "открыв базу на ЗАПИСЬ, а бэкап боевую базу не чинит "
                f"(фейл-клоуз). Снимок не берём. Исходная ошибка: {exc}"
            ) from exc
        raise SnapshotError(
            f"источник {src} не является читаемой базой SQLite: {exc}"
        ) from exc
    return conn


def _discard_partial(dst: Path) -> None:
    """Убрать файл, который мы сами и создали, когда снимок не состоялся."""
    try:
        dst.unlink(missing_ok=True)
    except OSError as exc:
        # Не глотаем (DEV-18): оставшийся огрызок — повод для шума, но не
        # повод подменить им настоящую причину провала.
        logger.warning("недоделанный снимок %s не удалось убрать: %s", dst, exc)


def snapshot_sqlite(src: str | Path, dst: str | Path, *, timeout: float = 30.0) -> Path:
    """Согласованный снимок живой SQLite-базы БЕЗ остановки писателя.

    Онлайновый backup API (`sqlite3.Connection.backup`), а не файловая копия
    и не `VACUUM INTO` (тот требует записи в источник).

    Источник открывается СТРОГО НА ЧТЕНИЕ — URI `file:<путь>?mode=ro`,
    `sqlite3.connect(..., uri=True)`. Процесс бэкапа не имеет права
    ни изменить боевую базу, ни создать рядом с ней журнал.

    dst не должен существовать (перезапись молча = потеря предыдущего
    снимка); родительский каталог создаётся. Возвращает Path(dst).

    SnapshotError с внятным текстом на: отсутствующий/нечитаемый источник;
    источник, который не является базой SQLite; существующий dst;
    любую ошибку sqlite. Исходное исключение цепляется через `raise ... from`.

    Горячий журнал отката — ГРОМКИЙ отказ, а не «починим» (`_connect_ro`):
    отката на открытие read-write нет даже как запасного пути.

    `timeout` уезжает в `sqlite3.connect` обоих соединений: снимок обязан
    ПОДОЖДАТЬ занятую базу, а не упасть в ту же миллисекунду.
    """
    src = Path(src)
    dst = Path(dst)

    if not src.exists():
        raise SnapshotError(f"источник {src} не существует — снимать нечего")
    if not src.is_file():
        raise SnapshotError(f"источник {src} не файл — базой SQLite быть не может")
    if dst.exists():
        # Перезапись молча стоила бы предыдущего снимка: единственная копия
        # заменялась бы на новую ДО того, как новая доказана.
        raise SnapshotError(
            f"цель {dst} уже существует — молча перезаписать снимок нельзя"
        )

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SnapshotError(f"каталог {dst.parent} не создаётся: {exc}") from exc

    source = _connect_ro(src, timeout)
    try:
        try:
            target = sqlite3.connect(str(dst), timeout=timeout)
        except sqlite3.Error as exc:
            raise SnapshotError(f"цель {dst} не открывается на запись: {exc}") from exc
        try:
            source.backup(target)
        except sqlite3.Error as exc:
            raise SnapshotError(f"снимок {src} -> {dst} не удался: {exc}") from exc
        finally:
            target.close()
    except SnapshotError:
        # Недоделанный файл снимка не имеет права остаться: он выглядит как
        # снимок и считает свой sha256 не хуже настоящего.
        _discard_partial(dst)
        raise
    finally:
        source.close()
    return dst


#: Таблицы, без которых величины манифеста не имеют смысла. Литеральный
#: список, а не интроспекция схемы: выведенный из `_SCHEMA` он согласился бы
#: с ней по определению и промолчал бы ровно там, где она забыла
#: ([[jarvis-literal-lists-not-introspection]]).
_COUNT_TABLES = ("contacts", "messages")


def snapshot_counts(db_path: str | Path) -> dict:
    """Величины, которые едут в манифест РЯДОМ с sha256 и которые дрил потом
    сверяет (§4.3 шаги 4–5):

        {"dialogs": int, "messages": int, "last_message_at": float | None}

    dialogs  = COUNT(*) FROM contacts
    messages = COUNT(*) FROM messages
    last_message_at = MAX(ts) FROM messages  (None на пустой базе)

    Читать ТОЛЬКО на чтение, обычным соединением `mode=ro`, а НЕ через
    `chatter.storage.db.Store`: `Store.__init__` исполняет DDL и миграции,
    то есть меняет базу, которую мы пришли измерить. (На дриле — наоборот,
    там открывать обязан именно `Store`; это другая половина работы.)

    Отсутствие таблицы = SnapshotError, а не ноль: «таблицы нет» и «строк
    нет» — разные вещи, и склеивать их нельзя.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise SnapshotError(f"база {db_path} не существует — считать нечего")
    if not db_path.is_file():
        raise SnapshotError(f"база {db_path} не файл — базой SQLite быть не может")

    conn = _connect_ro(db_path, 30.0)
    try:
        present = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = [t for t in _COUNT_TABLES if t not in present]
        if missing:
            raise SnapshotError(
                f"в базе {db_path} нет таблиц {', '.join(missing)} — «таблицы "
                "нет» и «строк нет» разные вещи, нулём не прикрываем"
            )
        try:
            dialogs = conn.execute("SELECT count(*) FROM contacts").fetchone()[0]
            messages = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
            last_ts = conn.execute("SELECT max(ts) FROM messages").fetchone()[0]
        except sqlite3.Error as exc:
            raise SnapshotError(f"величины из {db_path} не считались: {exc}") from exc
    finally:
        conn.close()

    return {
        "dialogs": int(dialogs),
        "messages": int(messages),
        "last_message_at": float(last_ts) if last_ts is not None else None,
    }
