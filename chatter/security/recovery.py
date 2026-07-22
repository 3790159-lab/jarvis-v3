"""Экспорт/импорт бэкапа секретов под паролем владельца (P1/P2 §12).

🔴 Блокер приёмки P1/P2: DPAPI привязан к учётке+машине, поэтому смерть
диска/профиля без внешнего бэкапа = потеря всех сессий клиентов. Экспорт
НАМЕРЕННО не через DPAPI (циркулярность — бэкап должен открываться на
ЧУЖОЙ машине): пароль владельца → scrypt → AES-256-GCM.

Формат (версионирован):
    MAGIC(7) | version(1) | salt(16) | nonce(12) | AESGCM(payload)
payload = JSON {имя: base64(bytes)}; magic+version идут в AAD — подмена
заголовка рвёт тег. Правило процесса: экспорт обязателен после подключения
КАЖДОГО нового клиента, хранение вне машины (ONBOARDING_MANUAL).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BUNDLE_MAGIC = b"JRVBAK\x00"
BUNDLE_VERSION = 1

_SALT_LEN = 16
_NONCE_LEN = 12
# Параметры scrypt: интерактивный профиль (~100мс), защита от офлайн-брута
# бэкапа, утёкшего с телефона/облака владельца.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 15, 8, 1


class RecoveryError(Exception):
    """Экспорт/импорт бэкапа не удался. Явно, не тихо (DEV-18)."""


def _derive_key(password: str, salt: bytes) -> bytes:
    if not password:
        raise RecoveryError("пустой пароль недопустим для бэкапа секретов")
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, maxmem=64 * 1024 * 1024,
        dklen=32)


def _header(version: int) -> bytes:
    return BUNDLE_MAGIC + bytes([version])


def export_bundle(secrets: dict[str, bytes], password: str) -> bytes:
    payload = json.dumps(
        {name: base64.b64encode(data).decode("ascii")
         for name, data in secrets.items()},
        ensure_ascii=False).encode("utf-8")
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    header = _header(BUNDLE_VERSION)
    ct = AESGCM(_derive_key(password, salt)).encrypt(nonce, payload, header)
    return header + salt + nonce + ct


def import_bundle(blob: bytes, password: str) -> dict[str, bytes]:
    if not blob.startswith(BUNDLE_MAGIC):
        raise RecoveryError(
            "нет магического префикса JRVBAK — это не бэкап секретов "
            "(битый файл? чужой формат?)")
    version_off = len(BUNDLE_MAGIC)
    version = blob[version_off]
    if version != BUNDLE_VERSION:
        raise RecoveryError(
            f"бэкап версии {version}, поддерживается {BUNDLE_VERSION} — "
            "нужна соответствующая версия инструмента восстановления")
    body = blob[version_off + 1:]
    if len(body) < _SALT_LEN + _NONCE_LEN + 16:  # 16 = GCM-тег
        raise RecoveryError("бэкап обрезан — файл повреждён")
    salt = body[:_SALT_LEN]
    nonce = body[_SALT_LEN:_SALT_LEN + _NONCE_LEN]
    ct = body[_SALT_LEN + _NONCE_LEN:]
    try:
        payload = AESGCM(_derive_key(password, salt)).decrypt(
            nonce, ct, _header(version))
    except InvalidTag as exc:
        raise RecoveryError(
            "расшифровка бэкапа не удалась: неверный пароль или файл "
            "повреждён") from exc
    parsed = json.loads(payload.decode("utf-8"))
    return {name: base64.b64decode(b64) for name, b64 in parsed.items()}


def export_bundle_to_file(path: str | Path, secrets: dict[str, bytes],
                          password: str) -> None:
    Path(path).write_bytes(export_bundle(secrets, password))


def import_bundle_from_file(path: str | Path,
                            password: str) -> dict[str, bytes]:
    p = Path(path)
    try:
        blob = p.read_bytes()
    except OSError as exc:
        raise RecoveryError(f"не удалось прочитать бэкап {p}: {exc}") from exc
    return import_bundle(blob, password)
