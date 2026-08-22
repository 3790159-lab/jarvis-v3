# -*- coding: utf-8 -*-
"""DEV-46: ЗАЛИВКА клиентского набора. Сторожа ОТ СПЕКИ, до реализации.

ЧТО ЗДЕСЬ СТОРОЖИТСЯ. Не примитив шифрования (он в
`tests/test_backup_crypto.py`), не отбор источников (он в
`tests/test_client_data_sources.py`), а КОНВЕЙЕР между ними: что именно
уходит в `upload_file`, под какими ключами, с каким манифестом и что
происходит, когда ключа нет. §7 пп. 1, 3, 4, 6, 8 и §9.1 спеки
`docs/superpowers/specs/2026-08-21-dev46-client-data-backup.md`.

ВЕДУЩИЙ СТОРОЖ — §7 п. 4, «наружу не уходит открытый текст». Он ведущий не
по порядку в файле, а по цене ошибки: не доехавшую базу заметят при
восстановлении, а уехавшую открытым текстом переписку не заметит никто и
никогда. Проверяется по СОДЕРЖИМОМУ того, что ушло бы в загрузку, а не по
имени файла и не по наличию вызова шифрования: вызов, результат которого не
используется, выглядит в коде совершенно нормально.

ПОЧЕМУ ПРОВЕРКА ИДЁТ ЧЕРЕЗ ПЕРЕХВАТ `upload_file`, А НЕ ЧЕРЕЗ ВОЗВРАЩЁННЫЙ
`BackupResult`. `BackupResult` — это рассказ конвейера о себе. Байты, отданные
в загрузку, — это то, что реально оказалось бы в чужом хранилище. Расхождение
между рассказом и байтами и есть тот класс дефекта, ради которого §3 написан
([[jarvis-two-numbers-for-one-thing]]).

ЧЕГО ЗДЕСЬ НЕТ. Ни одного обращения в R2 (`upload_file`/`list_objects`
подставные), ни одного живого файла: деревья строятся в `tmp_path`, живой
`.secrets/` не читается ни разу, боевые `.secrets/*.db` не открываются.
Ключевая пара порождается в тесте (`generate_keypair`) — на диске её нет ни
до, ни после.

ГДЕ КОНТРАКТ ДОПУСКАЕТ ДВА ЧТЕНИЯ — выбран строгий вариант, и это сказано
словами в докстроке соответствующего теста, а не спрятано в assert'е.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services import backup_crypto as bc
from app.services import state_backup as sb
from app.services.r2_storage import R2Config
from chatter.storage.db import Store

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "state_backup.py"

# ── Контракт ЛИТЕРАЛЬНО ──────────────────────────────────────────────────────
# Ни одна константа ниже не импортируется из реализации. Значение, взятое из
# кода, согласно с кодом по определению и молчит ровно там, где код забыл
# ([[jarvis-literal-lists-not-introspection]]). Ключ объекта — не деталь
# реализации, а обещание наружу: по нему ходят ротация, `verify_uploaded` и
# дрил восстановления.
CLIENT_PREFIX = "backups/client"
STATE_PREFIX = "backups/state"

PUBLIC_KEY_ENV = "JARVIS_BACKUP_PUBLIC_KEY"
SALT_ENV = "JARVIS_BACKUP_KEY_SALT"

DATE = "2026-08-22"
OTHER_DATE = "2026-08-21"
NOW = datetime(2026, 8, 22, 4, 0, 0, tzinfo=timezone.utc)
OTHER_NOW = datetime(2026, 8, 21, 4, 0, 0, tzinfo=timezone.utc)

# ── Синтетические слаги ──────────────────────────────────────────────────────
# НЕ живые (`volska`/`yarina`) намеренно: сторож не имеет права зависеть от
# того, кого сегодня включили в боевом реестре, и не должен давать повода
# заглянуть в живой `.secrets/`. В обоих слагах есть буквы вне hex-алфавита
# («l», «p», «h», «t»), поэтому найти слаг внутри шестнадцатеричного
# псевдонима случайно нельзя — красное в тесте §7 п. 8 будет настоящим.
SLUG_A = "alpha"
SLUG_B = "beta"
SLUGS: tuple[str, ...] = (SLUG_A, SLUG_B)

# (диалогов, сообщений) на слаг — РАЗНЫЕ, чтобы перепутанные местами величины
# манифеста нельзя было принять за совпавшие.
DB_SHAPE: dict[str, tuple[int, int]] = {SLUG_A: (2, 3), SLUG_B: (1, 2)}
TS_BASE = 1_700_000_000.0

# Узнаваемые строки. Если хоть одна из них найдётся в том, что ушло в
# загрузку, — наружу поехал открытый текст.
MARKER_TEXT = "ОЧЕНЬ-СЕКРЕТНАЯ-ПЕРЕПИСКА"
MARKER_ACCOUNT = "UA903052992990004149123456789"
SQLITE_MAGIC = b"SQLite format 3"

# Соседи по каталогу: ровно те файлы, которые утащил бы широкий глоб, и то,
# чем оборачивается утечка каждого. Экземпляры, а не шаблоны.
NEIGHBOURS: tuple[tuple[str, str], ...] = (
    (f".secrets/{SLUG_A}.session",
     "session-файл Telethon = полный доступ к аккаунту клиента без пароля, "
     "немедленно и молча"),
    (f".secrets/{SLUG_B}.session",
     "то же самое для второго клиента: сторож на одном примере слеп ко "
     "второму"),
    (".secrets/entropy.bin",
     "материал опознания: из него выводятся ключи, которыми зашифрованы "
     "остальные секреты машины"),
    (".env",
     "все ключи хоста разом — R2, Telegram, Anthropic; лежит в корне того "
     "самого дерева, которое обходит новый корень"),
)

# Что каждое отсутствующее имя обязано уметь — текстом, чтобы красное
# называло не только «нет атрибута», но и зачем он был нужен.
_CONTRACT: dict[str, str] = {
    "run_client_backup": (
        "run_client_backup(repo_root, *, now, env, upload_file, list_objects, "
        "client, config) -> BackupResult — заливка клиентского набора"),
    "client_object_keys": (
        "client_object_keys(slug_pseudonym, date_str) -> dict с ключами "
        f"{CLIENT_PREFIX}/<дата>/<псевдоним>/db и .../requisites.yaml"),
    "ClientBackupRefused": (
        "ClientBackupRefused — фейл-клоуз: без публичного ключа, без соли "
        "псевдонимов или с временным корнем в запретной зоне заливка не "
        "начинается ВООБЩЕ (амендмент Д)"),
}


def _need(name: str):
    """Имя из контракта или ГРОМКИЙ отказ с объяснением, зачем оно.

    `pytest.importorskip`/`skip` здесь были бы враньём: пропущенный сторож
    выглядит в отчёте так же, как сторож, которому нечего сказать.
    """
    obj = getattr(sb, name, None)
    if obj is None:
        raise AttributeError(
            f"`app.services.state_backup.{name}` не существует. Контракт "
            f"DEV-46: {_CONTRACT.get(name, name)}"
        )
    return obj


# ── подставной бакет ─────────────────────────────────────────────────────────
class _Bucket:
    """Всё, что ушло бы наружу, и ничего сверх того.

    Байты читаются ВНУТРИ `upload_file`, а не после прогона: манифест
    заливается из временного файла, который конвейер тут же удаляет
    (`unlink` в `finally` у `run_backup`), и «прочитаем потом» не увидело бы
    ровно того объекта, который не шифруется.
    """

    def __init__(self) -> None:
        self.blobs: list[tuple[str, bytes]] = []   # (ключ, байты) по порядку
        self.objects: dict[str, bytes] = {}
        self.list_prefixes: list[str] = []

    def upload_file(self, path, key=None, *, client=None, config=None, **kwargs):
        if not key:
            raise AssertionError(
                f"upload_file вызван без ключа объекта (path={path!r}). Ключ "
                f"клиентского объекта обязан быть {CLIENT_PREFIX}/<дата>/"
                f"<псевдоним>/<имя>, иначе ротация и дрил его не найдут."
            )
        data = Path(path).read_bytes()
        self.blobs.append((key, data))
        self.objects[key] = data
        return key

    def list_objects(self, prefix="", *, client=None, config=None, **kwargs):
        self.list_prefixes.append(prefix)
        return sorted(
            ({"key": k, "size": len(v)} for k, v in self.objects.items()
             if k.startswith(prefix)),
            key=lambda o: o["key"],
        )

    def client_keys(self) -> list[str]:
        return sorted(k for k in self.objects if k.startswith(CLIENT_PREFIX + "/"))

    def data_keys(self) -> list[str]:
        """Ключи объектов с данными: всё, кроме манифеста."""
        return [k for k in self.client_keys() if not k.endswith("/manifest.json")]


def _fake_config() -> R2Config:
    """Конфиг-заглушка. Передаётся ЯВНО, чтобы конвейер ни при каком раскладе
    не сходил в `load_backup_config()` за живыми ключами R2 из окружения."""
    return R2Config(
        account_id="acc-test",
        access_key_id="ak-test",
        secret_access_key="sk-test",
        bucket="bucket-test",
        endpoint="https://r2.invalid",
        public_base_url="",
    )


# ── ключи ────────────────────────────────────────────────────────────────────
def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


@pytest.fixture
def keys(monkeypatch):
    """Свежая пара X25519 и соль псевдонимов — только в памяти теста.

    Значения кладутся И в словарь `env`, И в `os.environ`: контракт даёт
    конвейеру параметр `env`, но сторож на «наружу не уходит открытый текст»
    не должен краснеть по причине «реализация читает окружение напрямую» —
    это другой дефект и другой разговор.
    """
    private, public = bc.generate_keypair()
    salt = b"\x11" * 24
    env = {PUBLIC_KEY_ENV: _b64(public), SALT_ENV: _b64(salt)}
    monkeypatch.setenv(PUBLIC_KEY_ENV, env[PUBLIC_KEY_ENV])
    monkeypatch.setenv(SALT_ENV, env[SALT_ENV])
    return {"private": private, "public": public, "salt": salt, "env": env}


# ── дерево в tmp_path ────────────────────────────────────────────────────────
def _registry_text(enabled: tuple[str, ...] = SLUGS) -> str:
    lines = ["clients:"]
    for slug in SLUGS:
        lines.append(f"  {slug}:")
        lines.append(f"    enabled: {'true' if slug in enabled else 'false'}")
        lines.append(f"    personas: [{slug}]")
        lines.append(f"    session: .secrets/{slug}.session")
        lines.append(f"    db: .secrets/{slug}.db")
    return "\n".join(lines) + "\n"


def _build_db(path: Path, slug: str) -> float:
    """База НАСТОЯЩИМ слоем хранения, а не руками через sqlite3.

    Величины манифеста и дрил считаются по таблицам `contacts` и `messages`,
    и схему им обязан задавать тот же `chatter.storage.db.Store`, который
    поднимает продукт. Возвращает `ts` последнего сообщения.
    """
    dialogs, messages = DB_SHAPE[slug]
    path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(path)
    last = None
    try:
        for d in range(dialogs):
            store.get_or_create_contact(f"{slug}-contact-{d}")
        for i in range(messages):
            ts = TS_BASE + i
            store.add_message(f"{slug}-contact-{i % dialogs}", "user",
                              f"{MARKER_TEXT} {slug} #{i}", ts)
            last = ts
    finally:
        store.close()
    return last


def _requisites_text(slug: str) -> str:
    return (
        "bank:\n"
        f"  holder: ФОП {slug}\n"
        f"  iban: {MARKER_ACCOUNT}\n"
        "  currency: UAH\n"
    )


def _repo_tree(tmp_path: Path, *, enabled: tuple[str, ...] = SLUGS,
               without_requisites: tuple[str, ...] = ()) -> Path:
    """Корень репозитория, где данные и секреты лежат ВПЕРЕМЕШКУ.

    Так же, как на живом диске: `.secrets/alpha.db` и `.secrets/alpha.session`
    отличаются одним хвостом имени и лежат в одном каталоге.
    """
    root = tmp_path / "repo"
    for slug in SLUGS:
        _build_db(root / ".secrets" / f"{slug}.db", slug)
        if slug not in without_requisites:
            req = root / "chatter" / "clients" / slug / "requisites.yaml"
            req.parent.mkdir(parents=True, exist_ok=True)
            req.write_text(_requisites_text(slug), encoding="utf-8")
    for rel, _why in NEIGHBOURS:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"секрет-соседа {rel}", encoding="utf-8")
    reg = root / "chatter" / "clients" / "registry.yaml"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(_registry_text(enabled), encoding="utf-8")
    return root


def _run(root: Path, bucket: _Bucket, env: dict, *, now: datetime = NOW):
    return _need("run_client_backup")(
        root,
        now=now,
        env=env,
        upload_file=bucket.upload_file,
        list_objects=bucket.list_objects,
        config=_fake_config(),
    )


# ── ключи объектов и конверты ────────────────────────────────────────────────
def _db_key(pseudonym: str, date: str = DATE) -> str:
    return f"{CLIENT_PREFIX}/{date}/{pseudonym}/db"


def _req_key(pseudonym: str, date: str = DATE) -> str:
    return f"{CLIENT_PREFIX}/{date}/{pseudonym}/requisites.yaml"


def _manifest_key(date: str = DATE) -> str:
    return f"{CLIENT_PREFIX}/{date}/manifest.json"


def _aad_candidates(key: str) -> list[bytes]:
    """Чем МОГ БЫ быть `aad` этого объекта.

    Докстрока `backup_crypto` говорит «ключ объекта» дословно, и это первый
    кандидат. Остальные — те же данные без префикса и без даты: сторож не
    обязан знать, где именно реализация обрезала строку, он обязан требовать,
    чтобы привязка БЫЛА. Что она есть, доказывает перекрёстная проверка ниже,
    а не этот список.
    """
    rest = key[len(CLIENT_PREFIX) + 1:] if key.startswith(CLIENT_PREFIX + "/") else key
    tail = rest.split("/", 1)[1] if "/" in rest else rest
    return [c.encode("utf-8") for c in (key, rest, tail)]


def _open_object(private: bytes, key: str, blob: bytes) -> tuple[bytes, bytes]:
    """Открыть конверт его законным `aad`. Возвращает `(данные, aad)`."""
    tried: list[str] = []
    for aad in _aad_candidates(key):
        try:
            return bc.decrypt_with(private, blob, aad=aad), aad
        except bc.BackupCryptoError as exc:
            tried.append(f"  aad={aad!r} -> {exc}")
    pytest.fail(
        f"объект {key} не открывается приватным ключом НИ ПРИ ОДНОМ ожидаемом "
        f"`aad`.\nЛибо конверт зашифрован не тем ключом, либо привязка сделана "
        f"не из ключа объекта — тогда её нельзя воспроизвести на дриле, и "
        f"годовой набор нечитаем.\nЧто пробовали:\n" + "\n".join(tried),
        pytrace=False,
    )


def _must_not_open(private: bytes, blob: bytes, aad: bytes, *, what: str) -> None:
    try:
        opened = bc.decrypt_with(private, blob, aad=aad)
    except bc.BackupCryptoError:
        return
    pytest.fail(
        f"{what}\n`aad`={aad!r} открыл ЧУЖОЙ конверт ({len(opened)} Б). Значит "
        f"привязки к месту нет: конверт одной даты/одного клиента "
        f"расшифровывается на месте другого. Переставленный объект тогда "
        f"неотличим от своего, и дрил докажет восстановление не того набора.",
        pytrace=False,
    )


def _manifest(bucket: _Bucket, date: str = DATE) -> tuple[dict, str]:
    key = _manifest_key(date)
    raw = bucket.objects.get(key)
    if raw is None:
        pytest.fail(
            f"манифеста {key} в бакете нет.\nБез него хост не может доказать "
            f"даже «байты доехали» (§9.2 п. 3), а дрилу нечего сверять с "
            f"восстановленной базой.\nЧто залито: {bucket.client_keys()}",
            pytrace=False,
        )
    text = raw.decode("utf-8")
    return json.loads(text), text


# ══ 1. НИ ОДНОГО ОТКРЫТОГО БАЙТА НАРУЖУ (§7 п. 4) ════════════════════════════
def _marker_forms(marker: str) -> list[tuple[str, bytes]]:
    """Одна и та же строка в тех видах, в которых она могла бы уехать.

    utf-8 — как лежит в SQLite и в yaml; utf-16 — как её отдаст сериализатор,
    писавший через Windows-API; cp1251 — как её запишет консоль без
    `PYTHONUTF8`; base64 — как её «спрячет» реализация, перепутавшая
    кодирование с шифрованием.
    """
    forms = [("utf-8", marker.encode("utf-8")),
             ("utf-16-le", marker.encode("utf-16-le")),
             ("utf-16-be", marker.encode("utf-16-be")),
             ("base64(utf-8)", base64.b64encode(marker.encode("utf-8")))]
    try:
        forms.append(("cp1251", marker.encode("cp1251")))
    except UnicodeEncodeError:
        pass
    return forms


def test_not_one_open_byte_of_client_data_leaves_the_machine(tmp_path, keys):
    """Без него молча проехало бы: набор уехал в чужое хранилище ОТКРЫТЫМ.

    Ведущий сторож арки. Дефект выглядит как забытая строка (`upload_file`
    получил исходный путь, а не путь конверта) и НЕ виден ни в одном отчёте:
    заливка зелёная, манифест зелёный, листинг зелёный. Обнаружился бы он
    ровно один раз — когда бакет прочитает кто-то посторонний.
    """
    root = _repo_tree(tmp_path)
    bucket = _Bucket()
    _run(root, bucket, keys["env"])

    assert bucket.blobs, (
        "в загрузку не ушло НИ ОДНОГО байта: сторож на открытый текст на "
        "пустом прогоне зелен по построению, и это не проверка, а её "
        "видимость. Клиентский набор не собрался вовсе."
    )

    where = {key: len(data) for key, data in bucket.blobs}

    for marker, why in ((MARKER_TEXT, "текст сообщения из истории воронки"),
                        (MARKER_ACCOUNT, "номер счёта из платёжных реквизитов")):
        for form, needle in _marker_forms(marker):
            hits = [key for key, data in bucket.blobs if needle in data]
            assert not hits, (
                f"наружу уехал ОТКРЫТЫЙ ТЕКСТ: {why} — «{marker}» в виде "
                f"{form} найден в объектах {hits}.\n"
                f"Всё, что ушло в upload_file: {where}.\n"
                f"Требование §3.1: сквозное шифрование ПОД НАШИМ ключом до "
                f"загрузки; at-rest у провайдера от утечки наших же ключей "
                f"доступа не защищает."
            )

    hits = [key for key, data in bucket.blobs if SQLITE_MAGIC in data]
    assert not hits, (
        f"в бакет уехал файл SQLite как есть: заголовок {SQLITE_MAGIC!r} "
        f"найден в {hits}.\nЭто вся история воронки клиента открытым текстом "
        f"— достаточно скачать объект и открыть его чем угодно.\n"
        f"Всё, что ушло в upload_file: {where}."
    )

    # Обратная сторона: манифест открытым БЫТЬ ОБЯЗАН (§3.4), иначе целостность
    # нельзя проверить, не открыв содержимое. Если зашифровано ВСЁ, включая
    # манифест, — проверка выше зелёная по неправильной причине.
    manifest, _text = _manifest(bucket)
    assert isinstance(manifest.get("files"), list), (
        f"манифест не читается как JSON-объект с полем `files`: {manifest!r}.\n"
        f"Манифест шифроваться НЕ должен (§3.4) — он нужен, чтобы проверить "
        f"целостность, НЕ открывая содержимое. Иначе тест на открытый текст "
        f"выше зеленеет просто потому, что зашифровано вообще всё."
    )


# ══ 2. ФЕЙЛ-КЛОУЗ НА КЛЮЧЕ (§3.3, §9.1) ═════════════════════════════════════
@pytest.mark.parametrize(
    "missing,why",
    [(PUBLIC_KEY_ENV, "шифровать нечем — набор поехал бы открытым"),
     (SALT_ENV, "псевдоним собрать нечем — в ключ объекта поехал бы слаг")],
    ids=[PUBLIC_KEY_ENV, SALT_ENV],
)
def test_a_missing_key_refuses_and_uploads_nothing(tmp_path, keys, monkeypatch,
                                                   missing, why):
    """Без него молча проехало бы: «ключа нет — значит, поедем без шифрования».

    Две половины, и вторая важнее. «Упало» без «ничего не ушло» — это не
    фейл-клоуз: конвейер, который залил файл и упал на манифесте, уже отдал
    наружу всё, что собирался. Поэтому счётчик вызовов `upload_file`
    проверяется явно, а не подразумевается из исключения.
    """
    refused = _need("ClientBackupRefused")
    root = _repo_tree(tmp_path)
    bucket = _Bucket()
    env = {k: v for k, v in keys["env"].items() if k != missing}
    monkeypatch.delenv(missing, raising=False)

    with pytest.raises(refused) as excinfo:
        _run(root, bucket, env)

    assert bucket.blobs == [], (
        f"без {missing} заливка ОТКАЗАЛА, но {len(bucket.blobs)} объект(ов) "
        f"уже ушли в бакет: {[k for k, _ in bucket.blobs]}.\n"
        f"«Упало» без «ничего не ушло» фейл-клоузом не является: {why}."
    )
    assert missing in str(excinfo.value), (
        f"отказ не называет ПРИЧИНУ: в тексте `{excinfo.value}` нет "
        f"`{missing}`.\nВ 04:00 читать это будет не автор кода, а владелец по "
        f"алерту, и «ClientBackupRefused» без имени переменной не подсказывает, "
        f"что чинить."
    )


def test_a_refusal_is_not_confused_with_an_ordinary_failure():
    """Без него молча проехало бы: отказ и поломка приехали одним классом.

    Скрипт обязан различить «ключа нет» (rc НЕ портим) и «заливка упала»
    (rc ненулевой) — §7 п. 12. Склеенные наследованием, они станут одним
    `except` и одним решением, и настоящую поломку простят вместе с
    ненастроенным ключом ([[jarvis-blocked-verdict-swallows-the-red]]).
    """
    refused = _need("ClientBackupRefused")
    assert isinstance(refused, type) and issubclass(refused, Exception), (
        f"`ClientBackupRefused` не класс-исключение, а {refused!r}."
    )
    for foreign in (sb.ForbiddenTravel, sb.RetentionError, bc.BackupCryptoError):
        assert not issubclass(refused, foreign) and not issubclass(foreign, refused), (
            f"`ClientBackupRefused` и `{foreign.__name__}` связаны наследованием: "
            f"`except` на один поймает другой, и «ключа не настроили» станет "
            f"неотличимо от «в отбор попал секрет»."
        )


# ══ 3. СЛАГА НЕТ НИГДЕ + ПСЕВДОНИМ СТАБИЛЕН (§7 п. 8, §9.1) ═════════════════
def test_no_slug_appears_in_any_key_or_in_the_manifest(tmp_path, keys):
    """Без него молча проехало бы: конверт запечатан, адрес надписан снаружи.

    Тот, кто получил доступ к бакету, читает список чужих бизнесов, не открыв
    ни одного объекта, — ровно то, от чего ответ 4 владельца защищает
    манифест. Поиск идёт по ПОЛНОМУ ключу: `.../<дата>/<slug>/db` прячет слаг
    от проверки хвоста и не прячет ни от кого больше.

    Вторая половина в том же тесте (§7 п. 8 дословно): псевдоним ОДНОГО слага
    одинаков в двух прогонах подряд. Плавающий псевдоним теряет объект для
    ротации, `verify_uploaded` и дрила — годовой набор превращается в 365
    сирот.
    """
    root = _repo_tree(tmp_path)
    first = _Bucket()
    _run(root, first, keys["env"])
    manifest, manifest_text = _manifest(first)

    assert first.client_keys(), (
        f"под префиксом {CLIENT_PREFIX}/ не оказалось ни одного объекта — "
        f"искать слаг не в чем. Залито: {sorted(first.objects)}"
    )

    for slug in SLUGS:
        hits = [k for k in first.client_keys() if slug in k]
        assert not hits, (
            f"слаг `{slug}` виден в ПОЛНОМ ключе объекта: {hits}.\n"
            f"Слаг — это имя чужого бизнеса, и оно читается листингом бакета "
            f"без единого скачивания (§9.1). В ключ обязан ехать "
            f"`pseudonym(slug, salt)`."
        )
        assert slug not in manifest_text, (
            f"слаг `{slug}` найден в теле манифеста {_manifest_key()}.\n"
            f"Манифест НЕ шифруется и лежит рядом с перепиской (§3.4): "
            f"положить туда слаг — значит отдать список клиентов открытым "
            f"текстом.\nМанифест: {manifest_text[:400]}"
        )
        for entry in manifest.get("files", []):
            rel = str(entry.get("rel_path", ""))
            assert slug not in rel, (
                f"слаг `{slug}` попал в `rel_path` записи манифеста: {rel!r}."
            )

    # ПСЕВДОНИМ СТАБИЛЕН: те же слаги, та же соль, та же дата — те же ключи.
    second = _Bucket()
    _run(root, second, keys["env"])
    assert second.client_keys() == first.client_keys(), (
        f"два прогона подряд дали РАЗНЫЕ ключи объектов.\n"
        f"первый:  {first.client_keys()}\nвторой:  {second.client_keys()}\n"
        f"Псевдоним обязан быть стабильным (§9.1): по ключам ходят ротация, "
        f"`verify_uploaded` и дрил, который ищет ВЧЕРАШНИЙ объект. Плавающий "
        f"псевдоним не теряет данные — он теряет способность их найти."
    )


# ══ 4. AAD ПРИВЯЗЫВАЕТ КОНВЕРТ К МЕСТУ ══════════════════════════════════════
def test_the_envelope_does_not_open_in_another_place(tmp_path, keys):
    """Без него молча проехало бы: зашифровали, но не привязали к месту.

    `encrypt_for` требует `aad` без значения по умолчанию — значит передать
    туда константу (`b""`, имя схемы, что угодно неизменное) технически можно,
    и выглядит это в коде совершенно обычно. Тест не гадает, ЧТО именно
    положили в `aad`: он берёт `aad`, который открыл СВОЙ объект, и требует,
    чтобы тот же `aad` НЕ открыл объект другой даты и другого клиента.
    Константа проваливает обе проверки сразу.
    """
    private = keys["private"]
    salt = keys["salt"]
    root = _repo_tree(tmp_path)

    today, yesterday = _Bucket(), _Bucket()
    _run(root, today, keys["env"], now=NOW)
    _run(root, yesterday, keys["env"], now=OTHER_NOW)

    pa = bc.pseudonym(SLUG_A, salt)
    pb = bc.pseudonym(SLUG_B, salt)
    key_a = _db_key(pa, DATE)
    assert key_a in today.objects, (
        f"объекта {key_a} в бакете нет — привязку проверять не на чем. "
        f"Залито: {today.client_keys()}"
    )

    plain, aad = _open_object(private, key_a, today.objects[key_a])
    assert MARKER_TEXT.encode("utf-8") in plain, (
        f"конверт {key_a} открылся, но внутри не история воронки клиента "
        f"`{SLUG_A}`: маркера «{MARKER_TEXT}» в расшифрованных {len(plain)} Б "
        f"нет. Значит открыт не тот объект, и всё, что тест докажет дальше, "
        f"будет доказано про чужие байты."
    )

    key_a_yesterday = _db_key(pa, OTHER_DATE)
    assert key_a_yesterday in yesterday.objects, (
        f"вчерашнего объекта {key_a_yesterday} нет: {yesterday.client_keys()}"
    )
    _must_not_open(
        private, yesterday.objects[key_a_yesterday], aad,
        what=(f"ДАТА не привязана: `aad` объекта {key_a} открыл объект "
              f"{key_a_yesterday}."))

    key_b = _db_key(pb, DATE)
    assert key_b in today.objects, (
        f"объекта второго клиента {key_b} нет: {today.client_keys()}"
    )
    _must_not_open(
        private, today.objects[key_b], aad,
        what=(f"КЛИЕНТ не привязан: `aad` объекта {key_a} открыл объект "
              f"{key_b} — переписку другого клиента."))


# ══ 5. КЛИЕНТСКИЙ НАБОР ДОЕЗЖАЕТ — ПО СЛАГАМ РЕЕСТРА (§7 п. 1) ══════════════
@pytest.mark.parametrize("slug", SLUGS)
def test_every_enabled_client_set_reaches_the_bucket(tmp_path, keys, slug):
    """Без него молча проехало бы: набор собран по ОДНОМУ примеру.

    Реализация, написанная под живой реестр, легко оказывается верной для
    первого клиента и слепой для второго. Параметризация по слагам делает
    так, что падение называет виноватого, а не «какой-то клиент не доехал».
    """
    root = _repo_tree(tmp_path)
    bucket = _Bucket()
    _run(root, bucket, keys["env"])

    pseudonym = bc.pseudonym(slug, keys["salt"])
    declared = _need("client_object_keys")(pseudonym, DATE)
    assert isinstance(declared, dict), (
        f"`client_object_keys` вернул {type(declared).__name__}, а контракт "
        f"обещает dict."
    )
    declared_values = {str(v) for v in declared.values()}

    for expected, what in ((_db_key(pseudonym), "история воронки"),
                           (_req_key(pseudonym), "платёжные реквизиты")):
        assert expected in declared_values, (
            f"`client_object_keys({pseudonym!r}, {DATE!r})` не объявляет ключ "
            f"{expected} ({what}).\nОбъявлено: {sorted(declared_values)}.\n"
            f"Форма ключа — обещание наружу: по нему ходят ротация, "
            f"`verify_uploaded` и дрил, ищущий вчерашний объект."
        )
        assert expected in bucket.objects, (
            f"клиент `{slug}`: {what} НЕ уехала в бакет — ключа {expected} "
            f"среди залитого нет.\nЗалито: {bucket.client_keys()}.\n"
            f"Чем оборачивается потеря: второй копии этих данных не "
            f"существует ни в git, ни в бандле, ни в Telegram; пропажа "
            f"обнаружится не в момент потери, а когда клиент спросит про май."
        )
        assert len(bucket.objects[expected]) > 0, (
            f"объект {expected} залит ПУСТЫМ (0 байт). Нулевой объект "
            f"проходит листинг как существующий и не содержит ничего."
        )


# ══ 6. СОСЕДИ ПО КАТАЛОГУ НЕ ДОЕЗЖАЮТ (§7 п. 3) ═════════════════════════════
@pytest.mark.parametrize("rel,why", NEIGHBOURS, ids=[e[0] for e in NEIGHBOURS])
def test_no_neighbour_of_the_directory_reaches_the_bucket(tmp_path, keys, rel, why):
    """Без него молча проехало бы: новый корень утащил соседей по каталогу.

    Главный риск правки, названный в §2.3 прямо. Широкий глоб проходит первую
    половину сторожа (база доехала!) и увозит вместе с ней полный доступ к
    аккаунту клиента. Разница между «данные» и «ключи от аккаунта» здесь —
    хвост имени файла.
    """
    root = _repo_tree(tmp_path)
    bucket = _Bucket()
    _run(root, bucket, keys["env"])

    assert bucket.client_keys(), (
        "в бакет не ушло ничего — сторож на «лишнее не доехало» на пустом "
        "прогоне зелен по построению."
    )

    name = Path(rel).name
    hits = [k for k in bucket.client_keys() if name in k]
    assert not hits, (
        f"в бакет уехал сосед по каталогу `{rel}` (ключи: {hits}).\n"
        f"Чем оборачивается: {why}.\n"
        f"Запрет держится в ОБЕ стороны: нужное доехало И лишнее не доехало. "
        f"Вторая половина важнее — не доехавшую базу заметят при "
        f"восстановлении, уехавшую сессию не заметит никто."
    )

    expected = 2 * len(SLUGS)
    assert len(bucket.data_keys()) == expected, (
        f"объектов с данными {len(bucket.data_keys())}, а контракт называет "
        f"ровно {expected} (db + requisites.yaml на каждого из {len(SLUGS)} "
        f"включённых клиентов).\nЗалито: {bucket.data_keys()}.\n"
        f"Лишний объект — это файл, которого никто не называл: имя его "
        f"псевдонимизировано, и что внутри, по ключу не видно."
    )


# ══ 7. БАЗА ЕДЕТ СНИМКОМ, А НЕ ФАЙЛОВОЙ КОПИЕЙ (§4.2) ═══════════════════════
def _dir_state(directory: Path) -> dict[str, tuple[int, int]]:
    return {
        p.name: (p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(directory.iterdir()) if p.is_file()
    }


def test_the_live_db_is_untouched_and_no_journal_or_bak_appears(tmp_path, keys):
    """Без него молча проехало бы: снимок сделан открытием боевой базы.

    Проверяется СЛЕДСТВИЕМ, а не вызовом: заглянуть, звался ли
    `snapshot_sqlite`, значит сторожить имя функции. Открытие живой базы на
    запись оставляет рядом `-journal`, а `Store.__init__` на непустой базе
    делает `.bak` миграции — оба следа появляются в каталоге, где сегодня
    лежат единственные копии клиентов.
    """
    root = _repo_tree(tmp_path)
    secrets = root / ".secrets"
    before = _dir_state(secrets)

    bucket = _Bucket()
    _run(root, bucket, keys["env"])

    after = _dir_state(secrets)
    appeared = sorted(set(after) - set(before))
    assert not appeared, (
        f"рядом с боевыми базами появились файлы: {appeared}.\n"
        f"`-journal` означает, что источник открывали на ЗАПИСЬ; `.bak` — что "
        f"его открыли `Store`, то есть прогнали по нему миграции; временный "
        f"снимок в `.secrets/` подпадает под глоб сирот `.secrets/*.db` и "
        f"будет назван ничьим.\nБыло: {sorted(before)}\nСтало: {sorted(after)}"
    )
    for slug in SLUGS:
        name = f"{slug}.db"
        assert after[name] == before[name], (
            f"источник `.secrets/{name}` ИЗМЕНИЛСЯ за прогон бэкапа: "
            f"(размер, mtime_ns) {before[name]} -> {after[name]}.\n"
            f"Бэкап боевую базу не чинит и не трогает — он строго читатель "
            f"(§4.2, §6 п. 4)."
        )


def test_the_decrypted_object_opens_with_the_real_storage_layer(tmp_path, keys):
    """Без него молча проехало бы: байты доехали, а база не поднимается.

    sha256 рваного снимка совпадает с рваным снимком — проверка целостности
    на нём ЗЕЛЁНАЯ, а база не открывается (§4.1 дословно). Поэтому объект
    открывается НАСТОЯЩИМ слоем хранения `chatter.storage.db.Store`, а не
    `sqlite3` руками: иначе доказано, что открывается файл, а не что работает
    продукт (§4.3 шаг 3).
    """
    root = _repo_tree(tmp_path)
    bucket = _Bucket()
    _run(root, bucket, keys["env"])

    pseudonym = bc.pseudonym(SLUG_A, keys["salt"])
    key = _db_key(pseudonym)
    assert key in bucket.objects, (
        f"объекта {key} нет: {bucket.client_keys()}"
    )
    plain, _aad = _open_object(keys["private"], key, bucket.objects[key])

    restored = tmp_path / "restored" / f"{SLUG_A}.db"
    restored.parent.mkdir(parents=True, exist_ok=True)
    restored.write_bytes(plain)

    try:
        store = Store(restored)
    except Exception as exc:  # noqa: BLE001 — любая причина одинаково красная
        pytest.fail(
            f"расшифрованный объект {key} НЕ открывается `chatter.storage.db."
            f"Store`: {type(exc).__name__}: {exc}\n"
            f"Байты доехали ({len(plain)} Б, sha256 сойдётся), а продукт из "
            f"них не поднимается — ровно тот рваный снимок, ради которого "
            f"§4.2 требует онлайновый backup API вместо файловой копии.",
            pytrace=False,
        )
    try:
        dialogs, messages = DB_SHAPE[SLUG_A]
        rows = []
        for d in range(dialogs):
            rows.extend(store.history(f"{SLUG_A}-contact-{d}"))
    finally:
        store.close()

    assert len(rows) == messages, (
        f"в восстановленной базе {len(rows)} сообщений, а в источнике на "
        f"момент снимка было {messages}.\nСнимок согласован не полностью: "
        f"часть истории воронки не доехала, и заметить это можно было только "
        f"здесь — sha256 такого снимка сходится сам с собой."
    )
    assert any(MARKER_TEXT in r["text"] for r in rows), (
        f"история восстановилась, но текста «{MARKER_TEXT}» в ней нет: "
        f"{[r['text'][:40] for r in rows]}"
    )


# ══ 8. МАНИФЕСТ: sha256 ОТ ШИФРОТЕКСТА ══════════════════════════════════════
def _entry_for(manifest: dict, suffix: str) -> dict:
    for entry in manifest.get("files", []):
        if str(entry.get("rel_path", "")).replace("\\", "/").endswith(suffix):
            return entry
    pytest.fail(
        f"в манифесте нет записи с rel_path, оканчивающимся на {suffix!r}.\n"
        f"Записи: {[e.get('rel_path') for e in manifest.get('files', [])]}",
        pytrace=False,
    )


def test_the_manifest_sha256_is_taken_from_the_ciphertext(tmp_path, keys):
    """Без него молча проехало бы: манифест считает отпечаток ОТКРЫТОГО файла.

    Разница не косметическая. sha256 в манифесте лежит НЕЗАШИФРОВАННЫМ рядом
    с конвертами: отпечаток открытого текста даёт постороннему проверку
    догадок о содержимом («та же база, что вчера?», «тот же файл
    реквизитов?»), а хосту не даёт ничего — сверить он может только то, что
    залил, то есть шифротекст (§9.2 п. 3, «байты доехали»).
    """
    root = _repo_tree(tmp_path)
    bucket = _Bucket()
    _run(root, bucket, keys["env"])
    manifest, manifest_text = _manifest(bucket)

    pseudonym = bc.pseudonym(SLUG_A, keys["salt"])
    key = _db_key(pseudonym)
    entry = _entry_for(manifest, f"{pseudonym}/db")
    ciphertext = bucket.objects[key]

    assert entry.get("sha256") == hashlib.sha256(ciphertext).hexdigest(), (
        f"sha256 в манифесте не совпадает с байтами, которые ушли в бакет по "
        f"ключу {key}.\nв манифесте: {entry.get('sha256')}\n"
        f"у шифротекста: {hashlib.sha256(ciphertext).hexdigest()} "
        f"({len(ciphertext)} Б)\nМанифест обязан отвечать на вопрос «доехали "
        f"ли байты», а доезжает шифротекст."
    )
    assert entry.get("size") == len(ciphertext), (
        f"`size` в манифесте {entry.get('size')}, а объект {key} весит "
        f"{len(ciphertext)} Б. Сверка размером — единственное, что хост может "
        f"сделать без приватного ключа."
    )

    # Обратная сторона: отпечатка ОТКРЫТОГО текста в манифесте быть не должно.
    plain, _aad = _open_object(keys["private"], key, ciphertext)
    leaks = {
        "sha256 расшифрованного снимка": hashlib.sha256(plain).hexdigest(),
        "sha256 исходной базы на диске": hashlib.sha256(
            (root / ".secrets" / f"{SLUG_A}.db").read_bytes()).hexdigest(),
        "sha256 файла реквизитов": hashlib.sha256(
            (root / "chatter" / "clients" / SLUG_A
             / "requisites.yaml").read_bytes()).hexdigest(),
    }
    for what, digest in leaks.items():
        assert digest not in manifest_text, (
            f"в незашифрованном манифесте лежит {what} ({digest}).\n"
            f"Это отпечаток СОДЕРЖИМОГО: доставку он проверить не помогает, а "
            f"постороннему позволяет подтверждать догадки о том, что внутри "
            f"конверта."
        )


# ══ 9. counts — величины, которыми доказывают восстановление (§7 п. 6) ══════
@pytest.mark.parametrize("slug", SLUGS)
def test_the_manifest_counts_match_the_content(tmp_path, keys, slug):
    """Без него молча проехало бы: дрилу нечего сверять с восстановленной базой.

    Величины едут в манифест рядом с sha256 именно для §4.3 шага 5: «база
    вернётся» доказывают counts, а не отпечаток. Сторож требует ровно те
    числа, которые в базу положили, — расхождение назовёт оба, а не «restore
    failed».
    """
    root = _repo_tree(tmp_path)
    bucket = _Bucket()
    _run(root, bucket, keys["env"])
    manifest, _text = _manifest(bucket)

    pseudonym = bc.pseudonym(slug, keys["salt"])
    entry = _entry_for(manifest, f"{pseudonym}/db")
    counts = entry.get("counts")
    assert isinstance(counts, dict), (
        f"у записи `{pseudonym}/db` нет словаря `counts` (получено "
        f"{counts!r}).\nБез него дрил доказывает только «байты доехали» — то "
        f"есть ровно то, что §4.1 объявляет НЕдоказательством восстановления."
    )

    dialogs, messages = DB_SHAPE[slug]
    assert counts.get("dialogs") == dialogs, (
        f"клиент `{slug}`: в манифесте dialogs={counts.get('dialogs')}, а в "
        f"базе контактов {dialogs}. Оба числа названы — чинить есть что."
    )
    assert counts.get("messages") == messages, (
        f"клиент `{slug}`: в манифесте messages={counts.get('messages')}, а в "
        f"базе сообщений {messages}."
    )
    expected_last = TS_BASE + messages - 1
    assert counts.get("last_message_at") == pytest.approx(expected_last), (
        f"клиент `{slug}`: last_message_at={counts.get('last_message_at')}, а "
        f"последнее сообщение записано в {expected_last}. Время последнего "
        f"сообщения — единственная величина, по которой видно, что снимок "
        f"свежий, а не вчерашний."
    )


# ══ 9б. МАНИФЕСТ НАЗЫВАЕТ КЛЮЧ, КОТОРЫМ НАБОР ЗАПЕЧАТАН (амендмент А) ════════
def test_the_manifest_names_the_fingerprint_of_the_key_that_really_sealed_it(
        tmp_path, keys, monkeypatch):
    """Без него молча проехало бы: отпечаток в манифесте не про тот ключ, которым набор запечатан.

    Дрил сверяет `key_fingerprint` с ВЫДАННЫМ ему приватным ключом и на
    расхождении отказывается работать вовсе (rc=2, вердикта нет). Отпечаток,
    зашитый константой или снятый не с того ключа, превращает эту сверку в
    лотерею: годный набор объявляется чужим либо чужой — годным.

    Поэтому сторож требует двух вещей сразу: поле равно
    `public_key_fingerprint` выданного заливке ключа И этим же ключом объекты
    ДЕЙСТВИТЕЛЬНО открываются. Второй прогон другой парой доказывает, что
    поле СЛЕДУЕТ за ключом, а не написано однажды и навсегда.
    """
    root = _repo_tree(tmp_path)

    bucket_a = _Bucket()
    _run(root, bucket_a, keys["env"])
    manifest_a, _text_a = _manifest(bucket_a)
    fp_a = bc.public_key_fingerprint(keys["public"])

    assert manifest_a.get("key_fingerprint") == fp_a, (
        f"key_fingerprint в манифесте {manifest_a.get('key_fingerprint')!r}, а "
        f"отпечаток публичного ключа, выданного заливке, {fp_a!r}.\n"
        f"Поле обязательно (амендмент А): без него дрил не отличит «нам выдали "
        f"не тот ключ» от «объект не открывается» и объявит негодным бэкап "
        f"там, где негоден ключ в его руках.\n"
        f"Манифест целиком: {manifest_a!r}"
    )

    sealed_a = bucket_a.data_keys()
    assert sealed_a, (
        "в бакет не уехало НИ ОДНОГО объекта с данными: сверять отпечаток не "
        "с чем, и сторож был бы зелен по построению."
    )
    for key in sealed_a:
        # Падает громко, если объект не открывается ключом, чей отпечаток
        # назван: отпечаток обязан указывать на ключ, которым РЕАЛЬНО
        # запечатано, а не на тот, что случайно лежал в окружении.
        _open_object(keys["private"], key, bucket_a.objects[key])

    # Второй прогон ДРУГОЙ парой. Соль та же, значит псевдонимы и ключи
    # объектов не меняются — меняется ровно ключ шифрования.
    private_b, public_b = bc.generate_keypair()
    fp_b = bc.public_key_fingerprint(public_b)
    assert fp_b != fp_a, (
        f"предусловие: отпечатки двух разных ключей совпали ({fp_a}) — "
        f"контрпример не о том."
    )
    env_b = {PUBLIC_KEY_ENV: _b64(public_b), SALT_ENV: _b64(keys["salt"])}
    monkeypatch.setenv(PUBLIC_KEY_ENV, env_b[PUBLIC_KEY_ENV])

    bucket_b = _Bucket()
    _run(root, bucket_b, env_b)
    manifest_b, _text_b = _manifest(bucket_b)

    assert manifest_b.get("key_fingerprint") == fp_b, (
        f"после смены публичного ключа манифест называет "
        f"{manifest_b.get('key_fingerprint')!r}, а отпечаток нового ключа "
        f"{fp_b!r}.\nПоле обязано СЛЕДОВАТЬ за ключом: зашитое или снятое с "
        f"чего-то другого, оно одинаково подпишет и старый набор, и новый — а "
        f"обнаружится это через год, на попытке открыть архив."
    )

    for key in bucket_b.data_keys():
        _data, aad = _open_object(private_b, key, bucket_b.objects[key])
        try:
            bc.decrypt_with(keys["private"], bucket_b.objects[key], aad=aad)
        except bc.BackupCryptoError:
            continue
        pytest.fail(
            f"объект {key} второго прогона открывается СТАРЫМ приватным "
            f"ключом, хотя манифест называет отпечаток нового ({fp_b}).\n"
            f"Значит заливка сменила отпечаток, а ключ шифрования — нет: дрил "
            f"откажется работать с набором, который на самом деле открывается, "
            f"и сторож на хосте прочтёт это как «бэкапа нет».",
            pytrace=False,
        )


# ══ 10. ПРОПАВШИЕ РЕКВИЗИТЫ НАЗВАНЫ ═════════════════════════════════════════
def test_missing_requisites_are_named_and_the_db_still_travels(tmp_path, keys):
    """Без него молча проехало бы: «файла нет» и «файл не искали» слились.

    Пропавший `requisites.yaml` — это либо клиент, который их ещё не прислал,
    либо путь, сломанный правкой. Молчаливый пропуск делает эти два случая
    неотличимыми, и второй живёт до дня, когда реквизиты понадобятся. При
    этом отсутствие ОДНОГО файла не имеет права отменить заливку остального:
    база того же клиента обязана уехать.
    """
    root = _repo_tree(tmp_path, without_requisites=(SLUG_B,))
    bucket = _Bucket()
    result = _run(root, bucket, keys["env"])

    failed = getattr(result, "failed", None)
    failed_text = json.dumps(failed, ensure_ascii=False, default=str).lower()
    pseudonym_b = bc.pseudonym(SLUG_B, keys["salt"])
    assert "requisites" in failed_text, (
        f"пропавший `chatter/clients/{SLUG_B}/requisites.yaml` НЕ назван в "
        f"result.failed: {failed!r}.\n«Файла нет» и «файл не искали» обязаны "
        f"быть различимы — иначе сломанный путь неотличим от клиента, который "
        f"реквизиты не прислал."
    )
    assert (SLUG_B in failed_text) or (pseudonym_b in failed_text), (
        f"в result.failed сказано про requisites, но не сказано, У КОГО: ни "
        f"слага `{SLUG_B}`, ни его псевдонима `{pseudonym_b}` в записи нет.\n"
        f"{failed!r}\nЗапись, по которой нельзя найти клиента, — шум, а не "
        f"улика. (result.failed живёт НА ХОСТЕ и наружу не уезжает: слаг в нём "
        f"законен, псевдоним принимается как равноценный.)"
    )

    assert _db_key(pseudonym_b) in bucket.objects, (
        f"у клиента `{SLUG_B}` нет реквизитов — и вместе с ними не уехала его "
        f"БАЗА (ключа {_db_key(pseudonym_b)} в бакете нет).\n"
        f"Залито: {bucket.client_keys()}.\nОтсутствие одного файла не имеет "
        f"права отменить заливку остального: сбой на одном файле никогда не "
        f"прерывает прогон целиком."
    )
    assert _db_key(bc.pseudonym(SLUG_A, keys["salt"])) in bucket.objects, (
        f"заодно не уехала база ПЕРВОГО клиента: {bucket.client_keys()}"
    )


# ══ 11. СВЕРКА ЛИСТИНГОМ ИДЁТ ПО КЛИЕНТСКОМУ ПРЕФИКСУ ═══════════════════════
def test_the_listing_check_walks_the_client_prefix(tmp_path, keys):
    """Без него молча проехало бы: сверили не тот префикс и обрадовались.

    `verify_uploaded` собран вокруг `BACKUP_PREFIX` умолчанием. Забыть
    передать `prefix` — правка в одну строку, и она не обязана краснеть:
    ожидания, собранные из того же неверного префикса, сойдутся сами с собой,
    и «подтверждено листингом» будет сказано про чужие объекты. Лечит это
    один сторож: смотреть, ЧТО спросили у бакета.
    """
    root = _repo_tree(tmp_path)
    bucket = _Bucket()
    result = _run(root, bucket, keys["env"])

    expected_prefix = f"{CLIENT_PREFIX}/{DATE}/"
    assert bucket.list_prefixes, (
        "`list_objects` не вызывался НИ РАЗУ: заливка посчитана, но не "
        "доказана. `upload_file`, вернувший управление, — это ещё не бэкап "
        "(не тот бакет, политика токена на запись без чтения, ретенция)."
    )
    assert any(p == expected_prefix for p in bucket.list_prefixes), (
        f"сверка листингом ни разу не спросила {expected_prefix!r}.\n"
        f"Спрашивали: {bucket.list_prefixes}.\nПроверка чужого префикса "
        f"отвечает на чужой вопрос: клиентские объекты лежат под "
        f"{CLIENT_PREFIX}/ ради СВОЕГО срока хранения (§9.3)."
    )
    stray = [p for p in bucket.list_prefixes if p.startswith(STATE_PREFIX)]
    assert not stray, (
        f"сверка клиентского набора листала префикс набора `state`: {stray}.\n"
        f"Это ровно тот случай, ради которого §9.3 развёл префиксы: у них "
        f"разные сроки хранения, и путать их листинги нельзя."
    )
    assert not getattr(result, "failed", None), (
        f"прогон на исправном дереве вернул ошибки: {result.failed!r}.\n"
        f"Все четыре файла на месте, обе переменные окружения заданы, бакет "
        f"принимает всё — красное здесь означает, что сверка не сошлась сама "
        f"с собой."
    )


# ══ 12. ВРЕМЕННЫЙ КОРЕНЬ ЗАЛИВКИ (амендмент Д) ══════════════════════════════
#
# Дрил гоняется раз в неделю у владельца; заливка — КАЖДЫЙ ДЕНЬ в 04:00 на
# хосте, и в её временный каталог ложится снимок живой базы клиента ОТКРЫТЫМ
# ТЕКСТОМ. Значит сито у неё обязано быть то же самое, что у дрила, и по тем
# же причинам: корень читается из `TMPDIR`/`TEMP`/`TMP` В МОМЕНТ ВЫЗОВА (иначе
# шва нет и проверить нельзя ничего), запретная зона проверяется ДО создания
# каталога, снос безусловный и громкий.
#
# `shutil.rmtree(..., ignore_errors=True)` — это не уборка, а её видимость:
# каталог со снимком переживает прогон, и никто об этом не узнаёт (DEV-18).


def _use_temp_root(monkeypatch, path: Path) -> Path:
    """Указать заливке временный корень — тремя переменными сразу.

    Три, а не одна: `tempfile` смотрит на `TMPDIR`, `TEMP` и `TMP`, и какая
    из них сработает, зависит от платформы. Сторож не обязан угадывать."""
    path.mkdir(parents=True, exist_ok=True)
    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(path))
    return path


def _files_under(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(p.relative_to(root).as_posix()
                  for p in root.rglob("*") if p.is_file())


def _all_files(root: Path) -> set[str]:
    return set(_files_under(root))


# Байты, по которым узнаётся ОТКРЫТАЯ клиентская база и её реквизиты. Ровно
# те же маркеры, что у ведущего сторожа §7 п. 4: там они не должны уехать
# наружу, здесь — не должны остаться на диске.
def _open_client_bytes(path: Path) -> list[str]:
    try:
        blob = path.read_bytes()
    except OSError:
        return []
    found = []
    if SQLITE_MAGIC in blob:
        found.append("файл SQLite целиком")
    if MARKER_TEXT.encode("utf-8") in blob:
        found.append("текст сообщения из воронки")
    if MARKER_ACCOUNT.encode("utf-8") in blob:
        found.append("номер счёта из реквизитов")
    return found


class _WatchingBucket(_Bucket):
    """Бакет, запоминающий, ОТКУДА пришёл каждый загружаемый файл.

    Смотреть после прогона поздно: заливка удаляет временный файл сразу после
    отправки, и «в каталоге пусто» было бы зелено по построению — хоть бы она
    работала совсем в другом месте."""

    def __init__(self) -> None:
        super().__init__()
        self.sources: list[Path] = []

    def upload_file(self, path, key=None, *, client=None, config=None, **kwargs):
        self.sources.append(Path(path))
        return super().upload_file(path, key, client=client, config=config, **kwargs)


class _HandleHoldingBucket(_Bucket):
    """Держит открытый хэндл на первый же загруженный файл.

    На Windows открытый файл не удаляется, значит снос временного корня
    ПРОВАЛИТСЯ — ровно та ситуация, которую `ignore_errors=True` проглатывает
    молча, оставляя снимок клиентской базы лежать открытым текстом."""

    def __init__(self) -> None:
        super().__init__()
        self.held = None
        self.held_path: Path | None = None

    def upload_file(self, path, key=None, *, client=None, config=None, **kwargs):
        result = super().upload_file(path, key, client=client, config=config, **kwargs)
        if self.held is None:
            self.held_path = Path(path)
            self.held = self.held_path.open("rb")
        return result

    def release(self) -> None:
        if self.held is not None:
            self.held.close()
            self.held = None


@pytest.mark.parametrize("where", ["tree_root", "secrets", "dotdot"])
def test_a_temp_root_inside_the_tree_is_refused_before_the_directory_is_made(
        where, tmp_path, keys, monkeypatch):
    """Без него молча проехало бы: снимок живой базы клиента распаковывается ВНУТРЬ рабочего дерева — туда, где гардиан поднимает раннер (DEV-31), а `.secrets/*.db` уже лежат под живыми раннерами.

    Отказ обязан быть ДО создания каталога: каталог, созданный и потом
    убранный, успевает подхватиться и `git add -A`, и обходом бэкапа.
    """
    root = _repo_tree(tmp_path)
    if where == "tree_root":
        forbidden = root / "guard-temp-root"
    elif where == "secrets":
        forbidden = root / ".secrets" / "guard-temp"
    else:
        # Строкой путь уходит вбок, а разбирается — внутрь дерева.
        forbidden = root / "chatter" / ".." / ".secrets" / "guard-temp"
    real = Path(os.path.normpath(str(forbidden)))
    assert not real.exists(), f"подготовка: {real} уже существует"

    for var in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(var, str(forbidden))

    bucket = _Bucket()
    with pytest.raises(_need("ClientBackupRefused")) as err:
        _run(root, bucket, keys["env"])

    assert not real.exists(), (
        f"заливка СОЗДАЛА {real} внутри дерева и только потом отказалась "
        f"(«{err.value}»). Отказ обязан быть до создания: в этот каталог "
        "ложится снимок клиентской базы открытым текстом, а дерево — это то, "
        "что копируют, коммитят и поднимают гардианом"
    )
    assert bucket.blobs == [], (
        f"при запретном временном корне заливка всё же что-то отправила: "
        f"{[k for k, _ in bucket.blobs]}. Фейл-клоуз означает «не начинается "
        f"ВООБЩЕ», а не «начинается и на середине передумывает»"
    )


def test_no_temporary_directory_survives_a_successful_upload(tmp_path, keys,
                                                             monkeypatch):
    """Без него молча проехало бы: снимок живой базы клиента остаётся лежать открытым текстом во временном каталоге после КАЖДОГО ночного прогона — а прогон ежедневный, и никто туда не смотрит.

    Якорь — не «каталог пуст», а «файлы вообще приходили ОТСЮДА»: заливка,
    работающая мимо `TMPDIR`/`TEMP`/`TMP`, оставила бы сторожа зелёным по
    построению, ничего не проверив.
    """
    root = _repo_tree(tmp_path)
    temp_root = _use_temp_root(monkeypatch, tmp_path / "temproot")

    bucket = _WatchingBucket()
    _run(root, bucket, keys["env"])

    assert bucket.sources, "в загрузку не ушло ни одного файла — проверять нечего"
    outside = [str(p) for p in bucket.sources
               if temp_root not in p.resolve().parents]
    assert not outside, (
        f"заливка отправляла файлы НЕ из временного корня {temp_root}: "
        f"{outside[:5]}.\nЛибо корень берётся мимо TMPDIR/TEMP/TMP (значит шва "
        f"нет и запретную зону проверить невозможно — амендмент Д), либо "
        f"снимок пишется рядом с боевой базой, и тогда `.secrets/*.db` "
        f"пополняется сиротой"
    )

    left = _files_under(temp_root)
    assert left == [], (
        f"после успешного прогона во временном корне осталось {len(left)} "
        f"файл(ов): {left[:10]}.\nТам лежит снимок клиентской базы открытым "
        f"текстом. Заливка идёт КАЖДЫЙ ДЕНЬ в 04:00: остаток не «однажды», а "
        f"каждые сутки, и растёт"
    )


def test_a_failed_sweep_of_the_temp_root_is_not_swallowed(tmp_path, keys,
                                                          monkeypatch, caplog):
    """Без него молча проехало бы: снос упал, `ignore_errors=True` его съел, и снимок клиентской базы остался на диске — при полностью зелёном отчёте заливки.

    Провал уборки — не пустяк, а ровно то, что DEV-18 запрещает глотать:
    лог, алерт или re-raise, но не тишина. Сторож не требует конкретной из
    трёх реакций — он требует, чтобы реакция БЫЛА.
    """
    root = _repo_tree(tmp_path)
    temp_root = _use_temp_root(monkeypatch, tmp_path / "temproot")

    bucket = _HandleHoldingBucket()
    raised: Exception | None = None
    try:
        with caplog.at_level(logging.ERROR):
            try:
                _run(root, bucket, keys["env"])
            except Exception as exc:  # noqa: BLE001 — громкость важнее класса
                raised = exc

        assert bucket.held_path is not None, (
            "заливка не загрузила ни одного файла — хэндл держать не на чем, "
            "и сторож ничего не проверил"
        )
        leftovers = _files_under(temp_root)
        assert leftovers, (
            f"предусловие: открытый хэндл на {bucket.held_path} не помешал "
            f"сносу временного корня. На Windows открытый на чтение файл "
            f"удалить нельзя; если это перестало быть так, сторожа надо "
            f"переписать, а не считать зелёным"
        )

        loud = raised is not None or any(
            rec.levelno >= logging.ERROR for rec in caplog.records)
        assert loud, (
            f"временный корень пережил заливку ({leftovers[:10]}), и об этом "
            f"не сказано НИЧЕМ: ни исключения, ни записи уровня ERROR.\n"
            f"Внутри — снимок клиентской базы открытым текстом. "
            f"`shutil.rmtree(..., ignore_errors=True)` это не уборка, а её "
            f"видимость: ежедневный таск в 04:00 будет копить снимки и "
            f"докладывать об успехе"
        )
    finally:
        bucket.release()


def test_not_one_open_byte_of_the_client_base_stays_on_disk_after_the_upload(
        tmp_path, keys, monkeypatch):
    """Без него молча проехало бы: наружу не уехало ничего открытого, а НА ДИСКЕ осталось — снимок во временном каталоге, `.bak` рядом с базой, копия «на всякий случай». Кто взял хост, забирает переписку, не открывая ни одного конверта.

    Сторож смотрит на ВСЁ, что появилось за прогон, а не на список известных
    мест: дефект этого класса всегда лежит там, куда не смотрели.
    """
    root = _repo_tree(tmp_path)
    temp_root = _use_temp_root(monkeypatch, tmp_path / "temproot")
    before = _all_files(tmp_path)

    bucket = _Bucket()
    _run(root, bucket, keys["env"])

    appeared = sorted(_all_files(tmp_path) - before)
    leaks = {}
    for rel in appeared:
        found = _open_client_bytes(tmp_path / rel)
        if found:
            leaks[rel] = found
    assert not leaks, (
        f"после заливки на диске осталось ОТКРЫТОЕ клиентское: {leaks}.\n"
        f"Всё, что появилось за прогон: {appeared[:20]}.\n"
        f"Наружу это не уехало — и потому не поймано ни одним сторожем §7 "
        f"п. 4. Но лежит оно на арендованном хосте, ради недоверия к которому "
        f"и выбран вариант B (§3.3)"
    )
    assert _files_under(temp_root) == [], (
        f"временный корень не снесён: {_files_under(temp_root)[:10]}"
    )


# ══ 13. СКРИПТ: «ключа нет» и «заливка упала» — РАЗНЫЕ rc ═══════════════════
def _load_script():
    spec = importlib.util.spec_from_file_location(
        "state_backup_script_dev46", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["state_backup_script_dev46"] = mod
    spec.loader.exec_module(mod)
    return mod


def _mentions_the_client_set(text: str) -> bool:
    """Сводка НАЗЫВАЕТ клиентский набор.

    Точных слов контракт не даёт, поэтому требуется минимум: чтобы читатель
    сводки понял, о каком из двух наборов речь. «Ошибок: 1» без имени набора
    отправляет владельца искать причину не там.
    """
    low = (text or "").lower()
    return "клиент" in low or "client" in low


def _state_ok_result():
    return sb.BackupResult(
        date=DATE, uploaded=["users.json"], verified=["users.json"],
        manifest_key=f"{STATE_PREFIX}/{DATE}/manifest.json", total_bytes=100)


def _mute_state_half(monkeypatch, mod) -> dict:
    """Половина `state` — заведомо зелёная, чтобы rc говорил о КЛИЕНТСКОЙ."""
    monkeypatch.setattr(sb, "run_backup", lambda root: _state_ok_result())
    monkeypatch.setattr(sb, "rotate_all_backups",
                        lambda: {STATE_PREFIX: [], CLIENT_PREFIX: []})
    captured: dict = {}

    def _send(text):
        captured["text"] = text
        return True

    monkeypatch.setattr(mod, "send_telegram", _send)
    return captured


def test_script_without_a_key_says_so_and_keeps_the_return_code(monkeypatch):
    """Без него молча проехало бы: ненастроенный ключ уронил ежедневный таск.

    Публичного ключа на машине может не быть законно (владелец ещё не положил
    его), и это не авария набора `state`, который ездит с 08.08. Портить rc
    здесь значит завести лампу, которая горит по бытовой причине, — и приучить
    читать её красное как «опять не настроено»
    ([[jarvis-ask-bridge-auto-allow-suspect]]).
    """
    refused = _need("ClientBackupRefused")
    _need("run_client_backup")
    mod = _load_script()
    captured = _mute_state_half(monkeypatch, mod)
    monkeypatch.delenv(PUBLIC_KEY_ENV, raising=False)

    def _refuse(*args, **kwargs):
        raise refused(f"переменная {PUBLIC_KEY_ENV} не задана")

    monkeypatch.setattr(sb, "run_client_backup", _refuse)

    rc = mod.main([])
    text = captured.get("text", "")

    assert rc == 0, (
        f"скрипт вернул rc={rc} только потому, что на машине нет "
        f"{PUBLIC_KEY_ENV}.\nНабор `state` при этом залит и подтверждён "
        f"листингом. Ненулевой rc здесь — ежедневный ложный алерт по бытовой "
        f"причине.\nСводка: {text!r}"
    )
    assert _mentions_the_client_set(text), (
        f"про клиентский набор в сводке НЕТ НИ СЛОВА: {text!r}\n"
        f"Тихий пропуск неотличим от успешной заливки: владелец будет год "
        f"считать, что база клиента в бакете."
    )


def test_script_with_a_failed_client_upload_says_so_and_fails(monkeypatch, keys):
    """Без него молча проехало бы: настоящую поломку простили вместе с ключом.

    Вторая половина обязательна. Сторож, проверяющий только «ключа нет — rc
    ноль», зеленеет и на реализации, которая гасит ЛЮБОЙ исход клиентской
    заливки: тогда провал доставки годового набора ничем не отличается от
    ненастроенного ключа, и оба каждый день выглядят как успех.
    """
    _need("ClientBackupRefused")
    _need("run_client_backup")
    mod = _load_script()
    captured = _mute_state_half(monkeypatch, mod)

    pseudonym = bc.pseudonym(SLUG_A, keys["salt"])
    broken = sb.BackupResult(
        date=DATE,
        uploaded=[f"{pseudonym}/requisites.yaml"],
        failed=[{"rel_path": f"{pseudonym}/db",
                 "error": "R2 отказал: история воронки не доехала"}],
        manifest_key=_manifest_key(),
    )
    monkeypatch.setattr(sb, "run_client_backup", lambda *a, **k: broken)

    rc = mod.main([])
    text = captured.get("text", "")

    assert rc != 0, (
        f"история воронки клиента НЕ доехала в бакет, а скрипт вернул rc={rc}.\n"
        f"result.failed = {broken.failed!r}\nТаск в 04:00 отчитается успехом, "
        f"и единственная копия данных не появится ни сегодня, ни в любой "
        f"следующий день — молча.\nСводка: {text!r}"
    )
    assert _mentions_the_client_set(text), (
        f"rc испорчен, но сводка не говорит, ЧТО упало: {text!r}\n"
        f"Владелец увидит красное и не узнает, клиентский это набор или "
        f"`state`; действия у них разные."
    )
