# -*- coding: utf-8 -*-
"""Сторожа дрила восстановления — DEV-46, §4.1–§4.4, §9.2, §9.4, §7 п. 2 и 6.

Написаны ОТ СПЕКИ, до реализации и не глядя в неё
([[jarvis-guards-not-by-the-plan-author]]): сторож, написанный автором кода,
наследует то же неверное допущение и молчит ровно там, где код забыл.

Дефект, ради которого §4 существует, назван в §4.1 дословно: **sha256 рваного
снимка совпадает с рваным снимком**. «Байты доехали» и «база поднимется» —
разные утверждения, и первое не влечёт второго. Поэтому здесь ни один сторож
не считает успехом «объект скачался и сошёлся по sha»: успех — это ОТКРЫЛСЯ
НАСТОЯЩИМ СЛОЕМ ХРАНЕНИЯ (`chatter.storage.db.Store`) и вернул величины,
совпавшие с манифестом.

Вторая половина работы — DEV-43 внутри дрила (§9.4): «не смог отработать» и
«нашёл дефект» обязаны приезжать РАЗНЫМИ кодами, и первое не имеет права
писать вердикт вообще. Обвинить бэкап в негодности, которой не проверяли,
нельзя; вердикт-обвинение, выписанный не глядя, читается сторожем на хосте
ровно так же, как настоящий.

Амендмент А (22.08) добавляет к этой паре третью, и склеивать её нельзя по той
же причине: **«нам выдали не тот ключ» и «объект не открывается»**. Различает
их `key_fingerprint` в манифесте — отпечаток публичного ключа, которым набор
запечатан (`backup_crypto.public_key_fingerprint`). Дрил обязан сверить его с
отпечатком ВЫДАННОГО ему приватного ключа; расхождение или отсутствие поля —
это «не состоялся» (rc=2, вердикта нет), а не найденный дефект бэкапа.

Амендмент Б (22.08) уточняет §4.3 п. 6: песочница с РАСШИФРОВАННЫМИ данными
клиента сносится на любом пути выхода, а не только на зелёном. Красный путь —
как раз тот, после которого к машине идут разбираться, и оставленная там
переписка живёт ровно столько, сколько длится разбор.

Контракт, по которому написаны сторожа:

    python scripts/restore_drill.py --private-key <путь> [--date YYYY-MM-DD]
                                    [--verdict-out <путь>] [--keep]

    ключи объектов  backups/client/<дата>/<rel_path>
    манифест        не шифрован, несёт sha256 ШИФРОТЕКСТА, counts для баз и
                    `key_fingerprint` — отпечаток ПУБЛИЧНОГО ключа, которым
                    набор запечатан (амендмент А)
    aad конверта    полный ключ объекта
    коды возврата   0 сверка прошла / 1 сверка не сошлась (вердикт ok=false)
                    2 дрил НЕ СОСТОЯЛСЯ — вердикт не пишется ВОВСЕ
    песочница       сносится на ЛЮБОМ пути выхода — 0, 1 и 2; единственное
                    исключение — явный `--keep` (амендмент Б)

🔴 Ни одной живой базы, ни одного вызова в R2, ни одного сетевого сокета: все
базы и ключи строятся в `tmp_path`, бакет подставлен в память, боевой клиент
boto3 подменён и на настоящий не откатывается. Ключевая пара — своя, из
`generate_keypair`, на каждый тест новая.
"""
from __future__ import annotations

import ast
import base64
import contextlib
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import backup_crypto as bc
from app.services import r2_storage
from app.services.backup_crypto import (encrypt_for, generate_keypair,
                                        pseudonym, public_key_fingerprint)
from app.services.restore_drill_verdict import VERDICT_REL, read_verdict
from app.services.sqlite_snapshot import snapshot_counts
from chatter.storage.db import PaymentsMigrationBlocked, Store

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "scripts" / "restore_drill.py"
_KEYGEN = _ROOT / "scripts" / "backup_keygen.py"

PREFIX = "backups/client"
DATE = "2026-08-20"
OTHER_DATE = "2026-08-19"

SLUG = "guard-client"
# ≥16 байт (`load_pseudonym_salt`), своя, не боевая.
SALT = b"guard-salt-for-dev46-restore-dr!"
PSEUDO = pseudonym(SLUG, SALT)

# Имя объекта внутри псевдонима слага не содержит: слага нет в ПОЛНОМ ключе
# (§9.1), а `.db` в хвосте оставлен намеренно — дрил вправе опознавать базу и
# по расширению, и по наличию `counts` в манифесте, и сторож не должен
# краснеть на выборе способа.
DB_NAME = "data.db"
REQ_NAME = "requisites.yaml"

REQUISITES = "card: '0000 0000 0000 0000'\nholder: guard\n".encode("utf-8")

# Узнаваемая строка ВНУТРИ переписки и заголовок файла базы. Сторож на снос
# песочницы (амендмент Б) ищет именно их: «каталог существует» утечкой не
# является — пустой каталог никому ничего не рассказывает, а один забытый
# `.db` рассказывает всю историю воронки.
LEAK_MARKER = "ЛИЧНАЯ-ПЕРЕПИСКА-ЛИДА-DEV46"
SQLITE_MAGIC = b"SQLite format 3"

_R2_ENV = {
    "R2_ACCOUNT_ID": "guard-account",
    "R2_ACCESS_KEY_ID": "guard-access-key",
    "R2_SECRET_ACCESS_KEY": "guard-secret",
    "R2_ENDPOINT": "https://guard.invalid",
    "R2_BACKUP_BUCKET": "guard-bucket",
}


# ── доступ к скрипту ───────────────────────────────────────────────────────

def _load_drill():
    """Загрузить `scripts/restore_drill.py` как модуль.

    Скрипта может ещё не быть — тогда красное обязано называть ИМЕННО это, а
    не рассыпаться `ModuleNotFoundError` из середины фикстуры."""
    assert _SCRIPT.is_file(), (
        f"нет {_SCRIPT} — дрил восстановления (DEV-46 §4.3) не написан. "
        "Сторожа краснеют на отсутствии скрипта, а не на опечатке в фикстуре: "
        "остальные красные в этом файле начнутся отсюда же.")
    spec = importlib.util.spec_from_file_location("restore_drill_script", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["restore_drill_script"] = mod
    spec.loader.exec_module(mod)
    assert hasattr(mod, "main"), (
        f"{_SCRIPT} не даёт `main(argv) -> int`. Коды возврата 0/1/2 — часть "
        "контракта дрила (§9.4), и проверить их можно только через функцию, "
        "возвращающую код, а не через `sys.exit` из-под `if __name__`.")
    return mod


@pytest.fixture
def drill():
    return _load_drill()


@pytest.fixture
def keypair():
    """Своя пара X25519 на каждый тест. Приватный ключ не покидает `tmp_path`."""
    return generate_keypair()


@pytest.fixture
def key_file(tmp_path, keypair):
    """Файл приватного ключа в ТОМ ЖЕ формате, что пишет `scripts/backup_keygen.py`.

    Формат берётся из самого keygen, а не переписывается сюда руками: две
    копии формата разъезжаются молча, и разъезд обнаружился бы на живом
    восстановлении через год."""
    private_raw, _pub = keypair
    spec = importlib.util.spec_from_file_location("backup_keygen_for_guard", _KEYGEN)
    keygen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(keygen)
    path = tmp_path / "owner" / "jarvis-backup.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        keygen.private_key_file_body(private_raw, "0" * 16, "2026-08-21T00:00:00"),
        encoding="utf-8")
    return path


# ── подставленный бакет ────────────────────────────────────────────────────

class FakeBucket:
    """Бакет в памяти: `{ключ: байты}`. Ни одного сетевого вызова.

    Подставляется НА ДВУХ уровнях сразу, потому что сторож не видел кода и не
    знает, как дрил ходит за объектами:

    * уровень boto3 — `r2_storage._make_client`; его зовут и `list_objects`,
      и `download_file`, даже когда они попали в чужую сигнатуру значением по
      умолчанию (то есть были связаны на импорте и подмену атрибута модуля
      уже не увидят);
    * уровень `app.services.r2_storage` — на случай, если дрил импортировал
      функции по имени в своё пространство.
    """

    def __init__(self, objects: dict[str, bytes]):
        self.objects = dict(objects)
        self.downloads: list[str] = []
        self.listings: list[str] = []

    # -- то, что видит boto3-клиент
    def list_objects_v2(self, **kwargs):
        prefix = kwargs.get("Prefix", "")
        self.listings.append(prefix)
        contents = [
            {"Key": key, "Size": len(blob), "LastModified": None}
            for key, blob in sorted(self.objects.items())
            if key.startswith(prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}

    def download_file(self, bucket, key, filename):  # noqa: ARG002
        if key not in self.objects:
            # Тот же класс ошибки, что настоящий R2 (`ClientError` → `R2Error`):
            # дрил, ловящий R2Error, обязан вести себя так же, как живьём.
            raise r2_storage.R2Error(
                f"R2 download_file failed for {key}: NoSuchKey (подставленный бакет)")
        self.downloads.append(key)
        dest = Path(filename)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.objects[key])
        return dest

    # -- то, что видит app.services.r2_storage
    def list_objects_api(self, prefix: str = "", *, client=None, config=None):  # noqa: ARG002
        return [
            {"key": o["Key"], "size": o["Size"], "last_modified": None}
            for o in self.list_objects_v2(Prefix=prefix)["Contents"]
        ]

    def download_file_api(self, key, dest, *, client=None, config=None):  # noqa: ARG002
        return self.download_file(None, key, dest)


def _install_bucket(monkeypatch, drill, bucket: FakeBucket) -> None:
    monkeypatch.setattr(r2_storage, "_make_client", lambda config: bucket)
    for module in (drill, r2_storage):
        for name, fn in (("list_objects", bucket.list_objects_api),
                         ("download_file", bucket.download_file_api)):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, fn)


@pytest.fixture
def env(monkeypatch, keypair):
    """Окружение ХОСТА: реквизиты R2, публичный ключ, соль псевдонимов.

    Приватного ключа здесь нет и быть не может — он приезжает только
    аргументом командной строки (§3.3 B, сторож на это — отдельный тест)."""
    _priv, pub = keypair
    for name, value in _R2_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("JARVIS_BACKUP_PUBLIC_KEY",
                       base64.b64encode(pub).decode("ascii"))
    monkeypatch.setenv("JARVIS_BACKUP_KEY_SALT",
                       base64.b64encode(SALT).decode("ascii"))
    monkeypatch.delenv("JARVIS_BACKUP_PRIVATE_KEY", raising=False)


def _spy_decrypt(monkeypatch, drill) -> list[dict]:
    """Перехватить `decrypt_with`, не подменяя поведения.

    Нужен двум сторожам сразу: одному — доказать, что расшифровка НЕ
    запускалась (битый sha ловится до неё), другому — что она, наоборот,
    запускалась (значит сверка байтов пройдена, и упало открытие)."""
    calls: list[dict] = []
    real = bc.decrypt_with

    def spy(private_key, blob, *, aad):
        calls.append({"aad": aad, "size": len(blob)})
        return real(private_key, blob, aad=aad)

    monkeypatch.setattr(bc, "decrypt_with", spy)
    if hasattr(drill, "decrypt_with"):
        monkeypatch.setattr(drill, "decrypt_with", spy)
    return calls


# ── сборка объектов «как заливка» ──────────────────────────────────────────

def _sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _key(date: str, name: str) -> str:
    return f"{PREFIX}/{date}/{PSEUDO}/{name}"


def _manifest_key(date: str) -> str:
    return f"{PREFIX}/{date}/manifest.json"


def _entry(rel_path: str, blob: bytes, counts: dict | None = None) -> dict:
    """Запись манифеста. `sha256` — по ШИФРОТЕКСТУ, `counts` — только у баз."""
    entry = {"rel_path": rel_path, "size": len(blob), "sha256": _sha256(blob)}
    if counts is not None:
        entry["counts"] = dict(counts)
    return entry


