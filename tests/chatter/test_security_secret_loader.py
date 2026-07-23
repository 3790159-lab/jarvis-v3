"""P1/P2: secret_loader — общий загрузчик секретов Jarvis (решение O1).

`.env.enc` → decrypt в память → environ (plaintext на диске не лежит);
StringSession → `.enc` (plaintext-сессии на диске нет НИКОГДА, спека §2.2).
Фолбэк на plaintext — ВРЕМЕННЫЙ (этап 2 миграции O1), только по явному
флагу, снимается в конце спринта.
"""
from __future__ import annotations

import sys

import pytest

from chatter.security.crypto import encrypt, encrypt_to_file
from chatter.security.secret_loader import (
    SecretLoaderError,
    load_env,
    load_string_session,
    migrate_plaintext_file,
    parse_env_text,
    save_string_session,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="DPAPI есть только на Windows")


# --- parse_env_text: та же толерантная семантика, что _parse_env_file -------

def test_parse_env_text_semantics():
    text = (
        "# комментарий\n"
        "\n"
        "PLAIN=value\n"
        'QUOTED="in quotes"\n'
        "SPACED = padded \n"
        "broken-no-equals\n"
        "=no-key\n"
    )
    assert parse_env_text(text) == {
        "PLAIN": "value", "QUOTED": "in quotes", "SPACED": "padded"}


# --- load_env: .env.enc → environ -------------------------------------------

def test_load_env_from_encrypted_file(tmp_path):
    enc = tmp_path / ".env.enc"
    encrypt_to_file(enc, b"API_KEY=s3cret\nOTHER=x\n")
    environ: dict[str, str] = {}
    loaded = load_env(enc, environ=environ)
    assert environ == {"API_KEY": "s3cret", "OTHER": "x"}
    assert loaded == {"API_KEY": "s3cret", "OTHER": "x"}


def test_load_env_does_not_override_real_environment(tmp_path):
    """Прецедент проекта (load_api_credentials, env_bootstrap): реальная
    переменная окружения ВСЕГДА побеждает файловый фолбэк."""
    enc = tmp_path / ".env.enc"
    encrypt_to_file(enc, b"API_KEY=from-file\n")
    environ = {"API_KEY": "from-real-env"}
    load_env(enc, environ=environ)
    assert environ["API_KEY"] == "from-real-env"


def test_load_env_missing_file_is_explicit_error(tmp_path):
    """Нет .enc и фолбэк не разрешён → явная ошибка старта с путём,
    не тихий фейл (спека §4.2/4.4, DEV-18)."""
    with pytest.raises(SecretLoaderError, match="env.enc"):
        load_env(tmp_path / ".env.enc", environ={})


def test_load_env_plaintext_fallback_requires_explicit_opt_in(tmp_path):
    """Фолбэк O1 — временный и ЯВНЫЙ: лежащий рядом plaintext .env сам по
    себе не подхватывается (иначе фолбэк «навсегда» и незаметно)."""
    (tmp_path / ".env").write_text("API_KEY=plain\n", encoding="utf-8")
    with pytest.raises(SecretLoaderError):
        load_env(tmp_path / ".env.enc", environ={})


def test_load_env_plaintext_fallback_when_opted_in(tmp_path):
    plain = tmp_path / ".env"
    plain.write_text("API_KEY=plain\n", encoding="utf-8")
    environ: dict[str, str] = {}
    load_env(tmp_path / ".env.enc", environ=environ, fallback_plaintext=plain)
    assert environ == {"API_KEY": "plain"}


def test_load_env_prefers_encrypted_over_fallback(tmp_path):
    """Если .enc есть — plaintext-фолбэк игнорируется (иначе откат на
    незашифрованное прошёл бы незамеченным)."""
    enc = tmp_path / ".env.enc"
    encrypt_to_file(enc, b"API_KEY=encrypted\n")
    plain = tmp_path / ".env"
    plain.write_text("API_KEY=plain\n", encoding="utf-8")
    environ: dict[str, str] = {}
    load_env(enc, environ=environ, fallback_plaintext=plain)
    assert environ["API_KEY"] == "encrypted"


# --- фолбэк ДОЛЖЕН КРИЧАТЬ: TG-алерт на каждое фактическое использование ----
# Требование Даниила 2026-07-22: тихий фолбэк = «думаем, что зашифровано,
# а оно нет». Warning в лог — шёпот; алерт в Telegram — крик.

