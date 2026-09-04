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
   каталог, и ничего кроме него. Корень песочницы имеет РОВНО ОДИН шов
   наружу — `TMPDIR`/`TEMP`/`TMP`, читаемые в момент вызова
   (`default_sandbox_root`); ключа CLI на него нет намеренно. Само сито
   (запретная зона, шов, снос) живёт в `app/services/backup_sandbox.py`
   ОДНИМ экземпляром на всю арку: ежедневная заливка спрашивает его же. Каталог назначения внутри `.secrets/` или
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
3. **ВОЗРАСТ набора судится наравне с его содержимым.** Без этого дрил
   зеленеет вечно на последнем удачно залитом наборе, когда заливка встала:
   «самый свежий» из мёртвого бакета — это набор месячной давности, и он
   честно расшифровывается и честно открывается. Порог —
   `CLIENT_SET_MAX_AGE_DAYS`, граница строгая; явный `--date` суд отменяет.

4. **sha256 ШИФРОТЕКСТА сверяется ДО расшифровки.** Так «байты не доехали»
   отделяется от «доехали, но не открываются» — это два разных дефекта с
   разной починкой, и склеивать их нельзя.
5. **ТРИ кода возврата, а не два.**

       0  сверка прошла            вердикт записан, ok: true
       1  сверка НЕ сошлась        вердикт записан, ok: false
       2  дрил НЕ СОСТОЯЛСЯ        ВЕРДИКТА НЕТ, причина громко в stderr

   Умолчательный путь вердикта считается от атрибута модуля `_ROOT` в
   МОМЕНТ ВЫЗОВА (`default_verdict_path`): иначе прогон суиты в живом дереве
   писал бы свежий `ok: true` в улику, которую проба читает каждые 30 с, —
   то есть тесты доказывали бы восстановление, которого не было.

   Третий код — главное здесь. Записать `ok: false`, когда мы не смогли даже
   начать (нет объектов за дату, не читается манифест, файл ключа негоден,
   сеть недоступна), значило бы обвинить бэкап в негодности, КОТОРУЮ МЫ НЕ
   ПРОВЕРЯЛИ. «Не смогли проверить» и «нашли дефект» — это ровно DEV-43, и
   лечится оно тем же: разными выходами, а не одним. Отсутствие свежего
   вердикта проба увидит сама и назовёт `drill_never`/`drill_stale` (§9.4).

ЧАСЫ У ДРИЛА ОДНИ (`_now`), и шов к ним модульный, а не второй ключ CLI:
`--today` был бы боевой поверхностью ради теста и первым же способом подделать
улику. `ran_at` вердикта и «сегодня» в суде о возрасте выводятся из ОДНОГО
вызова часов — два источника на одну вещь расходятся молча.

Исключения не глотаются (DEV-18): предвиденный отказ — громкий текст и код
возврата, непредвиденное падает трассировкой.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import sqlite3
import sys
import time
from datetime import date as _Date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

#: Корень дерева, от которого считается УМОЛЧАТЕЛЬНЫЙ ПУТЬ ВЕРДИКТА.
#: Читается `default_verdict_path()` В МОМЕНТ ВЫЗОВА — см. там, почему это
#: не косметика. Подмена этого атрибута двигает ТОЛЬКО вердикт: запретная
#: зона песочницы считается от настоящего расположения кода
#: (`app/services/backup_sandbox.py`), и подменить её отсюда нельзя.
_ROOT = Path(__file__).resolve().parents[1]

