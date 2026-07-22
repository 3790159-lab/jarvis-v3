"""P1/P2 wiring (спека §4 пункты 3-5): раннер и логин работают с .enc.

Ключевое решение §2.2: НИКОГДА не держать plaintext-сессию на диске.
Раннер: .enc → DPAPI-decrypt в память → StringSession → TelegramClient.
Логин: интерактивная сессия в памяти → StringSession-строка → сразу .enc.
Все тесты на фикстурах/фейках — ни реального TelegramClient, ни живых
секретов (мокнутый клиент, фейковый конвертер сессий).
"""
from __future__ import annotations

import sys

import pytest

from chatter.security.crypto import decrypt_from_file
from chatter.security.secret_loader import (
    SecretLoaderError, load_string_session, save_string_session,
)
from chatter.telethon_login import persist_login_session
from chatter.telethon_run import build_session, derive_session_enc_path

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="DPAPI есть только на Windows")


class FakeStringSession:
    """Фейк StringSession: фиксирует, из какой СТРОКИ построен (пункт 3
    спеки: клиент конструируется из строки, не из файла)."""

    def __init__(self, string: str) -> None:
        self.string = string


def test_enc_path_derived_from_session_path():
    assert derive_session_enc_path(".secrets/demo.session") == \
        ".secrets/demo.session.enc"


# --- пункт 3: раннер строит сессию из .enc-строки, не из файла --------------

def test_build_session_from_enc(tmp_path):
    session_path = tmp_path / "demo.session"
    save_string_session(derive_session_enc_path(str(session_path)),
                        "1ApWfake-string")
    session = build_session(str(session_path),
                            string_session_cls=FakeStringSession)
    assert isinstance(session, FakeStringSession)
    assert session.string == "1ApWfake-string"


def test_build_session_prefers_enc_over_plaintext(tmp_path):
    """Есть и .enc, и plaintext (бэкап до шреда) → работаем с .enc,
    повторная миграция НЕ запускается."""
    session_path = tmp_path / "demo.session"
    session_path.write_bytes(b"legacy-sqlite")
    save_string_session(derive_session_enc_path(str(session_path)), "from-enc")

    def exploding_converter(path):  # миграция не должна дёргаться
        raise AssertionError("re-migration must not happen")

    session = build_session(str(session_path),
                            string_session_cls=FakeStringSession,
                            session_to_string=exploding_converter)
    assert session.string == "from-enc"


# --- пункт 4: нет ни .enc, ни legacy → явная ошибка старта ------------------

def test_build_session_nothing_on_disk_is_explicit_error(tmp_path):
    with pytest.raises(SecretLoaderError, match="acme.session"):
        build_session(str(tmp_path / "acme.session"),
                      string_session_cls=FakeStringSession)


# --- пункт 5: legacy plaintext есть, .enc нет → одноразовая миграция --------

def test_build_session_migrates_legacy_plaintext(tmp_path):
    session_path = tmp_path / "demo.session"
    session_path.write_bytes(b"legacy-sqlite-bytes")
    converted: list[str] = []

    def fake_converter(path: str) -> str:
        converted.append(path)
        return "converted-string-session"

    session = build_session(str(session_path),
                            string_session_cls=FakeStringSession,
                            session_to_string=fake_converter)

    assert converted == [str(session_path)]
    assert session.string == "converted-string-session"
    # .enc создан и расшифровывается в ту же строку (проверка миграции §6 п.4)
    enc = derive_session_enc_path(str(session_path))
    assert load_string_session(enc) == "converted-string-session"
    # plaintext остаётся бэкапом отката — шред только на cutover п.8, руками
    assert session_path.exists()


def test_build_session_migration_empty_string_is_error(tmp_path):
    """SQLite-сессия без auth_key даёт пустую строку → это НЕ сессия,
    явная ошибка вместо тихого сохранения пустого .enc."""
    session_path = tmp_path / "demo.session"
    session_path.write_bytes(b"no-auth-key")
    with pytest.raises(SecretLoaderError, match="auth"):
        build_session(str(session_path),
                      string_session_cls=FakeStringSession,
                      session_to_string=lambda p: "")
    # пустой .enc не должен остаться на диске
    from pathlib import Path
    assert not Path(derive_session_enc_path(str(session_path))).exists()


# --- логин: сессия из памяти сразу в .enc, plaintext не пишется -------------

def test_persist_login_session_writes_enc_only(tmp_path):
    enc = tmp_path / "acme.session.enc"
    persist_login_session(object(), str(enc),
                          session_to_string=lambda s: "post-login-string")
    assert load_string_session(enc) == "post-login-string"
    assert b"post-login-string" not in enc.read_bytes()
    # рядом не появился plaintext .session
    assert list(tmp_path.iterdir()) == [enc]


def test_persist_login_session_no_auth_is_explicit_error(tmp_path):
    with pytest.raises(SecretLoaderError, match="auth"):
        persist_login_session(object(), str(tmp_path / "x.enc"),
                              session_to_string=lambda s: "")
