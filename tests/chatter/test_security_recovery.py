"""P1/P2 §12: процедура восстановления — 🔴 блокер приёмки.

DPAPI привязан к учётке+машине → смерть диска/профиля без экспорта = потеря
всех сессий. Экспорт НАМЕРЕННО не через DPAPI (циркулярность): пароль
владельца → scrypt → AES-GCM, формат с версией. Хранение вне машины.
"""
from __future__ import annotations

import pytest

from chatter.security.recovery import (
    BUNDLE_MAGIC,
    RecoveryError,
    export_bundle,
    export_bundle_to_file,
    import_bundle,
    import_bundle_from_file,
)

SECRETS = {
    "volska.session": b"1ApWapzMBu4-fake-string-session",
    ".env": "API_KEY=s3cret\nUNICODE=значение\n".encode("utf-8"),
}


def test_round_trip():
    blob = export_bundle(SECRETS, "owner-password")
    assert import_bundle(blob, "owner-password") == SECRETS


def test_bundle_is_ciphertext():
    """Смысл §12: бандл хранится вне машины (телефон/облако) — plaintext
    в нём недопустим даже фрагментом."""
    blob = export_bundle(SECRETS, "owner-password")
    assert b"1ApWapzMBu4" not in blob
    assert b"s3cret" not in blob
    assert "volska".encode() not in blob  # имена файлов тоже секрет не выдают


def test_wrong_password_is_explicit_error():
    blob = export_bundle(SECRETS, "owner-password")
    with pytest.raises(RecoveryError, match="пароль|повреж"):
        import_bundle(blob, "wrong-password")


def test_tampered_bundle_raises():
    blob = bytearray(export_bundle(SECRETS, "pw"))
    blob[-1] ^= 0xFF
    with pytest.raises(RecoveryError):
        import_bundle(bytes(blob), "pw")


def test_not_a_bundle_raises_clear_error():
    with pytest.raises(RecoveryError, match="магическ|формат"):
        import_bundle(b"random junk that is not a bundle", "pw")


def test_unknown_version_raises_with_version():
    """Формат с версией: бандл из будущей версии → явное «нужна новая
    версия инструмента», не InvalidTag-мусор."""
    blob = bytearray(export_bundle(SECRETS, "pw"))
    blob[len(BUNDLE_MAGIC)] = 99  # байт версии сразу после магии
    with pytest.raises(RecoveryError, match="верси|99"):
        import_bundle(bytes(blob), "pw")


def test_two_exports_differ():
    """Соль/nonce свежие на каждый экспорт — одинаковый вход не даёт
    одинаковый шифртекст (иначе утечка через сравнение бэкапов)."""
    assert export_bundle(SECRETS, "pw") != export_bundle(SECRETS, "pw")


def test_empty_password_rejected():
    with pytest.raises(RecoveryError, match="парол"):
        export_bundle(SECRETS, "")


def test_file_round_trip(tmp_path):
    p = tmp_path / "jarvis_secrets_backup.jrvbak"
    export_bundle_to_file(p, SECRETS, "owner-password")
    assert import_bundle_from_file(p, "owner-password") == SECRETS


def test_import_missing_file_raises_with_path(tmp_path):
    with pytest.raises(RecoveryError, match="nope.jrvbak"):
        import_bundle_from_file(tmp_path / "nope.jrvbak", "pw")