def _manifest(date: str, entries: list[dict], *, pub: bytes | None = None,
              fingerprint: str | None = None) -> bytes:
    """Манифест набора. `key_fingerprint` ОБЯЗАТЕЛЕН (амендмент А).

    Отпечаток называют либо ключом (`pub=` — считается тем же
    `public_key_fingerprint`, что зовёт заливка), либо явной строкой
    (`fingerprint=`); второе нужно ровно тем сторожам, что подсовывают дрилу
    ЧУЖОЙ отпечаток. Пустая строка убирает поле ВОВСЕ: манифест без отпечатка
    — отдельный случай, и заказываться он обязан словом, а не забывчивостью
    автора фикстуры."""
    if fingerprint is None:
        assert pub is not None, (
            "_manifest зовут либо с `pub`, либо с явным `fingerprint`: "
            "манифест без отпечатка ключа собирается только нарочно")
        fingerprint = public_key_fingerprint(pub)
    payload = {
        "generated_at": f"{date}T04:00:00+00:00",
        "date": date,
        "count": len(entries),
        "total_bytes": sum(e["size"] for e in entries),
    }
    if fingerprint:
        payload["key_fingerprint"] = fingerprint
    payload["files"] = entries
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def _remanifest(bucket: "FakeBucket", date: str = DATE, **kwargs) -> None:
    """Переписать манифест уже собранного набора, поменяв ТОЛЬКО отпечаток.

    Записи файлов остаются те же самые: набор, у которого испорчен один
    отпечаток, во всём остальном обязан быть ЗЕЛЁНЫМ — иначе красное пришло бы
    откуда угодно, и сторож доказывал бы не то."""
    old = json.loads(bucket.objects[_manifest_key(date)].decode("utf-8"))
    bucket.objects[_manifest_key(date)] = _manifest(date, old["files"], **kwargs)


def _seal(pub: bytes, plaintext: bytes, key: str, *, aad_key: str | None = None) -> bytes:
    """Зашифровать «как заливка»: aad = ПОЛНЫЙ ключ объекта.

    `aad_key` отличается от `key` ровно в одном сторожe — том, что проверяет
    привязку конверта к своему месту."""
    return encrypt_for(pub, plaintext, aad=(aad_key or key).encode("utf-8"))


def _seed_db(path: Path, contacts: dict[str, int], *, base_ts: float = 1_700_000_000.0,
             text_len: int = 24, text: str | None = None) -> dict:
    """Создать базу НАСТОЯЩЕЙ схемой (`Store`) и вернуть её честные величины.

    Ни один контакт не остаётся без сообщений: спека зовёт величину «число
    диалогов», и на пустом контакте `COUNT(contacts)` разошлось бы с
    `COUNT(DISTINCT messages.contact_id)` — сторож обязан краснеть на дефекте,
    а не на разночтении спеки."""
    body = text if text is not None else "x" * text_len
    written = 0
    with Store(path) as store:
        for contact_id, n in contacts.items():
            store.get_or_create_contact(contact_id)
            for _ in range(n):
                written += 1
                store.add_message(contact_id, "user", body, base_ts + written)
    return snapshot_counts(path)


def _green_bucket(pub: bytes, tmp_path: Path, *, date: str = DATE,
                  contacts: dict[str, int] | None = None) -> tuple[FakeBucket, dict]:
    """Полный клиентский набор за дату: база + реквизиты + манифест."""
    contacts = contacts or {"lead-1": 120, "lead-2": 130, "lead-3": 90}
    db_path = tmp_path / "source" / "client.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    counts = _seed_db(db_path, contacts)

    db_key = _key(date, DB_NAME)
    req_key = _key(date, REQ_NAME)
    db_blob = _seal(pub, db_path.read_bytes(), db_key)
    req_blob = _seal(pub, REQUISITES, req_key)

    entries = [
        _entry(f"{PSEUDO}/{DB_NAME}", db_blob, counts),
        _entry(f"{PSEUDO}/{REQ_NAME}", req_blob),
    ]
    bucket = FakeBucket({
        db_key: db_blob,
        req_key: req_blob,
        _manifest_key(date): _manifest(date, entries, pub=pub),
    })
    return bucket, counts