# `sys.path` — второй, СОВСЕМ ДРУГОЙ смысл того же значения: где лежит код,
# который надо суметь импортировать. Он расходуется ЗДЕСЬ, на импорте, и
# позднейшая подмена `_ROOT` на него уже не влияет. Разведены намеренно:
# одна переменная на два смысла — это способ подменить второй, целясь в
# первый ([[jarvis-two-numbers-for-one-thing]]).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 🔴 СЕКРЕТЫ ДОЛЖЕН ПРИНЕСТИ ДРИЛ САМ, И ИМЕННО ЗДЕСЬ — ДО первого чтения
# окружения. Замер 04.09.2026: пять переменных бакета (`R2_ACCOUNT_ID`,
# `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT`,
# `R2_BACKUP_BUCKET`) живут ТОЛЬКО в `.env` — ни в User, ни в Machine их нет, —
# а этот файл `env_bootstrap` не импортировал. Писатель бэкапа
# (`scripts/state_backup.py`) импортирует, поэтому ЗАЛИВКА шла, а ПРОВЕРКА
# молчала: задача падала с rc=2 «конфигурация бакета не собралась» с 30.08, и
# единственным следом был `LastTaskResult=2` в планировщике.
#
# ⚠️ На `sitecustomize.py` в корне полагаться НЕЛЬЗЯ, хотя он тоже зовёт
# `load_dotenv`: интерпретатор находит его, только если корень попал в
# `sys.path` на старте (запуск ИЗ корня), а задача запускает скрипт ПО ПУТИ —
# и тогда `sys.path[0]` это `scripts/`, а не корень. Один и тот же скрипт
# работал бы из консоли и падал из планировщика — худший вид разницы.
import app.env_bootstrap  # noqa: F401,E402  side-effect: грузит .env

from cryptography.exceptions import UnsupportedAlgorithm  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import x25519  # noqa: E402

from app.services import backup_sandbox, r2_storage, state_backup  # noqa: E402
from app.services.backup_crypto import (  # noqa: E402
    BackupCryptoError,
    decrypt_with,
    public_key_fingerprint,
)
from app.services.restore_drill_verdict import VERDICT_REL, write_verdict  # noqa: E402
from app.services.sqlite_snapshot import snapshot_counts  # noqa: E402
from chatter.storage.db import Store  # noqa: E402

# Определение «дерева репозитория» и всё сито временных каталогов — ОДНО на
# всю арку, в `app/services/backup_sandbox.py`. Второе, написанное рядом,
# разошлось бы с первым молча и в сторону СЛАБЕЕ, потому что слабое не
# краснеет: ровно так ежедневная заливка писала открытую базу в дерево, пока
# дрил туда отказывался распаковывать (амендмент Д).
from app.services.backup_sandbox import (  # noqa: E402,F401
    TMP_ENV_VARS,
    SandboxRefused,
    default_sandbox_root,
    forbidden_sandbox_reason,
    remove_sandbox,
    repo_roots,
)

RC_OK = 0
RC_MISMATCH = 1
RC_NOT_RUN = 2

#: ЧАСЫ ДРИЛА. Один источник времени на весь прогон, и шов к нему МОДУЛЬНЫЙ.
#:
#: Почему шов, а не второй ключ CLI: «сегодня» и «время прогона» — это не то,
#: что человек назначает боевому дрилу. Ключ `--today` был бы боевой
#: поверхностью, существующей исключительно ради теста, и первым же способом
#: подделать улику: назначил вчера — набор снова свежий, вердикт снова
#: зелёный. Шов на уровне модуля доступен сторожу, который гоняет `main(argv)`,
#: и НЕДОСТУПЕН тому, кто запускает `restore_drill.py` из планировщика.
#:
#: Почему ОДНИ часы, а не отдельный источник «сегодня» рядом: `ran_at` в
#: вердикте и дата, по которой судится возраст набора, обязаны происходить из
#: ОДНОГО вызова. Два источника на одну вещь расходятся молча — вердикт сказал
#: бы «прогон в 23:59 вторника», а возраст считался бы от среды
#: ([[jarvis-two-numbers-for-one-thing]]). Поэтому `run_drill` зовёт часы РОВНО
#: ОДИН раз (`started`), а «сегодня» ВЫВОДИТСЯ из этого же числа.
#:
#: Явный `now=` в аргументах `run_drill` продолжает выигрывать у модульного:
#: он адресный и не переживает вызова, тогда как подмена `_now` действует на
#: весь процесс.
_now: Callable[[], float] = time.time

#: Величины §4.3, которые едут в манифест рядом с sha256 и которые дрил
#: сверяет. Список ЛИТЕРАЛЬНЫЙ, а не выведенный из `snapshot_counts`:
#: выведенный согласен с ней по определению и промолчит ровно там, где она
#: забыла ([[jarvis-literal-lists-not-introspection]]).
COUNT_KEYS = ("dialogs", "messages", "last_message_at")

