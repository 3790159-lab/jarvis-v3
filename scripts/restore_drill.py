# -*- coding: utf-8 -*-
"""Дрил восстановления клиентского набора (DEV-46 §4.3) — запускается У ВЛАДЕЛЬЦА.

    python scripts/restore_drill.py --private-key E:\\keys\\jarvis-backup.key

ЗАЧЕМ ОН ЕСТЬ. `verify_uploaded` доказывает, что объект виден листингом,
`verify_restored_file` сравнивает sha256. Обе проверки честны, и обе отвечают
на ОДИН вопрос — «доехали ли байты». Ни одна не отвечает на вопрос «поднимется
ли из этих байтов база»: **sha256 рваного снимка SQLite совпадает с рваным
снимком** — проверка зелёная, база не открывается (§4.1). Доказательством
служит только повторяемый дрил, который открывает снимок НАСТОЯЩИМ слоем
хранения (`chatter.storage.db.Store`), а не `sqlite3` руками: иначе доказано,
что открывается файл, а не что работает продукт.

ГДЕ ОН ЖИВЁТ. Шаг «расшифровать» требует ПРИВАТНОГО ключа, а по варианту B
(§3.3) приватного ключа на хосте нет и не будет никогда — хост умеет писать
бэкап и не умеет его читать. Значит еженедельная задача заводится на машине
владельца (§9.2), а сторож на хосте читает УЛИКУ — файл вердикта
(`app/services/restore_drill_verdict.py`), а не результат прогона.

ЧЕТЫРЕ СВОЙСТВА, КОТОРЫЕ ЗДЕСЬ НЕ УКРАШЕНИЕ:

1. **Приватный ключ приходит ТОЛЬКО из `--private-key`.** Ни из окружения, ни
   из известного места в дереве, ни по умолчанию. Функции-загрузчика
   приватного ключа в `app/` нет намеренно (см. докстроку
   `app/services/backup_crypto.py`), и появиться она не должна: она сделала бы
   возможным ровно то, ради чего выбран вариант B.
2. **Открытый текст живёт ИСКЛЮЧИТЕЛЬНО в песочнице** — свой временный
   каталог, и ничего кроме него. Каталог назначения внутри `.secrets/` или
   внутри дерева репозитория отвергается, и проверка идёт по РАЗОБРАННОМУ пути
   (`resolve()`), а не по строке: `C:\\jarvis\\..\\jarvis\\.secrets` — это
   внутри дерева, и строковое сравнение этого не видит.

   **Песочница сносится на ЛЮБОМ пути выхода** — зелёном, красном и «не
   состоялся», включая непредвиденное исключение и ранний `return`.
   Единственное исключение — явный `--keep`. Формулировки «сносится в
   finally» для этого НЕ хватало: `finally` честно отрабатывал и на рваном
   снимке, а каталог оставался — снос упирался в открытый хэндл SQLite
   (см. `_release_sqlite_handle`). Гарантию даёт РЕЗУЛЬТАТ сноса, а не факт
   его вызова, поэтому `remove_sandbox` смотрит, что осталось на диске, и
   называет остаток вслух (DEV-18).
3. **sha256 ШИФРОТЕКСТА сверяется ДО расшифровки.** Так «байты не доехали»
   отделяется от «доехали, но не открываются» — это два разных дефекта с
   разной починкой, и склеивать их нельзя.
4. **ТРИ кода возврата, а не два.**

       0  сверка прошла            вердикт записан, ok: true
       1  сверка НЕ сошлась        вердикт записан, ok: false
       2  дрил НЕ СОСТОЯЛСЯ        ВЕРДИКТА НЕТ, причина громко в stderr

   Третий код — главное здесь. Записать `ok: false`, когда мы не смогли даже
   начать (нет объектов за дату, не читается манифест, файл ключа негоден,
   сеть недоступна), значило бы обвинить бэкап в негодности, КОТОРУЮ МЫ НЕ
   ПРОВЕРЯЛИ. «Не смогли проверить» и «нашли дефект» — это ровно DEV-43, и
   лечится оно тем же: разными выходами, а не одним. Отсутствие свежего
   вердикта проба увидит сама и назовёт `drill_never`/`drill_stale` (§9.4).

Исключения не глотаются (DEV-18): предвиденный отказ — громкий текст и код
возврата, непредвиденное падает трассировкой.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import gc
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Callable

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from cryptography.exceptions import UnsupportedAlgorithm  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import x25519  # noqa: E402

from app.services import r2_storage, state_backup  # noqa: E402
from app.services.backup_crypto import (  # noqa: E402
    BackupCryptoError,
    decrypt_with,
    public_key_fingerprint,
)
from app.services.restore_drill_verdict import VERDICT_REL, write_verdict  # noqa: E402
from app.services.sqlite_snapshot import snapshot_counts  # noqa: E402
from chatter.storage.db import Store  # noqa: E402

# Определение «дерева репозитория» обязано быть ОДНО. Второе, написанное
# рядом, разойдётся с первым молча — и разойдётся в сторону слабее, потому что
# слабое не краснеет. `scripts/backup_keygen.py` уже разбирает случай worktree
# (`.git` — файл со ссылкой на главное дерево), и запретная зона у нас та же.
from backup_keygen import repo_roots  # noqa: E402

RC_OK = 0
RC_MISMATCH = 1
RC_NOT_RUN = 2

#: Величины §4.3, которые едут в манифест рядом с sha256 и которые дрил
#: сверяет. Список ЛИТЕРАЛЬНЫЙ, а не выведенный из `snapshot_counts`:
#: выведенный согласен с ней по определению и промолчит ровно там, где она
#: забыла ([[jarvis-literal-lists-not-introspection]]).
COUNT_KEYS = ("dialogs", "messages", "last_message_at")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SECRETS_DIR = ".secrets"
_KEY_RAW_LEN = 32


class DrillNotRun(Exception):
    """Дрил НЕ СОСТОЯЛСЯ — проверить не удалось, о бэкапе не известно ничего.

    Отдельный класс, потому что отдельный выход: rc 2 и НИ ОДНОЙ строки
    вердикта. Это не «бэкап плохой», это «мы не смотрели», и путать их —
    ровно DEV-43."""


# --------------------------------------------------------------------------
# Приватный ключ: только из аргумента, и никак иначе
# --------------------------------------------------------------------------

def key_material_from_arg(raw_path: str) -> bytes:
    """32 сырых байта X25519 из файла, ПУТЬ К КОТОРОМУ НАЗВАЛ ЧЕЛОВЕК.

    Имя функции говорит про аргумент намеренно: это не «загрузчик приватного
    ключа», который можно позвать откуда угодно и получить ключ из
    окружения или из известного места. Другого источника у ключа нет —
    умолчания у `--private-key` не существует.

    Формат — тот, что пишет `scripts/backup_keygen.py`: строки-комментарии с
    `#` и base64 в одну строку. Любая беда с файлом — `DrillNotRun`: негодный
    ключ означает «проверить нечем», а не «бэкап негоден»."""
    if not raw_path or not raw_path.strip():
        raise DrillNotRun(
            "--private-key пуст: путь к приватному ключу называет человек, "
            "умолчания у этого аргумента нет намеренно (DEV-46 §3.3 B)")
    path = Path(raw_path).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise DrillNotRun(
            f"файла приватного ключа нет: {path}. Дрил гоняется только там, "
            "где лежит ключ (§9.2); на хосте его нет и быть не должно") from None
    except OSError as exc:
        raise DrillNotRun(
            f"файл приватного ключа не читается ({type(exc).__name__}): "
            f"{path}: {exc}") from exc
    except ValueError as exc:
        raise DrillNotRun(
            f"файл приватного ключа не в utf-8: {path}: {exc}") from exc

    body = "".join(
        line.strip() for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#"))
    if not body:
        raise DrillNotRun(
            f"в файле {path} нет ничего, кроме комментариев: ключ должен быть "
            "строкой base64 (32 сырых байта X25519), как её пишет "
            "scripts/backup_keygen.py")
    try:
        material = base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise DrillNotRun(
            f"тело файла {path} не является корректным base64: {exc}") from exc
    if len(material) != _KEY_RAW_LEN:
        raise DrillNotRun(
            f"файл {path} декодируется в {len(material)} Б, а приватный ключ "
            f"X25519 — ровно {_KEY_RAW_LEN} Б: это не тот файл")
    return material


def fingerprint_of(key_material: bytes) -> str:
    """Отпечаток ПУБЛИЧНОГО ключа, соответствующего этому приватному.

    Тот же `public_key_fingerprint`, что уезжает в манифест. Нужен, чтобы
    отличить «нам дали не тот ключ» (проверить нечем — rc 2) от «ключ тот, а
    конверт не открывается» (это уже дефект бэкапа — rc 1). Без этой сверки
    оба случая приехали бы одним красным."""
    try:
        material = x25519.X25519PrivateKey.from_private_bytes(key_material)
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise DrillNotRun(
            f"ключ не является скаляром X25519: {exc}") from exc
    public_raw = material.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    return public_key_fingerprint(public_raw)


# --------------------------------------------------------------------------
# Песочница: открытый текст не имеет права оказаться нигде больше
# --------------------------------------------------------------------------

def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def forbidden_sandbox_reason(path: Path) -> str | None:
    """Почему в этот каталог нельзя класть расшифрованные данные (или None).

    Проверка по РАЗОБРАННОМУ пути: `resolve()` схлопывает `..`, и
    `C:\\jarvis\\..\\jarvis\\state\\tmp` опознаётся как дерево репозитория,
    хотя строкой на него не похоже. Запретны:

    * любой каталог `.secrets` на пути — там живут боевые базы клиенток под
      живыми раннерами, и класть рядом расшифрованный снимок нельзя даже
      «на минуту»;
    * дерево репозитория (и главное дерево, если мы в worktree) — гейты,
      гардианы и автодеплой ходят по нему постоянно, а расшифрованная
      переписка не должна пережить прогон ни секунды.
    """
    resolved = Path(path).expanduser().resolve()
    for part in resolved.parts:
        if part.lower() == _SECRETS_DIR:
            return (f"каталог назначения {resolved} лежит внутри {_SECRETS_DIR}: "
                    "там боевые базы клиенток под живыми раннерами. "
                    "Расшифрованный снимок рядом с оригиналом — это лишняя "
                    "копия чужой переписки в самом опасном месте дерева")
    for root in repo_roots():
        if _is_inside(resolved, root):
            return (f"каталог назначения {resolved} лежит внутри дерева "
                    f"репозитория {root}: по дереву ходят гейты, гардианы и "
                    "автодеплой, а открытый текст переписки и реквизитов не "
                    "должен пережить прогон ни секунды. Укажите каталог вне "
                    "дерева (переменные TEMP/TMP)")
    return None


def make_sandbox(sandbox_root: str | Path | None = None) -> Path:
    """Свой временный каталог — и ничего кроме него.

    Корень проверяется ДО `mkdtemp`: иначе отказ уже создал бы каталог там,
    куда мы отказываемся писать. Созданный каталог проверяется ЕЩЁ РАЗ —
    симлинк в корне временных файлов может увести куда угодно, а `resolve()`
    его разворачивает."""
    root = Path(sandbox_root) if sandbox_root is not None else Path(tempfile.gettempdir())
    reason = forbidden_sandbox_reason(root)
    if reason is not None:
        raise DrillNotRun(reason)
    try:
        root.mkdir(parents=True, exist_ok=True)
        path = Path(tempfile.mkdtemp(prefix="jarvis-restore-drill-", dir=str(root)))
    except OSError as exc:
        raise DrillNotRun(
            f"песочница не создаётся в {root} ({type(exc).__name__}): {exc}") from exc

    reason = forbidden_sandbox_reason(path)
    if reason is not None:
        remove_sandbox(path)
        raise DrillNotRun(reason)
    return path


def _surviving_files(path: Path) -> list[Path]:
    """Файлы, ПЕРЕЖИВШИЕ снос. Ответ на «что осталось», а не «упало ли rmtree».

    Гарантию даёт состояние диска, а не то, что вызов не бросил исключение."""
    if not path.exists():
        return []
    try:
        return sorted(p for p in path.rglob("*") if p.is_file())
    except OSError:
        return [path]


def _rmtree_forcing(path: Path) -> None:
    """`rmtree`, который не бросает работу на первом же неподатливом файле.

    Голый `shutil.rmtree` бросает на первой ошибке и оставляет всё, до чего
    не дошёл: замер 22.08 — один запертый `plain/<псевдоним>/db` оставил на
    диске ещё и `manifest.json`, и `requisites.yaml`, которые снеслись бы
    свободно. Обработчик снимает read-only, пробует ещё раз и в любом случае
    даёт обходу идти дальше, поэтому открытого текста на диске остаётся
    МИНИМУМ возможного, а не всё подряд.

    Ошибки здесь не глотаются, а откладываются: остаток называет поимённо
    `remove_sandbox`, посмотрев на то, что реально осталось на диске."""
    def _on_error(func, target, exc) -> None:  # noqa: ANN001
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass  # остаток назовёт _surviving_files — тишины не будет

    try:
        shutil.rmtree(path, onexc=_on_error)
    except OSError:
        pass


def remove_sandbox(path: Path) -> list[Path]:
    """Снести песочницу ЦЕЛИКОМ. Возвращает то, что снести НЕ удалось.

    Провал сноса — ГРОМКИЙ и ПОИМЁННЫЙ, а не `ignore_errors=True`:
    оставшийся каталог — это расшифрованная переписка и реквизиты на диске,
    о которых никто не знает. Молчать про это нельзя (DEV-18). Но и
    подменять этим настоящую причину провала дрила тоже нельзя — поэтому
    текст в stderr и возвращённый список, а не исключение: код возврата
    обязан говорить про БЭКАП, а не про уборку.

    Второй заход после `gc.collect()` — не суеверие, а следствие корня
    (`_release_sqlite_handle`): на Windows файл держит открытый ХЭНДЛ, и
    хэндл, застрявший в недостижимом цикле ссылок, освобождает только
    сборщик мусора. Если второй заход помог — значит соединение всё-таки
    утекло, и это НАЗЫВАЕТСЯ вслух: снос такую утечку маскирует, а не лечит.
    """
    try:
        _rmtree_forcing(path)
        left = _surviving_files(path)
        if left:
            gc.collect()
            _rmtree_forcing(path)
            after = _surviving_files(path)
            if not after:
                print(
                    f"⚠ песочница {path} снеслась только СО ВТОРОГО захода, "
                    f"после сборки мусора: {len(left)} файл(ов) держал "
                    "открытый хэндл. Снос это замаскировал; чинить надо "
                    "утечку соединения, а не уборку.", file=sys.stderr)
                return []
            left = after
        if left:
            print(f"🚨 ПЕСОЧНИЦА НЕ СНЕСЕНА: {path}\n"
                  f"   осталось файлов: {len(left)}", file=sys.stderr)
            for item in left[:20]:
                print(f"   - {item}", file=sys.stderr)
            if len(left) > 20:
                print(f"   ... и ещё {len(left) - 20}", file=sys.stderr)
            print("   В них лежит РАСШИФРОВАННАЯ переписка и реквизиты. "
                  "Удалите каталог руками.", file=sys.stderr)
        return left
    except Exception as exc:  # noqa: BLE001
        # Уборка зовётся из `finally`. Исключение отсюда подменило бы собой
        # настоящую причину провала дрила — и человек чинил бы уборку вместо
        # бэкапа. Поэтому громко и без повторного возбуждения.
        print(f"🚨 СНОС ПЕСОЧНИЦЫ САМ УПАЛ ({type(exc).__name__}): "
              f"{path}: {exc}\n   Считайте, что открытый текст остался на "
              "диске: проверьте каталог руками.", file=sys.stderr)
        return [path]


# --------------------------------------------------------------------------
# Манифест
# --------------------------------------------------------------------------

def safe_rel_path(rel: Any) -> str:
    """`rel_path` из манифеста, годный как кусок пути и как кусок ключа.

    Манифест приезжает из бакета, то есть снаружи. `..` или абсолютный путь в
    нём — это запись мимо песочницы; отказываем, а не «нормализуем молча»."""
    if not isinstance(rel, str) or not rel.strip():
        raise DrillNotRun(f"в манифесте пустой или нестроковый rel_path: {rel!r}")
    if "\\" in rel or ":" in rel:
        raise DrillNotRun(
            f"rel_path {rel!r} содержит разделитель тома или обратный слэш — "
            "ключи объектов пишутся через '/'")
    parts = PurePosixPath(rel).parts
    if rel.startswith("/") or not parts or any(p in ("..", ".") for p in parts):
        raise DrillNotRun(
            f"rel_path {rel!r} выводит за пределы песочницы: абсолютный путь "
            "или '..' в манифесте — это запись мимо неё")
    return "/".join(parts)


def parse_manifest(raw: str) -> dict:
    """Разобрать манифест С ПРОВЕРКОЙ ФОРМЫ. Любая беда — `DrillNotRun`.

    Нечитаемый манифест — это «не смогли проверить», а не «бэкап негоден»:
    объекты при этом могут быть в полном порядке."""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise DrillNotRun(f"манифест не JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DrillNotRun(f"манифест не JSON-объект: {type(data).__name__}")

    fingerprint = data.get("key_fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        raise DrillNotRun(
            "в манифесте нет key_fingerprint: без отпечатка нельзя отличить "
            "«нам дали не тот ключ» от «объект не открывается», а склеивать "
            "их нельзя")

    files = data.get("files")
    if not isinstance(files, list):
        raise DrillNotRun(
            f"в манифесте нет списка files (найдено {type(files).__name__})")
    entries: list[dict] = []
    for item in files:
        if not isinstance(item, dict):
            raise DrillNotRun(
                f"запись манифеста не объект: {type(item).__name__}")
        rel = safe_rel_path(item.get("rel_path"))
        sha = item.get("sha256")
        if not isinstance(sha, str) or not sha.strip():
            raise DrillNotRun(f"у записи {rel} нет sha256")
        counts = item.get("counts")
        if counts is not None and not isinstance(counts, dict):
            raise DrillNotRun(
                f"у записи {rel} counts не объект: {type(counts).__name__}")
        entries.append({"rel_path": rel, "sha256": sha.strip().lower(),
                        "counts": counts})

    if not any(e["counts"] is not None for e in entries):
        raise DrillNotRun(
            "ни у одной записи манифеста нет counts: сверять величины не с "
            "чем. Дрил доказывает, что вернётся БАЗА (§4.3), а манифест без "
            "величин этого не позволяет — это «проверить нечем», а не "
            "«бэкап негоден»")

    return {"generated_at": data.get("generated_at"),
            "key_fingerprint": fingerprint.strip().lower(),
            "count": data.get("count"),
            "files": entries}


# --------------------------------------------------------------------------
# Чтение восстановленной базы
# --------------------------------------------------------------------------

def _release_sqlite_handle(store: Any) -> None:
    """Закрыть соединение SQLite, ДАЖЕ ЕСЛИ конструктор `Store` не доработал.

    КОРЕНЬ ДЕФЕКТА, ради которого эта функция существует, — и он не в сносе
    песочницы. `Store.__init__` (`chatter/storage/db.py:285`) открывает
    соединение ПЕРВОЙ строкой, а схему исполняет следующей. На рваном
    снимке — ровно на том дефекте, ради которого дрил и написан (§4.1), —
    падает `executescript`, и объект `Store` наружу не возвращается ВООБЩЕ:
    закрывать некому. Соединение остаётся в НЕДОСТИЖИМОМ ЦИКЛЕ ссылок
    (кадр `__init__` <- трассировка исключения -> кадр), то есть переживает
    подсчёт ссылок и ждёт сборщика мусора. На Windows открытый хэндл держит
    файл, и `shutil.rmtree` получает `PermissionError [WinError 32]` —
    песочница с РАСШИФРОВАННОЙ базой клиента переживает прогон.

    Замер 22.08, один рваный файл, четыре случая: `Store(файл)` без
    закрытия -> `rmtree` падает `WinError 32`; тот же случай плюс
    `gc.collect()` -> сносится; объект, созданный `__new__` до `__init__` и
    закрытый явно -> сносится; обнуление `__traceback__` НЕ помогает — цикл
    внутренний.

    Отсюда форма: объект берётся `Store.__new__` ДО `__init__`, поэтому он у
    нас на руках независимо от того, доработал конструктор или упал."""
    state = vars(store)
    conn = state.get("_conn")
    if conn is None:
        if state:
            # `_conn` — первое, что присваивает `Store.__init__`. Атрибуты
            # есть, а `_conn` нет — значит слой хранения переименовал
            # соединение, и мы МОЛЧА перестали его закрывать. Дефект
            # вернулся бы ровно в прежнем виде, поэтому говорим вслух.
            print("⚠ у объекта Store нет поля `_conn`, а другие поля есть: "
                  "слой хранения переименовал соединение. Закрыть его нечем — "
                  "песочница снова начнёт переживать прогон. Чинить здесь.",
                  file=sys.stderr)
        # Пустой `vars` — `sqlite3.connect` не отработал, хэндла нет:
        # закрывать нечего и предупреждать не о чем.
        return
    try:
        conn.close()
    except sqlite3.Error as exc:
        print(f"⚠ соединение SQLite не закрылось ({type(exc).__name__}): "
              f"{exc}. Файл снимка может остаться запертым.", file=sys.stderr)


def read_restored_db(path: Path) -> dict:
    """Открыть снимок НАСТОЯЩИМ слоем хранения и прочитать величины §4.3.

    Порядок именно такой, и он же — весь смысл дрила (§4.1):

    1. `chatter.storage.db.Store` — не `sqlite3` руками. `Store.__init__`
       исполняет схему и миграции и делает реальный запрос: доказано, что
       работает ПРОДУКТ, а не что файл открывается.
    2. `snapshot_counts` — тем же кодом, каким величины считались в источнике
       перед заливкой. Так расхождение в числах означает расхождение В
       ДАННЫХ, а не разницу двух способов посчитать. Публичного способа
       пересчитать всю базу через `Store` нет, а лезть в его приватное
       соединение — значит завести третий способ счёта.
    """
    # `Store.__new__` плюс явный `__init__` вместо `Store(path)` — НЕ стиль,
    # а единственный способ получить объект, когда конструктор падает: см.
    # `_release_sqlite_handle`. Рваный снимок — рабочий случай дрила, а не
    # редкость: ровно его дрил и пришёл ловить.
    store = Store.__new__(Store)
    try:
        store.__init__(path)  # noqa: PLC2801
        # Реальный запрос через продукт: DDL мог пройти и на пустышке.
        store.count_dialogs_since(0.0)
    finally:
        _release_sqlite_handle(store)
    return snapshot_counts(path)


def _compare_counts(expected: Any, actual: dict, rel: str) -> list[str]:
    """Расхождения по величинам, КАЖДОЕ с обоими числами (§7 п. 6).

    «restore failed» без чисел не говорит, что чинить."""
    problems: list[str] = []
    if not isinstance(expected, dict):
        return [f"{rel}: counts в манифесте не объект ({type(expected).__name__})"]
    for field in COUNT_KEYS:
        if field not in expected:
            problems.append(f"{rel}: в манифесте нет величины {field}")
            continue
        want, got = expected[field], actual.get(field)
        if want != got:
            problems.append(f"{rel}: {field} ждали {want!r}, нашли {got!r}")
    return problems


def _aggregate(values: list[dict]) -> dict:
    """Три величины §4.3 одним набором: суммы и максимум времени.

    Поля ровно те, что названы в `restore_drill_verdict`, — вердикт читает
    сторож на другой машине, и форма у него одна на любое число клиентов.
    Кто именно разошёлся, называет `detail`: аргумент тот же, что у §7 п. 6 —
    число без имени не говорит, что чинить."""
    out: dict = {"dialogs": 0, "messages": 0, "last_message_at": None}
    for item in values:
        for field in ("dialogs", "messages"):
            got = item.get(field)
            if isinstance(got, (int, float)) and not isinstance(got, bool):
                out[field] += int(got)
        last = item.get("last_message_at")
        if isinstance(last, (int, float)) and not isinstance(last, bool):
            out["last_message_at"] = (
                float(last) if out["last_message_at"] is None
                else max(out["last_message_at"], float(last)))
    return out


# --------------------------------------------------------------------------
# Сам дрил
# --------------------------------------------------------------------------

def resolve_date(date: str | None, *, list_objects, client, config) -> str:
    """Дата дрила: названная человеком или САМАЯ СВЕЖАЯ под `backups/client/`."""
    if date is not None:
        if not _DATE_RE.match(date.strip()):
            raise DrillNotRun(
                f"--date {date!r} не в формате YYYY-MM-DD: ключи объектов "
                "строятся по этой строке буквально")
        return date.strip()
    try:
        dates = state_backup.list_backup_dates(
            prefix=state_backup.CLIENT_PREFIX,
            list_objects=list_objects, client=client, config=config)
    except Exception as exc:  # noqa: BLE001 — честный «не состоялся», не тишина
        raise DrillNotRun(
            f"даты под {state_backup.CLIENT_PREFIX}/ не перечислились "
            f"({type(exc).__name__}): {exc}") from exc
    if not dates:
        raise DrillNotRun(
            f"под {state_backup.CLIENT_PREFIX}/ нет ни одного объекта: "
            "клиентский набор не заливался вообще, либо смотрим не в тот "
            "бакет. Проверять нечего — это НЕ «бэкап негоден»")
    return dates[-1]


def _fetch(download_file, key: str, dest: Path, *, client, config) -> Path:
    """Скачать объект. Провал — всегда «не состоялся» (rc 2), и вот почему.

    Отличить «объекта нет в бакете» от «сеть моргнула» на этом слое нечем:
    оба приезжают одним `R2Error`. Записать `ok: false` на моргнувшей сети —
    значит обвинить бэкап в негодности, которой мы не проверяли."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        return download_file(key, dest, client=client, config=config)
    except Exception as exc:  # noqa: BLE001
        raise DrillNotRun(
            f"объект {key} не скачался ({type(exc).__name__}): {exc}. "
            "«Объекта нет» и «сеть недоступна» здесь неразличимы, поэтому "
            "вердикт НЕ пишется") from exc


