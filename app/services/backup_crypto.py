# -*- coding: utf-8 -*-
"""Сквозное шифрование клиентского набора бэкапа (DEV-46 §3.3, вариант B).

**Хост умеет ПИСАТЬ бэкап и НЕ умеет его ЧИТАТЬ.** Решение владельца 21.08,
дословно: «Хост арендованный, „заберут вместе с диском“ — не гипотеза».
На хосте лежит только ПУБЛИЧНЫЙ ключ (`JARVIS_BACKUP_PUBLIC_KEY`); приватный
создаёт и хранит владелец вне машины и не привозит сюда НИКОГДА — ни для
дрила, ни «временно, на время восстановления» (§3.3, §9.2).

Отсюда прямое требование к этому файлу, и это не стилистика:

    В модуле НЕТ и не должно появиться `load_private_key` — и вообще ни
    одной функции, читающей приватный ключ с диска, из окружения или из
    любого другого хранилища хоста.

Такая функция сделала бы возможным ровно то, ради чего вариант B и выбран:
хост стал бы уметь читать. Приватный ключ попадает в код единственным путём —
аргументом `decrypt_with`; дрил восстановления (§4.3) запускается на машине
владельца и получает путь к ключу аргументом командной строки. Сторож §7 п. 9
проверяет это с обеих сторон: приватного ключа в дереве нет, и попытка
расшифровать свежий объект средствами хоста ПРОВАЛИВАЕТСЯ.

Конструкция — готовые примитивы `cryptography`, своего шифра здесь нет
(§3.2, §6 п. 3): эфемерная пара X25519 → ECDH с публичным ключом получателя →
HKDF-SHA256 → AES-256-GCM на данные. Это та же схема «ключ сеанса под
асимметрией + тот же AES-256-GCM», что описана в §3.3, и тот же формат
конверта в духе `chatter/security/recovery.py`:

    MAGIC(8) | version(1) | ephemeral_pub(32) | nonce(12) | AESGCM(payload)

`aad` обязателен и НЕ имеет значения по умолчанию. Вызывающий передаёт туда
ключ объекта (`backups/client/<дата>/<псевдоним>/<имя>`), и конверт тем самым
привязан к своему месту: переставить его на другую дату или другого клиента
молча нельзя — `decrypt_with` с чужим `aad` падает, а не возвращает данные.
Забытая привязка — это молчаливое ослабление, поэтому умолчания нет.

Псевдонимы (§9.1) живут здесь же: прятать слаг в манифесте и оставить его в
ИМЕНИ ОБЪЕКТА — значит запечатать конверт и надписать адрес снаружи. В ключ
объекта едет `pseudonym()`; он обязан быть СТАБИЛЬНЫМ, потому что
`verify_uploaded` сверяет ожидаемые имена, ротация ходит по ключам, а дрил
ищет вчерашний объект. Граница названа в спеке вслух: псевдоним защищает от
читателя БАКЕТА, а не от того, кто взял хост, и наружу всё равно видны число
объектов и их размеры.

Ошибки не глотаются (DEV-18): всё, что отказало, приезжает как
`BackupCryptoError` — разные тексты, один класс, причина сохранена через
`raise ... from`.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
from collections.abc import Mapping

from cryptography.exceptions import InvalidTag, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Сериализация зовётся через модуль (`serialization.PrivateFormat`), а не
# импортом имени: в пространстве имён этого модуля не должно быть НИ ОДНОГО
# имени со словом private — сторож §7 п. 9 ищет на хосте именно это, и
# служебный enum библиотеки не должен выглядеть как загрузчик ключа.

# 8 байт: магический префикс отличает клиентский конверт от бандла секретов
# (`JRVBAK\x00`) и от DPAPI-блоба (`JRVSEC1\x00`) — «чужой формат» обязан
# читаться как явная ошибка, а не как мусор из глубин криптографии.
MAGIC = b"JRVCLI1\x00"
VERSION = 1

# Конверт: MAGIC(8) | version(1) | ephemeral_pub(32) | nonce(12) | AESGCM_ct
_KEY_LEN = 32          # X25519 raw
_NONCE_LEN = 12        # AES-GCM
_TAG_LEN = 16          # AES-GCM
_HEADER_LEN = len(MAGIC) + 1 + _KEY_LEN + _NONCE_LEN
_MIN_BLOB_LEN = _HEADER_LEN + _TAG_LEN  # пустой payload — всё ещё валиден

# AES-GCM за один вызов держит и вход, и выход в памяти целиком: молча съесть
# гигабайты — это падение ежедневного таска в 04:00 без объяснения.
MAX_PLAINTEXT_BYTES = 512 * 1024 * 1024

# Разделение доменов HKDF: ключ сеанса привязан к схеме И к паре ключей, так
# что конверт нельзя «пересчитать» на другого получателя.
_HKDF_INFO_PREFIX = b"jarvis/dev46/backup-client/v1"

_PUBLIC_KEY_ENV = "JARVIS_BACKUP_PUBLIC_KEY"
_PSEUDONYM_SALT_ENV = "JARVIS_BACKUP_KEY_SALT"
_MIN_PSEUDONYM_SALT_LEN = 16

# 16 hex = 8 байт. Достаточно, чтобы различать клиентов и ключи в имени
# объекта и в манифесте, и мало, чтобы имя оставалось читаемым человеком.
_DIGEST_HEX_LEN = 16


class BackupCryptoError(Exception):
    """Любой отказ шифрования/расшифровки/загрузки ключа клиентского набора.

    Явно, не тихо (DEV-18). Неверный тег GCM, повреждённый конверт, чужой
    ключ, пустая переменная окружения — тексты разные, класс один."""


def _header(version: int, ephemeral_pub: bytes) -> bytes:
    return MAGIC + bytes([version]) + ephemeral_pub


def _aead_aad(version: int, ephemeral_pub: bytes, aad: bytes) -> bytes:
    """Под тегом GCM лежит ВЕСЬ заголовок плюс привязка вызывающего.

    Заголовок в AAD — тем же приёмом, что в `recovery.py` (там под тег идут
    magic+version): подмена заголовка рвёт тег, а не уезжает в разбор."""
    return _header(version, ephemeral_pub) + aad


def _derive_key(shared: bytes, ephemeral_pub: bytes,
                recipient_pub: bytes) -> bytes:
    try:
        return HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None,
            info=_HKDF_INFO_PREFIX + ephemeral_pub + recipient_pub,
        ).derive(shared)
    except (UnsupportedAlgorithm, ValueError) as exc:
        raise BackupCryptoError(
            f"не удалось вывести ключ сеанса HKDF-SHA256: {exc}") from exc


def _check_raw_key(key: bytes, what: str) -> bytes:
    if not isinstance(key, (bytes, bytearray)):
        raise BackupCryptoError(
            f"{what} должен быть bytes ({_KEY_LEN} сырых байт X25519), "
            f"получено {type(key).__name__}")
    if len(key) != _KEY_LEN:
        raise BackupCryptoError(
            f"{what}: ожидается ровно {_KEY_LEN} сырых байт X25519, "
            f"получено {len(key)}")
    return bytes(key)


def generate_keypair() -> tuple[bytes, bytes]:
    """Новая пара X25519: `(приватный_raw32, публичный_raw32)`.

    Приватный ключ НИКОГДА не пишется на диск этим модулем — его создаёт и
    хранит владелец вне машины (§3.3 B). Функция только возвращает байты;
    что с ними делать, решает человек у себя, а не хост."""
    private = x25519.X25519PrivateKey.generate()
    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption())
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    return private_raw, public_raw


def encrypt_for(public_key: bytes, data: bytes, *, aad: bytes) -> bytes:
    """Зашифровать `data` для владельца публичного ключа `public_key`.

    Гибрид (§3.2 — свой шифр не пишем): эфемерная пара X25519 → ECDH с ключом
    получателя → HKDF-SHA256 → AES-256-GCM. Эфемерный ПРИВАТНЫЙ ключ живёт
    только внутри этого вызова и наружу не отдаётся ничем — именно поэтому
    хост, зашифровав, прочитать уже не может.

    `aad` привязывает конверт к его месту (ключ объекта) и не имеет значения
    по умолчанию: забытая привязка — молчаливое ослабление.

    Два вызова на одних данных дают РАЗНЫЕ байты: и эфемерная пара, и nonce
    свежие на каждый вызов."""
    recipient_raw = _check_raw_key(public_key, "публичный ключ получателя")
    if not isinstance(data, (bytes, bytearray)):
        raise BackupCryptoError(
            f"данные для шифрования должны быть bytes, "
            f"получено {type(data).__name__}")
    if not isinstance(aad, (bytes, bytearray)):
        raise BackupCryptoError(
            f"aad должен быть bytes (ключ объекта в utf-8), "
            f"получено {type(aad).__name__}")
    if len(data) > MAX_PLAINTEXT_BYTES:
        raise BackupCryptoError(
            f"вход {len(data)} Б больше предела {MAX_PLAINTEXT_BYTES} Б: "
            "AES-GCM за один вызов держит всё в памяти, режьте на части")

    try:
        recipient = x25519.X25519PublicKey.from_public_bytes(recipient_raw)
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise BackupCryptoError(
            f"публичный ключ получателя не является точкой X25519: "
            f"{exc}") from exc

    ephemeral = x25519.X25519PrivateKey.generate()
    ephemeral_pub = ephemeral.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    try:
        shared = ephemeral.exchange(recipient)
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise BackupCryptoError(
            f"ECDH X25519 с публичным ключом получателя не удался: "
            f"{exc}") from exc

    key = _derive_key(shared, ephemeral_pub, recipient_raw)
    # 12 случайных байт на КАЖДЫЙ вызов. Никакого счётчика и никакого
    # фиксированного значения: повтор nonce под одним ключом ломает GCM.
    nonce = os.urandom(_NONCE_LEN)
    try:
        ciphertext = AESGCM(key).encrypt(
            nonce, bytes(data), _aead_aad(VERSION, ephemeral_pub, bytes(aad)))
    except (ValueError, OverflowError) as exc:
        raise BackupCryptoError(
            f"AES-256-GCM не смог зашифровать вход: {exc}") from exc
    return _header(VERSION, ephemeral_pub) + nonce + ciphertext


def decrypt_with(private_key: bytes, blob: bytes, *, aad: bytes) -> bytes:
    """Обратное к `encrypt_for`.

    Живёт ради ДРИЛА восстановления (§4.3) и запускается НА МАШИНЕ ВЛАДЕЛЬЦА,
    где лежит приватный ключ. В боевом пути хоста не зовётся никогда: на
    хосте приватного ключа нет и появиться ему неоткуда — загрузчика для него
    в этом модуле нет намеренно (см. докстроку модуля).

    Разбор конверта строгий и идёт ДО любой криптографии: не тот MAGIC, не та
    версия, длина меньше заголовка → `BackupCryptoError`. «Попробуем всё
    равно» здесь нет.

    `aad` обязан совпасть с тем, что был при шифровании: чужой `aad` — это
    провал, а не данные."""
    private_raw = _check_raw_key(private_key, "приватный ключ получателя")
    if not isinstance(blob, (bytes, bytearray)):
        raise BackupCryptoError(
            f"конверт должен быть bytes, получено {type(blob).__name__}")
    if not isinstance(aad, (bytes, bytearray)):
        raise BackupCryptoError(
            f"aad должен быть bytes (ключ объекта в utf-8), "
            f"получено {type(aad).__name__}")
    blob = bytes(blob)

    if len(blob) < _MIN_BLOB_LEN:
        raise BackupCryptoError(
            f"конверт обрезан: {len(blob)} Б при минимуме {_MIN_BLOB_LEN} Б "
            f"(заголовок {_HEADER_LEN} + тег GCM {_TAG_LEN}) — файл повреждён "
            "или докачан не до конца")
    if not blob.startswith(MAGIC):
        raise BackupCryptoError(
            f"нет магического префикса {MAGIC!r} — это не клиентский конверт "
            "бэкапа (чужой формат? битый файл?)")

    version = blob[len(MAGIC)]
    if version != VERSION:
        raise BackupCryptoError(
            f"конверт версии {version}, поддерживается {VERSION} — нужна "
            "соответствующая версия инструмента восстановления")

    off = len(MAGIC) + 1
    ephemeral_pub = blob[off:off + _KEY_LEN]
    off += _KEY_LEN
    nonce = blob[off:off + _NONCE_LEN]
    off += _NONCE_LEN
    ciphertext = blob[off:]
    if len(ciphertext) - _TAG_LEN > MAX_PLAINTEXT_BYTES:
        raise BackupCryptoError(
            f"конверт распакуется в {len(ciphertext) - _TAG_LEN} Б, что "
            f"больше предела {MAX_PLAINTEXT_BYTES} Б — расшифровка съела бы "
            "память целиком")

    try:
        private = x25519.X25519PrivateKey.from_private_bytes(private_raw)
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise BackupCryptoError(
            f"приватный ключ не является скаляром X25519: {exc}") from exc
    try:
        ephemeral = x25519.X25519PublicKey.from_public_bytes(ephemeral_pub)
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise BackupCryptoError(
            f"эфемерный публичный ключ в конверте испорчен: {exc}") from exc
    try:
        shared = private.exchange(ephemeral)
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise BackupCryptoError(
            f"ECDH X25519 при расшифровке не удался: {exc}") from exc

    recipient_pub = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    key = _derive_key(shared, ephemeral_pub, recipient_pub)
    try:
        return AESGCM(key).decrypt(
            nonce, ciphertext, _aead_aad(version, ephemeral_pub, bytes(aad)))
    except InvalidTag as exc:
        raise BackupCryptoError(
            "тег AES-GCM не сошёлся: конверт зашифрован ДРУГИМ ключом, "
            "испорчен побайтно, либо `aad` не тот — конверт привязан к своему "
            "ключу объекта и на чужом месте не открывается") from exc
    except (ValueError, OverflowError) as exc:
        raise BackupCryptoError(
            f"AES-256-GCM не смог расшифровать конверт: {exc}") from exc


def load_public_key(env: Mapping[str, str] | None = None) -> bytes:
    """Публичный ключ получателя из `JARVIS_BACKUP_PUBLIC_KEY`.

    Значение — base64 от 32 сырых байт X25519. `env=None` → `os.environ`.

    ФЕЙЛ-КЛОУЗ: нет переменной / не base64 / не 32 байта → `BackupCryptoError`.
    Ни при каких условиях не возвращает None и не подразумевает «тогда без
    шифрования»: молчаливый откат к открытому тексту — это ровно тот дефект,
    ради которого §3 написан."""
    source = os.environ if env is None else env
    raw = source.get(_PUBLIC_KEY_ENV)
    if raw is None:
        raise BackupCryptoError(
            f"переменная {_PUBLIC_KEY_ENV} не задана: без публичного ключа "
            "клиентский набор шифровать нечем, а везти его открытым нельзя "
            "(DEV-46 §3)")
    if not raw.strip():
        raise BackupCryptoError(
            f"переменная {_PUBLIC_KEY_ENV} пуста — это не «шифрование "
            "выключено», это ненастроенный бэкап")
    key = _b64_or_fail(raw.strip(), _PUBLIC_KEY_ENV)
    if len(key) != _KEY_LEN:
        raise BackupCryptoError(
            f"{_PUBLIC_KEY_ENV} декодируется в {len(key)} Б, а публичный ключ "
            f"X25519 — ровно {_KEY_LEN} Б: в переменной лежит не тот ключ")
    return key


def public_key_fingerprint(public_key: bytes) -> str:
    """`sha256(публичного ключа)`, первые 16 hex.

    Едет в манифест, чтобы было видно, КАКИМ ключом зашифровано. Без отпечатка
    смена ключа обнаружится только на дриле — и только через год, когда
    понадобится открыть старый объект."""
    raw = _check_raw_key(public_key, "публичный ключ")
    return hashlib.sha256(raw).hexdigest()[:_DIGEST_HEX_LEN]


def load_pseudonym_salt(env: Mapping[str, str] | None = None) -> bytes:
    """Соль псевдонимов из `JARVIS_BACKUP_KEY_SALT` (base64, ≥16 байт).

    Живёт на хосте рядом с публичным ключом (§9.1). Фейл-клоуз, как у
    публичного ключа: без соли имя объекта пришлось бы собирать из слага —
    то есть надписать адрес чужого бизнеса снаружи конверта."""
    source = os.environ if env is None else env
    raw = source.get(_PSEUDONYM_SALT_ENV)
    if raw is None:
        raise BackupCryptoError(
            f"переменная {_PSEUDONYM_SALT_ENV} не задана: без соли псевдоним "
            "слага собрать нечем, а класть слаг в ключ объекта нельзя "
            "(DEV-46 §9.1)")
    if not raw.strip():
        raise BackupCryptoError(
            f"переменная {_PSEUDONYM_SALT_ENV} пуста — это не «псевдонимы "
            "выключены», это ненастроенный бэкап")
    salt = _b64_or_fail(raw.strip(), _PSEUDONYM_SALT_ENV)
    if len(salt) < _MIN_PSEUDONYM_SALT_LEN:
        raise BackupCryptoError(
            f"{_PSEUDONYM_SALT_ENV} декодируется в {len(salt)} Б при минимуме "
            f"{_MIN_PSEUDONYM_SALT_LEN} Б: короткая соль перебирается по "
            "списку известных слагов за секунды")
    return salt


def pseudonym(slug: str, salt: bytes) -> str:
    """`HMAC-SHA256(соль, слаг)`, первые 16 hex — имя клиента в ключе объекта.

    СТАБИЛЬНЫЙ по построению (§9.1): `verify_uploaded` сверяет ожидаемые
    имена, ротация ходит по ключам, дрил ищет вчерашний объект — плавающий
    псевдоним потерял бы объект. Соответствие «псевдоним → слаг»
    восстанавливается только у владельца, вместе с приватным ключом."""
    if not isinstance(slug, str):
        raise BackupCryptoError(
            f"слаг должен быть str, получено {type(slug).__name__}")
    if not slug.strip():
        raise BackupCryptoError(
            "пустой слаг: псевдоним от пустого имени стабилен и одинаков у "
            "всех клиентов — объекты склеились бы в одну папку")
    if not isinstance(salt, (bytes, bytearray)):
        raise BackupCryptoError(
            f"соль псевдонимов должна быть bytes, "
            f"получено {type(salt).__name__}")
    if not salt:
        raise BackupCryptoError(
            "пустая соль псевдонимов: HMAC без ключа — это открытый sha256 "
            "слага, который перебирается по списку клиентов")
    digest = hmac.new(bytes(salt), slug.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:_DIGEST_HEX_LEN]


def _b64_or_fail(value: str, env_name: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BackupCryptoError(
            f"{env_name} не является корректным base64: {exc}") from exc