def test_fallback_sends_telegram_alert(tmp_path):
    plain = tmp_path / ".env"
    plain.write_text("API_KEY=plain\nTELEGRAM_BOT_TOKEN=tok123\n",
                     encoding="utf-8")
    sent: list[tuple[str, bytes]] = []
    load_env(tmp_path / ".env.enc", environ={},
             fallback_plaintext=plain,
             alert_transport=lambda url, payload: sent.append((url, payload)))
    assert len(sent) == 1
    url, payload = sent[0]
    assert "tok123" in url                      # токен из только что
    assert b"plaintext" in payload.lower()      # загруженных секретов
    assert str(plain).encode() in payload or plain.name.encode() in payload


def test_no_alert_when_encrypted_path_used(tmp_path):
    enc = tmp_path / ".env.enc"
    encrypt_to_file(enc, b"API_KEY=enc\nTELEGRAM_BOT_TOKEN=tok\n")
    sent: list = []
    load_env(enc, environ={},
             fallback_plaintext=tmp_path / ".env",
             alert_transport=lambda url, payload: sent.append(1))
    assert sent == []


def test_fallback_without_token_logs_error_but_loads(tmp_path, caplog):
    """Нет токена → алерт послать нечем: громкая ошибка в лог (DEV-18),
    но секреты загружены — старт не блокируем, блокировка = свой отказ."""
    plain = tmp_path / ".env"
    plain.write_text("API_KEY=plain\n", encoding="utf-8")
    with caplog.at_level("ERROR"):
        values = load_env(tmp_path / ".env.enc", environ={},
                          fallback_plaintext=plain,
                          alert_transport=lambda url, payload: None)
    assert values == {"API_KEY": "plain"}
    assert any("алерт" in r.message.lower() or "token" in r.message.lower()
               for r in caplog.records)


def test_fallback_alert_transport_failure_is_logged_not_raised(tmp_path, caplog):
    """Упавший алерт не роняет старт, но и не глотается молча (DEV-18)."""
    plain = tmp_path / ".env"
    plain.write_text("API_KEY=plain\nTELEGRAM_BOT_TOKEN=tok\n",
                     encoding="utf-8")

    def broken(url, payload):
        raise OSError("network down")

    with caplog.at_level("ERROR"):
        values = load_env(tmp_path / ".env.enc", environ={},
                          fallback_plaintext=plain, alert_transport=broken)
    assert values["API_KEY"] == "plain"
    assert any("network down" in r.message or "алерт" in r.message.lower()
               for r in caplog.records)


# --- StringSession: plaintext-сессии на диске нет никогда (§2.2) ------------

def test_string_session_round_trip(tmp_path):
    enc = tmp_path / "demo.session.enc"
    save_string_session(enc, "1ApWapzMBu4-fake-string-session")
    assert load_string_session(enc) == "1ApWapzMBu4-fake-string-session"
    assert b"1ApWapzMBu4" not in enc.read_bytes()  # на диске только шифртекст


def test_load_string_session_missing_file(tmp_path):
    with pytest.raises(SecretLoaderError, match="acme.session.enc"):
        load_string_session(tmp_path / "acme.session.enc")


# --- одноразовая миграция legacy plaintext → .enc (спека §4.5) --------------

def test_migrate_plaintext_creates_enc_and_keeps_backup(tmp_path):
    """Plaintext остаётся как бэкап отката — шред только на cutover п.8,
    после верификации, руками (спека §6)."""
    plain = tmp_path / "demo.session"
    plain.write_bytes(b"legacy-session-bytes")
    enc = tmp_path / "demo.session.enc"

    migrated = migrate_plaintext_file(plain, enc)

    assert migrated is True
    from chatter.security.crypto import decrypt_from_file
    assert decrypt_from_file(enc) == b"legacy-session-bytes"
    assert plain.exists()  # бэкап на месте


def test_migrate_is_noop_when_enc_exists(tmp_path):
    """Существующий .enc не затираем (повторный запуск = no-op)."""
    plain = tmp_path / "demo.session"
    plain.write_bytes(b"new-material")
    enc = tmp_path / "demo.session.enc"
    encrypt_to_file(enc, b"already-migrated")

    migrated = migrate_plaintext_file(plain, enc)

    assert migrated is False
    from chatter.security.crypto import decrypt_from_file
    assert decrypt_from_file(enc) == b"already-migrated"


def test_migrate_without_plaintext_is_error(tmp_path):
    """Нет ни plaintext, ни .enc → явная ошибка (спека §4.4)."""
    with pytest.raises(SecretLoaderError, match="demo.session"):
        migrate_plaintext_file(tmp_path / "demo.session",
                               tmp_path / "demo.session.enc")
