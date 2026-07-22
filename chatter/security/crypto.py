"""DPAPI-шифрование секретов at rest (P1/P2, Спринт 0 п.1).

Windows DPAPI (CryptProtectData, scope=CurrentUser): блоб расшифровывается
только тем же пользователем на той же машине → украденный диск/бэкап
бесполезен (критерий Даниила). Пароль/passphrase не нужен — раннер под
гардианом стартует без консоли; S4U-эксперимент 2026-07-22 подтвердил
расшифровку из планировщика (P1P2_SPEC §9.1).

ctypes к crypt32.dll вместо pywin32 — без нового пакета в requirements
(P1P2_SPEC §2.1).
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

# Магический префикс отличает наш .enc от plaintext: на нём строится
# поэтапная миграция O1 (temporary plaintext-фолбэк) и явные ошибки
# «это не зашифрованный блоб» вместо мусора из глубин DPAPI.
MAGIC = b"JRVSEC1\x00"

_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class CryptoError(Exception):
    """Любой отказ шифрования/расшифровки. Явный, не тихий (DEV-18)."""


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_in(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data) or 1)
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _dpapi(data: bytes, protect: bool) -> bytes:
    if sys.platform != "win32":  # pragma: no cover - прод только Windows
        raise CryptoError("DPAPI доступен только на Windows")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    src, out = _blob_in(data), _DATA_BLOB()
    ok = fn(ctypes.byref(src), None, None, None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        op = "шифрование" if protect else "расшифровка"
        raise CryptoError(
            f"DPAPI: {op} не удалась (WinError {ctypes.GetLastError()}). "
            "Возможные причины: блоб повреждён или зашифрован другим "
            "пользователем/на другой машине.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def encrypt(data: bytes) -> bytes:
    """bytes → шифртекст с магическим префиксом (user-scope DPAPI)."""
    return MAGIC + _dpapi(data, protect=True)


def decrypt(blob: bytes) -> bytes:
    """Шифртекст → bytes. CryptoError: нет магии / tamper / чужой блоб."""
    if not blob.startswith(MAGIC):
        raise CryptoError(
            "нет магического префикса JRVSEC1 — это не зашифрованный "
            "секрет (plaintext? чужой формат?)")
    return _dpapi(blob[len(MAGIC):], protect=False)


def is_encrypted_blob(blob: bytes) -> bool:
    return blob.startswith(MAGIC)


def encrypt_to_file(path: str | Path, data: bytes) -> None:
    Path(path).write_bytes(encrypt(data))


def decrypt_from_file(path: str | Path) -> bytes:
    p = Path(path)
    try:
        blob = p.read_bytes()
    except OSError as exc:
        raise CryptoError(f"не удалось прочитать секрет {p}: {exc}") from exc
    return decrypt(blob)