def run_drill(
    private_key_path: str,
    *,
    date: str | None = None,
    verdict_out: str | Path | None = None,
    keep: bool = False,
    sandbox_root: str | Path | None = None,
    list_objects: Callable[..., list[dict]] = r2_storage.list_objects,
    download_file: Callable[..., Path] = r2_storage.download_file,
    client: Any | None = None,
    config: Any | None = None,
    now: Callable[[], float] = time.time,
) -> int:
    """Весь ход дрила (§4.3). Возвращает код возврата: 0 / 1 / 2.

    `DrillNotRun` наружу не летит — он приезжает сюда, печатается и становится
    кодом 2 БЕЗ вердикта."""
    started = now()
    key_material = key_material_from_arg(private_key_path)
    own_fingerprint = fingerprint_of(key_material)

    if config is None:
        try:
            config = state_backup.load_backup_config()
        except Exception as exc:  # noqa: BLE001
            raise DrillNotRun(
                f"конфигурация бакета бэкапа не собралась "
                f"({type(exc).__name__}): {exc}") from exc

    date_str = resolve_date(date, list_objects=list_objects, client=client,
                            config=config)
    prefix = f"{state_backup.CLIENT_PREFIX}/{date_str}"

    sandbox = make_sandbox(sandbox_root)
    try:
        manifest_path = _fetch(download_file, f"{prefix}/manifest.json",
                               sandbox / "manifest.json",
                               client=client, config=config)
        try:
            raw = Path(manifest_path).read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            raise DrillNotRun(
                f"манифест {prefix}/manifest.json не читается "
                f"({type(exc).__name__}): {exc}") from exc
        manifest = parse_manifest(raw)

        if manifest["key_fingerprint"] != own_fingerprint:
            raise DrillNotRun(
                f"манифест за {date_str} зашифрован ключом с отпечатком "
                f"{manifest['key_fingerprint']}, а нам дали ключ с отпечатком "
                f"{own_fingerprint}. Это НЕ ТОТ ключ — проверять нечем. "
                "Вердикт не пишется: негодности бэкапа мы не видели")

        problems: list[str] = []
        expected_sets: list[dict] = []
        actual_sets: list[dict] = []
        checked = 0

        declared = manifest.get("count")
        if isinstance(declared, int) and not isinstance(declared, bool):
            if declared != len(manifest["files"]):
                problems.append(
                    f"манифест обещает count={declared}, а записей "
                    f"{len(manifest['files'])}: набор неполон")

        for entry in manifest["files"]:
            rel = entry["rel_path"]
            key = f"{prefix}/{rel}"
            blob_path = _fetch(download_file, key, sandbox / "cipher" / rel,
                               client=client, config=config)

            # sha256 ШИФРОТЕКСТА — ДО расшифровки. «Байты не доехали» и
            # «доехали, но не открываются» — разные дефекты с разной
            # починкой, и разделяет их ровно этот порядок.
            got_sha = state_backup.sha256_file(Path(blob_path))
            if got_sha != entry["sha256"]:
                problems.append(
                    f"{rel}: sha256 шифротекста ждали {entry['sha256']}, "
                    f"нашли {got_sha} — байты не доехали, расшифровку не "
                    "пробуем")
                continue

            try:
                plain = decrypt_with(key_material, Path(blob_path).read_bytes(),
                                     aad=key.encode("utf-8"))
            except BackupCryptoError as exc:
                problems.append(
                    f"{rel}: байты доехали (sha256 сошёлся), но конверт НЕ "
                    f"ОТКРЫЛСЯ тем самым ключом, отпечаток которого обещал "
                    f"манифест: {exc}")
                continue
            except OSError as exc:
                raise DrillNotRun(
                    f"скачанный объект {key} не читается с диска "
                    f"({type(exc).__name__}): {exc}") from exc

            plain_path = sandbox / "plain" / rel
            plain_path.parent.mkdir(parents=True, exist_ok=True)
            plain_path.write_bytes(plain)

            if entry["counts"] is None:
                continue

            checked += 1
            expected_sets.append(dict(entry["counts"]))
            try:
                actual = read_restored_db(plain_path)
            except Exception as exc:  # noqa: BLE001 — дефект, а не тишина
                problems.append(
                    f"{rel}: снимок НЕ ОТКРЫЛСЯ настоящим слоем хранения "
                    f"({type(exc).__name__}): {exc}. Байты доехали и конверт "
                    "открылся — не поднимается сама база, ровно тот дефект, "
                    "который sha256 не видит (§4.1)")
                continue
            actual_sets.append(actual)
            problems.extend(_compare_counts(entry["counts"], actual, rel))

        expected = _aggregate(expected_sets)
        actual = _aggregate(actual_sets)
        ok = not problems
        head = (f"дата {date_str}, объектов {len(manifest['files'])}, баз "
                f"сверено {checked}")
        detail = (f"{head}: снимок открыт настоящим слоем хранения "
                  f"(chatter.storage.db.Store), величины сошлись"
                  if ok else f"{head}. РАСХОЖДЕНИЕ: " + "; ".join(problems))

        target = Path(verdict_out) if verdict_out is not None else _ROOT / VERDICT_REL
        write_verdict(target, ran_at=started, ok=ok, expected=expected,
                      actual=actual, detail=detail)
        print(("✅ ДРИЛ ПРОШЁЛ: " if ok else "🔴 ДРИЛ НАШЁЛ РАСХОЖДЕНИЕ: ") + detail)
        print(f"вердикт: {target}")
        return RC_OK if ok else RC_MISMATCH
    finally:
        # Снос БЕЗУСЛОВЕН: зелёный, красный, «не состоялся», непредвиденное
        # исключение — любой путь выхода. Единственное исключение — явный
        # `--keep`, и он ГРОМКИЙ, потому что оставляет открытый текст.
        if keep:
            print(f"⚠️ песочница ОСТАВЛЕНА по --keep: {sandbox}\n"
                  "   в ней лежит РАСШИФРОВАННАЯ переписка и реквизиты — "
                  "удалите её, когда закончите смотреть.", file=sys.stderr)
        else:
            remove_sandbox(sandbox)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="restore_drill.py",
        description=(
            "Дрил восстановления клиентского набора (DEV-46 §4.3). Скачивает "
            "объекты за дату, сверяет sha256 ШИФРОТЕКСТА с манифестом, "
            "расшифровывает во временный каталог, открывает базу настоящим "
            "слоем хранения и сверяет величины. Запускается на машине "
            "владельца — там, где лежит приватный ключ (§9.2)."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Коды возврата:\n"
            "  0  величины сошлись        (вердикт записан, ok: true)\n"
            "  1  величины НЕ сошлись     (вердикт записан, ok: false)\n"
            "  2  дрил НЕ СОСТОЯЛСЯ       (вердикта НЕТ, причина в stderr)\n"))
    parser.add_argument(
        "--private-key", required=True, metavar="ПУТЬ",
        help=("файл приватного ключа (base64, как его пишет "
              "scripts/backup_keygen.py). Единственный источник ключа: ни "
              "окружения, ни умолчания у этого аргумента нет намеренно."))
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD",
        help="дата набора. По умолчанию — самая свежая под backups/client/.")
    parser.add_argument(
        "--verdict-out", metavar="ПУТЬ",
        help=f"куда положить вердикт. По умолчанию <корень дерева>/{VERDICT_REL}.")
    parser.add_argument(
        "--keep", action="store_true",
        help=("не сносить песочницу (разбор полёта). По умолчанию она "
              "сносится всегда, в том числе при провале."))
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            # Консоль под cp866/cp1251 иначе роняет вывод на первом же тире —
            # и причину, которую человек обязан прочитать, он не увидит.
            reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return run_drill(args.private_key, date=args.date,
                         verdict_out=args.verdict_out, keep=args.keep)
    except DrillNotRun as exc:
        print(f"🚫 ДРИЛ НЕ СОСТОЯЛСЯ: {exc}", file=sys.stderr)
        print("   Вердикт НЕ записан: «не смогли проверить» — это не «нашли "
              "дефект» (DEV-43). Отсутствие свежего вердикта проба увидит "
              "сама как drill_stale/drill_never.", file=sys.stderr)
        return RC_NOT_RUN


if __name__ == "__main__":
    raise SystemExit(main())