#: Насколько СТАРЫМ имеет право быть набор, который дрил признал годным.
#: Ритм заливки СУТОЧНЫЙ: один-два пропуска подряд — это сбой сети, третий
#: подряд — это система. Граница СТРОГАЯ (`>`), как у `BUNDLE_MAX_LAG_DAYS` и
#: `DRILL_MAX_LAG_DAYS` в `scripts/ops_watchdog.py`: сторож, загорающийся
#: ровно на границе ритма, приучает к тому, что он слегка врёт.
#:
#: Литерал, а не выражение от ритма заливки. Выведенный порог согласен с
#: ритмом ПО ОПРЕДЕЛЕНИЮ и промолчит ровно там, где ритм задан неверно
#: ([[jarvis-literal-lists-not-introspection]]).
#:
#: ЗАЧЕМ ЭТОТ ПОРОГ ВООБЩЕ ЕСТЬ. Без него дрил берёт САМЫЙ СВЕЖИЙ набор под
#: `backups/client/` и ни с чем дату не сверяет. Заливка встала — отравился
#: `TEMP`, протухли ключи R2, выключили задачу, пропал публичный ключ, — и
#: узнать об этом неоткуда: дрил каждое воскресенье берёт последний УДАЧНО
#: залитый набор, тот честно расшифровывается и честно открывается настоящим
#: слоем хранения, вердикт зелёный, проба на хосте зелёная — ВЕЧНО, хотя
#: бэкапу месяц и в нём нет ни одного диалога за тридцать суток. Лампа горит
#: зелёным не потому, что всё хорошо, а потому, что смотрит не на то. Спека
#: §4.3 п. 1 говорит буквально «скачать ВЧЕРАШНИЙ объект»; «самый свежий» —
#: молчаливое ослабление этого требования.
CLIENT_SET_MAX_AGE_DAYS = 3.0

#: Допуск на набор, датированный БУДУЩИМ. Ровно одни сутки, и это не запас
#: «на всякий случай»: дату в ключ пишет ХОСТ по UTC, а судит её НОУТБУК
#: (§9.2), и около полуночи UTC они законно расходятся на календарный день.
#: Сверх допуска возраст недоказуем, и зажимать его в ноль нельзя: ушедшие
#: вперёд часы хоста держали бы набор «вечно свежим» — то же зелёное ПО
#: ПОСТРОЕНИЮ, только с другого конца. Тот же довод, что у допуска на отметку
#: прогона в будущем в `ops_watchdog.py`.
CLIENT_SET_FUTURE_TOLERANCE_DAYS = 1.0

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

def make_sandbox(sandbox_root: str | Path | None = None) -> Path:
    """Песочница дрила. Решение принимает общее сито, СЛОВАРЬ здесь свой.

    Обёртка, а не копия: `backup_sandbox.make_sandbox` решает, куда можно
    писать открытый текст, одинаково для дрила и для ежедневной заливки.
    Перевод отказа в `DrillNotRun` нужен потому, что рассказывают о нём эти
    двое по-разному: у дрила отказ — это rc 2 и НИ ОДНОЙ строки вердикта
    («мы не смотрели», не «бэкап негоден»), у заливки — «наружу не ушло ни
    байта»."""
    try:
        return backup_sandbox.make_sandbox(
            sandbox_root, prefix="jarvis-restore-drill-")
    except SandboxRefused as exc:
        raise DrillNotRun(str(exc)) from exc


# --------------------------------------------------------------------------
# Манифест
# --------------------------------------------------------------------------