def _run(drill, key_file, *, date: str | None = DATE,
         verdict_out: Path | None = None, keep: bool = False) -> int:
    """Позвать `main(argv)`. `SystemExit` от argparse — тоже код возврата.

    `--date` передаётся явно ВЕЗДЕ, где дата набора не является предметом
    проверки: сторож, опирающийся на часы машины, врёт в конце года и в чужом
    часовом поясе.

    `date=None` — отдельный режим и отдельный предмет: дрил сам берёт самую
    свежую дату под префиксом и СУДИТ ЕЁ ВОЗРАСТ (амендмент Ж). Часы там
    подменяются швом `_now`, а не оставляются настоящими: из него же берётся
    `ran_at` вердикта, и второго источника времени у дрила нет."""
    argv = ["--private-key", str(key_file)]
    if date is not None:
        argv += ["--date", date]
    if verdict_out is not None:
        argv += ["--verdict-out", str(verdict_out)]
    if keep:
        argv.append("--keep")
    try:
        return drill.main(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2


def _files_under(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(p.relative_to(root).as_posix()
                  for p in root.rglob("*") if p.is_file())


def _write_stale_verdict(path: Path) -> bytes:
    """Прошлый НАСТОЯЩИЙ вердикт на диске; возвращает его байты.

    Дрил, который не состоялся, не имеет права ни снести его, ни переписать:
    последняя настоящая проверка не стирается тем, что проверкой не было."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "ran_at": 1_786_000_000.0, "ok": True,
        "expected": {"dialogs": 3, "messages": 340},
        "actual": {"dialogs": 3, "messages": 340},
        "detail": "прошлый прогон, трогать нельзя",
    }, ensure_ascii=False), encoding="utf-8")
    return path.read_bytes()


# Иглы, по которым узнаётся расшифрованный набор. Первая — родовая: файл
# SQLite бывает и чужой, поэтому в ЖИВОМ дереве ею не ищут. Вторая и третья
# принадлежат только этому тесту: совпасть случайно им не с чем.
_CLIENT_NEEDLES: tuple[tuple[str, bytes], ...] = (
    ("заголовок файла SQLite", SQLITE_MAGIC),
    ("текст сообщения клиента", LEAK_MARKER.encode("utf-8")),
    ("платёжные реквизиты", REQUISITES),
)
_UNIQUE_NEEDLES = _CLIENT_NEEDLES[1:]


def _plaintext_leaks(root: Path, needles=_CLIENT_NEEDLES) -> list[str]:
    """Файлы под `root`, в которых лежит ОТКРЫТЫЙ клиентский текст.

    Ищется содержимое расшифрованного набора: заголовок файла SQLite, текст
    сообщения из переписки, платёжные реквизиты. Именно содержимое, а не
    «каталог существует»: снос, оставивший пустой каталог, никому ничего не
    рассказал, а один забытый `.db` рассказал всё.

    `needles` сужают набор игл там, где сторож ходит по ЖИВОМУ дереву и
    родовая игла дала бы ложное красное на чужом файле."""
    if not root.exists():
        return []
    leaks: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        for what, needle in needles:
            if needle in blob:
                leaks.append(f"{path.relative_to(root).as_posix()} :: {what}")
    return leaks


# ── 1. зелёный круговорот ──────────────────────────────────────────────────

def test_green_round_trip_reports_the_numbers_that_are_really_in_the_base(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него молча проехал бы дрил, который зеленеет, не открыв базу: вердикт `ok=true` с величинами, переписанными из манифеста, а не прочитанными из восстановленного файла."""
    _priv, pub = keypair
    bucket, counts = _green_bucket(pub, tmp_path)
    _install_bucket(monkeypatch, drill, bucket)
    calls = _spy_decrypt(monkeypatch, drill)
    verdict_path = tmp_path / "out" / "verdict.json"

    rc = _run(drill, key_file, verdict_out=verdict_path)

    assert rc == 0, (
        f"полный, целый и правильно зашифрованный набор за {DATE} дал rc={rc}, "
        f"ожидалось 0. Скачано объектов: {bucket.downloads}, "
        f"расшифровок: {len(calls)}")
    assert bucket.downloads, (
        "дрил не скачал НИ ОДНОГО объекта — подставленный бакет остался "
        "нетронутым. Либо загрузчик ходит мимо `app.services.r2_storage` "
        "(тогда назовите шов в контракте), либо сверка вообще не запускалась "
        "и rc=0 означает «ничего не проверили».")
    assert calls, (
        "ни одной расшифровки: rc=0 получен без открытия конвертов — это "
        "«байты доехали» (§4.1), выданное за доказанное восстановление")

    verdict = read_verdict(verdict_path)
    assert verdict["ok"] is True, f"вердикт не зелёный: {verdict}"
    for field in ("dialogs", "messages"):
        assert verdict["actual"].get(field) == counts[field], (
            f"вердикт называет {field}={verdict['actual'].get(field)!r}, а в "
            f"восстановленной базе на самом деле {counts[field]}. Величины в "
            "вердикте обязаны быть ПРОЧИТАННЫМИ, а не переписанными из "
            "манифеста")
        assert verdict["expected"].get(field) == counts[field], (
            f"манифест обещал {field}={counts[field]}, а вердикт записал "
            f"ожидание {verdict['expected'].get(field)!r}")


def test_every_envelope_is_opened_with_the_aad_of_its_own_object_key(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него проехал бы дрил, подставляющий в `aad` что угодно своё, — и привязка конверта к месту (§3.4) перестала бы что-либо значить, оставшись зелёной."""
    _priv, pub = keypair
    bucket, _counts = _green_bucket(pub, tmp_path)
    _install_bucket(monkeypatch, drill, bucket)
    calls = _spy_decrypt(monkeypatch, drill)

    rc = _run(drill, key_file, verdict_out=tmp_path / "out" / "verdict.json")

    assert rc == 0, f"зелёный набор дал rc={rc}"
    used = {c["aad"] for c in calls}
    expected = {_key(DATE, DB_NAME).encode("utf-8"),
                _key(DATE, REQ_NAME).encode("utf-8")}
    assert expected <= used, (
        f"конверты открывались с aad={sorted(used)!r}, а обязаны — полным "
        f"ключом объекта: {sorted(expected)!r}")


# ── 2. открывает НАСТОЯЩИМ слоем хранения (§4.3 п. 3) ──────────────────────

def _legacy_payments_db(path: Path, contacts: dict[str, int], *,
                        text: str | None = None) -> dict:
    """База, которую `sqlite3` открывает и читает, а ПРОДУКТ открывать отказывается.

    Способ честный, а не подстроенный: старая схема `payments` (без
    `dedup_key`) с одной строкой внутри. `Store.__init__` видит незакрытое
    окно перестройки и падает `PaymentsMigrationBlocked` ДО любого DDL, тогда
    как `sqlite3.connect` и любой запрос по `contacts`/`messages` проходят
    чисто. Величины при этом СХОДЯТСЯ с манифестом — значит покраснеть тест
    может ровно по одной причине: открывали не тем слоем."""
    counts = _seed_db(path, contacts, text=text)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("DROP TABLE payments")
        conn.execute(
            "CREATE TABLE payments (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "contact_id TEXT NOT NULL, amount REAL, ts REAL NOT NULL)")
        conn.execute("INSERT INTO payments(contact_id, amount, ts) VALUES (?,?,?)",
                     ("lead-1", 100.0, 1_700_000_500.0))
        conn.commit()
    finally:
        conn.close()
    return counts


def test_a_file_sqlite_opens_but_the_product_refuses_is_red(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него зеленело бы «открывается ФАЙЛ» вместо «работает ПРОДУКТ» (§4.3 п. 3): дрил на `sqlite3` руками пройдёт базу, на которой раннер не поднимется."""
    _priv, pub = keypair
    db_path = tmp_path / "source" / "legacy.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    counts = _legacy_payments_db(db_path, {"lead-1": 40, "lead-2": 60})

    # Предусловие 1: голый sqlite3 эту базу открывает и ЧИТАЕТ те же величины.
    probe = tmp_path / "probe-sqlite.db"
    shutil.copy2(db_path, probe)
    conn = sqlite3.connect(f"file:{probe.as_posix()}?mode=ro", uri=True)
    try:
        seen = conn.execute("SELECT count(*) FROM messages").fetchone()[0]
    finally:
        conn.close()
    assert seen == counts["messages"], (
        f"предусловие: sqlite3 читает {seen} сообщений, а ожидалось "
        f"{counts['messages']} — контрпример не о том")

    # Предусловие 2: настоящий слой хранения на ней ПАДАЕТ.
    probe2 = tmp_path / "probe-store.db"
    shutil.copy2(db_path, probe2)
    with pytest.raises(PaymentsMigrationBlocked):
        Store(probe2).close()

    db_key = _key(DATE, DB_NAME)
    blob = _seal(pub, db_path.read_bytes(), db_key)
    bucket = FakeBucket({
        db_key: blob,
        _manifest_key(DATE): _manifest(
            DATE, [_entry(f"{PSEUDO}/{DB_NAME}", blob, counts)], pub=pub),
    })
    _install_bucket(monkeypatch, drill, bucket)
    calls = _spy_decrypt(monkeypatch, drill)
    verdict_path = tmp_path / "out" / "verdict.json"

    rc = _run(drill, key_file, verdict_out=verdict_path)

    assert calls, "конверт даже не открывали — до проверки продуктом не дошло"
    assert rc == 1, (
        f"rc={rc}, ожидалось 1. Байты целы и величины СХОДЯТСЯ с манифестом "
        f"({counts}), поэтому пройти этот набор может только дрил, открывший "
        "базу голым sqlite3: продукт (`chatter.storage.db.Store`) на ней "
        "падает PaymentsMigrationBlocked. rc=2 тоже неверен — дрил отработал "
        "и НАШЁЛ дефект, а не «не смог проверить»")
    verdict = read_verdict(verdict_path)
    assert verdict["ok"] is False, f"вердикт зелёный на негодной базе: {verdict}"
    assert str(verdict["detail"]).strip(), (
        "detail пуст: «база не открылась» без единого слова о том, чем именно "
        "она не открылась, чинить нечем")


# ── 3. ведущий сторож: рваная база, sha сходится ───────────────────────────

def test_torn_base_passes_the_byte_check_and_fails_on_opening(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него бэкап считался бы доказанным по sha256 — а §4.1 дословно: sha256 рваного снимка совпадает с рваным снимком, и продукт на нём не поднимается."""
    _priv, pub = keypair
    whole = tmp_path / "source" / "whole.db"
    whole.parent.mkdir(parents=True, exist_ok=True)
    counts = _seed_db(whole, {"lead-1": 400}, text_len=512)
    raw = whole.read_bytes()
    assert len(raw) > 64 * 1024, (
        f"база {len(raw)} байт — слишком мала, обрезка может не задеть данных")

    torn = raw[: int(len(raw) * 0.6)]

    # Предусловие: именно этот огрызок продукт открыть НЕ может.
    probe = tmp_path / "probe-torn.db"
    probe.write_bytes(torn)
    with pytest.raises(sqlite3.DatabaseError) as err:
        with Store(probe) as store:
            store.history("lead-1")
    assert "malformed" in str(err.value) or "not a database" in str(err.value), (
        f"предусловие: рваная база обязана не открываться; получено {err.value!r}")

    db_key = _key(DATE, DB_NAME)
    blob = _seal(pub, torn, db_key)
    # sha ЧЕСТНЫЙ — посчитан по тому самому шифротексту, что лежит в бакете.
    entry = _entry(f"{PSEUDO}/{DB_NAME}", blob, counts)
    assert entry["sha256"] == _sha256(blob), "sha в манифесте обязан быть честным"
    bucket = FakeBucket({
        db_key: blob,
        _manifest_key(DATE): _manifest(DATE, [entry], pub=pub),
    })
    _install_bucket(monkeypatch, drill, bucket)
    calls = _spy_decrypt(monkeypatch, drill)
    verdict_path = tmp_path / "out" / "verdict.json"

    rc = _run(drill, key_file, verdict_out=verdict_path)

    assert db_key in bucket.downloads, (
        f"объект {db_key} не скачивали — сверка байтов не проводилась вовсе")
    assert calls, (
        "расшифровка не запускалась: значит красное (если оно есть) пришло от "
        "проверки байтов, а сторож задуман про то, что байты СОШЛИСЬ")
    assert rc == 1, (
        f"rc={rc}, ожидалось 1. sha256 шифротекста совпадает с манифестом "
        f"побайтово ({entry['sha256'][:12]}…), то есть сегодняшняя проверка "
        "целостности на этом наборе ЗЕЛЁНАЯ; красным его делает только "
        "открытие настоящим слоем хранения. rc=0 здесь — ровно тот дефект, "
        "ради которого §4 написан; rc=2 — обратный: дрил отработал")
    verdict = read_verdict(verdict_path)
    assert verdict["ok"] is False, f"вердикт зелёный на рваной базе: {verdict}"
    assert str(verdict["detail"]).strip(), "detail пуст: чинить нечем"


# ── 4. расхождение величин названо ОБОИМИ числами (§7 п. 6) ────────────────

def test_count_mismatch_names_both_numbers_not_restore_failed(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него расхождение приезжало бы словами «restore failed»: два потерянных сообщения и две тысячи потерянных выглядели бы одинаково, и решить, что чинить, было бы не по чему."""
    _priv, pub = keypair
    db_path = tmp_path / "source" / "short.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    real = _seed_db(db_path, {"lead-1": 100, "lead-2": 120, "lead-3": 118})
    assert real["messages"] == 338, f"подготовка: в базе {real['messages']} сообщений"

    promised = dict(real)
    promised["messages"] = 340  # манифест обещает на два больше, чем есть

    db_key = _key(DATE, DB_NAME)
    blob = _seal(pub, db_path.read_bytes(), db_key)
    bucket = FakeBucket({
        db_key: blob,
        _manifest_key(DATE): _manifest(
            DATE, [_entry(f"{PSEUDO}/{DB_NAME}", blob, promised)], pub=pub),
    })
    _install_bucket(monkeypatch, drill, bucket)
    verdict_path = tmp_path / "out" / "verdict.json"

    rc = _run(drill, key_file, verdict_out=verdict_path)

    assert rc == 1, (
        f"rc={rc}, ожидалось 1: манифест обещает 340 сообщений, в базе 338. "
        "Дрил отработал и нашёл расхождение — это не «не состоялся» (rc=2)")
    verdict = read_verdict(verdict_path)
    assert verdict["ok"] is False, f"вердикт зелёный при расхождении: {verdict}"
    assert verdict["expected"].get("messages") == 340, (
        f"expected.messages={verdict['expected'].get('messages')!r}, "
        "а манифест обещал 340")
    assert verdict["actual"].get("messages") == 338, (
        f"actual.messages={verdict['actual'].get('messages')!r}, "
        "а в восстановленной базе 338")
    detail = str(verdict["detail"])
    assert "340" in detail and "338" in detail, (
        f"detail не называет ОБА числа (§7 п. 6): {detail!r}. «restore failed» "
        "без чисел не говорит, что чинить, — потеряны два сообщения или две "
        "тысячи, читается одинаково")


# ── 5. «не смог отработать» ≠ «нашёл дефект» (DEV-43 внутри дрила) ─────────

def _blocked_bucket(case: str, tmp_path: Path, pub: bytes) -> FakeBucket:
    if case == "no_objects_for_date":
        # Бакет НЕ пуст: объекты есть, но за другую дату. Иначе тест не
        # отличал бы «за дату ничего нет» от «бакет недоступен».
        bucket, _counts = _green_bucket(pub, tmp_path, date=OTHER_DATE)
        return bucket
    if case == "manifest_unreadable":
        bucket, _counts = _green_bucket(pub, tmp_path)
        bucket.objects[_manifest_key(DATE)] = b"\x00\x01not a json at all\xff"
        return bucket
    if case == "bad_key_file":
        bucket, _counts = _green_bucket(pub, tmp_path)
        return bucket
    raise AssertionError(f"неизвестный случай {case}")


@pytest.mark.parametrize("case", [
    "no_objects_for_date",     # объектов за дату нет
    "manifest_unreadable",     # манифест не читается
    "bad_key_file",            # файл ключа негоден
])
def test_drill_that_could_not_run_writes_no_verdict_at_all(
        case, tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него «не смог проверить» приезжало бы вердиктом `ok=false` — обвинением бэкапа в негодности, которой не проверяли; сторож на хосте прочёл бы его как настоящий провал (§9.4, DEV-43)."""
    _priv, pub = keypair
    bucket = _blocked_bucket(case, tmp_path, pub)
    _install_bucket(monkeypatch, drill, bucket)

    if case == "bad_key_file":
        broken = tmp_path / "owner" / "broken.key"
        broken.write_text("это не ключ, а записка\n", encoding="utf-8")
        key_arg = broken
    else:
        key_arg = key_file

    # (а) вердикта не было — он не имеет права появиться.
    fresh = tmp_path / "out" / "fresh-verdict.json"
    rc = _run(drill, key_arg, verdict_out=fresh)
    assert rc == 2, (
        f"случай {case!r} дал rc={rc}, ожидалось 2. Дрил НЕ СОСТОЯЛСЯ: он "
        "ничего не узнал о бэкапе. rc=1 означал бы «проверили и нашли "
        "дефект» — обвинение, которого никто не проверял; rc=0 — «всё "
        "хорошо», сказанное вслепую")
    assert not fresh.exists(), (
        f"случай {case!r}: дрил создал вердикт {fresh}, хотя не отработал. "
        f"Содержимое: {fresh.read_text(encoding='utf-8', errors='replace')!r}. "
        "«Вердикта нет» и «вердикт красный» — разные состояния и разные "
        "действия (§9.4)")

    # (б) старый вердикт лежал — он не имеет права ни пропасть, ни измениться.
    stale = tmp_path / "out" / "stale-verdict.json"
    before = _write_stale_verdict(stale)

    rc2 = _run(drill, key_arg, verdict_out=stale)

    assert rc2 == 2, f"случай {case!r} со старым вердиктом дал rc={rc2}, ожидалось 2"
    assert stale.exists(), (
        f"случай {case!r}: несостоявшийся дрил СНЁС прошлый вердикт. Сторож на "
        "хосте прочтёт это как `drill_never` и поднимет задачу вместо того, "
        "чтобы верить последней настоящей проверке")
    assert stale.read_bytes() == before, (
        f"случай {case!r}: несостоявшийся дрил переписал прошлый вердикт.\n"
        f"было:  {before!r}\nстало: {stale.read_bytes()!r}")


# ── 5б. НЕ ТОТ КЛЮЧ — это ОТКАЗ, а не найденный дефект (амендмент А) ───────
#
# Отпечаток публичного ключа в манифесте нужен ровно затем, чтобы «нам выдали
# не тот ключ» и «объект не открывается» приезжали РАЗНЫМИ кодами. Без него
# оба состояния выглядят одинаково — тег AES-GCM не сошёлся, — и на хосте
# загорается `drill_failed` там, где бэкап цел.

def test_a_manifest_without_the_key_fingerprint_is_refused_without_a_verdict(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него дрил открывал бы набор ВСЛЕПУЮ: не зная, каким ключом набор запечатан, он не отличит «выдали не тот ключ» от «объект не открывается», а чинятся они в разных местах и разными людьми."""
    _priv, pub = keypair
    bucket, _counts = _green_bucket(pub, tmp_path)
    # Набор ЗЕЛЁНЫЙ во всём остальном: байты целы, конверты запечатаны тем
    # самым ключом, величины сойдутся. Убрано ровно одно поле.
    _remanifest(bucket, DATE, fingerprint="")
    assert b"key_fingerprint" not in bucket.objects[_manifest_key(DATE)], (
        "фикстура не убрала поле из манифеста — сторож проверял бы не то")
    _install_bucket(monkeypatch, drill, bucket)

    fresh = tmp_path / "out" / "fresh-verdict.json"
    rc = _run(drill, key_file, verdict_out=fresh)

    assert rc == 2, (
        f"манифест без `key_fingerprint` дал rc={rc}, ожидалось 2. Поле "
        "обязательно (амендмент А): rc=0 здесь — зелёное, сказанное вслепую "
        "(набор в остальном исправен, и дрил просто не заметил пропажи); "
        "rc=1 — обвинение бэкапа в негодности, которой никто не проверял")
    assert not fresh.exists(), (
        f"дрил написал вердикт {fresh}, хотя не состоялся. Содержимое: "
        f"{fresh.read_text(encoding='utf-8', errors='replace')!r}. Отсутствие "
        "свежего вердикта проба на хосте прочтёт сама (drill_stale/"
        "drill_never); выписанный вслепую вердикт она прочтёт как настоящий")

    # И прошлый настоящий вердикт обязан уцелеть.
    stale = tmp_path / "out" / "stale-verdict.json"
    before = _write_stale_verdict(stale)
    rc2 = _run(drill, key_file, verdict_out=stale)
    assert rc2 == 2, f"со старым вердиктом на месте дрил дал rc={rc2}, ожидалось 2"
    assert stale.read_bytes() == before, (
        f"несостоявшийся дрил переписал прошлый вердикт.\n"
        f"было:  {before!r}\nстало: {stale.read_bytes()!r}")


def test_a_manifest_naming_another_key_is_refused_without_a_verdict(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него `key_fingerprint` остался бы украшением: дрил, не сверивший его с ВЫДАННЫМ ему приватным ключом, объявит доказанным восстановление набора, который на самом деле запечатан для кого-то другого."""
    _priv, pub = keypair
    _other_priv, other_pub = generate_keypair()
    foreign = public_key_fingerprint(other_pub)
    ours = public_key_fingerprint(pub)
    assert foreign != ours, (
        f"предусловие: отпечатки двух разных ключей совпали ({foreign}) — "
        "контрпример не о том")

    # Объекты запечатаны НАШИМ ключом и выданным ключом открываются; врёт
    # ровно одно поле манифеста. Дрил, сверяющий отпечаток, обязан отказаться
    # ДО того, как поверит такому манифесту.
    bucket, _counts = _green_bucket(pub, tmp_path)
    _remanifest(bucket, DATE, fingerprint=foreign)
    _install_bucket(monkeypatch, drill, bucket)

    fresh = tmp_path / "out" / "fresh-verdict.json"
    rc = _run(drill, key_file, verdict_out=fresh)

    assert rc == 2, (
        f"манифест называет ключ {foreign}, а дрилу выдан ключ {ours}; "
        f"rc={rc}, ожидалось 2. Объекты в этом бакете выданным ключом "
        "ОТКРЫВАЮТСЯ — значит rc=0 означает ровно одно: отпечаток из "
        "манифеста не сверяли ни с чем, и поле можно было не писать")
    assert not fresh.exists(), (
        f"дрил написал вердикт {fresh} на манифесте с чужим отпечатком: "
        f"{fresh.read_text(encoding='utf-8', errors='replace')!r}")


def test_being_handed_the_wrong_private_key_is_not_reported_as_a_broken_backup(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него «дрилу выдали не тот ключ» приезжало бы кодом «бэкап негоден»: на хосте загорается `drill_failed`, чинить бегут бэкап, а сломан ключ в руках дрила — та же склейка «не смогли проверить» с «нашли дефект», что в DEV-43."""
    our_priv, our_pub = keypair
    _other_priv, other_pub = generate_keypair()

    # Весь набор запечатан ЧУЖИМ ключом, и манифест ЧЕСТНО называет его
    # отпечаток. Выданный дрилу ключ (`key_file`) — наш.
    bucket, _counts = _green_bucket(other_pub, tmp_path)
    manifest = json.loads(bucket.objects[_manifest_key(DATE)].decode("utf-8"))
    assert manifest.get("key_fingerprint") == public_key_fingerprint(other_pub), (
        "предусловие: манифест обязан называть отпечаток ТОГО ключа, которым "
        f"набор запечатан; получено {manifest.get('key_fingerprint')!r}")

    # Предусловие: выданным ключом конверт НЕ открывается. Значит дрил без
    # сверки отпечатка упёрся бы в «тег AES-GCM не сошёлся» — и назвал бы это
    # негодной базой, потому что снаружи это выглядит именно так.
    db_key = _key(DATE, DB_NAME)
    with pytest.raises(bc.BackupCryptoError):
        bc.decrypt_with(our_priv, bucket.objects[db_key],
                        aad=db_key.encode("utf-8"))

    _install_bucket(monkeypatch, drill, bucket)
    fresh = tmp_path / "out" / "fresh-verdict.json"
    rc = _run(drill, key_file, verdict_out=fresh)

    assert rc != 1, (
        f"дрил вернул rc=1: «проверили и нашли дефект». Проверить он ничего "
        f"не мог — набор запечатан ключом {public_key_fingerprint(other_pub)}, "
        f"а на руках ключ {public_key_fingerprint(our_pub)}, и манифест это "
        "называет прямо. rc=1 отсюда — обвинение исправного бэкапа, и на "
        "хосте оно загорится как срочная авария")
    assert rc == 2, (
        f"rc={rc}, ожидалось 2. «Не тот ключ» — это «дрил НЕ СОСТОЯЛСЯ»: о "
        "бэкапе не узнали ничего")
    assert not fresh.exists(), (
        f"вердикт написан там, где ничего не проверяли: "
        f"{fresh.read_text(encoding='utf-8', errors='replace')!r}")


def test_the_fingerprint_is_taken_from_the_private_key_not_from_the_host_env(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него сверка отпечатка держалась бы на `JARVIS_BACKUP_PUBLIC_KEY` — переменной, которой на машине владельца НЕТ: на хосте зелено, на ноутбуке дрил либо падает на ровном месте, либо тихо пропускает сверку и снова склеивает «не тот ключ» с «база не открылась»."""
    _priv, pub = keypair
    # Дрил живёт там, где приватный ключ (§9.2), а публичный ключ и соль
    # псевдонимов — это хозяйство ХОСТА. Отпечаток выводится из выданного
    # приватного ключа, и больше ниоткуда.
    monkeypatch.delenv("JARVIS_BACKUP_PUBLIC_KEY", raising=False)

    # (а) зелёный набор обязан пройти БЕЗ переменной.
    bucket, _counts = _green_bucket(pub, tmp_path / "first")
    _install_bucket(monkeypatch, drill, bucket)
    green = tmp_path / "out" / "green.json"
    rc = _run(drill, key_file, verdict_out=green)
    assert rc == 0, (
        f"без JARVIS_BACKUP_PUBLIC_KEY зелёный набор дал rc={rc}, ожидалось 0. "
        "Публичный ключ для сверки выводится из ПРИВАТНОГО, выданного "
        "аргументом; на машине владельца хостовой переменной нет вовсе, и "
        "дрил, читающий её, там не запустится ни разу")
    assert read_verdict(green)["ok"] is True, "вердикт не зелёный"

    # (б) и чужой отпечаток обязан быть пойман БЕЗ неё же.
    _other_priv, other_pub = generate_keypair()
    foreign = public_key_fingerprint(other_pub)
    spoiled, _c2 = _green_bucket(pub, tmp_path / "second")
    _remanifest(spoiled, DATE, fingerprint=foreign)
    _install_bucket(monkeypatch, drill, spoiled)
    fresh = tmp_path / "out" / "foreign.json"
    rc2 = _run(drill, key_file, verdict_out=fresh)
    assert rc2 == 2, (
        f"без переменной окружения чужой отпечаток {foreign} прошёл: rc={rc2}, "
        "ожидалось 2. Сверка, отключающаяся вместе с хостовой переменной, — "
        "это сверка, которой на машине владельца нет")
    assert not fresh.exists(), (
        f"вердикт написан там, где ничего не проверяли: "
        f"{fresh.read_text(encoding='utf-8', errors='replace')!r}")


# ── 6. битый sha ловится ДО расшифровки ────────────────────────────────────

def test_corrupted_bytes_are_caught_before_any_decryption(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него порча байтов приезжала бы словами «база не открылась»: чинили бы базу, а сломан транспорт, — и расшифровка гоняла бы заведомо битый объект."""
    _priv, pub = keypair
    db_path = tmp_path / "source" / "client.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    counts = _seed_db(db_path, {"lead-1": 50})

    db_key = _key(DATE, DB_NAME)
    good = _seal(pub, db_path.read_bytes(), db_key)
    entry = _entry(f"{PSEUDO}/{DB_NAME}", good, counts)

    # Манифест прежний, а в бакете — подменённые байты в СЕРЕДИНЕ объекта.
    middle = len(good) // 2
    spoiled = good[:middle] + bytes([good[middle] ^ 0xFF]) + good[middle + 1:]
    assert len(spoiled) == len(good), "порча не должна менять размер объекта"
    assert _sha256(spoiled) != entry["sha256"], "порча обязана менять sha"

    bucket = FakeBucket({
        db_key: spoiled,
        _manifest_key(DATE): _manifest(DATE, [entry], pub=pub),
    })
    _install_bucket(monkeypatch, drill, bucket)
    calls = _spy_decrypt(monkeypatch, drill)
    verdict_path = tmp_path / "out" / "verdict.json"

    rc = _run(drill, key_file, verdict_out=verdict_path)

    assert rc == 1, (
        f"rc={rc}, ожидалось 1: объект в бакете разошёлся с манифестом по "
        "sha256, это НАЙДЕННЫЙ дефект бэкапа, а не «не смогли проверить»")
    assert calls == [], (
        f"расшифровка запускалась {len(calls)} раз(а) на объекте, который уже "
        "разошёлся с манифестом по байтам. Порядок обязателен: сверка sha — "
        "ДО криптографии, иначе причина приедет из глубин AES-GCM «тег не "
        "сошёлся» и будет прочитана как испорченный ключ")
    verdict = read_verdict(verdict_path)
    assert verdict["ok"] is False, f"вердикт зелёный на битых байтах: {verdict}"
    detail = str(verdict["detail"]).lower()
    assert "sha" in detail or "байт" in detail or "byte" in detail, (
        f"detail говорит не про БАЙТЫ: {verdict['detail']!r}. Порча "
        "транспорта и негодная база чинятся в разных местах, и текст обязан "
        "сказать, какое из двух")


# ── 7. приватный ключ только из аргумента (AST по скрипту) ─────────────────

def _dotted(node) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _looks_like_key_path(value: str) -> bool:
    low = value.lower()
    if low.endswith((".key", ".pem")):
        return True
    return ".secrets" in low and "key" in low


def _arity(args: ast.arguments) -> int:
    return (len(getattr(args, "posonlyargs", [])) + len(args.args)
            + len(args.kwonlyargs) + (1 if args.vararg else 0)
            + (1 if args.kwarg else 0))


def _private_key_violations(source: str) -> list[str]:
    """Найти в исходнике способы добыть приватный ключ МИМО аргумента.

    Разбор AST, а не поиск подстроки: комментарий про `load_private_key` в
    докстроке дефектом не является, а вызов — является. Нужная слепота к
    комментариям здесь бесплатна ([[jarvis-approved-text-three-places]]).

    Функция с ПАРАМЕТРОМ (`def read_private_key(path)`) нарушением не
    считается: ключ ей всё равно кто-то называет. Нарушение — функция БЕЗ
    параметров: такая обязана найти ключ сама.
    """
    tree = ast.parse(source)
    found: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name.split(".")[-1] in ("getenv", "get") and node.args:
                head = node.args[0]
                if (isinstance(head, ast.Constant) and isinstance(head.value, str)
                        and "PRIVATE" in head.value.upper()
                        and ("environ" in name or "getenv" in name)):
                    found.append("env_private")
            if name.split(".")[-1] == "add_argument":
                flags = [a.value for a in node.args
                         if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                if any("private" in f.lower() for f in flags):
                    for kw in node.keywords:
                        if kw.arg != "default":
                            continue
                        if not (isinstance(kw.value, ast.Constant)
                                and kw.value.value is None):
                            found.append("default_private_key")
            for kw in node.keywords:
                if (kw.arg == "default" and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                        and _looks_like_key_path(kw.value.value)):
                    found.append("key_path_literal")
        if isinstance(node, ast.Subscript) and _dotted(node.value).endswith("environ"):
            key = node.slice
            if (isinstance(key, ast.Constant) and isinstance(key.value, str)
                    and "PRIVATE" in key.value.upper()):
                found.append("env_private")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if "private" in node.name.lower() and _arity(node.args) == 0:
                found.append("zero_arg_private_loader")

    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if (isinstance(node.value.value, str)
                    and _looks_like_key_path(node.value.value)):
                found.append("key_path_literal")

    return sorted(set(found))


_PLANTED_BAD = '''\
# -*- coding: utf-8 -*-
"""Образец с дефектами. Живьём такого файла нет — он существует ради того,
чтобы сторож доказал СВОЮ способность краснеть."""
import argparse
import os

DEFAULT_PRIVATE_KEY = "C:/jarvis/.secrets/backup-private.key"


def load_private_key():
    return os.environ["JARVIS_BACKUP_PRIVATE_KEY"]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-key", default=DEFAULT_PRIVATE_KEY)
    parser.add_argument("--fallback", default="/etc/jarvis/owner.pem")
    return 0
'''

_PLANTED_GOOD = '''\
# -*- coding: utf-8 -*-
"""Образец без дефектов: ключ приезжает аргументом и только им.

Здесь нарочно упомянуты и `load_private_key`, и JARVIS_BACKUP_PRIVATE_KEY, и
путь E:\\\\keys\\\\owner.key — в ТЕКСТЕ. Сторож, ищущий подстроку, покраснел бы
на этой докстроке; сторож на AST обязан промолчать."""
import argparse


def read_private_key(path):
    return path.read_bytes()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-key", required=True)
    parser.add_argument("--date", default=None)
    return 0
'''


def test_the_private_key_guard_itself_goes_red_on_a_planted_sample(tmp_path):
    """Без него сторож «приватного ключа мимо аргумента нет» был бы зелен ПО ПОСТРОЕНИЮ: пустой список нарушений одинаково означает и «чисто», и «искать не умеем»."""
    bad = tmp_path / "planted_bad.py"
    bad.write_text(_PLANTED_BAD, encoding="utf-8")
    good = tmp_path / "planted_good.py"
    good.write_text(_PLANTED_GOOD, encoding="utf-8")

    seen = _private_key_violations(bad.read_text(encoding="utf-8"))
    assert seen == ["default_private_key", "env_private", "key_path_literal",
                    "zero_arg_private_loader"], (
        f"на образце с ЧЕТЫРЬМЯ дефектами сторож увидел {seen} — то, чего он "
        "не видит на образце, он не увидит и в боевом скрипте")

    clean = _private_key_violations(good.read_text(encoding="utf-8"))
    assert clean == [], (
        f"на чистом образце сторож нашёл {clean}. Он краснеет на ТЕКСТЕ "
        "докстроки, а не на действии — такой сторож заставит автора кода "
        "убрать объяснение вместо дефекта")


def test_the_drill_takes_the_private_key_only_from_its_argument(drill):  # noqa: ARG001
    """Без него на хост тихо вернулось бы умение ЧИТАТЬ свои бэкапы — умолчательным путём или переменной окружения, — и вариант B (§3.3) остался бы только на бумаге."""
    source = _SCRIPT.read_text(encoding="utf-8")
    seen = _private_key_violations(source)
    assert seen == [], (
        f"{_SCRIPT} добывает приватный ключ мимо аргумента: {seen}.\n"
        "  env_private             — чтение приватного ключа из окружения\n"
        "  default_private_key     — умолчание у --private-key\n"
        "  key_path_literal        — зашитый путь к файлу ключа\n"
        "  zero_arg_private_loader — функция-загрузчик без параметров\n"
        "Приватный ключ на хосте не появляется НИКОГДА — ни для дрила, ни "
        "«временно, на время восстановления» (§3.3). Любой из четырёх "
        "способов возвращает хосту умение читать то, что он записал.")


# ── 8. вердикт живёт В ДЕРЕВЕ — это его ДОМ, а не нарушение ────────────
#
# Амендмент В (22.08). Шаг 4в уже смержен, и в нём одна строка на двоих:
# `restore_drill_verdict.VERDICT_REL` — куда дрил кладёт улику, и
# `ops_watchdog.RESTORE_DRILL_REL` — где проба на хосте её читает; сторож шага
# 4в требует их равенства литерально. Дрил, отказывающийся писать в дерево,
# делает хостовую половину арки НЕДОСТИЖИМОЙ: улике неоткуда взяться, и
# `drill_never` горит вечно при исправном бэкапе. Правило «в живое дерево не
# писать» написано для ПЕСОЧНИЦЫ, где лежит расшифрованная переписка, — не для
# файла из пяти полей статуса. `state/` и есть место, где живут файлы
# состояния (`state/ops_watchdog_state.json`, `state/panel_events.jsonl`).
#
# Амендмент Е (22.08) — шов, без которого эти сторожа сами были бы дефектом.
# После мержа они лягут в `C:\jarvis\tests\`, и ЛЮБОЙ полный прогон суиты в
# живом дереве написал бы в `C:\jarvis\state\backup\restore_drill.json`
# вердикт со свежим `ran_at` и `ok=true`. Проба прочла бы его как «дрил
# прошёл»: прогон ТЕСТОВ подделывал бы улику восстановления. Поэтому корень,
# от которого дрил считает умолчательный путь, обязан читаться из АТРИБУТА
# МОДУЛЯ в момент вызова, а не запекаться в константу на импорте. Боевое
# поведение это не меняет ни на йоту, лишнего ключа CLI не заводит, а сторожу
# даёт подменить корень и не касаться живого дерева вовсе.

# Имя атрибута — часть контракта, и оно ОДНО: `_ROOT`, по конвенции репозитория
# (так же зовётся корень в `backup_keygen.py` и `scripts/state_backup.py`).
# Второго имени на ту же вещь не заводим — два имени гасят друг друга молча
# ([[jarvis-two-numbers-for-one-thing]]).
ROOT_ATTR = "_ROOT"


def _install_repo_root(monkeypatch, drill, root: Path) -> Path:
    """Подменить корень дерева и вернуть ожидаемый умолчательный путь вердикта.

    Красное здесь — не «сторож сломался», а «шва нет»: корень, запечённый в
    константу на импорте, подмену увидеть не может, и проверить умолчательный
    путь можно было бы только записью в ЖИВОЕ дерево."""
    assert hasattr(drill, ROOT_ATTR), (
        f"{_SCRIPT} не даёт атрибута `{ROOT_ATTR}` — корень дерева, от "
        "которого считается умолчательный путь вердикта (амендмент Е). Он "
        "обязан ЧИТАТЬСЯ в момент вызова: `Path(_ROOT) / VERDICT_REL`, а "
        "не быть свёрнут в готовую константу на импорте. Без шва любой полный "
        "прогон суиты в живом дереве подделал бы улику восстановления — "
        "свежий ran_at, ok=true, и проба на хосте прочла бы это как «дрил "
        "прошёл»")
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(drill, ROOT_ATTR, root)
    return root / VERDICT_REL


@contextlib.contextmanager
def _live_tree_verdict_untouched():
    """Живой улики не касается НИ ОДИН сторож — и это проверяется.

    Шов амендмента Е уводит умолчательный путь в `tmp_path`. Сторож обязан
    краснеть, если реализация шов проигнорировала: подделанное зелёное хуже
    любого красного ([[jarvis-gate-mutates-the-deploy-tree]]). Файл, если его
    всё-таки тронули, возвращается байт в байт — красный сторож не имеет
    права оставить за собой вердикт, которого не было."""
    path = _ROOT / VERDICT_REL
    had = path.exists()
    before = path.read_bytes() if had else None
    try:
        yield path
    finally:
        now = path.read_bytes() if path.exists() else None
        if (path.exists(), now) != (had, before):
            if had:
                path.write_bytes(before)
            elif path.exists():
                path.unlink()
            raise AssertionError(
                f"дрил тронул ЖИВОЙ путь вердикта {path} мимо шва "
                f"`{ROOT_ATTR}` (файл был: {had}, стал: {now is not None}). "
                "Это и есть тот дефект, ради которого шов заведён: полный "
                "прогон суиты в `C:\\jarvis` подделал бы улику "
                "восстановления. Файл возвращён на место тестом")


def test_by_default_the_verdict_lands_exactly_where_the_probe_reads_it(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него две половины арки разъехались бы ПО МЕСТУ: дрил кладёт улику одной дорогой, проба на хосте ходит другой — и `drill_never` горит вечно при полностью исправном бэкапе."""
    _priv, pub = keypair
    bucket, _counts = _green_bucket(pub, tmp_path)
    _install_bucket(monkeypatch, drill, bucket)
    default_path = _install_repo_root(monkeypatch, drill, tmp_path / "tree")

    with _live_tree_verdict_untouched():
        before = time.time()
        rc = _run(drill, key_file)          # БЕЗ --verdict-out
        after = time.time()

    assert rc == 0, f"зелёный набор без --verdict-out дал rc={rc}"
    assert default_path.is_file(), (
        f"вердикта нет по пути {default_path}. Это не «дрил написал куда-то "
        f"ещё» — это `VERDICT_REL` = {VERDICT_REL!r} от корня дерева, "
        "ЕДИНСТВЕННОЕ место, куда смотрит проба на хосте: шаг 4в держит одну "
        "строку у дрила и у пробы литеральной сверкой. Мимо неё улика не "
        "доедет никуда, и хостовая половина арки останется без входа")
    verdict = read_verdict(default_path)
    assert before - 5 <= verdict["ran_at"] <= after + 5, (
        f"по пути {default_path} лежит вердикт с ran_at={verdict['ran_at']}, а "
        f"прогон был в окне [{before}, {after}] — это не свежая запись. "
        "«Написал» и «нашёл чужое» сторож обязан различать")
    assert verdict["ok"] is True, f"вердикт не зелёный: {verdict}"


def test_verdict_out_is_obeyed_and_the_default_place_stays_untouched(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него `--verdict-out` был бы украшением: дрил, всегда пишущий и по умолчанию, затирал бы улику каждым прогоном сторожей — а прогон сторожей проверкой бэкапа не является."""
    _priv, pub = keypair
    bucket, _counts = _green_bucket(pub, tmp_path)
    _install_bucket(monkeypatch, drill, bucket)
    default_path = _install_repo_root(monkeypatch, drill, tmp_path / "tree")

    custom = tmp_path / "out" / "elsewhere.json"
    with _live_tree_verdict_untouched():
        rc = _run(drill, key_file, verdict_out=custom)

    assert rc == 0, f"зелёный набор дал rc={rc}"
    assert custom.is_file(), (
        f"--verdict-out {custom} не соблюдён: по названному пути вердикта нет. "
        "Улику увозят с ноутбука на хост ОТДЕЛЬНЫМ шагом (§9.2 п. 2), и у "
        "этого шага свой путь")
    assert read_verdict(custom)["ok"] is True, "вердикт по своему пути не зелёный"
    assert not default_path.exists(), (
        f"при явном --verdict-out дрил написал ЕЩЁ И по умолчанию "
        f"({default_path}). На живой машине это значит, что каждый прогон "
        "сторожей переписывает улику, которую проба читает как настоящий "
        "прогон дрила: «дрил гонялся» станет неотличимо от «тесты гонялись»")


def test_nothing_from_the_decrypted_set_lands_next_to_the_verdict(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него дрил мог бы распаковать набор ПРЯМО в дерево, рядом с вердиктом: переписка клиента открытым текстом легла бы в `state/` — каталог, который уезжает в бэкап, попадает под `git add` по недосмотру и живёт годами."""
    _priv, pub = keypair
    db_path = tmp_path / "source" / "leaky.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    counts = _seed_db(db_path, {"lead-1": 30, "lead-2": 25}, text=LEAK_MARKER)

    db_key = _key(DATE, DB_NAME)
    req_key = _key(DATE, REQ_NAME)
    db_blob = _seal(pub, db_path.read_bytes(), db_key)
    req_blob = _seal(pub, REQUISITES, req_key)
    bucket = FakeBucket({
        db_key: db_blob, req_key: req_blob,
        _manifest_key(DATE): _manifest(
            DATE, [_entry(f"{PSEUDO}/{DB_NAME}", db_blob, counts),
                   _entry(f"{PSEUDO}/{REQ_NAME}", req_blob)], pub=pub),
    })
    _install_bucket(monkeypatch, drill, bucket)

    fake_root = tmp_path / "tree"
    default_path = _install_repo_root(monkeypatch, drill, fake_root)

    # Прогон БЕЗ --verdict-out: ближе к дереву дрил не подходит вообще. Ровно
    # здесь и проверяем, что дальше вердикта он в дерево ничего не принёс.
    with _live_tree_verdict_untouched():
        rc = _run(drill, key_file)

    assert rc == 0, f"зелёный набор без --verdict-out дал rc={rc}"
    assert default_path.is_file(), f"вердикта нет по пути {default_path}"

    here = _plaintext_leaks(default_path.parent)
    assert here == [], (
        f"рядом с вердиктом ({default_path.parent}) остался расшифрованный "
        f"клиентский набор: {here[:10]}.\nВердикт в дереве законен — это пять "
        "полей статуса, и проба читает их именно отсюда. Переписка клиента и "
        "его платёжные реквизиты в дереве незаконны ничем")
    wider = _plaintext_leaks(fake_root)
    assert wider == [], (
        f"в дерево попал открытый клиентский текст: {wider[:10]}. `state/` "
        "уезжает в бэкап набором `jarvis` (§5.3) и живёт годами: утечка "
        "отсюда переживёт и дрил, и машину")
    live = _plaintext_leaks(_ROOT / "state", needles=_UNIQUE_NEEDLES)
    assert live == [], (
        f"открытый клиентский текст оказался в ЖИВОМ дереве: {live[:10]}. "
        f"Шов `{ROOT_ATTR}` подменён, значит дрил считает корень мимо "
        "него")


def test_sandbox_is_swept_after_a_green_run_and_kept_only_with_keep(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него расшифрованная переписка клиента оставалась бы лежать открытым текстом во временном каталоге — §4.3 п. 6 «каталог снести» превратился бы в необязательный."""
    _priv, pub = keypair
    sandbox_root = tmp_path / "sandroot"
    sandbox_root.mkdir()
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(sandbox_root))

    bucket, _counts = _green_bucket(pub, tmp_path)
    _install_bucket(monkeypatch, drill, bucket)

    # --keep: каталог обязан ОСТАТЬСЯ. Заодно это единственное доказательство,
    # что песочница вообще живёт здесь, — без него проверка «пусто после
    # прогона» была бы зелёной просто потому, что дрил работал не тут.
    rc_keep = _run(drill, key_file, verdict_out=tmp_path / "out" / "k.json", keep=True)
    assert rc_keep == 0, f"зелёный набор с --keep дал rc={rc_keep}"
    kept = _files_under(sandbox_root)
    assert kept, (
        f"с --keep в {sandbox_root} не осталось НИ ОДНОГО файла. Либо --keep "
        "ничего не держит, либо песочница живёт не во временном каталоге "
        "(TMPDIR/TEMP/TMP) — тогда проверка «после прогона пусто» ничего не "
        "проверяет и назовите шов в контракте")

    shutil.rmtree(sandbox_root)
    sandbox_root.mkdir()

    rc = _run(drill, key_file, verdict_out=tmp_path / "out" / "verdict.json")
    assert rc == 0, f"зелёный набор без --keep дал rc={rc}"
    left = _files_under(sandbox_root)
    assert left == [], (
        f"после зелёного прогона в песочнице осталось {len(left)} файл(ов): "
        f"{left[:10]}. Там лежит расшифрованная переписка клиента открытым "
        "текстом; §4.3 п. 6 — «каталог снести», и снос не зависит от исхода")


def _red_bucket(case: str, pub: bytes, tmp_path: Path) -> FakeBucket:
    """Набор, на котором дрил обязан вернуть rc=1, и в котором есть чему утечь.

    В базе лежит узнаваемый текст переписки, рядом — платёжные реквизиты:
    сторож на снос ищет ИХ, а не «каталог существует»."""
    db_path = tmp_path / "source" / f"red-{case}.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()      # второй вызов не должен досыпать сообщений
    contacts = {"lead-1": 12, "lead-2": 9}

    if case == "count_mismatch":
        # Величины манифеста разошлись с базой: дрил ОТРАБОТАЛ и нашёл дефект.
        promised = dict(_seed_db(db_path, contacts, text=LEAK_MARKER))
        promised["messages"] += 2
    elif case == "product_refuses":
        # Величины сходятся, но настоящий слой хранения базу не открывает.
        promised = _legacy_payments_db(db_path, contacts, text=LEAK_MARKER)
    else:
        raise AssertionError(f"неизвестный случай {case}")

    db_key = _key(DATE, DB_NAME)
    req_key = _key(DATE, REQ_NAME)
    db_blob = _seal(pub, db_path.read_bytes(), db_key)
    req_blob = _seal(pub, REQUISITES, req_key)
    entries = [_entry(f"{PSEUDO}/{DB_NAME}", db_blob, promised),
               _entry(f"{PSEUDO}/{REQ_NAME}", req_blob)]
    return FakeBucket({db_key: db_blob, req_key: req_blob,
                       _manifest_key(DATE): _manifest(DATE, entries, pub=pub)})


@pytest.mark.parametrize("case", [
    "count_mismatch",      # база открылась, величины не сошлись
    "product_refuses",     # база не открылась настоящим слоем хранения
])
def test_the_sandbox_is_swept_after_a_red_run_too(
        case, tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него расшифрованная переписка клиента оставалась бы открытым текстом ровно после тех прогонов, за которыми идёт разбор: снос песочницы (§4.3 п. 6, амендмент Б) обязан быть на ЛЮБОМ пути выхода, а зелёный — единственный, где о нём вспоминают."""
    _priv, pub = keypair
    # Два РАЗНЫХ каталога, а не один вычищенный между прогонами: файл, который
    # дрил забыл закрыть, на Windows не удаляется, и уборка после якоря
    # приезжала бы WinError 32 вместо внятного красного.
    anchor_root = tmp_path / "sandroot-keep"
    sandbox_root = tmp_path / "sandroot"
    anchor_root.mkdir()
    sandbox_root.mkdir()

    # (а) ЯКОРЬ. Тот же красный прогон с `--keep` обязан ОСТАВИТЬ открытый
    #     текст. Без якоря «после прогона пусто» зелено по построению: дрил
    #     мог работать в другом каталоге или вовсе не дойти до распаковки.
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(anchor_root))
    _install_bucket(monkeypatch, drill, _red_bucket(case, pub, tmp_path))
    rc_keep = _run(drill, key_file, verdict_out=tmp_path / "out" / "keep.json",
                   keep=True)
    assert rc_keep == 1, (
        f"случай {case!r} с --keep дал rc={rc_keep}, ожидалось 1: набор красен "
        "по построению, и якорь обязан стоять именно на КРАСНОМ прогоне")
    leaked = _plaintext_leaks(anchor_root)
    assert leaked, (
        f"случай {case!r}: с --keep в {anchor_root} не нашлось НИ ОДНОГО "
        f"открытого байта клиентского набора (файлов там "
        f"{len(_files_under(anchor_root))}). Либо --keep ничего не держит, "
        "либо песочница живёт не во временном каталоге (TMPDIR/TEMP/TMP) — "
        "тогда назовите шов в контракте: иначе проверка «после прогона пусто» "
        "ничего не проверяет")

    # (б) Тот же набор без `--keep`, в чистом каталоге.
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(sandbox_root))
    _install_bucket(monkeypatch, drill, _red_bucket(case, pub, tmp_path))
    verdict_path = tmp_path / "out" / "verdict.json"
    rc = _run(drill, key_file, verdict_out=verdict_path)

    assert rc == 1, (
        f"случай {case!r} дал rc={rc}, ожидалось 1. Сторож про снос песочницы "
        "после КРАСНОГО прогона, и прогон обязан быть красным — иначе "
        "проверяется не тот путь выхода")
    assert read_verdict(verdict_path)["ok"] is False, (
        f"случай {case!r}: rc=1, а вердикт зелёный — красное не состоялось")

    left = _plaintext_leaks(sandbox_root)
    assert left == [], (
        f"случай {case!r}: после КРАСНОГО прогона в песочнице остался ОТКРЫТЫЙ "
        f"клиентский текст: {left[:10]}.\nЭто расшифрованная база клиента и "
        "его платёжные реквизиты, оставленные на диске машины, где дрил всего "
        "лишь проверял бэкап. Снос (§4.3 п. 6) от исхода не зависит: "
        "единственное исключение — явный --keep")
    assert _files_under(sandbox_root) == [], (
        f"случай {case!r}: файлы в песочнице остались: "
        f"{_files_under(sandbox_root)[:10]}. Открытого текста сторож в них не "
        "нашёл, но каталог обязан быть снесён ЦЕЛИКОМ — то, что уцелело "
        "сегодня, завтра окажется распакованной базой")


@pytest.mark.parametrize("kind", ["dotdot_posix", "dotdot_windows", "absolute"])
def test_a_manifest_pointing_outside_the_set_is_refused_and_leaves_nothing(
        kind, tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него дрил раскладывал бы объекты ПО ИМЕНАМ ИЗ МАНИФЕСТА: `rel_path` с выходом наверх кладёт расшифрованную базу клиента ВНЕ песочницы — туда, где её не достанет уже никакой снос."""
    _priv, pub = keypair
    sandbox_root = tmp_path / "sandroot"
    sandbox_root.mkdir()
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(sandbox_root))

    if kind == "dotdot_posix":
        escape = "../../escaped-posix.db"
    elif kind == "dotdot_windows":
        escape = "..\\..\\escaped-windows.db"
    else:
        escape = (tmp_path / "escaped-absolute.db").as_posix()

    # ВТОРОЙ элемент манифеста уводит наверх, первый — настоящая база. Дрил,
    # разбирающий манифест по порядку, к этому месту уже расшифровал первый
    # объект: отказавшись, он обязан не оставить его на диске. Дрил, дочитавший
    # манифест ДО распаковки, ничего не расшифрует вовсе — и тоже пройдёт;
    # ловится здесь тот, кто путь из манифеста ИСПОЛНЯЕТ.
    bucket, _counts = _green_bucket(pub, tmp_path)
    manifest = json.loads(bucket.objects[_manifest_key(DATE)].decode("utf-8"))
    entries = manifest["files"]
    assert entries[0]["rel_path"].endswith(DB_NAME), (
        f"подготовка: первым в манифесте обязана идти база, а идёт "
        f"{entries[0]['rel_path']!r}")

    escaped_key = f"{PREFIX}/{DATE}/{escape}"
    escaped_blob = _seal(pub, REQUISITES, escaped_key)
    entries[1] = _entry(escape, escaped_blob)
    bucket.objects.pop(_key(DATE, REQ_NAME), None)
    bucket.objects[escaped_key] = escaped_blob
    bucket.objects[_manifest_key(DATE)] = _manifest(DATE, entries, pub=pub)
    _install_bucket(monkeypatch, drill, bucket)

    fresh = tmp_path / "out" / "verdict.json"
    rc = _run(drill, key_file, verdict_out=fresh)

    assert rc == 2, (
        f"манифест называет rel_path={escape!r} — путь ВНЕ набора; rc={rc}, "
        "ожидалось 2. Дрил не восстановил ничего и о бэкапе не узнал ничего: "
        "rc=0 означал бы, что путь исполнен, rc=1 — что бэкап объявлен "
        "негодным без единой проверки величин")
    assert not fresh.exists(), (
        f"вердикт написан там, где ничего не проверяли: "
        f"{fresh.read_text(encoding='utf-8', errors='replace')!r}")

    # Ни в песочнице, ни ЗА ЕЁ ПРЕДЕЛАМИ не осталось открытого текста.
    # `source/` — свой же, фикстурный: там база лежит открытой по построению.
    left = [x for x in _plaintext_leaks(tmp_path) if not x.startswith("source/")]
    assert left == [], (
        f"после отказа остался ОТКРЫТЫЙ клиентский текст: {left[:10]}.\n"
        "Либо песочница не снесена — снос обязан быть на ЛЮБОМ пути выхода, "
        "включая «не состоялся», — либо объект уехал по пути из манифеста, "
        f"наружу песочницы ({escape!r}), где его не снесёт уже ничто")


def test_drill_refuses_a_sandbox_inside_the_live_tree(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него распакованная база клиента могла бы лечь ВНУТРЬ рабочего дерева — а работа в живом дереве равна автодеплою (DEV-31), и гардиан поднял бы из него раннер."""
    _priv, pub = keypair
    sandbox_root = _ROOT / "state" / "backup" / f"guard-sandbox-{os.getpid()}"
    sandbox_root.mkdir(parents=True, exist_ok=True)
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(sandbox_root))

    bucket, _counts = _green_bucket(pub, tmp_path)
    _install_bucket(monkeypatch, drill, bucket)

    try:
        rc = _run(drill, key_file, verdict_out=tmp_path / "out" / "verdict.json")
        assert rc == 2, (
            f"дрил согласился развернуть песочницу в {sandbox_root} и вернул "
            f"rc={rc}. Это дерево репозитория: сюда смотрит гардиан, отсюда "
            "поднимается раннер, и здесь же живут `.secrets/*.db` под живыми "
            "раннерами. Каталог песочницы обязан быть проверен так же, как "
            "путь вердикта")
        assert _files_under(sandbox_root) == [], (
            f"дрил успел положить в живое дерево: "
            f"{_files_under(sandbox_root)[:10]}")
    finally:
        shutil.rmtree(sandbox_root, ignore_errors=True)
        try:
            parent = _ROOT / "state" / "backup"
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass


def test_the_seam_does_not_open_the_live_tree_to_the_sandbox(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него шов амендмента Е стал бы способом ОБОЙТИ отказ: подменил корень — и живое дерево, где `.secrets/*.db` лежат под живыми раннерами, перестало быть запретным для распаковки клиентских данных."""
    _priv, pub = keypair
    _install_repo_root(monkeypatch, drill, tmp_path / "tree")
    bucket, _counts = _green_bucket(pub, tmp_path)
    _install_bucket(monkeypatch, drill, bucket)

    # (а) живое дерево остаётся запретным ДАЖЕ при подменённом `_ROOT`.
    #     Запретная зона обязана считаться от места, где скрипт лежит, а не от
    #     переменной, которую сторож только что подменил: иначе она защищает
    #     ровно до первого теста.
    forbidden = _ROOT / "state" / "backup" / f"guard-seam-{os.getpid()}"
    forbidden.mkdir(parents=True, exist_ok=True)
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(forbidden))
    try:
        rc = _run(drill, key_file, verdict_out=tmp_path / "out" / "a.json")
        assert rc == 2, (
            f"дрил согласился развернуть песочницу в {forbidden} и вернул "
            f"rc={rc}, хотя это ЖИВОЕ дерево репозитория. Подменённый "
            f"`{ROOT_ATTR}` уводит умолчательный путь вердикта — и только его; "
            "ослаблять запретную зону он не имеет права, иначе шов, "
            "заведённый ради сторожей, станет способом обойти отказ")
        assert _plaintext_leaks(forbidden) == [], (
            f"в живом дереве остался открытый клиентский текст: "
            f"{_plaintext_leaks(forbidden)[:10]}")
    finally:
        shutil.rmtree(forbidden, ignore_errors=True)
        with contextlib.suppress(OSError):
            parent = _ROOT / "state" / "backup"
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()

    # (б) и это не «отказывает всегда»: песочница вне обоих деревьев проходит.
    ok_root = tmp_path / "sandroot"
    ok_root.mkdir()
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(ok_root))
    rc2 = _run(drill, key_file, verdict_out=tmp_path / "out" / "b.json")
    assert rc2 == 0, (
        f"песочница в {ok_root} — вне живого дерева и вне подменённого корня, "
        f"а дрил вернул rc={rc2}. Сторож на запретную зону, красный ВЕЗДЕ, "
        "проверяет не зону, а собственную способность отказывать")


# ── 11. ВОЗРАСТ ПРОВЕРЕННОГО НАБОРА (амендмент Ж) ──────────────────────────
#
# Дата без `--date` — это самая свежая дата под `backups/client/`, и сама по
# себе она ни о чём не говорит. Заливка встала (ключи R2 протухли, задача
# выключена, временный корень отравлен) — дрил каждое воскресенье берёт
# последний удачно залитый набор, тот честно расшифровывается, открывается
# настоящим слоем хранения, величины сходятся, вердикт `ok: true`. Лампа на
# хосте ЗЕЛЁНАЯ ВЕЧНО, а бэкапу месяц, и в нём нет ни одного диалога за
# тридцать суток. Это зелёное ПО ПОСТРОЕНИЮ — тот же класс, что вердикт «из
# будущего», зажимавший возраст в ноль.
#
# Устаревший набор — это НАЙДЕННЫЙ дефект (rc=1), а не отказ: проверка
# состоялась, величины измерены, вердикт писать есть чем. DEV-43 здесь ни при
# чём — там не мерили, здесь померили и нашли.

# Порог литерален и назван: суточный ритм заливки, один-два пропуска — сбой
# сети, третий подряд — система. Граница СТРОГАЯ: сторож, загорающийся ровно
# на границе ритма, приучает к тому, что он слегка врёт. Та же форма, что у
# `BUNDLE_MAX_LAG_DAYS` и у порога 10 суток в пробе.
STALE_AFTER_DAYS = 3

# Шов — к ЧАСАМ, а не к дате (амендмент З). Часы у дрила УЖЕ есть: из них
# берётся `ran_at` вердикта. Отдельный источник «сегодня» рядом с ними — это
# два числа на одну вещь ([[jarvis-two-numbers-for-one-thing]]): «когда
# проверяли» и «относительно чего судили возраст» разъехались бы молча.
# Поэтому модульный вызываемый `_now() -> float` (эпоха), читаемый В МОМЕНТ
# ВЫЗОВА, а «сегодня» ВЫВОДИТСЯ из него.
NOW_ATTR = "_now"

# Полдень UTC: дата этой эпохи одна и та же в любом разумном поясе, поэтому
# сторож не покраснеет из-за часового пояса машины. Отстоит от настоящих часов
# на месяцы намеренно — вторые часы в реализации сразу дадут другой ответ.
NOW_EPOCH = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc).timestamp()

# Считается ровно тем же счётом, каким дату набора в ключе пишет хост, — по
# UTC. Локальный пояс дал бы ложное красное на границе суток.
TODAY = datetime.fromtimestamp(NOW_EPOCH, timezone.utc).date()


def _install_now(monkeypatch, drill, epoch: float = NOW_EPOCH) -> float:
    """Подменить ЧАСЫ дрила. Красное здесь — «шва нет», а не «сторож сломался»."""
    assert hasattr(drill, NOW_ATTR), (
        f"{_SCRIPT} не даёт `{NOW_ATTR}()` — часов, из которых берётся и "
        "`ran_at` вердикта, и «сегодня» для суда о возрасте (амендменты Ж, З). "
        "Они обязаны браться вызовом в момент работы, а не `time.time()` "
        "прямо в теле: иначе проверить суд о возрасте можно только настоящими "
        "часами, то есть один раз в сутки и с миганием на полуночи. Второй "
        "источник даты рядом с этими часами заводить нельзя — «сегодня» "
        "выводится из ТОГО ЖЕ значения")
    monkeypatch.setattr(drill, NOW_ATTR, lambda: epoch)
    return epoch


def _dated(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


@pytest.mark.parametrize("days_ago", [4, 10, 31])
def test_a_set_older_than_the_threshold_is_a_found_defect(
        days_ago, tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него лампа на хосте была бы ЗЕЛЁНОЙ ВЕЧНО при остановившейся заливке: дрил каждую неделю брал бы последний удачно залитый набор, тот честно открывался бы, и «восстановление доказано» означало бы «доказано восстановление бэкапа месячной давности»."""
    _priv, pub = keypair
    set_date = _dated(days_ago)
    bucket, counts = _green_bucket(pub, tmp_path, date=set_date)
    _install_bucket(monkeypatch, drill, bucket)
    _install_now(monkeypatch, drill)
    verdict_path = tmp_path / "out" / "verdict.json"

    rc = _run(drill, key_file, date=None, verdict_out=verdict_path)

    assert rc == 1, (
        f"набор за {set_date} при сегодняшнем {TODAY.isoformat()} — это "
        f"{days_ago} суток при пороге {STALE_AFTER_DAYS}; rc={rc}, ожидалось 1. "
        "rc=0 — зелёное по построению: набор откроется и сойдётся по величинам "
        "хоть через год, потому что он и есть последний удачный. rc=2 — тоже "
        "неверно: дрил ОТРАБОТАЛ, величины измерил и нашёл дефект")
    verdict = read_verdict(verdict_path)
    assert verdict["ok"] is False, f"вердикт зелёный на устаревшем наборе: {verdict}"
    assert verdict["actual"].get("messages") == counts["messages"], (
        f"actual.messages={verdict['actual'].get('messages')!r}, а в наборе "
        f"{counts['messages']}. Устаревание — НАЙДЕННЫЙ дефект, а не отказ: "
        "величины обязаны быть измерены и записаны, иначе это вердикт о "
        "невыполненном замере — ровно то, что DEV-43 запрещает")
    detail = str(verdict["detail"])
    for piece, what in ((set_date, "дата набора"),
                        (TODAY.isoformat(), "сегодняшняя дата"),
                        (str(days_ago), "возраст в сутках")):
        assert piece in detail, (
            f"в detail не названо: {what} ({piece}). detail={detail!r}\n"
            "«бэкап устарел» без чисел не говорит, устарел он на сутки или на "
            "месяц, — а это разные действия: подождать или бежать чинить")


@pytest.mark.parametrize("days_ago", [0, 1, STALE_AFTER_DAYS])
def test_a_set_at_or_under_the_threshold_still_passes(
        days_ago, tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него сторож на возраст был бы ПОЛОВИНОЙ сторожа: реализация, объявляющая устаревшим вообще всё, прошла бы красную половину и сделала бы лампу красной вечно — а красная лампа при исправном бэкапе учит не читать лампы."""
    _priv, pub = keypair
    set_date = _dated(days_ago)
    bucket, counts = _green_bucket(pub, tmp_path, date=set_date)
    _install_bucket(monkeypatch, drill, bucket)
    _install_now(monkeypatch, drill)
    verdict_path = tmp_path / "out" / "verdict.json"

    rc = _run(drill, key_file, date=None, verdict_out=verdict_path)

    assert rc == 0, (
        f"набор за {set_date} — это {days_ago} суток при пороге "
        f"{STALE_AFTER_DAYS}; rc={rc}, ожидалось 0. Граница СТРОГАЯ (`>`): "
        f"{STALE_AFTER_DAYS} суток — это ещё не устаревание. Сторож, "
        "загорающийся ровно на границе ритма, приучает к тому, что он слегка "
        "врёт, — и настоящее красное прочтут так же")
    verdict = read_verdict(verdict_path)
    assert verdict["ok"] is True, f"вердикт не зелёный на свежем наборе: {verdict}"
    assert verdict["actual"].get("messages") == counts["messages"], (
        f"величины разошлись: {verdict['actual']} против {counts}")


def test_an_explicit_date_switches_the_age_judgement_off(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него человек, разбирающий старый набор руками, получал бы rc=1 «бэкап негоден» на наборе, который он выбрал НАМЕРЕННО, — и настоящий сигнал устаревания утонул бы среди ложных."""
    _priv, pub = keypair
    set_date = _dated(31)
    bucket, counts = _green_bucket(pub, tmp_path, date=set_date)
    _install_bucket(monkeypatch, drill, bucket)
    _install_now(monkeypatch, drill)
    verdict_path = tmp_path / "out" / "verdict.json"

    rc = _run(drill, key_file, date=set_date, verdict_out=verdict_path)

    assert rc == 0, (
        f"с явным --date {set_date} дрил вернул rc={rc}, ожидалось 0. Явная "
        "дата отменяет суд о возрасте ВОВСЕ, а не смягчает порог: человек, "
        "назвавший дату, знает, что берёт старое, и проверяет он не свежесть, "
        "а восстановимость")
    verdict = read_verdict(verdict_path)
    assert verdict["ok"] is True, f"вердикт не зелёный: {verdict}"
    assert verdict["actual"].get("messages") == counts["messages"], (
        f"величины разошлись: {verdict['actual']} против {counts}")


# Слова, которыми может быть назван второй дефект — «база не открылась».
# Список литеральный: выведенный из реализации, он согласился бы с ней по
# определению и промолчал ровно там, где она забыла назвать второй дефект.
_OPEN_FAILURE_WORDS = ("откры", "открыл", "open", "malformed", "not a database",
                       "store", "база", "sqlite", "поднял")


def test_a_stale_set_that_also_fails_to_open_names_both_defects(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него первый найденный дефект прятал бы второй: починив заливку, увидели бы «снова красное» и решили бы, что чинили не то, — а бэкап всё это время был ещё и нечитаемым."""
    _priv, pub = keypair
    days_ago = 20
    set_date = _dated(days_ago)

    whole = tmp_path / "source" / "whole.db"
    whole.parent.mkdir(parents=True, exist_ok=True)
    counts = _seed_db(whole, {"lead-1": 400}, text_len=512)
    raw = whole.read_bytes()
    assert len(raw) > 64 * 1024, f"база {len(raw)} байт — обрезка может не задеть данных"
    torn = raw[: int(len(raw) * 0.6)]

    probe = tmp_path / "probe-torn.db"
    probe.write_bytes(torn)
    with pytest.raises(sqlite3.DatabaseError):
        with Store(probe) as store:
            store.history("lead-1")

    db_key = _key(set_date, DB_NAME)
    blob = _seal(pub, torn, db_key)
    bucket = FakeBucket({
        db_key: blob,
        _manifest_key(set_date): _manifest(
            set_date, [_entry(f"{PSEUDO}/{DB_NAME}", blob, counts)], pub=pub),
    })
    _install_bucket(monkeypatch, drill, bucket)
    _install_now(monkeypatch, drill)
    verdict_path = tmp_path / "out" / "verdict.json"

    rc = _run(drill, key_file, date=None, verdict_out=verdict_path)

    assert rc == 1, f"устаревший И нечитаемый набор дал rc={rc}, ожидалось 1"
    verdict = read_verdict(verdict_path)
    assert verdict["ok"] is False, f"вердикт зелёный: {verdict}"
    detail = str(verdict["detail"])
    assert set_date in detail and str(days_ago) in detail, (
        f"в detail не назван ВОЗРАСТ ({set_date}, {days_ago} суток): "
        f"{detail!r}")
    low = detail.lower()
    assert any(word in low for word in _OPEN_FAILURE_WORDS), (
        f"в detail назван только возраст, но не второй дефект — база не "
        f"открылась настоящим слоем хранения: {detail!r}\n"
        f"Искали любое из: {_OPEN_FAILURE_WORDS}.\nПервый дефект, прячущий "
        "второй, стоит одного лишнего круга починки: заливку поправят, "
        "красное останется, и решат, что чинили не то")


def test_the_verdict_time_and_the_age_come_from_the_same_clock(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него у дрила завелись бы ВТОРЫЕ часы: `ran_at` от одних, суд о возрасте от других — и «когда проверяли» разошлось бы с «относительно чего судили» молча, а проба на хосте считает возраст вердикта именно по `ran_at`."""
    _priv, pub = keypair
    # Набор ровно на границе ПОДМЕНЁННЫХ часов: при них он свежий (граница
    # строгая), а при настоящих — старше сотни суток. Зелёный rc поэтому
    # доказывает, что возраст судили по шву, а не по вторым часам.
    set_date = _dated(STALE_AFTER_DAYS)
    bucket, counts = _green_bucket(pub, tmp_path, date=set_date)
    _install_bucket(monkeypatch, drill, bucket)
    frozen = _install_now(monkeypatch, drill)
    verdict_path = tmp_path / "out" / "verdict.json"

    real_clock = time.time()
    assert abs(real_clock - frozen) > 86_400, (
        f"подготовка: подменённые часы ({frozen}) слишком близко к настоящим "
        f"({real_clock}) — вторые часы дали бы тот же ответ, и сторож ничего "
        "не проверил бы")

    rc = _run(drill, key_file, date=None, verdict_out=verdict_path)

    assert rc == 0, (
        f"набор за {set_date} при подменённых часах — это {STALE_AFTER_DAYS} "
        f"суток, то есть ещё свежий; rc={rc}, ожидалось 0. По НАСТОЯЩИМ часам "
        "этот же набор давно устарел: rc=1 здесь означает, что суд о возрасте "
        f"смотрит мимо шва `{NOW_ATTR}` — то есть у дрила вторые часы")
    verdict = read_verdict(verdict_path)
    assert verdict["ok"] is True, f"вердикт не зелёный: {verdict}"
    assert verdict["actual"].get("messages") == counts["messages"], (
        f"величины разошлись: {verdict['actual']} против {counts}")

    assert verdict["ran_at"] == pytest.approx(frozen, abs=1.0), (
        f"ran_at={verdict['ran_at']!r}, а часы дрила подменены на {frozen} "
        f"({TODAY.isoformat()} 12:00 UTC).\nВозраст набора судился по шву — "
        "значит и `ran_at` обязан быть из ТОГО ЖЕ вызова часов. Два источника "
        "времени на одну вещь расходятся молча: проба на хосте считает "
        "возраст вердикта именно по `ran_at`, и вердикт, помеченный чужими "
        "часами, будет либо вечно свежим, либо вечно протухшим")
    assert abs(verdict["ran_at"] - real_clock) > 86_400, (
        f"ran_at={verdict['ran_at']} совпал с НАСТОЯЩИМ временем прогона "
        f"({real_clock}), хотя часы подменены. Значит `ran_at` берётся мимо "
        f"`{NOW_ATTR}` — вторыми часами, и они уже разошлись с теми, по "
        "которым судился возраст")


# ── 12. ВОЗРАСТ, КОТОРЫЙ НЕДОКАЗУЕМ (амендмент К) ──────────────────────────
#
# Возраст считается «сегодня минус дата набора». Дату в ключ пишет ХОСТ, судит
# её НОУТБУК. Уйдут часы хоста вперёд — дата набора окажется в будущем,
# возраст станет отрицательным, и порог `CLIENT_SET_MAX_AGE_DAYS` не сработает
# НИКОГДА: весь суд о возрасте (амендмент Ж) гасится молча, лампа снова
# зелёная по построению. Тот же класс, из-за которого вердикт «из будущего»
# стал `unreadable` вместо зажатого в ноль возраста (§9.4).
#
# Допуск нужен и он ровно один: дату пишет хост по UTC, судит ноутбук, и около
# полуночи UTC они законно расходятся на один календарный день. Всё, что
# дальше, — это не разъезд поясов, а сломанные часы, и возраст по такому
# набору НЕДОКАЗУЕМ.

# Литеральные пины порогов. Значения написаны здесь ЧЕЛОВЕКОМ и не
# импортируются: пин, выведенный из реализации, согласен с ней по определению
# и молчит ровно там, где она забыла ([[jarvis-literal-lists-not-introspection]]).
# Задранный допуск ловится и поведением, и этим пином — поведение обходится
# сдвигом одного случая, пин не обходится ничем.
FUTURE_TOLERANCE_DAYS = 1.0

AGE_CONSTANTS: tuple[tuple[str, float, str], ...] = (
    ("CLIENT_SET_MAX_AGE_DAYS", float(STALE_AFTER_DAYS),
     "задранный порог гасит суд о возрасте: заливка встала, а дрил каждую "
     "неделю честно доказывает восстановимость набора месячной давности"),
    ("CLIENT_SET_FUTURE_TOLERANCE_DAYS", FUTURE_TOLERANCE_DAYS,
     "задранный допуск на будущее гасит суд о возрасте ЦЕЛИКОМ: ушедшие "
     "вперёд часы хоста делают возраст отрицательным, и порог не сработает "
     "никогда"),
)


@pytest.mark.parametrize("name,expected,cost", AGE_CONSTANTS,
                         ids=[c[0] for c in AGE_CONSTANTS])
def test_the_age_thresholds_are_pinned_literally(name, expected, cost, drill):
    """Без него порог правится одним числом и молча: поведение обходится сдвигом единственного случая, а число, названное в двух местах, обязано быть исправлено дважды и осознанно."""
    actual = getattr(drill, name, None)
    assert actual is not None, (
        f"{_SCRIPT} не даёт константы `{name}`. Порог обязан быть ИМЕНОВАННОЙ "
        f"константой модуля, а не числом внутри выражения: {cost}")
    assert isinstance(actual, (int, float)) and not isinstance(actual, bool), (
        f"`{name}` = {actual!r} типа {type(actual).__name__}: порог, который "
        "нельзя сравнить арифметикой, порогом не является")
    assert float(actual) == expected, (
        f"`{name}` = {float(actual)}, а контракт называет {expected}.\n"
        f"Чем это оборачивается: {cost}.\n"
        "Число здесь ЛИТЕРАЛЬНОЕ и правится человеком: если порог меняется "
        "осознанно, правок должно быть две — в коде и здесь")


def test_a_set_dated_one_day_ahead_is_still_judged_fresh(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него дрил краснел бы на ЗАКОННОМ разъезде часовых поясов: дату в ключ пишет хост по UTC, судит её ноутбук, и около полуночи UTC они расходятся на календарный день — красная лампа при исправном бэкапе учит не читать лампы."""
    _priv, pub = keypair
    ahead = int(FUTURE_TOLERANCE_DAYS)          # ровно допуск, граница строгая
    set_date = _dated(-ahead)
    bucket, counts = _green_bucket(pub, tmp_path, date=set_date)
    _install_bucket(monkeypatch, drill, bucket)
    _install_now(monkeypatch, drill)
    verdict_path = tmp_path / "out" / "verdict.json"

    rc = _run(drill, key_file, date=None, verdict_out=verdict_path)

    assert rc == 0, (
        f"набор за {set_date} при сегодняшнем {TODAY.isoformat()} — это "
        f"{ahead} сутки ВПЕРЁД, ровно допуск {FUTURE_TOLERANCE_DAYS}; rc={rc}, "
        "ожидалось 0. Допуск существует именно ради этого случая: около "
        "полуночи UTC хост и ноутбук законно называют разные календарные дни")
    verdict = read_verdict(verdict_path)
    assert verdict["ok"] is True, f"вердикт не зелёный на законном разъезде: {verdict}"
    assert verdict["actual"].get("messages") == counts["messages"], (
        f"величины разошлись: {verdict['actual']} против {counts}")


# Чем может быть названа недоказуемость возраста. Список литеральный: слово
# «недоказуем» названо контрактом, остальные — его же формы.
_UNPROVABLE_WORDS = ("недоказ", "не доказ", "недостовер", "unprovable")


def test_a_set_dated_further_ahead_is_a_found_defect_of_unprovable_age(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него ушедшие вперёд часы хоста гасили бы ВЕСЬ суд о возрасте: дата набора в будущем делает возраст отрицательным, порог не срабатывает никогда, и лампа снова зелёная по построению — при заливке, вставшей месяц назад."""
    _priv, pub = keypair
    ahead = int(FUTURE_TOLERANCE_DAYS) + 1      # на сутки за допуск
    set_date = _dated(-ahead)
    bucket, counts = _green_bucket(pub, tmp_path, date=set_date)
    _install_bucket(monkeypatch, drill, bucket)
    _install_now(monkeypatch, drill)
    verdict_path = tmp_path / "out" / "verdict.json"

    rc = _run(drill, key_file, date=None, verdict_out=verdict_path)

    assert rc == 1, (
        f"набор за {set_date} при сегодняшнем {TODAY.isoformat()} — это "
        f"{ahead} суток ВПЕРЁД при допуске {FUTURE_TOLERANCE_DAYS}; rc={rc}, "
        "ожидалось 1.\nrc=0 означает, что допуск задран (или суда о будущем "
        "нет вовсе) — и тогда ушедшие вперёд часы хоста гасят порог "
        "устаревания навсегда. rc=2 тоже неверно: проверка СОСТОЯЛАСЬ, "
        "величины измерены, писать вердикт есть чем")
    verdict = read_verdict(verdict_path)
    assert verdict["ok"] is False, f"вердикт зелёный на наборе из будущего: {verdict}"
    assert verdict["actual"].get("messages") == counts["messages"], (
        f"actual.messages={verdict['actual'].get('messages')!r}, а в наборе "
        f"{counts['messages']}. Недоказуемый возраст — НАЙДЕННЫЙ дефект, а не "
        "отказ: величины обязаны быть измерены и записаны, иначе это вердикт "
        "о невыполненном замере (DEV-43)")
    detail = str(verdict["detail"])
    assert set_date in detail, (
        f"в detail не названа дата набора ({set_date}): {detail!r}. Без неё "
        "непонятно, на сколько ушли часы — на сутки или на год")
    low = detail.lower()
    assert any(word in low for word in _UNPROVABLE_WORDS), (
        f"в detail не сказано, что возраст НЕДОКАЗУЕМ: {detail!r}\n"
        f"Искали любое из: {_UNPROVABLE_WORDS}.\n«Набор устарел» и «возраст "
        "проверить невозможно» — разные вещи и разная починка: во втором "
        "случае чинят ЧАСЫ ХОСТА, а не заливку")


# ── 9. aad проверяется ─────────────────────────────────────────────────────

def test_envelope_sealed_for_another_date_does_not_open(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него проехал бы дрил, расшифровывающий «как получится»: конверт, переставленный на чужую дату или чужого клиента, открылся бы молча, и привязка к месту (§3.4) осталась бы украшением."""
    _priv, pub = keypair
    db_path = tmp_path / "source" / "client.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    counts = _seed_db(db_path, {"lead-1": 70})

    db_key = _key(DATE, DB_NAME)
    # Байты лежат по ключу за DATE, а запечатаны под ключ за OTHER_DATE.
    blob = _seal(pub, db_path.read_bytes(), db_key,
                 aad_key=_key(OTHER_DATE, DB_NAME))
    entry = _entry(f"{PSEUDO}/{DB_NAME}", blob, counts)
    bucket = FakeBucket({
        db_key: blob,
        _manifest_key(DATE): _manifest(DATE, [entry], pub=pub),
    })
    _install_bucket(monkeypatch, drill, bucket)
    calls = _spy_decrypt(monkeypatch, drill)
    verdict_path = tmp_path / "out" / "verdict.json"

    rc = _run(drill, key_file, verdict_out=verdict_path)

    assert calls, (
        "расшифровку даже не пробовали: sha256 шифротекста в манифесте честный, "
        "байты целы — до конверта дойти обязаны")
    assert rc == 1, (
        f"rc={rc}, ожидалось 1. Конверт запечатан под aad {_key(OTHER_DATE, DB_NAME)!r}, "
        f"а лежит по ключу {db_key!r}. Открыться он не может; если открылся — "
        "дрил подставляет в aad что-то своё, и объект, переставленный на "
        "чужое место, проедет молча")
    verdict = read_verdict(verdict_path)
    assert verdict["ok"] is False, f"вердикт зелёный на чужом конверте: {verdict}"


# ── 10. вердикт пригоден для пробы ─────────────────────────────────────────

def test_the_verdict_the_drill_wrote_is_readable_by_the_probe(
        tmp_path, monkeypatch, drill, keypair, key_file, env):
    """Без него две половины арки разъехались бы молча: вердикт, который проба на хосте не может прочесть, равен ОТСУТСТВИЮ вердикта — а отсутствие она читает как `drill_never` и винит выключенный ноутбук, пока бэкап негоден."""
    _priv, pub = keypair
    bucket, _counts = _green_bucket(pub, tmp_path)
    _install_bucket(monkeypatch, drill, bucket)
    verdict_path = tmp_path / "out" / "verdict.json"

    before = time.time()
    rc = _run(drill, key_file, verdict_out=verdict_path)
    after = time.time()

    assert rc == 0, f"зелёный набор дал rc={rc}"
    assert verdict_path.is_file(), f"вердикт не записан: {verdict_path}"

    raw = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert isinstance(raw["ran_at"], (int, float)) and not isinstance(raw["ran_at"], bool), (
        f"ran_at={raw['ran_at']!r} типа {type(raw['ran_at']).__name__}. Проба "
        "считает возраст вердикта арифметикой по этому полю (§9.4): строка "
        "«2026-08-21» её сломает, а `true` проедет как прогон в первую "
        "секунду эпохи — формально годный, предельно устаревший вердикт")
    assert isinstance(raw["ok"], bool), (
        f"ok={raw['ok']!r} типа {type(raw['ok']).__name__}. Строка \"false\" "
        "в Python ИСТИННА: вердикт-провал прочитался бы зелёным")
    assert before - 5 <= raw["ran_at"] <= after + 5, (
        f"ran_at={raw['ran_at']} вне окна прогона [{before}, {after}]. Возраст "
        "считается по времени ПРОГОНА внутри вердикта, а не по mtime файла: "
        "файл переезжает между машинами, и mtime переезда — не время проверки")

    # И ровно то же самое — глазами пробы, её собственным разбором формы.
    parsed = read_verdict(verdict_path)
    assert parsed["ok"] is True
    assert set(("ran_at", "ok", "expected", "actual", "detail")) <= set(parsed)
