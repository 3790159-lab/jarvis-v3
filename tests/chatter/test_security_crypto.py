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
    ENTROPY_SIZE,
    MAGIC,
    CryptoError,
    decrypt,
    decrypt_from_file,
    encrypt,
    encrypt_to_file,
    generate_entropy,
    is_encrypted_blob,
    load_entropy,
    restrict_to_system_admins,
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


# ---------------------------------------------------------------------------
# Рев. 2 (спека §2.1/§4.6): machine-scope + optionalEntropy


def test_entropy_generate_creates_exactly_32_bytes(tmp_path):
    p = tmp_path / "e.bin"
    assert generate_entropy(p) is True
    assert p.stat().st_size == ENTROPY_SIZE == 32


def test_entropy_generate_does_not_overwrite_existing(tmp_path):
    """Спека §4.6: повторный вызов НЕ перезаписывает файл — перезапись
    entropy = все существующие блобы навсегда нечитаемы."""
    p = tmp_path / "e.bin"
    generate_entropy(p)
    before = p.read_bytes()
    assert generate_entropy(p) is False
    assert p.read_bytes() == before


def test_load_entropy_missing_raises_with_path(tmp_path):
    """Спека §4.6: нет entropy.bin → явная ошибка, НЕ тихий fallback на
    без-entropy (иначе блоб теряет второй фактор незаметно)."""
    missing = tmp_path / "no_entropy.bin"
    with pytest.raises(CryptoError, match="no_entropy.bin"):
        load_entropy(missing)


def test_load_entropy_wrong_size_raises(tmp_path):
    """Обрезанный/повреждённый entropy-файл → явная ошибка, не блоб на
    мусорном факторе."""
    p = tmp_path / "e.bin"
    p.write_bytes(b"\x01" * 16)
    with pytest.raises(CryptoError, match="32"):
        load_entropy(p)


def test_round_trip_with_explicit_entropy_path(tmp_path):
    p = tmp_path / "e.bin"
    generate_entropy(p)
    data = "секрет-🔑".encode("utf-8")
    assert decrypt(encrypt(data, entropy_path=p), entropy_path=p) == data


def test_decrypt_with_different_entropy_raises(tmp_path):
    """Спека §4.6: блоб, шифрованный с другой entropy → ошибка, не мусор."""
    e1, e2 = tmp_path / "e1.bin", tmp_path / "e2.bin"
    generate_entropy(e1)
    generate_entropy(e2)
    blob = encrypt(b"payload", entropy_path=e1)
    with pytest.raises(CryptoError):
        decrypt(blob, entropy_path=e2)


def test_encrypt_without_entropy_file_raises(tmp_path):
    """Нет entropy → encrypt тоже падает явно: нельзя молча создать блоб
    без второго фактора."""
    with pytest.raises(CryptoError):
        encrypt(b"x", entropy_path=tmp_path / "absent.bin")


def test_default_entropy_path_comes_from_env(tmp_path, monkeypatch):
    """Раннер/гардиан задают JARVIS_ENTROPY_FILE; без него — дефолт
    .secrets/entropy.bin (спека §0.3)."""
    p = tmp_path / "env_entropy.bin"
    generate_entropy(p)
    monkeypatch.setenv("JARVIS_ENTROPY_FILE", str(p))
    assert decrypt(encrypt(b"via-env")) == b"via-env"


def test_flags_include_local_machine_scope():
    """Рев. 2: scope = LocalMachine (0x04) — блоб не зависит от логона
    юзера (спека §2.1). Сам скоуп в процессе не наблюдаем (DPAPI того же
    юзера читает оба) — фиксируем флаг, живая проверка = §8.1."""
    from chatter.security import crypto

    assert crypto._CRYPTPROTECT_LOCAL_MACHINE == 0x04
    assert crypto._PROTECT_FLAGS & crypto._CRYPTPROTECT_LOCAL_MACHINE


def test_restrict_acl_strips_inheritance_and_leaves_system_admins(tmp_path):
    """Спека §0.3: ACL на entropy = SYSTEM + Administrators, наследование
    срезано. Проверяем фактическим icacls-выводом: ни одного (I)-ACE и
    ровно два ACE."""
    import subprocess

    p = tmp_path / "e.bin"
    generate_entropy(p)
    restrict_to_system_admins(p)
    out = subprocess.run(
        ["icacls", str(p)], capture_output=True, text=True, check=True,
    ).stdout
    aces = [ln.strip() for ln in out.splitlines()
            if ":(" in ln and "Successfully" not in ln]
    assert len(aces) == 2, out
    assert not any("(I)" in a for a in aces), out