def safe_rel_path(rel: Any) -> str:
    """`rel_path` из манифеста, годный как кусок пути и как кусок ключа.

    Манифест приезжает из бакета, то есть СНАРУЖИ. `..` или абсолютный путь в
    нём — это запись мимо песочницы; отказываем, а не «нормализуем молча».

    Отказ — `DrillNotRun`, то есть rc 2 и НИ ОДНОЙ строки вердикта, и это
    решение, а не мелочь. rc 1 обязывает вердикт написать, а вердикт несёт
    `expected`/`actual` — числа, которых мы НЕ МЕРИЛИ: распаковка не
    состоялась. Вердикт с неизмеренными числами — ровно DEV-43, «красное на
    невыполненном замере». «Не смогли проверить» не равно «нашли дефект»,
    даже когда причина отказа сама по себе тревожна.

    Судится СЫРАЯ строка, посегментно, а не разобранный путь. `PurePosixPath`
    молча выбрасывает `.` и схлопывает `//`: замер 22.08 — `./db` проходил
    сито и превращался в `db`, то есть ровно в ту тихую нормализацию, которую
    докстрока обещала не делать. Обещание и поведение разошлись бы молча,
    а разошлись бы в сторону слабее."""
    if not isinstance(rel, str) or not rel.strip():
        raise DrillNotRun(f"в манифесте пустой или нестроковый rel_path: {rel!r}")
    if "\\" in rel or ":" in rel:
        raise DrillNotRun(
            f"rel_path {rel!r} содержит разделитель тома или обратный слэш — "
            "ключи объектов пишутся через '/'")
    segments = rel.split("/")
    if any(seg in ("", "..", ".") for seg in segments):
        raise DrillNotRun(
            f"rel_path {rel!r} выводит за пределы песочницы или нуждается в "
            "нормализации: абсолютный путь, '..', '.' и пустой сегмент в "
            "манифесте запрещены все четыре. Нормализовать молча нельзя — "
            "исполнять путь, который мы сами переписали, значит доверять "
            "манифесту больше, чем он заслуживает")
    return "/".join(segments)


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

    # 🔴 ПОЛНОТА НАБОРА СЧИТАЕТСЯ ДО ОТКАЗА ПО ВЕЛИЧИНАМ, и порядок здесь —
    # содержание, а не стиль. «Набор неполон» это ЗНАНИЕ о дефекте: мы точно
    # видим, что часть объявленных объектов не описана. «Нет counts» — это
    # незнание. Ответить незнанием там, где есть знание, значит промолчать о
    # найденном дефекте: лампа на хосте увидела бы drill_never/drill_stale и
    # прочитала аварию как «ноутбук был выключен».
    expected = data.get("expected")
    complete = data.get("complete")
    incomplete: list[str] = []
    if isinstance(expected, list):
        have = {e["rel_path"] for e in entries}
        # Сверяем СПИСКИ, а не только признак: `complete` может соврать,
        # поимённое расхождение — нет.
        gone = sorted(str(x) for x in expected if str(x) not in have)
        if gone:
            incomplete.append(
                "набор неполон: манифест ожидает %d объектов, а описаны не "
                "все — нет %s" % (len(expected), ", ".join(gone)))
    if complete is False:
        incomplete.append(
            "манифест объявил себя неполным (complete: false): часть объектов "
            "набора не описана, набор непроверяем целиком")

    if not incomplete and not any(e["counts"] is not None for e in entries):
        # Старый манифест без `expected`/`complete`: судить о полноте нечем,
        # и «проверить нечем» остаётся честным ответом.
        raise DrillNotRun(
            "ни у одной записи манифеста нет counts: сверять величины не с "
            "чем. Дрил доказывает, что вернётся БАЗА (§4.3), а манифест без "
            "величин этого не позволяет — это «проверить нечем», а не "
            "«бэкап негоден»")

    return {"generated_at": data.get("generated_at"),
            "key_fingerprint": fingerprint.strip().lower(),
            "count": data.get("count"),
            "expected": expected,
            "complete": complete,
            "incomplete": incomplete,
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

def default_verdict_path() -> Path:
    """Куда ляжет вердикт, если `--verdict-out` не назвали.

    Корень читается из атрибута модуля `_ROOT` В МОМЕНТ ВЫЗОВА, а не
    запекается в константу на импорте. Довод не про удобство теста, а про
    ПОДДЕЛКУ УЛИКИ.

    `VERDICT_REL` — то же самое `state/backup/restore_drill.json`, которое
    проба на хосте читает каждые 30 секунд как доказательство того, что
    восстановление проверено (§9.4). После мержа сторожа лягут в `tests/`
    живого дерева, и любой полный прогон суиты записал бы туда вердикт со
    свежим `ran_at` и `ok: true` — то есть прогон тестов доказывал бы
    восстановление, которого не было. Это не «узкое окно», это зелёное,
    взявшееся из ниоткуда, и лечится оно швом, а не осторожностью.

    Ключа CLI на корень нет намеренно: лишняя боевая поверхность ради теста —
    плохой размен. Боевое поведение не меняется ни на йоту.

    ГРАНИЦА, названная вслух: подменённый `_ROOT` двигает ТОЛЬКО этот путь.
    Запретная зона песочницы считается `app/services/backup_sandbox.py` от
    НАСТОЯЩЕГО расположения кода, и обойти отказ подменой атрибута нельзя —
    иначе шов для сторожа был бы дырой в защите."""
    return Path(_ROOT) / VERDICT_REL


def set_age_days(date_str: str, today: _Date) -> int:
    """Возраст набора В СУТКАХ: от даты в ключах объектов до сегодняшней даты.

    Дата набора — та, что заливка положила в ключ
    (`backups/client/<дата>/…`), а считала она её по UTC. Сегодняшняя берётся
    так же по UTC, и это не педантизм: дрил идёт на ДРУГОЙ машине (§9.2), и
    вычитать «дату в бакете минус локальную полночь ноутбука» значит получать
    лишние сутки возраста по вечерам и недостачу по утрам.

    Неразбираемая дата — `DrillNotRun`: возраст судить нечем, а угадывать
    его нельзя. Это «не смогли проверить», а не «нашли дефект»."""
    try:
        made = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as exc:
        raise DrillNotRun(
            f"дата набора {date_str!r} не разбирается как YYYY-MM-DD, "
            f"возраст судить нечем: {exc}") from exc
    return (today - made).days


def age_problems(date_str: str, today: _Date) -> list[str]:
    """Расхождения по ВОЗРАСТУ набора — каждое с ОБОИМИ числами (§7 п. 6).

    «Набор устарел» без чисел не говорит, что чинить: непонятно, отстала
    заливка на сутки или на месяц, и надо ли будить владельца сейчас.

    Возвращает СПИСОК и вливается в общий `problems`, а не отдельным выходом.
    Отсюда два свойства, оба нужные:

    * устаревший набор — это **rc 1**, найденный дефект, а не отказ. Проверка
      СОСТОЯЛАСЬ, величины измерены, вердикт писать есть чем — и он обязан
      быть написан с `ok: false`. Это НЕ случай DEV-43: там мы не мерили,
      здесь померили и нашли. Отказ (rc 2) оставил бы пробу на хосте без
      улики, то есть превратил бы «бэкап негоден» в «дрил не гонялся» —
      состояния с противоположными действиями (§9.4);
    * набор, который устарел И НЕ ОТКРЫВАЕТСЯ, назовёт ОБА дефекта, а не
      только первый: список не прерывает сверку величин."""
    age = set_age_days(date_str, today)
    if age > CLIENT_SET_MAX_AGE_DAYS:
        return [f"набор УСТАРЕЛ: дата набора {date_str}, сегодня "
                f"{today.isoformat()}, возраст {age} сут при пороге "
                f"{CLIENT_SET_MAX_AGE_DAYS:g} сут (граница строгая). Ритм "
                f"заливки СУТОЧНЫЙ, значит она встала — а дрил всё это время "
                f"зеленел на последнем удачно залитом наборе"]
    if age < -CLIENT_SET_FUTURE_TOLERANCE_DAYS:
        return [f"набор ИЗ БУДУЩЕГО: дата набора {date_str}, сегодня "
                f"{today.isoformat()}, возраст {age} сут при допуске "
                f"{CLIENT_SET_FUTURE_TOLERANCE_DAYS:g} сут. Возраст такого "
                f"набора НЕДОКАЗУЕМ: либо часы разъехались сильнее суток, "
                f"либо дату в ключ положил не наш хост. Считать его свежим "
                f"нельзя — так лампа зеленеет вечно"]
    return []


def resolve_date(date: str | None, *, list_objects, client, config) -> str:
    """Дата дрила: названная человеком или САМАЯ СВЕЖАЯ под `backups/client/`.

    «Самая свежая» сама по себе НИЧЕГО не гарантирует: свежайший из мёртвого
    бакета — это набор месячной давности. Возраст выбранной даты судит
    `age_problems`, и только когда дату выбрали МЫ (см. `run_drill`)."""
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
    now: Callable[[], float] | None = None,
) -> int:
    """Весь ход дрила (§4.3). Возвращает код возврата: 0 / 1 / 2.

    `DrillNotRun` наружу не летит — он приезжает сюда, печатается и становится
    кодом 2 БЕЗ вердикта."""
    # Часы спрашиваются РОВНО ОДИН РАЗ за прогон. `ran_at` в вердикте и
    # «сегодня» в суде о возрасте выводятся из ЭТОГО ЖЕ числа: два обращения к
    # часам разошлись бы молча на границе суток, и вердикт сказал бы про
    # вторник, пока возраст считался от среды.
    #
    # Явный `now=` адресен и выигрывает; иначе берутся модульные `_now` — их
    # читает `main`, и через них сторож, гоняющий CLI, назначает «сегодня».
    started = (now if now is not None else _now)()
    # Сегодняшняя дата МАШИНЫ, ГДЕ ИДЁТ ДРИЛ, и по UTC — тем же счётом, каким
    # хост писал дату в ключ объекта. Выводится из `started`, а не из
    # `date.today()`: второй источник даты — это второе число на одну вещь.
    today = datetime.fromtimestamp(started, timezone.utc).date()
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

        # ВОЗРАСТ судится ТОЛЬКО когда дату выбирали мы сами. Явный `--date` —
        # это человек, который знает, что берёт старое (разбор полёта, сверка
        # с конкретным днём); судить его за это значит спорить с прямым
        # указанием. Отменяет ПОЛНОСТЬЮ, а не «с другим порогом»: порог,
        # который сдвигается сам, перестаёт что-либо означать.
        if date is None:
            problems.extend(age_problems(date_str, today))

        expected_sets: list[dict] = []
        actual_sets: list[dict] = []
        checked = 0

        # Неполнота набора — ПРОБЛЕМА (вердикт с ok=false, rc=1), а не отказ:
        # см. `parse_manifest`.
        problems.extend(manifest.get("incomplete") or [])

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

        target = (Path(verdict_out) if verdict_out is not None
                  else default_verdict_path())
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
        help=("дата набора. По умолчанию — самая свежая под backups/client/, "
              "и тогда её ВОЗРАСТ судится: набор старше "
              f"{CLIENT_SET_MAX_AGE_DAYS:g} сут — это rc 1 (заливка встала). "
              "Явная дата суд о возрасте ОТМЕНЯЕТ: человек знает, что берёт."))
    parser.add_argument(
        "--verdict-out", metavar="ПУТЬ",
        help=(f"куда положить вердикт. По умолчанию <корень дерева>/"
              f"{VERDICT_REL} — тот самый файл, который проба на хосте читает "
              f"как улику восстановления."))
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
        # `now=_now` передаётся ЯВНО, а не оставляется на умолчание: через
        # CLI других швов к часам нет, и сторож, гоняющий `main(argv)`,
        # назначает «сегодня» подменой модульного `_now`.
        return run_drill(args.private_key, date=args.date,
                         verdict_out=args.verdict_out, keep=args.keep,
                         now=_now)
    except DrillNotRun as exc:
        print(f"🚫 ДРИЛ НЕ СОСТОЯЛСЯ: {exc}", file=sys.stderr)
        print("   Вердикт НЕ записан: «не смогли проверить» — это не «нашли "
              "дефект» (DEV-43). Отсутствие свежего вердикта проба увидит "
              "сама как drill_stale/drill_never.", file=sys.stderr)
        return RC_NOT_RUN


if __name__ == "__main__":
    raise SystemExit(main())
