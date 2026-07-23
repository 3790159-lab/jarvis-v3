"""DPAPI-шифрование секретов at rest (P1/P2, Спринт 0 п.1, рев. 2).

Windows DPAPI (CryptProtectData) **scope=LocalMachine + optionalEntropy**
(спека §2.1/§0.3): блоб расшифровывается только на этой машине процессом,
способным прочитать entropy-файл (ACL: SYSTEM+Administrators). От логона и
пароля юзера не зависит → AtStartup-старт до первого логона гарантирован
архитектурно (user-scope в честном окне мёртв — спека §13). Защита от
кражи диска — НЕ здесь, а в слое «диск» (BitLocker TPM-only, §0.2).

Entropy: 32 случайных байта в `.secrets/entropy.bin` (путь переопределяется
JARVIS_ENTROPY_FILE). Отсутствие файла = явная ошибка, тихого fallback на
без-entropy нет (§4.6). Повторная генерация НИКОГДА не перезаписывает
существующий файл — это убило бы все блобы.

ctypes к crypt32.dll вместо pywin32 — без нового пакета в requirements
(P1P2_SPEC §2.1).
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

# Магический префикс отличает наш .enc от plaintext: на нём строится
# поэтапная миграция O1 (temporary plaintext-фолбэк) и явные ошибки
# «это не зашифрованный блоб» вместо мусора из глубин DPAPI.
MAGIC = b"JRVSEC1\x00"

ENTROPY_SIZE = 32
# Дефолт относительно cwd раннера (C:\jarvis) — тот же принцип, что
# .secrets\<slug>.session; гардиан/тесты переопределяют через env.
_ENTROPY_ENV = "JARVIS_ENTROPY_FILE"
DEFAULT_ENTROPY_PATH = Path(".secrets") / "entropy.bin"

_CRYPTPROTECT_UI_FORBIDDEN = 0x01
_CRYPTPROTECT_LOCAL_MACHINE = 0x04
_PROTECT_FLAGS = _CRYPTPROTECT_UI_FORBIDDEN | _CRYPTPROTECT_LOCAL_MACHINE

# SID-формы вместо имён — не зависят от языка Windows.
_SID_SYSTEM = "*S-1-5-18"
_SID_ADMINISTRATORS = "*S-1-5-32-544"


class CryptoError(Exception):
    """Любой отказ шифрования/расшифровки. Явный, не тихий (DEV-18)."""


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob_in(data: bytes) -> _DATA_BLOB:
    buf = ctypes.create_string_buffer(data, len(data) or 1)
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _entropy_path(override: str | Path | None = None) -> Path:
    if override is not None:
        return Path(override)
    env = os.environ.get(_ENTROPY_ENV)
    return Path(env) if env else DEFAULT_ENTROPY_PATH


def generate_entropy(path: str | Path | None = None) -> bool:
    """Создаёт entropy-файл (ровно 32Б os.urandom). Существующий файл НЕ
    перезаписывается (спека §4.6) — перезапись = все блобы нечитаемы.
    Возвращает True, если файл создан этим вызовом."""
    p = _entropy_path(path)
    if p.exists():
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(os.urandom(ENTROPY_SIZE))
    try:
        tmp.rename(p)  # атомарно; проигрыш гонки = FileExistsError, не затирание
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        if p.exists():
            return False
        raise CryptoError(f"не удалось создать entropy {p}: {exc}") from exc
    return True


def load_entropy(path: str | Path | None = None) -> bytes:
    """Строгое чтение entropy: нет файла или не 32Б → CryptoError.
    Тихого fallback на без-entropy НЕТ (спека §4.6)."""
    p = _entropy_path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise CryptoError(
            f"нет entropy-файла {p} ({exc}). Сгенерировать: "
            "chatter.security.crypto.generate_entropy() на setup-шаге "
            "(P1P2_SPEC §0.3); тихого режима без entropy нет.") from exc
    if len(data) != ENTROPY_SIZE:
        raise CryptoError(
            f"entropy-файл {p} повреждён: {len(data)}Б вместо "
            f"{ENTROPY_SIZE}Б — блобы на таком факторе не создаём")
    return data


def restrict_to_system_admins(path: str | Path) -> None:
    """ACL: только SYSTEM + Administrators (Full), наследование срезано
    (спека §0.3). SID-формы — независимо от локали. Отказ icacls = явная
    ошибка (DEV-18), не тихо-открытый файл.

    На КАТАЛОГЕ гранты обязаны быть наследуемыми (OI)(CI): срез
    наследования выкидывает у детей унаследованные ACE, и без (OI)(CI)
    дети остаются с ПУСТЫМ DACL — Permission denied для всех, включая
    elevated Admin (вскрыто живым прогоном boot-probe)."""
    p = Path(path)
    perm = "(OI)(CI)(F)" if p.is_dir() else "(F)"
    res = subprocess.run(
        ["icacls", str(p), "/inheritance:r",
         "/grant:r", f"{_SID_SYSTEM}:{perm}", f"{_SID_ADMINISTRATORS}:{perm}"],
        capture_output=True, text=True)
    if res.returncode != 0:
        raise CryptoError(
            f"icacls не смог закрыть ACL на {p}: "
            f"{(res.stderr or res.stdout).strip()}")


def _dpapi(data: bytes, protect: bool, entropy: bytes) -> bytes:
    if sys.platform != "win32":  # pragma: no cover - прод только Windows
        raise CryptoError("DPAPI доступен только на Windows")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    src, out = _blob_in(data), _DATA_BLOB()
    ent = _blob_in(entropy)
    ok = fn(ctypes.byref(src), None, ctypes.byref(ent), None, None,
            _PROTECT_FLAGS, ctypes.byref(out))
    if not ok:
        op = "шифрование" if protect else "расшифровка"
        raise CryptoError(
            f"DPAPI: {op} не удалась (WinError {ctypes.GetLastError()}). "
            "Возможные причины: блоб повреждён, зашифрован на другой машине "
            "или с другой entropy (JARVIS_ENTROPY_FILE указывает не туда?).")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def encrypt(data: bytes, *, entropy_path: str | Path | None = None) -> bytes:
    """bytes → шифртекст с магическим префиксом (machine-scope + entropy)."""
    return MAGIC + _dpapi(data, protect=True,
                          entropy=load_entropy(entropy_path))


def decrypt(blob: bytes, *, entropy_path: str | Path | None = None) -> bytes:
    """Шифртекст → bytes. CryptoError: нет магии / tamper / чужая машина /
    чужая entropy."""
    if not blob.startswith(MAGIC):
        raise CryptoError(
            "нет магического префикса JRVSEC1 — это не зашифрованный "
            "секрет (plaintext? чужой формат?)")
    return _dpapi(blob[len(MAGIC):], protect=False,
                  entropy=load_entropy(entropy_path))


def is_encrypted_blob(blob: bytes) -> bool:
    return blob.startswith(MAGIC)


def encrypt_to_file(path: str | Path, data: bytes, *,
                    entropy_path: str | Path | None = None) -> None:
    Path(path).write_bytes(encrypt(data, entropy_path=entropy_path))


def decrypt_from_file(path: str | Path, *,
                      entropy_path: str | Path | None = None) -> bytes:
    p = Path(path)
    try:
        blob = p.read_bytes()
    except OSError as exc:
        raise CryptoError(f"не удалось прочитать секрет {p}: {exc}") from exc
    return decrypt(blob, entropy_path=entropy_path)
