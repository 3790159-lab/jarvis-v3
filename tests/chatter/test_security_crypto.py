"""P1/P2 (Спринт 0 п.1): DPAPI-шифрование секретов at rest.

Критерий Даниила: компрометация диска не даёт takeover — на диске только
шифртекст, расшифровка возможна лишь тем же Windows-пользователем на той же
машине. Тесты гоняют РЕАЛЬНЫЙ DPAPI на временных данных (мы на Windows,
S4U-эксперимент 2026-07-22 подтвердил decrypt под гардиан-тасками), живые
секреты не трогаются (P1P2_SPEC §4).
"""
from __future__ import annotations

import sys

import pytest

from chatter.security.crypto import (
    MAGIC,
    CryptoError,
    decrypt,
    decrypt_from_file,
    encrypt,
    encrypt_to_file,
    is_encrypted_blob,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="DPAPI есть только на Windows")


def test_round_trip():
    data = "секрет-🔑-test".encode("utf-8")
    assert decrypt(encrypt(data)) == data


def test_ciphertext_does_not_contain_plaintext():
    """Смысл P1/P2: на диске не должно быть исходника даже фрагментом."""
    data = b"SUPER-SECRET-MARKER-1234567890"
    assert data not in encrypt(data)


def test_blob_starts_with_magic():
    """Магический префикс отличает .enc от plaintext — на нём строится
    поэтапная миграция O1 (временный фолбэк на plaintext)."""
    assert encrypt(b"x").startswith(MAGIC)


def test_is_encrypted_blob():
    assert is_encrypted_blob(encrypt(b"x"))
    assert not is_encrypted_blob(b"API_KEY=plaintext")
    assert not is_encrypted_blob(b"")


def test_tampered_blob_raises_not_garbage():
    """Спека §4.1: битый байт → явная ошибка, не тихий мусор."""
    blob = bytearray(encrypt(b"payload"))
    blob[-1] ^= 0xFF
    with pytest.raises(CryptoError):
        decrypt(bytes(blob))


def test_non_encrypted_input_raises_clear_error():
    """decrypt чужого/не-нашего блоба падает явно (спека §4.1)."""
    with pytest.raises(CryptoError, match="магическ|magic"):
        decrypt(b"this is not an encrypted blob")


def test_truncated_blob_raises():
    blob = encrypt(b"payload")
    with pytest.raises(CryptoError):
        decrypt(blob[: len(MAGIC) + 3])


def test_empty_payload_round_trip():
    assert decrypt(encrypt(b"")) == b""


def test_file_round_trip(tmp_path):
    p = tmp_path / "demo.session.enc"
    encrypt_to_file(p, b"string-session-material")
    assert decrypt_from_file(p) == b"string-session-material"
    # на диске — шифртекст с магией, не plaintext
    raw = p.read_bytes()
    assert raw.startswith(MAGIC)
    assert b"string-session-material" not in raw


def test_decrypt_missing_file_raises_with_path(tmp_path):
    """Отсутствие .enc → понятная ошибка с путём, не FileNotFoundError
    из глубины (спека §4.2/4.4: явная ошибка старта, не тихий фейл)."""
    missing = tmp_path / "nope.enc"
    with pytest.raises(CryptoError, match="nope.enc"):
        decrypt_from_file(missing)
